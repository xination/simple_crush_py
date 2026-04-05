import re
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..backends.base import AssistantTurn, BackendError, BaseBackend, ToolCall
from ..backends.openai_compat import OpenAICompatBackend
from ..config import AppConfig, BackendConfig
from ..output_sanitize import sanitize_content, sanitize_text
from ..store.session_store import SessionMeta, SessionStore
from ..tools.base import ToolError
from ..tools.common import ensure_in_workspace, read_text_with_fallback
from ..tools.registry import ToolRegistry
from .intent_router import IntentDecision, heuristic_intent_decision, merge_intent_decision, route_intent_with_llm
from .prompt_intent import PromptIntent, classify_prompt_intent
from .runtime_prompts import (
    BASE_READ_HELPER_SYSTEM_PROMPT,
    DIRECT_ANSWER_APPENDIX,
    DIRECT_FILE_APPENDIX,
    GUIDE_APPENDIX,
    PLANNER_APPENDIX,
    TRACE_APPENDIX,
)
from .reader_runtime import ReaderRuntimeMixin
from .summary_runtime import SummaryRuntimeMixin
from .trace_runtime import TraceRuntimeMixin
from .guide_runtime import GuideRuntimeMixin

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
                    metadata={"raw_content": sanitize_content(turn.raw_content)},
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

    def _messages_for_backend(self, session_id: str) -> List[Dict[str, Any]]:
        state = self._state_for_session(session_id)
        stored_messages = self.session_store.load_messages(session_id)
        recent_messages = stored_messages[-MAX_RECENT_MESSAGES:]
        earlier_messages = stored_messages[:-MAX_RECENT_MESSAGES]
        history_summary = self._build_history_summary(state, earlier_messages)

        messages: List[Dict[str, Any]] = []
        if history_summary:
            messages.append({"role": "user", "content": "Conversation summary:\n{0}".format(history_summary)})

        for message in recent_messages:
            if self._skip_message_for_planner_history(message):
                continue
            if message.kind == "message":
                messages.append({"role": message.role, "content": message.content})
                continue
            if message.kind == "tool_use":
                messages.append({"role": message.role, "content": self._stored_tool_use_content(message)})
                continue
            if message.kind == "tool_result":
                if self._is_reader_summary_message(message):
                    messages.append({"role": "user", "content": self._reader_summary_history_content(message)})
                    continue
                messages.append(
                    {
                        "role": "user",
                        "content": self._stored_tool_result_content(message),
                    }
                )
        return messages

    def _build_history_summary(self, state: SessionRuntimeState, earlier_messages: List[Any]) -> str:
        lines = []
        if state.entry_point:
            lines.append("entry point: {0}".format(_single_line(state.entry_point, 180)))
        if state.confirmed_paths:
            lines.append("confirmed files: {0}".format(", ".join(state.confirmed_paths[-5:])))
        if state.file_summaries:
            items = []
            for path in sorted(state.file_summaries.keys())[-3:]:
                items.append("{0}: {1}".format(path, _single_line(state.file_summaries[path], 140)))
            lines.append("file summaries: {0}".format(" | ".join(items)))
        unresolved = state.unresolved_branches[-3:]
        if unresolved:
            lines.append("unresolved branches: {0}".format(" | ".join(_single_line(item, 140) for item in unresolved)))
        if earlier_messages:
            lines.append("older message count: {0}".format(len(earlier_messages)))
        return "\n".join(lines)

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
        conversation = list(messages)
        final_text = ""
        final_raw_content = []
        intent = self._prompt_intent(prompt)
        forced_cat_path = intent.direct_file_path
        reader_completed_paths = set()
        evidence_collected = False
        evidence_retry_used = False

        if forced_cat_path is not None:
            self._record_reader_delegate(session_id, forced_cat_path)
            reader_summary = self._run_reader_agent(session_id, backend, prompt, forced_cat_path, stream=stream)
            reader_completed_paths.add(forced_cat_path)
            evidence_collected = True
            if intent.direct_file_summary:
                final_text = self._finalize_direct_file_summary_output(session_id, prompt, reader_summary.strip())
                self._emit_stream_final_text(final_text, stream=stream)
                return self._store_final_assistant_text(session_id, final_text)
            if intent.guide_mode or intent.direct_file_trace:
                final_text = reader_summary.strip()
                self._emit_stream_final_text(final_text, stream=stream)
                return self._store_final_assistant_text(session_id, final_text)
            if self._should_accept_reader_summary_directly(prompt, reader_summary):
                final_text = reader_summary.strip()
                self._emit_stream_final_text(final_text, stream=stream)
                return self._store_final_assistant_text(session_id, final_text)
            conversation = self._append_reader_summary_message(conversation, forced_cat_path, reader_summary)

        for _ in range(MAX_TOOL_ROUNDS):
            current_tools = self.tools.specs(LOCATOR_TOOL_NAMES)
            turn = self._generate_turn_with_retry(backend, system_prompt, conversation, tools=current_tools, stream=stream)
            final_text = sanitize_text(turn.text).strip()
            final_raw_content = sanitize_content(turn.raw_content or self._assistant_text_blocks(turn))
            if not turn.tool_calls:
                if decision.needs_tools and not evidence_collected:
                    if not evidence_retry_used:
                        evidence_retry_used = True
                        conversation.append(
                            {
                                "role": "user",
                                "content": (
                                    "Evidence is required before answering this request.\n"
                                    "Use at least one local discovery tool first: ls, tree, find, or grep.\n"
                                    "Do not answer from prior knowledge alone."
                                ),
                            }
                        )
                        continue
                    fallback_text = self._repo_evidence_required_message(prompt)
                    self._emit_stream_final_text(fallback_text, stream=stream)
                    return self._store_final_assistant_text(session_id, fallback_text)
                if stream and final_text:
                    self._clear_thinking_indicator_line()
                    print(final_text, end="", flush=True)
                    print("")
                self.session_store.append_message(
                    session_id,
                    "assistant",
                    final_text,
                    metadata={"raw_content": final_raw_content},
                )
                return final_text

            assistant_content = self._assistant_content_for_tool_turn(turn)
            conversation.append({"role": "assistant", "content": assistant_content})
            executed_calls = executed_calls_from_turn(turn)
            self._record_agent_tool_use(session_id, "planner", turn, executed_calls)
            tool_results, candidate_paths = self._execute_agent_tool_calls(
                session_id,
                "planner",
                executed_calls,
                collect_candidate_paths=True,
            )
            if executed_calls:
                evidence_collected = True

            conversation.append({"role": "user", "content": tool_results})
            reader_path = self._decide_forced_cat(prompt, candidate_paths, tool_results)
            if reader_path and reader_path not in reader_completed_paths:
                self._record_reader_delegate(session_id, reader_path)
                reader_summary = self._run_reader_agent(session_id, backend, prompt, reader_path)
                reader_completed_paths.add(reader_path)
                evidence_collected = True
                conversation = self._append_reader_summary_message(conversation, reader_path, reader_summary)

        raise BackendError("Tool loop exceeded the maximum number of rounds.")

    def _ask_without_tools(
        self,
        session_id: str,
        backend: BaseBackend,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        stream: bool = False,
    ) -> str:
        if stream:
            final_text = self._stream_text_with_retry(backend, system_prompt, messages)
            final_raw_content = [{"type": "text", "text": final_text}] if final_text else []
        else:
            turn = self._generate_turn_with_retry(backend, system_prompt, messages, tools=None, stream=False)
            final_text = sanitize_text(turn.text).strip()
            final_raw_content = sanitize_content(turn.raw_content or self._assistant_text_blocks(turn))
        self.session_store.append_message(
            session_id,
            "assistant",
            final_text,
            metadata={"raw_content": final_raw_content},
        )
        return final_text

    def _stream_text_with_retry(
        self,
        backend: BaseBackend,
        system_prompt: str,
        messages: List[Dict[str, Any]],
    ) -> str:
        errors: List[str] = []
        attempts = [messages]
        fallback_messages = self._fallback_messages_for_retry(messages)
        if fallback_messages != messages:
            attempts.append(fallback_messages)
        for retry_index in range(MAX_BACKEND_RETRIES + 1):
            for candidate_messages in attempts:
                try:
                    chunks = []
                    self._clear_thinking_indicator_line()
                    for chunk in backend.stream_generate(system_prompt, candidate_messages, tools=None):
                        chunk_text = str(chunk)
                        if not chunk_text:
                            continue
                        chunks.append(chunk_text)
                        print(chunk_text, end="", flush=True)
                    print("")
                    return sanitize_text("".join(chunks)).strip()
                except BackendError as exc:
                    errors.append(str(exc))
            if retry_index >= MAX_BACKEND_RETRIES:
                break
        raise BackendError("Backend streaming turn failed after retry/fallback: {0}".format(" | ".join(errors)))

    def _repo_evidence_required_message(self, prompt: str) -> str:
        return (
            "I need to inspect local repository files before answering `{0}` reliably. "
            "Please try again, or ask with a concrete file/path such as `summarize README.md`."
        ).format(prompt.strip())

    def _normalize_quick_file_path(self, rel_path: str) -> str:
        normalized = str(rel_path or "").strip().replace("\\", "/")
        if not normalized:
            raise ToolError("`--file` requires a workspace-relative path.")
        abs_path = (self.config.workspace_root / normalized).resolve()
        ensure_in_workspace(self.config.workspace_root, abs_path)
        if not abs_path.exists():
            raise ToolError("File not found: {0}".format(normalized))
        if abs_path.is_dir():
            raise ToolError("Path is a directory: {0}".format(normalized))
        try:
            return abs_path.relative_to(self.config.workspace_root).as_posix()
        except ValueError:
            raise ToolError("Path is outside workspace root: {0}".format(normalized))

    def _read_quick_file(self, rel_path: str) -> Tuple[str, Dict[str, str]]:
        abs_path = (self.config.workspace_root / rel_path).resolve()
        stat = abs_path.stat()
        if stat.st_size > MAX_QUICK_FILE_SIZE:
            raise ToolError("File is too large for quick mode: {0}".format(rel_path))
        state = self._state_for_session(self.active_session.id)
        cache_key = (rel_path, stat.st_mtime, stat.st_size)
        if cache_key in state.quick_file_cache:
            return state.quick_file_cache[cache_key], {
                "status": "hit",
                "source": state.quick_file_cache_sources.get(cache_key, "memory_cache"),
            }
        text, _ = read_text_with_fallback(abs_path)
        state.quick_file_cache = {
            key: value
            for key, value in state.quick_file_cache.items()
            if not (key[0] == rel_path and key != cache_key)
        }
        state.quick_file_cache_sources = {
            key: value
            for key, value in state.quick_file_cache_sources.items()
            if not (key[0] == rel_path and key != cache_key)
        }
        state.quick_file_cache[cache_key] = text
        state.quick_file_cache_sources[cache_key] = "disk"
        return text, {"status": "miss", "source": "disk"}

    def _summarize_tool_result(self, session_id: str, tool_name: str, arguments: Dict[str, Any], result: str) -> str:
        state = self._state_for_session(session_id)
        if tool_name == "cat":
            self._maybe_cache_quick_file_from_cat(session_id, arguments, result)
            summary = self._cat_summary_from_cache(arguments, result)
            path = str(arguments.get("path", "")).strip()
            if path:
                if path not in state.confirmed_paths:
                    state.confirmed_paths.append(path)
                state.file_summaries[path] = summary
            return summary
        if tool_name == "get_outline":
            return self._summarize_outline_result(arguments, result)
        if tool_name == "grep":
            summary = self._summarize_grep_result(arguments, result)
            if "Narrow the search" in result:
                state.unresolved_branches.append("grep for `{0}` is still too broad".format(arguments.get("pattern", "")))
            return summary
        if tool_name == "find":
            return self._summarize_find_result(result)
        if tool_name in ("tree", "ls"):
            return "Directory overview gathered for `{0}`.".format(arguments.get("path", "."))
        return _single_line(result, 240)

    def _maybe_cache_quick_file_from_cat(self, session_id: str, arguments: Dict[str, Any], result: str) -> None:
        rel_path = str(arguments.get("path", "")).strip()
        if not rel_path or "File has more lines." in result:
            return
        normalized_path = rel_path.replace("\\", "/")
        abs_path = (self.config.workspace_root / normalized_path).resolve()
        try:
            ensure_in_workspace(self.config.workspace_root, abs_path)
        except ToolError:
            return
        if not abs_path.exists() or abs_path.is_dir():
            return
        stat = abs_path.stat()
        if stat.st_size > MAX_QUICK_FILE_SIZE:
            return
        text = self._extract_text_from_cat_result(result)
        if text is None:
            return
        state = self._state_for_session(session_id)
        cache_key = (normalized_path, stat.st_mtime, stat.st_size)
        state.quick_file_cache = {
            key: value
            for key, value in state.quick_file_cache.items()
            if not (key[0] == normalized_path and key != cache_key)
        }
        state.quick_file_cache_sources = {
            key: value
            for key, value in state.quick_file_cache_sources.items()
            if not (key[0] == normalized_path and key != cache_key)
        }
        state.quick_file_cache[cache_key] = text
        state.quick_file_cache_sources[cache_key] = "cat_full"

    def _extract_text_from_cat_result(self, result: str) -> Optional[str]:
        extracted_lines: List[str] = []
        saw_file_block = False
        for line in result.splitlines():
            if line.startswith("<file "):
                saw_file_block = True
                continue
            if line.startswith("</file>"):
                continue
            if "|" not in line:
                continue
            _, content = line.split("|", 1)
            extracted_lines.append(content)
        if not saw_file_block:
            return None
        return "\n".join(extracted_lines)

    def _cat_summary_from_cache(self, arguments: Dict[str, Any], result: str) -> str:
        rel_path = str(arguments.get("path", "")).strip()
        offset = int(arguments.get("offset", 0) or 0)
        limit = int(arguments.get("limit", 80) or 80)
        full = bool(arguments.get("full", False))
        abs_path = (self.config.workspace_root / rel_path).resolve()
        mtime = abs_path.stat().st_mtime if abs_path.exists() else 0.0
        state = self._state_for_any_session_path(rel_path)
        cache_key = (rel_path, mtime, offset, limit if not full else -1)
        if cache_key in state.summary_cache:
            return state.summary_cache[cache_key]

        numbered_lines = []
        for line in result.splitlines():
            if "|" not in line or line.startswith("<file") or line.startswith("</file>"):
                continue
            numbered_lines.append(line.strip())
        preview = numbered_lines[:6]
        if full:
            summary = "Read full file `{0}` ({1} line(s)). Key excerpts: {2}".format(
                rel_path or "<missing>",
                max(len(numbered_lines), 1),
                " ; ".join(_single_line(item, 80) for item in preview) if preview else "no text captured",
            )
        else:
            summary = "Read `{0}` lines {1}-{2}. Key excerpts: {3}".format(
                rel_path or "<missing>",
                offset + 1,
                offset + max(len(numbered_lines), 1),
                " ; ".join(_single_line(item, 80) for item in preview) if preview else "no text captured",
            )
        if not full and "File has more lines." in result:
            summary += " More lines remain."
        state.summary_cache[cache_key] = summary
        return summary

    def _state_for_any_session_path(self, rel_path: str) -> SessionRuntimeState:
        assert self.active_session is not None
        return self._state_for_session(self.active_session.id)

    def _summarize_find_result(self, result: str) -> str:
        lines = [line.strip() for line in result.splitlines() if line.strip() and not line.startswith("Results truncated")]
        if not lines:
            return "No file candidates found."
        preview = ", ".join(lines[:5])
        return "Find produced {0} candidate(s): {1}".format(len(lines), preview)

    def _summarize_outline_result(self, arguments: Dict[str, Any], result: str) -> str:
        path = str(arguments.get("path", "")).strip() or "<missing>"
        outline_lines = []
        for line in result.splitlines():
            if "|" not in line or line.startswith("<outline") or line.startswith("</outline>"):
                continue
            outline_lines.append(line.strip())
        if not outline_lines:
            return "Outline for `{0}` found no clear symbols.".format(path)
        return "Outline for `{0}`: {1}".format(path, " ; ".join(outline_lines[:6]))

    def _summarize_grep_result(self, arguments: Dict[str, Any], result: str) -> str:
        files = []
        for line in result.splitlines():
            if line.endswith(":") and not line.startswith("  "):
                files.append(line[:-1])
        if not files:
            return "Grep found no clear file candidates for `{0}`.".format(arguments.get("pattern", ""))
        return "Grep for `{0}` matched {1} file(s): {2}".format(
            arguments.get("pattern", ""),
            len(files),
            ", ".join(files[:5]),
        )

    def _decide_forced_cat(self, prompt: str, candidate_paths: List[str], tool_results: List[Dict[str, Any]]) -> Optional[str]:
        unique_paths = sorted(set(path for path in candidate_paths if path))
        grep_too_broad = any(
            item.get("tool_name") == "grep" and "Narrow the search" in item.get("content", "")
            for item in tool_results
        )
        if grep_too_broad:
            return None
        if len(unique_paths) == 1:
            return unique_paths[0]
        repo_overview_path = self._repo_overview_anchor_path(prompt)
        if repo_overview_path:
            return repo_overview_path
        return None

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
        if tool_name == "find":
            return [
                line.strip()
                for line in result.splitlines()
                if line.strip() and not line.startswith("Results truncated")
            ]
        if tool_name == "grep":
            paths = []
            for line in result.splitlines():
                item = line.strip()
                if item.endswith(":") and not item.startswith("Line ") and not item.startswith("Search was capped"):
                    paths.append(item[:-1])
            return sorted(set(paths))
        return []

    def _backend_tool_result_content(self, tool_name: str, result: str, summary: str) -> str:
        if tool_name in ("cat", "get_outline", "tree", "ls", "find", "grep") and len(result) <= MAX_INLINE_CAT_RESULT_CHARS:
            return result
        return summary

    def _tool_result_encoding(self, tool_name: str, result: str) -> str:
        if tool_name != "cat":
            return ""
        first_line = result.splitlines()[0] if result.splitlines() else ""
        match = re.search(r'encoding="([^"]+)"', first_line)
        if not match:
            return ""
        return match.group(1)

    def _stored_tool_use_content(self, message: Any) -> Any:
        tool_calls = message.metadata.get("tool_calls", [])
        if not tool_calls:
            tool_name = str(message.metadata.get("tool", "")).strip()
            tool_args = message.metadata.get("args", {})
            if tool_name:
                tool_calls = [{"id": "", "name": tool_name, "arguments": dict(tool_args) if isinstance(tool_args, dict) else {}}]
        assistant_text = str(message.metadata.get("assistant_text", "") or message.metadata.get("text", "")).strip()
        if self.session_store.trace_mode == "debug":
            raw_content = message.metadata.get("raw_content")
            if raw_content:
                return raw_content
        content_blocks = []
        if assistant_text:
            content_blocks.append({"type": "text", "text": assistant_text})
        for tool_call in tool_calls:
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_call.get("id", ""),
                    "name": tool_call.get("name", ""),
                    "input": dict(tool_call.get("arguments", {})),
                }
            )
        if content_blocks:
            return content_blocks
        return message.content

    def _stored_tool_result_content(self, message: Any) -> Any:
        if self.session_store.trace_mode == "debug":
            backend_content = message.metadata.get("backend_content")
            if backend_content:
                return backend_content
        tool_name = message.metadata.get("tool_name", "") or message.metadata.get("tool", "")
        if tool_name:
            return [
                {
                    "type": "tool_result",
                    "tool_use_id": message.metadata.get("tool_use_id", ""),
                    "tool_name": tool_name,
                    "content": message.content or message.metadata.get("summary", ""),
                }
            ]
        return message.content

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
        return None

    def _state_for_session(self, session_id: str) -> SessionRuntimeState:
        self._session_states.setdefault(session_id, SessionRuntimeState())
        return self._session_states[session_id]

    def _assistant_text_blocks(self, turn: AssistantTurn) -> List[Dict[str, str]]:
        if not turn.text:
            return []
        return [{"type": "text", "text": sanitize_text(turn.text)}]

    def _assistant_content_for_tool_turn(self, turn: AssistantTurn) -> List[Dict[str, Any]]:
        raw_content = sanitize_content(turn.raw_content or self._assistant_text_blocks(turn))
        if not turn.tool_calls:
            return raw_content
        return [item for item in raw_content if item.get("type") != "text"]

    def _squashed_assistant_text(self, turn: AssistantTurn) -> str:
        if turn.tool_calls:
            return ""
        return sanitize_text(turn.text).strip()

    def _generate_turn_with_retry(
        self,
        backend: BaseBackend,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[dict]] = None,
        stream: bool = False,
    ) -> AssistantTurn:
        errors: List[str] = []
        attempts = [messages]
        fallback_messages = self._fallback_messages_for_retry(messages)
        if fallback_messages != messages:
            attempts.append(fallback_messages)
        for retry_index in range(MAX_BACKEND_RETRIES + 1):
            for candidate_messages in attempts:
                try:
                    if stream:
                        turn = backend.stream_generate_turn(system_prompt, candidate_messages, tools=tools)
                        text = sanitize_text(turn.text)
                        if text:
                            return AssistantTurn(
                                text=text,
                                tool_calls=turn.tool_calls,
                                raw_content=sanitize_content(turn.raw_content or self._assistant_text_blocks(turn)),
                            )
                        if turn.tool_calls:
                            return AssistantTurn(
                                text="",
                                tool_calls=turn.tool_calls,
                                raw_content=sanitize_content(turn.raw_content),
                            )
                        # Empty streamed turn? Proceed to generate_turn.

                    turn = backend.generate_turn(system_prompt, candidate_messages, tools=tools)
                    text = sanitize_text(turn.text)
                    return AssistantTurn(
                        text=text,
                        tool_calls=turn.tool_calls,
                        raw_content=sanitize_content(turn.raw_content or self._assistant_text_blocks(turn)),
                    )
                except BackendError as exc:
                    errors.append(str(exc))
            if retry_index >= MAX_BACKEND_RETRIES:
                break
        raise BackendError("Backend turn failed after retry/fallback: {0}".format(" | ".join(errors)))

    def _fallback_messages_for_retry(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        fallback: List[Dict[str, Any]] = []
        changed = False
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if isinstance(content, list):
                compact_blocks = []
                for item in content:
                    compact_item = dict(item)
                    if compact_item.get("type") == "tool_result":
                        compact_content = self._compact_retry_tool_result(
                            compact_item.get("tool_name", ""),
                            compact_item.get("content", ""),
                        )
                        if compact_content != compact_item.get("content", ""):
                            changed = True
                        compact_item["content"] = compact_content
                    compact_blocks.append(compact_item)
                fallback.append({"role": role, "content": compact_blocks})
                continue
            if isinstance(content, str):
                compact_text = self._compact_retry_text(content)
                if compact_text != content:
                    changed = True
                fallback.append({"role": role, "content": compact_text})
                continue
            fallback.append(message)
        return fallback if changed else messages

    def _compact_retry_tool_result(self, tool_name: str, content: str) -> str:
        text = sanitize_text(content)
        if tool_name == "cat":
            return self._compact_retry_text(text, limit=800)
        if tool_name == "grep":
            return self._compact_retry_text(text, limit=600)
        return self._compact_retry_text(text, limit=400)

    def _compact_retry_text(self, text: str, limit: int = 800) -> str:
        normalized = sanitize_text(text)
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit].rstrip() + "\n...[retry fallback compacted]"

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
        final_text = sanitize_text(text).strip()
        self.session_store.append_message(
            session_id,
            "assistant",
            final_text,
            metadata={"raw_content": [{"type": "text", "text": final_text}]},
        )
        return final_text

    def _should_accept_reader_summary_directly(self, prompt: str, reader_summary: str) -> bool:
        summary = sanitize_text(reader_summary).strip()
        if not summary:
            return False
        intent = self._prompt_intent(prompt)
        if not intent.direct_file_path:
            return False
        if intent.repo_trace_hint or intent.guide_mode or intent.direct_file_trace or intent.direct_file_summary:
            return False
        lowered = prompt.lower()
        if lowered.startswith(("find ", "locate ", "grep ", "trace ")):
            return False
        markers = (
            "Confirmed path:",
            "Summary:",
            "According to `",
            "According to ",
            "File flow for human review:",
            "Flow trace for human review:",
            "Variable trace for human review:",
            "Candidate responsibilities for human review:",
            "Checklist:",
            "Beginner summary:",
        )
        if any(marker in summary for marker in markers):
            return True
        return len(summary) >= 120

    def _emit_stream_final_text(self, text: str, stream: bool = False) -> None:
        if not stream or not text:
            return
        self._clear_thinking_indicator_line()
        print(text, end="", flush=True)
        print("")

    def _record_agent_tool_use(
        self,
        session_id: str,
        agent: str,
        turn: AssistantTurn,
        executed_calls: List[ToolCall],
    ) -> None:
        self.session_store.append_message(
            session_id,
            "assistant",
            self._squashed_assistant_text(turn),
            kind="tool_use",
            metadata={
                "agent": agent,
                "tool": executed_calls[0].name if executed_calls else "",
                "tool_names": [tool_call.name for tool_call in executed_calls],
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": dict(tool_call.arguments),
                    }
                    for tool_call in executed_calls
                ],
                "assistant_text": self._squashed_assistant_text(turn),
            },
        )

    def _execute_agent_tool_calls(
        self,
        session_id: str,
        agent: str,
        executed_calls: List[ToolCall],
        collect_candidate_paths: bool = False,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        tool_results: List[Dict[str, Any]] = []
        candidate_paths: List[str] = []
        for tool_call in executed_calls:
            arguments = dict(tool_call.arguments)
            try:
                result = self.run_tool(tool_call.name, arguments)
            except ToolError as exc:
                result = "Tool error: {0}".format(exc)
            result = sanitize_text(result)
            summary = self._summarize_tool_result(session_id, tool_call.name, arguments, result)
            backend_tool_result = {
                "type": "tool_result",
                "tool_use_id": tool_call.id,
                "tool_name": tool_call.name,
                "content": self._backend_tool_result_content(tool_call.name, result, summary),
            }
            tool_results.append(backend_tool_result)
            if collect_candidate_paths:
                candidate_paths.extend(self._extract_candidate_paths(tool_call.name, result))
            self.session_store.append_message(
                session_id,
                "user",
                backend_tool_result["content"],
                kind="tool_result",
                metadata={
                    "agent": agent,
                    "tool": tool_call.name,
                    "tool_name": tool_call.name,
                    "tool_arguments": arguments,
                    "tool_use_id": tool_call.id,
                    "summary": summary,
                    "encoding_used": self._tool_result_encoding(tool_call.name, result),
                },
            )
        return tool_results, candidate_paths

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


def _single_line(text: str, max_length: int = 160) -> str:
    normalized = " ".join(str(text).strip().split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + " ..."


def _prompt_path_candidates(prompt: str) -> List[str]:
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
    match = re.search(r"Use offset >= (\d+) to continue\.", result)
    if not match:
        return None
    return int(match.group(1))


def executed_calls_from_turn(turn: AssistantTurn, limit: int = MAX_TOOL_CALLS_PER_ROUND) -> List[ToolCall]:
    if limit <= 0:
        return []
    return turn.tool_calls[:limit]

