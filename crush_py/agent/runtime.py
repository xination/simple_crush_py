import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from ..backends.base import AssistantTurn, BackendError, BaseBackend
from ..backends.openai_compat import OpenAICompatBackend
from ..config import AppConfig, BackendConfig
from ..output_sanitize import sanitize_text
from ..store.session_store import SessionMeta, SessionStore
from ..tools.common import read_text_with_fallback
from ..tools.registry import ToolRegistry
from .backend_retry import (
    ask_without_tools,
    compact_retry_text,
    compact_retry_tool_result,
    fallback_messages_for_retry,
    generate_turn_with_retry,
    stream_text_with_retry,
)
from .guide_runtime import GuideRuntimeMixin
from .intent_router import IntentDecision, heuristic_intent_decision, merge_intent_decision, route_intent_with_llm
from .message_builder import (
    assistant_content_for_tool_turn,
    assistant_text_blocks,
    build_history_summary,
    messages_for_backend,
    single_line as _single_line,
    squashed_assistant_text,
    stored_tool_result_content,
    stored_tool_use_content,
)
from .prompt_intent import PromptIntent, classify_prompt_intent
from .quick_file_cache import (
    cat_summary_from_cache,
    extract_text_from_cat_result,
    maybe_cache_quick_file_from_cat,
    normalize_quick_file_path,
    read_quick_file,
)
from .reader_runtime import ReaderRuntimeMixin
from .runtime_prompts import (
    BASE_READ_HELPER_SYSTEM_PROMPT,
    DIRECT_ANSWER_APPENDIX,
    DIRECT_FILE_APPENDIX,
    GUIDE_APPENDIX,
    PLANNER_APPENDIX,
    TRACE_APPENDIX,
)
from .summary_runtime import SummaryRuntimeMixin
from .tool_loop import (
    ask_with_tool_loop,
    emit_stream_final_text,
    execute_agent_tool_calls,
    executed_calls_from_turn,
    record_agent_tool_use,
    repo_evidence_required_message,
    should_accept_reader_summary_directly,
    store_final_assistant_text,
)
from .tool_result_formatter import (
    backend_tool_result_content,
    decide_forced_cat,
    extract_candidate_paths,
    summarize_find_result,
    summarize_grep_result,
    summarize_outline_result,
    summarize_tool_result,
    tool_result_encoding,
)
from .trace_runtime import TraceRuntimeMixin

MAX_TOOL_ROUNDS = 6
MAX_TOOL_CALLS_PER_ROUND = 2
MAX_RECENT_MESSAGES = 4
MAX_INLINE_CAT_RESULT_CHARS = 6000
MAX_BACKEND_RETRIES = 1
MAX_QUICK_FILE_SIZE = 1024 * 1024
LOCATOR_TOOL_NAMES = ("ls", "tree", "find", "grep")
READ_TOOL_NAMES = LOCATOR_TOOL_NAMES + ("cat",)


@dataclass
class SessionRuntimeState:
    entry_point: str = ""
    confirmed_paths: List[str] = field(default_factory=list)
    unresolved_branches: List[str] = field(default_factory=list)
    file_summaries: Dict[str, str] = field(default_factory=dict)
    summary_cache: Dict[Tuple[str, float, int, int], str] = field(default_factory=dict)
    intent_cache: Dict[str, IntentDecision] = field(default_factory=dict)
    quick_file_cache: Dict[Tuple[str, float, int], str] = field(default_factory=dict)
    quick_file_cache_sources: Dict[Tuple[str, float, int], str] = field(default_factory=dict)


class AgentRuntime(GuideRuntimeMixin, SummaryRuntimeMixin, TraceRuntimeMixin, ReaderRuntimeMixin):
    def __init__(self, config: AppConfig, session_store: SessionStore):
        self.config = config
        self.session_store = session_store
        self.active_session: Optional[SessionMeta] = None
        self.active_backend_name = config.default_backend
        self.tools = ToolRegistry(config)
        self._session_states: Dict[str, SessionRuntimeState] = {}

    def new_session(self, backend_name: Optional[str] = None, title: str = "Untitled Session") -> SessionMeta:
        backend_cfg = self._get_backend_config(backend_name)
        session = self.session_store.create_session(
            backend=backend_cfg.name,
            model=backend_cfg.model,
            title=title,
        )
        self.active_session = session
        self.active_backend_name = backend_cfg.name
        self._session_states[session.id] = SessionRuntimeState()
        return session

    def use_session(self, session_id: str) -> SessionMeta:
        session = self.session_store.load_session(session_id)
        self.active_session = session
        self.active_backend_name = session.backend
        self._session_states.setdefault(session.id, SessionRuntimeState())
        return session

    def set_session_model(self, model: str) -> SessionMeta:
        if self.active_session is None:
            self.new_session()
        assert self.active_session is not None
        session = self.session_store.update_session_model(self.active_session.id, model)
        self.active_session = session
        return session

    def ask(self, prompt: str, stream: bool = False, show_thinking: bool = False) -> str:
        if self.active_session is None:
            self.new_session()
        assert self.active_session is not None

        session = self.active_session
        backend_cfg = self._backend_config_for_session(session)
        backend = self._create_backend(backend_cfg)
        state = self._state_for_session(session.id)
        if not state.entry_point:
            state.entry_point = sanitize_text(prompt).strip()

        self.session_store.append_message(session.id, "user", prompt)
        messages = self._messages_for_backend(session.id)
        decision = self._intent_decision(prompt, backend=backend if backend.supports_tool_calls() else None)
        system_prompt = self._system_prompt_for_prompt(prompt, decision=decision)

        with self._thinking_indicator(enabled=(stream or show_thinking)):
            if backend.supports_tool_calls():
                if decision.needs_tools:
                    text = self._ask_with_tool_loop(
                        session.id,
                        backend,
                        messages,
                        prompt,
                        system_prompt,
                        decision,
                        stream=stream,
                    )
                    text = self._postprocess_direct_file_summary_output(session.id, prompt, text)
                else:
                    text = self._ask_without_tools(session.id, backend, messages, system_prompt, stream=stream)
            elif stream:
                chunks = []
                self._clear_thinking_indicator_line()
                for chunk in backend.stream_generate(system_prompt, messages):
                    chunks.append(chunk)
                    print(chunk, end="", flush=True)
                print("")
                text = sanitize_text("".join(chunks)).strip()
                self.session_store.append_message(
                    session.id,
                    "assistant",
                    text,
                    metadata={"raw_content": [{"type": "text", "text": text}]},
                )
            else:
                turn = self._generate_turn_with_retry(backend, system_prompt, messages)
                text = sanitize_text(turn.text).strip()
                self.session_store.append_message(
                    session.id,
                    "assistant",
                    text,
                    metadata={"raw_content": turn.raw_content},
                )

        self.active_session = self.session_store.load_session(session.id)
        return text

    def ask_quick_file(self, rel_path: str, prompt: str, stream: bool = False) -> str:
        if self.active_session is None:
            self.new_session()
        assert self.active_session is not None

        session = self.active_session
        backend_cfg = self._backend_config_for_session(session)
        backend = self._create_backend(backend_cfg)
        state = self._state_for_session(session.id)
        normalized_path = self._normalize_quick_file_path(rel_path)
        file_text, quick_file_debug = self._read_quick_file(normalized_path)
        user_prompt = prompt.strip()
        if not state.entry_point:
            state.entry_point = sanitize_text(user_prompt).strip()
        if normalized_path not in state.confirmed_paths:
            state.confirmed_paths.append(normalized_path)

        self.session_store.append_message(
            session.id,
            "user",
            "Quick file mode for `{0}`:\n{1}".format(normalized_path, user_prompt),
            metadata={
                "mode": "quick_file",
                "path": normalized_path,
                "quick_file_cache_status": quick_file_debug["status"],
                "quick_file_cache_source": quick_file_debug["source"],
            },
        )
        messages = [
            {
                "role": "user",
                "content": (
                    "Quick file mode is active.\n"
                    "Answer only from this file.\n"
                    "If the file does not support the request, say so clearly.\n"
                    "Target file: {0}"
                ).format(normalized_path),
            },
            {"role": "user", "content": "User request:\n{0}".format(user_prompt)},
            {
                "role": "user",
                "content": "File content from `{0}`:\n{1}".format(normalized_path, file_text),
            },
        ]
        system_prompt = BASE_READ_HELPER_SYSTEM_PROMPT + DIRECT_ANSWER_APPENDIX
        text = self._ask_without_tools(session.id, backend, messages, system_prompt, stream=stream)
        self.active_session = self.session_store.load_session(session.id)
        return text

    @contextmanager
    def _thinking_indicator(self, enabled: bool = False):
        stream = getattr(sys, "stdout", None)
        if not enabled or stream is None:
            yield
            return
        isatty = getattr(stream, "isatty", None)
        if not callable(isatty) or not isatty():
            yield
            return

        stop_event = threading.Event()
        thread = threading.Thread(target=self._run_thinking_spinner, args=(stream, stop_event), daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=0.2)
            self._clear_thinking_indicator_line()

    def _run_thinking_spinner(self, stream, stop_event):
        frames = ("[thinking   ]", "[thinking.  ]", "[thinking.. ]", "[thinking...]")
        index = 0
        while not stop_event.is_set():
            stream.write("\r" + frames[index % len(frames)])
            stream.flush()
            if stop_event.wait(0.18):
                break
            index += 1

    def _clear_thinking_indicator_line(self):
        stream = getattr(sys, "stdout", None)
        if stream is None:
            return
        isatty = getattr(stream, "isatty", None)
        if callable(isatty) and not isatty():
            return
        stream.write("\r" + (" " * 24) + "\r")
        stream.flush()

    def available_backends(self) -> List[str]:
        return sorted(self.config.backends.keys())

    def available_tools(self) -> List[str]:
        return self.tools.names()

    def run_tool(self, name: str, arguments: Dict[str, object]) -> str:
        return self.tools.run(name, arguments)

    def read_text_with_fallback(self, path):
        return read_text_with_fallback(path)

    def _messages_for_backend(self, session_id: str) -> List[Dict[str, Any]]:
        return messages_for_backend(self, session_id, MAX_RECENT_MESSAGES)

    def _build_history_summary(self, state: SessionRuntimeState, earlier_messages: List[Any]) -> str:
        return build_history_summary(state, earlier_messages)

    def _ask_with_tool_loop(
        self,
        session_id: str,
        backend: BaseBackend,
        messages: List[Dict[str, Any]],
        prompt: str,
        system_prompt: str,
        decision: IntentDecision,
        stream: bool = False,
    ) -> str:
        return ask_with_tool_loop(
            self,
            session_id,
            backend,
            messages,
            prompt,
            system_prompt,
            decision,
            stream,
            MAX_TOOL_ROUNDS,
            LOCATOR_TOOL_NAMES,
        )

    def _ask_without_tools(
        self,
        session_id: str,
        backend: BaseBackend,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        stream: bool = False,
    ) -> str:
        return ask_without_tools(self, session_id, backend, messages, system_prompt, stream=stream)

    def _stream_text_with_retry(
        self,
        backend: BaseBackend,
        system_prompt: str,
        messages: List[Dict[str, Any]],
    ) -> str:
        return stream_text_with_retry(self, backend, system_prompt, messages, MAX_BACKEND_RETRIES)

    def _repo_evidence_required_message(self, prompt: str) -> str:
        return repo_evidence_required_message(prompt)

    def _normalize_quick_file_path(self, rel_path: str) -> str:
        return normalize_quick_file_path(self, rel_path)

    def _read_quick_file(self, rel_path: str) -> Tuple[str, Dict[str, str]]:
        return read_quick_file(self, rel_path, MAX_QUICK_FILE_SIZE)

    def _summarize_tool_result(self, session_id: str, tool_name: str, arguments: Dict[str, Any], result: str) -> str:
        return summarize_tool_result(self, session_id, tool_name, arguments, result)

    def _maybe_cache_quick_file_from_cat(self, session_id: str, arguments: Dict[str, Any], result: str) -> None:
        return maybe_cache_quick_file_from_cat(self, session_id, arguments, result, MAX_QUICK_FILE_SIZE)

    def _extract_text_from_cat_result(self, result: str) -> Optional[str]:
        return extract_text_from_cat_result(result)

    def _cat_summary_from_cache(self, arguments: Dict[str, Any], result: str) -> str:
        return cat_summary_from_cache(self, arguments, result)

    def _state_for_any_session_path(self, rel_path: str) -> SessionRuntimeState:
        assert self.active_session is not None
        return self._state_for_session(self.active_session.id)

    def _summarize_find_result(self, result: str) -> str:
        return summarize_find_result(result)

    def _summarize_outline_result(self, arguments: Dict[str, Any], result: str) -> str:
        return summarize_outline_result(arguments, result)

    def _summarize_grep_result(self, arguments: Dict[str, Any], result: str) -> str:
        return summarize_grep_result(arguments, result)

    def _decide_forced_cat(self, prompt: str, candidate_paths: List[str], tool_results: List[Dict[str, Any]]) -> Optional[str]:
        return decide_forced_cat(self, prompt, candidate_paths, tool_results)

    def _repo_overview_anchor_path(self, prompt: str) -> Optional[str]:
        lowered = prompt.lower()
        if not any(term in lowered for term in ("repo", "repository", "project", "codebase")):
            return None
        if not any(term in lowered for term in ("what is", "what does", "explain", "describe", "for")):
            return None
        readme_path = self.config.workspace_root / "README.md"
        if readme_path.is_file():
            return "README.md"
        return None

    def _extract_candidate_paths(self, tool_name: str, result: str) -> List[str]:
        return extract_candidate_paths(tool_name, result)

    def _backend_tool_result_content(self, tool_name: str, result: str, summary: str) -> str:
        return backend_tool_result_content(tool_name, result, summary, MAX_INLINE_CAT_RESULT_CHARS)

    def _tool_result_encoding(self, tool_name: str, result: str) -> str:
        return tool_result_encoding(tool_name, result)

    def _stored_tool_use_content(self, message: Any) -> Any:
        return stored_tool_use_content(self, message)

    def _stored_tool_result_content(self, message: Any) -> Any:
        return stored_tool_result_content(self, message)

    def _prompt_direct_file_path(self, prompt: str) -> Optional[str]:
        for candidate in _prompt_path_candidates(prompt):
            path = (self.config.workspace_root / candidate).resolve()
            if path.is_file():
                try:
                    return path.relative_to(self.config.workspace_root).as_posix()
                except ValueError:
                    continue
        return self._implicit_single_doc_path(prompt)

    def _implicit_single_doc_path(self, prompt: str) -> Optional[str]:
        if not self._looks_like_doc_understanding_prompt(prompt):
            return None
        visible_files = []
        for path in sorted(self.config.workspace_root.iterdir()):
            if path.name.startswith(".") or not path.is_file():
                continue
            if path.name.lower() == "config.json":
                continue
            visible_files.append(path)
        if len(visible_files) != 1:
            return None
        candidate = visible_files[0]
        if candidate.suffix.lower() not in (".md", ".txt", ".rst"):
            return None
        try:
            return candidate.relative_to(self.config.workspace_root).as_posix()
        except ValueError:
            return None

    def _looks_like_doc_understanding_prompt(self, prompt: str) -> bool:
        lowered = prompt.lower()
        return any(
            term in lowered
            for term in (
                "instruction",
                "instructions",
                "doc",
                "document",
                "readme",
                "guide",
                "understand",
                "explain",
                "summarize",
                "summary",
            )
        )

    def _state_for_session(self, session_id: str) -> SessionRuntimeState:
        self._session_states.setdefault(session_id, SessionRuntimeState())
        return self._session_states[session_id]

    def _assistant_text_blocks(self, turn: AssistantTurn) -> List[Dict[str, str]]:
        return assistant_text_blocks(turn)

    def _assistant_content_for_tool_turn(self, turn: AssistantTurn) -> List[Dict[str, Any]]:
        return assistant_content_for_tool_turn(self, turn)

    def _squashed_assistant_text(self, turn: AssistantTurn) -> str:
        return squashed_assistant_text(turn)

    def _generate_turn_with_retry(
        self,
        backend: BaseBackend,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[dict]] = None,
        stream: bool = False,
    ) -> AssistantTurn:
        return generate_turn_with_retry(
            self,
            backend,
            system_prompt,
            messages,
            tools=tools,
            stream=stream,
            max_backend_retries=MAX_BACKEND_RETRIES,
        )

    def _fallback_messages_for_retry(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return fallback_messages_for_retry(self, messages)

    def _compact_retry_tool_result(self, tool_name: str, content: str) -> str:
        return compact_retry_tool_result(tool_name, content)

    def _compact_retry_text(self, text: str, limit: int = 800) -> str:
        return compact_retry_text(text, limit=limit)

    def _system_prompt_for_prompt(self, prompt: str, decision: Optional[IntentDecision] = None) -> str:
        intent = self._prompt_intent(prompt)
        if decision is None:
            if self.active_session is not None:
                decision = self._intent_decision(prompt)
            else:
                direct_file_path = intent.direct_file_path
                is_code_file = bool(direct_file_path and not self._prefer_cat_only_for_path(direct_file_path))
                decision = heuristic_intent_decision(prompt, direct_file_path, is_code_file, intent)
        if not decision.needs_tools:
            return BASE_READ_HELPER_SYSTEM_PROMPT + DIRECT_ANSWER_APPENDIX
        if intent.direct_file_trace:
            return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX + DIRECT_FILE_APPENDIX + TRACE_APPENDIX
        if intent.direct_file_path:
            if intent.guide_mode:
                return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX + DIRECT_FILE_APPENDIX + GUIDE_APPENDIX
            return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX + DIRECT_FILE_APPENDIX
        if intent.guide_mode:
            return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX + GUIDE_APPENDIX
        if intent.repo_trace_hint:
            return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX + TRACE_APPENDIX
        return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX

    def _prompt_intent(self, prompt: str) -> PromptIntent:
        return classify_prompt_intent(prompt, self._prompt_direct_file_path(prompt))

    def _intent_decision(self, prompt: str, backend: Optional[BaseBackend] = None) -> IntentDecision:
        assert self.active_session is not None
        state = self._state_for_session(self.active_session.id)
        if prompt in state.intent_cache:
            return state.intent_cache[prompt]
        prompt_intent = self._prompt_intent(prompt)
        direct_file_path = prompt_intent.direct_file_path
        is_code_file = bool(direct_file_path and not self._prefer_cat_only_for_path(direct_file_path))
        fallback = heuristic_intent_decision(prompt, direct_file_path, is_code_file, prompt_intent)
        decision = fallback
        if backend is not None and not prompt_intent.guide_mode:
            llm_decision = route_intent_with_llm(
                backend=backend,
                prompt=prompt,
                direct_file_path=direct_file_path,
                is_code_file=is_code_file,
            )
            decision = merge_intent_decision(llm_decision, fallback)
        state.intent_cache[prompt] = decision
        return decision

    def _store_final_assistant_text(self, session_id: str, text: str) -> str:
        return store_final_assistant_text(self, session_id, text)

    def _should_accept_reader_summary_directly(self, prompt: str, reader_summary: str) -> bool:
        return should_accept_reader_summary_directly(self, prompt, reader_summary)

    def _emit_stream_final_text(self, text: str, stream: bool = False) -> None:
        return emit_stream_final_text(self, text, stream=stream)

    def _record_agent_tool_use(self, session_id: str, agent: str, turn: AssistantTurn, executed_calls) -> None:
        return record_agent_tool_use(self, session_id, agent, turn, executed_calls)

    def _execute_agent_tool_calls(self, session_id: str, agent: str, executed_calls, collect_candidate_paths: bool = False):
        return execute_agent_tool_calls(
            self,
            session_id,
            agent,
            executed_calls,
            collect_candidate_paths=collect_candidate_paths,
        )

    def _get_backend_config(self, backend_name: Optional[str]) -> BackendConfig:
        name = backend_name or self.active_backend_name or self.config.default_backend
        try:
            return self.config.backends[name]
        except KeyError:
            raise BackendError("Unknown backend `{0}`.".format(name))

    def _backend_config_for_session(self, session: SessionMeta) -> BackendConfig:
        backend_cfg = self._get_backend_config(session.backend)
        if session.model and session.model != backend_cfg.model:
            return replace(backend_cfg, model=session.model)
        return backend_cfg

    def _create_backend(self, backend_cfg: BackendConfig) -> BaseBackend:
        if backend_cfg.type == "openai_compat":
            return OpenAICompatBackend(
                model=backend_cfg.model,
                api_key=backend_cfg.api_key,
                base_url=backend_cfg.base_url,
                timeout=backend_cfg.timeout,
                max_tokens=backend_cfg.max_tokens,
            )
        raise BackendError("Unsupported backend type `{0}`.".format(backend_cfg.type))


def _prompt_path_candidates(prompt: str) -> List[str]:
    import re

    candidates = []
    seen = set()
    for match in re.findall(r"[\w./-]+\.[A-Za-z0-9_]+", prompt):
        normalized = match.strip().strip("`'\"()[]{}<>.,!?;:").replace("\\", "/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def _next_cat_offset(result: str) -> Optional[int]:
    import re

    match = re.search(r"Use offset >= (\d+) to continue\.", result)
    if not match:
        return None
    return int(match.group(1))
