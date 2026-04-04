import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..backends.base import AssistantTurn, BackendError, BaseBackend, ToolCall
from ..backends.openai_compat import OpenAICompatBackend
from ..config import AppConfig, BackendConfig
from ..output_sanitize import sanitize_content, sanitize_text
from ..store.session_store import SessionMeta, SessionStore
from ..tools.base import ToolError
from ..tools.registry import ToolRegistry
from .runtime_prompts import (
    BASE_READ_HELPER_SYSTEM_PROMPT,
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
LOCATOR_TOOL_NAMES = ("ls", "tree", "find", "grep")
READ_TOOL_NAMES = LOCATOR_TOOL_NAMES + ("cat",)


@dataclass
class SessionRuntimeState:
    entry_point: str = ""
    confirmed_paths: List[str] = field(default_factory=list)
    unresolved_branches: List[str] = field(default_factory=list)
    file_summaries: Dict[str, str] = field(default_factory=dict)
    summary_cache: Dict[Tuple[str, float, int, int], str] = field(default_factory=dict)


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

    def ask(self, prompt: str, stream: bool = False) -> str:
        if self.active_session is None:
            self.new_session()
        assert self.active_session is not None

        session = self.active_session
        backend_cfg = self._get_backend_config(session.backend)
        backend = self._create_backend(backend_cfg)
        state = self._state_for_session(session.id)
        if not state.entry_point:
            state.entry_point = sanitize_text(prompt).strip()

        self.session_store.append_message(session.id, "user", prompt)
        messages = self._messages_for_backend(session.id)
        system_prompt = self._system_prompt_for_prompt(prompt)

        if backend.supports_tool_calls():
            text = self._ask_with_tool_loop(session.id, backend, messages, prompt, system_prompt, stream=stream)
            text = self._postprocess_direct_file_summary_output(session.id, prompt, text)
        elif stream:
            chunks = []
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
        stream: bool = False,
    ) -> str:
        conversation = list(messages)
        final_text = ""
        final_raw_content = []
        forced_cat_path = self._prompt_direct_file_path(prompt)
        direct_file_summary = self._is_direct_file_summary_prompt(prompt)
        direct_file_trace = self._is_direct_file_trace_prompt(prompt)
        direct_file_guide = self._is_direct_file_guide_prompt(prompt)
        reader_completed_paths = set()

        if forced_cat_path is not None:
            self._record_reader_delegate(session_id, forced_cat_path)
            reader_summary = self._run_reader_agent(session_id, backend, prompt, forced_cat_path, stream=stream)
            reader_completed_paths.add(forced_cat_path)
            if direct_file_summary:
                final_text = self._finalize_direct_file_summary_output(session_id, prompt, reader_summary.strip())
                final_raw_content = [{"type": "text", "text": final_text}]
                self.session_store.append_message(
                    session_id,
                    "assistant",
                    final_text,
                    metadata={"raw_content": final_raw_content},
                )
                return final_text
            if direct_file_guide:
                final_text = reader_summary.strip()
                final_raw_content = [{"type": "text", "text": final_text}]
                self.session_store.append_message(
                    session_id,
                    "assistant",
                    final_text,
                    metadata={"raw_content": final_raw_content},
                )
                return final_text
            if direct_file_trace:
                final_text = reader_summary.strip()
                final_raw_content = [{"type": "text", "text": final_text}]
                self.session_store.append_message(
                    session_id,
                    "assistant",
                    final_text,
                    metadata={"raw_content": final_raw_content},
                )
                return final_text
            conversation = self._append_reader_summary_message(conversation, forced_cat_path, reader_summary)

        for _ in range(MAX_TOOL_ROUNDS):
            current_tools = self.tools.specs(LOCATOR_TOOL_NAMES)
            turn = self._generate_turn_with_retry(backend, system_prompt, conversation, tools=current_tools, stream=stream)
            final_text = sanitize_text(turn.text).strip()
            final_raw_content = sanitize_content(turn.raw_content or self._assistant_text_blocks(turn))
            if not turn.tool_calls:
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
            self.session_store.append_message(
                session_id,
                "assistant",
                self._squashed_assistant_text(turn),
                kind="tool_use",
                metadata={
                    "agent": "planner",
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

            tool_results = []
            candidate_paths = []
            for tool_call in executed_calls:
                arguments = dict(tool_call.arguments)
                try:
                    result = self.run_tool(tool_call.name, arguments)
                except ToolError as exc:
                    result = "Tool error: {0}".format(exc)
                result = sanitize_text(result)

                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "tool_name": tool_call.name,
                    "content": result,
                }
                backend_tool_result = dict(tool_result_block)
                summary = self._summarize_tool_result(session_id, tool_call.name, arguments, result)
                backend_tool_result["content"] = self._backend_tool_result_content(tool_call.name, result, summary)
                tool_results.append(backend_tool_result)
                candidate_paths.extend(self._extract_candidate_paths(tool_call.name, result))
                self.session_store.append_message(
                    session_id,
                    "user",
                    backend_tool_result["content"],
                    kind="tool_result",
                    metadata={
                        "agent": "planner",
                        "tool": tool_call.name,
                        "tool_name": tool_call.name,
                        "tool_arguments": arguments,
                        "tool_use_id": tool_call.id,
                        "summary": summary,
                        "encoding_used": self._tool_result_encoding(tool_call.name, result),
                    },
                )

            conversation.append({"role": "user", "content": tool_results})
            reader_path = self._decide_forced_cat(prompt, candidate_paths, tool_results)
            if reader_path and reader_path not in reader_completed_paths:
                self._record_reader_delegate(session_id, reader_path)
                reader_summary = self._run_reader_agent(session_id, backend, prompt, reader_path)
                reader_completed_paths.add(reader_path)
                conversation = self._append_reader_summary_message(conversation, reader_path, reader_summary)

        raise BackendError("Tool loop exceeded the maximum number of rounds.")

    def _summarize_tool_result(self, session_id: str, tool_name: str, arguments: Dict[str, Any], result: str) -> str:
        state = self._state_for_session(session_id)
        if tool_name == "cat":
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
        if tool_name in ("cat", "get_outline", "tree", "ls") and len(result) <= MAX_INLINE_CAT_RESULT_CHARS:
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
                        chunks = []
                        for chunk in backend.stream_generate(system_prompt, candidate_messages, tools=tools):
                            chunks.append(chunk)
                            print(chunk, end="", flush=True)

                        if chunks:
                            print("")
                            text = "".join(chunks)
                            return AssistantTurn(
                                text=sanitize_text(text),
                                tool_calls=[],
                                raw_content=[{"type": "text", "text": text}],
                            )
                        # No chunks? Likely a tool call or empty response. Proceed to generate_turn.

                    turn = backend.generate_turn(system_prompt, candidate_messages, tools=tools)
                    text = sanitize_text(turn.text)
                    if stream and text:
                        print(text)
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

    def _system_prompt_for_prompt(self, prompt: str) -> str:
        lowered = prompt.lower()
        guide_mode = self._is_guide_prompt(prompt)
        if self._is_direct_file_trace_prompt(prompt):
            return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX + DIRECT_FILE_APPENDIX + TRACE_APPENDIX
        if self._prompt_direct_file_path(prompt):
            if guide_mode:
                return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX + DIRECT_FILE_APPENDIX + GUIDE_APPENDIX
            return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX + DIRECT_FILE_APPENDIX
        if guide_mode:
            return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX + GUIDE_APPENDIX
        if any(keyword in lowered for keyword in ("trace", "tracing", "call path", "used", "where ", "flow")):
            return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX + TRACE_APPENDIX
        return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX

    def _get_backend_config(self, backend_name: Optional[str]) -> BackendConfig:
        name = backend_name or self.active_backend_name or self.config.default_backend
        try:
            return self.config.backends[name]
        except KeyError:
            raise BackendError("Unknown backend `{0}`.".format(name))

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

