import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..backends.base import AssistantTurn, BackendError, BaseBackend, ToolCall
from ..backends.openai_compat import OpenAICompatBackend
from ..config import AppConfig, BackendConfig
from ..store.session_store import SessionMeta, SessionStore
from ..tools.base import ToolError
from ..tools.registry import ToolRegistry


BASE_READ_HELPER_SYSTEM_PROMPT = """You are crush_py, a repository reading helper for small local models.

Use workspace-relative paths only. Never invent file contents or success states.
Prefer `ls`/`tree` for structure, `find` for filename guesses, `grep` for symbols, `get_outline` for code shape, and `cat` only for exact files.
Answer from evidence and clearly mark uncertainty.
"""

PLANNER_APPENDIX = """
Planner mode:
- decide what to inspect next with the lightest possible tool
- prefer `ls`, `tree`, `find`, and `grep`
- do not read full file bodies yourself when a reader agent can do it
- once one concrete file is confirmed, delegate that file to the reader agent
"""

TRACE_APPENDIX = """
Tracing rules:
- do not assume same-name functions are the same implementation
- for C++, mention uncertainty from overloads, templates, macros, and polymorphism
- for Python, mention uncertainty from dynamic dispatch, monkey patching, and *args/**kwargs
- when tracing, include confirmed path, unconfirmed branches, and the next search step if still uncertain
"""

DIRECT_FILE_APPENDIX = """
Direct-file mode:
- the user already named one concrete file
- read that file before answering
- keep the answer short and file-focused
"""

READER_APPENDIX = """
Reader mode:
- you read one concrete file for the planner
- use only `get_outline` and `cat`
- for direct-file summaries, prefer `cat` first
- use `get_outline` first when the user asks about structure, classes, methods, functions, or architecture
- return:
  1) confirmed path
  2) concise file summary
  3) short evidence excerpts
  4) unresolved uncertainty
"""

MAX_TOOL_ROUNDS = 6
MAX_TOOL_CALLS_PER_ROUND = 2
MAX_RECENT_MESSAGES = 4
MAX_INLINE_CAT_RESULT_CHARS = 6000
SUMMARY_CHUNK_LIMIT = 400
MAX_SUMMARY_CHUNKS = 3
LOCATOR_TOOL_NAMES = ("ls", "tree", "find", "grep")
READ_TOOL_NAMES = LOCATOR_TOOL_NAMES + ("cat",)
READER_TOOL_NAMES = ("get_outline", "cat")
MAX_READER_ROUNDS = 4
MAX_READER_TOOL_CALLS = 3
BRIEF_SUMMARY_SIGNALS = (
    "briefly",
    "brief summary",
    "quickly summarize",
    "short summary",
    "just give me",
    "3 bullets",
    "three bullets",
    "short",
)
DIRECT_SUMMARY_CAT_CHAR_BUDGET = 1400


@dataclass
class SessionRuntimeState:
    entry_point: str = ""
    confirmed_paths: List[str] = field(default_factory=list)
    unresolved_branches: List[str] = field(default_factory=list)
    file_summaries: Dict[str, str] = field(default_factory=dict)
    summary_cache: Dict[Tuple[str, float, int, int], str] = field(default_factory=dict)


class AgentRuntime:
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
            state.entry_point = prompt.strip()

        self.session_store.append_message(session.id, "user", prompt)
        messages = self._messages_for_backend(session.id)
        system_prompt = self._system_prompt_for_prompt(prompt)

        if backend.supports_tool_calls():
            text = self._ask_with_tool_loop(session.id, backend, messages, prompt, system_prompt)
            text = self._postprocess_direct_file_summary_output(session.id, prompt, text)
        elif stream:
            chunks = []
            for chunk in backend.stream_generate(system_prompt, messages):
                chunks.append(chunk)
                print(chunk, end="", flush=True)
            print("")
            text = "".join(chunks).strip()
            self.session_store.append_message(
                session.id,
                "assistant",
                text,
                metadata={"raw_content": [{"type": "text", "text": text}]},
            )
        else:
            turn = backend.generate_with_metadata(system_prompt, messages)
            text = turn.text.strip()
            self.session_store.append_message(
                session.id,
                "assistant",
                text,
                metadata={"raw_content": turn.raw_content},
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
    ) -> str:
        conversation = list(messages)
        final_text = ""
        final_raw_content = []
        forced_cat_path = self._prompt_direct_file_path(prompt)
        direct_file_summary = self._is_direct_file_summary_prompt(prompt)
        reader_completed_paths = set()

        if forced_cat_path is not None:
            self._record_reader_delegate(session_id, forced_cat_path)
            reader_summary = self._run_reader_agent(session_id, backend, prompt, forced_cat_path)
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
            conversation = self._append_reader_summary_message(conversation, forced_cat_path, reader_summary)

        for _ in range(MAX_TOOL_ROUNDS):
            current_tools = self.tools.specs(LOCATOR_TOOL_NAMES)
            turn = backend.generate_turn(system_prompt, conversation, tools=current_tools)
            final_text = turn.text.strip()
            final_raw_content = turn.raw_content or self._assistant_text_blocks(turn)
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
                        "tool_name": tool_call.name,
                        "tool_arguments": arguments,
                        "tool_use_id": tool_call.id,
                        "summary": summary,
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
        if tool_name in ("cat", "get_outline") and len(result) <= MAX_INLINE_CAT_RESULT_CHARS:
            return result
        return summary

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
        return [{"type": "text", "text": turn.text}]

    def _assistant_content_for_tool_turn(self, turn: AssistantTurn) -> List[Dict[str, Any]]:
        raw_content = turn.raw_content or self._assistant_text_blocks(turn)
        if not turn.tool_calls:
            return raw_content
        return [item for item in raw_content if item.get("type") != "text"]

    def _squashed_assistant_text(self, turn: AssistantTurn) -> str:
        if turn.tool_calls:
            return ""
        return turn.text.strip()

    def _system_prompt_for_prompt(self, prompt: str) -> str:
        lowered = prompt.lower()
        if self._prompt_direct_file_path(prompt):
            return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX + DIRECT_FILE_APPENDIX
        if any(keyword in lowered for keyword in ("trace", "tracing", "call path", "used", "where ", "flow")):
            return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX + TRACE_APPENDIX
        return BASE_READ_HELPER_SYSTEM_PROMPT + PLANNER_APPENDIX

    def _run_reader_agent(self, session_id: str, backend: BaseBackend, prompt: str, rel_path: str) -> str:
        if self._is_direct_file_summary_prompt(prompt):
            return self._run_direct_file_summary_reader(session_id, backend, prompt, rel_path)
        strategy = (
            "This is a direct-file summary request. Use `cat` first unless the user explicitly asks about structure, classes, methods, functions, symbols, or architecture."
            if self._is_direct_file_summary_prompt(prompt)
            else "Use `get_outline` first if it helps, then `cat` if needed."
        )
        conversation = [
            {
                "role": "user",
                "content": (
                    "User request: {0}\n"
                    "Target file: {1}\n"
                    "Read only this file for the planner. {2}"
                ).format(prompt.strip(), rel_path, strategy),
            }
        ]
        final_text = ""
        tool_calls_used = 0
        for _ in range(MAX_READER_ROUNDS):
            remaining_tool_calls = max(0, MAX_READER_TOOL_CALLS - tool_calls_used)
            turn = backend.generate_turn(
                BASE_READ_HELPER_SYSTEM_PROMPT + READER_APPENDIX,
                conversation,
                tools=self.tools.specs(READER_TOOL_NAMES) if remaining_tool_calls > 0 else None,
            )
            final_text = turn.text.strip()
            if not turn.tool_calls:
                state = self._state_for_session(session_id)
                state.file_summaries[rel_path] = _single_line(final_text, 240)
                if rel_path and rel_path not in state.confirmed_paths:
                    state.confirmed_paths.append(rel_path)
                self.session_store.append_message(
                    session_id,
                    "assistant",
                    final_text,
                    kind="tool_result",
                    metadata={
                        "agent": "reader",
                        "tool_name": "reader",
                        "tool_arguments": {"path": rel_path},
                        "tool_use_id": "reader:{0}".format(rel_path),
                        "summary": final_text,
                    },
                )
                return final_text

            assistant_content = self._assistant_content_for_tool_turn(turn)
            conversation.append({"role": "assistant", "content": assistant_content})
            executed_calls = executed_calls_from_turn(turn, limit=remaining_tool_calls)
            if not executed_calls:
                continue
            tool_calls_used += len(executed_calls)
            self.session_store.append_message(
                session_id,
                "assistant",
                self._squashed_assistant_text(turn),
                kind="tool_use",
                metadata={
                    "agent": "reader",
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
            for tool_call in executed_calls:
                arguments = dict(tool_call.arguments)
                try:
                    result = self.run_tool(tool_call.name, arguments)
                except ToolError as exc:
                    result = "Tool error: {0}".format(exc)
                summary = self._summarize_tool_result(session_id, tool_call.name, arguments, result)
                backend_tool_result = {
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "tool_name": tool_call.name,
                    "content": self._backend_tool_result_content(tool_call.name, result, summary),
                }
                tool_results.append(backend_tool_result)
                self.session_store.append_message(
                    session_id,
                    "user",
                    backend_tool_result["content"],
                    kind="tool_result",
                    metadata={
                        "agent": "reader",
                        "tool_name": tool_call.name,
                        "tool_arguments": arguments,
                        "tool_use_id": tool_call.id,
                        "summary": summary,
                    },
                )
            conversation.append({"role": "user", "content": tool_results})

        raise BackendError("Reader agent exceeded the maximum number of rounds.")

    def _run_direct_file_summary_reader(self, session_id: str, backend: BaseBackend, prompt: str, rel_path: str) -> str:
        cat_payloads, coverage = self._collect_summary_file_reads(session_id, rel_path)
        cat_payloads = self._compact_reader_cat_payloads(cat_payloads)
        coverage_line = "Coverage: {0}".format(coverage)
        brief_summary_mode = self._is_brief_summary_prompt(prompt)
        request_instructions = self._direct_file_summary_reader_instructions(brief_summary_mode)
        conversation = [
            {
                "role": "user",
                "content": (
                    "User request: {0}\n"
                    "Target file: {1}\n"
                    "{2}\n"
                    "{3}"
                ).format(prompt.strip(), rel_path, coverage_line, request_instructions),
            },
            {"role": "user", "content": cat_payloads},
        ]
        turn = backend.generate_turn(BASE_READ_HELPER_SYSTEM_PROMPT + READER_APPENDIX, conversation, tools=None)
        final_text = turn.text.strip()
        if coverage != "complete" and "Preliminary summary" not in final_text:
            final_text = "Preliminary summary (partial file coverage).\n" + final_text

        state = self._state_for_session(session_id)
        state.file_summaries[rel_path] = _single_line(final_text, 240)
        if rel_path and rel_path not in state.confirmed_paths:
            state.confirmed_paths.append(rel_path)
        self.session_store.append_message(
            session_id,
            "assistant",
            final_text,
            kind="tool_result",
            metadata={
                "agent": "reader",
                "tool_name": "reader",
                "tool_arguments": {"path": rel_path, "coverage": coverage},
                "tool_use_id": "reader:{0}".format(rel_path),
                "summary": final_text,
            },
        )
        return final_text

    def _collect_summary_file_reads(self, session_id: str, rel_path: str) -> Tuple[List[Dict[str, Any]], str]:
        payloads: List[Dict[str, Any]] = []
        try:
            result = self._record_reader_cat_tool(session_id, {"path": rel_path, "full": True})
            payloads.append(
                {
                    "type": "tool_result",
                    "tool_use_id": "reader-cat-full:{0}".format(rel_path),
                    "tool_name": "cat",
                    "content": result,
                }
            )
            return payloads, "complete"
        except ToolError:
            offset = 0
            for _ in range(MAX_SUMMARY_CHUNKS):
                result = self._record_reader_cat_tool(
                    session_id,
                    {"path": rel_path, "offset": offset, "limit": SUMMARY_CHUNK_LIMIT},
                )
                payloads.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": "reader-cat-page:{0}:{1}".format(rel_path, offset),
                        "tool_name": "cat",
                        "content": result,
                    }
                )
                next_offset = _next_cat_offset(result)
                if next_offset is None:
                    return payloads, "complete"
                offset = next_offset
            return payloads, "partial"

    def _record_reader_cat_tool(self, session_id: str, arguments: Dict[str, Any]) -> str:
        tool_use_id = _tool_use_id_for_cat(arguments)
        self.session_store.append_message(
            session_id,
            "assistant",
            "",
            kind="tool_use",
            metadata={
                "agent": "reader",
                "tool_names": ["cat"],
                "tool_calls": [{"id": tool_use_id, "name": "cat", "arguments": dict(arguments)}],
                "assistant_text": "",
            },
        )
        result = self.run_tool("cat", arguments)
        summary = self._summarize_tool_result(session_id, "cat", arguments, result)
        self.session_store.append_message(
            session_id,
            "user",
            self._backend_tool_result_content("cat", result, summary),
            kind="tool_result",
            metadata={
                "agent": "reader",
                "tool_name": "cat",
                "tool_arguments": dict(arguments),
                "tool_use_id": tool_use_id,
                "summary": summary,
            },
        )
        return result

    def _append_reader_summary_message(
        self,
        messages: List[Dict[str, Any]],
        rel_path: str,
        reader_summary: str,
    ) -> List[Dict[str, Any]]:
        updated = list(messages)
        updated.append(
            {
                "role": "user",
                "content": "Reader agent summary for `{0}`:\n{1}".format(rel_path, reader_summary),
            }
        )
        return updated

    def _record_reader_delegate(self, session_id: str, rel_path: str) -> None:
        self.session_store.append_message(
            session_id,
            "assistant",
            "Delegating `{0}` to reader agent.".format(rel_path),
            kind="tool_use",
            metadata={
                "agent": "planner",
                "tool_names": ["reader"],
                "tool_calls": [
                    {
                        "id": "reader:{0}".format(rel_path),
                        "name": "reader",
                        "arguments": {"path": rel_path},
                    }
                ],
                "assistant_text": "Delegating `{0}` to reader agent.".format(rel_path),
            },
        )

    def _skip_message_for_planner_history(self, message: Any) -> bool:
        if message.metadata.get("agent") != "reader":
            return False
        return not self._is_reader_summary_message(message)

    def _is_reader_summary_message(self, message: Any) -> bool:
        return (
            message.kind == "tool_result"
            and message.metadata.get("agent") == "reader"
            and (message.metadata.get("tool_name") == "reader" or message.metadata.get("tool") == "reader")
        )

    def _reader_summary_history_content(self, message: Any) -> str:
        rel_path = str(message.metadata.get("tool_arguments", {}).get("path", "")).strip()
        if not rel_path:
            rel_path = str(message.metadata.get("args", {}).get("path", "")).strip()
        if rel_path:
            return "Reader summary for `{0}`:\n{1}".format(rel_path, message.content or message.metadata.get("summary", ""))
        return "Reader summary:\n{0}".format(message.content or message.metadata.get("summary", ""))

    def _latest_reader_coverage(self, session_id: str, rel_path: str) -> str:
        for message in reversed(self.session_store.load_messages(session_id)):
            if not self._is_reader_summary_message(message):
                continue
            args = message.metadata.get("tool_arguments", {}) or message.metadata.get("args", {}) or {}
            if str(args.get("path", "")).strip() != rel_path:
                continue
            coverage = str(args.get("coverage", "")).strip()
            if coverage:
                return coverage
        return "unknown"

    def _session_has_partial_reader_summary(self, session_id: str) -> bool:
        for message in reversed(self.session_store.load_messages(session_id)):
            if not self._is_reader_summary_message(message):
                continue
            args = message.metadata.get("tool_arguments", {}) or message.metadata.get("args", {}) or {}
            if str(args.get("coverage", "")).strip() and str(args.get("coverage", "")).strip() != "complete":
                return True
        return False

    def _postprocess_direct_file_summary_output(self, session_id: str, prompt: str, text: str) -> str:
        return self._finalize_direct_file_summary_output(session_id, prompt, text)

    def _finalize_direct_file_summary_output(self, session_id: str, prompt: str, text: str) -> str:
        if not self._is_direct_file_summary_prompt(prompt):
            return text
        processed = text
        if self._is_brief_summary_prompt(prompt):
            processed = self._format_brief_direct_file_summary(processed)
        if not self._session_has_partial_reader_summary(session_id):
            return processed
        if "Preliminary summary (partial file coverage)." in processed:
            return processed
        return "Preliminary summary (partial file coverage).\n" + processed

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

    def _is_direct_file_summary_prompt(self, prompt: str) -> bool:
        rel_path = self._prompt_direct_file_path(prompt)
        if not rel_path:
            return False
        lowered = prompt.lower()
        summary_terms = (
            "summarize",
            "summary",
            "explain",
            "what does",
            "responsible for",
            "負責什麼",
            "說明",
            "幾點",
            "3 點",
            "3點",
            "bullets",
        )
        structure_terms = (
            "class",
            "classes",
            "function",
            "functions",
            "method",
            "methods",
            "structure",
            "outline",
            "symbol",
            "architecture",
            "架構",
            "結構",
            "哪些類別",
            "哪些函式",
            "彼此怎麼合作",
        )
        trace_terms = (
            "trace",
            "call path",
            "used",
            "where",
            "flow",
            "import",
            "how ",
        )
        has_summary_signal = any(term in lowered for term in summary_terms)
        has_structure_signal = any(term in lowered for term in structure_terms)
        has_trace_signal = any(term in lowered for term in trace_terms)
        return has_summary_signal and not has_structure_signal and not has_trace_signal

    def _is_brief_summary_prompt(self, prompt: str) -> bool:
        if not self._is_direct_file_summary_prompt(prompt):
            return False
        lowered = prompt.lower()
        return any(term in lowered for term in BRIEF_SUMMARY_SIGNALS)

    def _direct_file_summary_reader_instructions(self, brief_summary_mode: bool) -> str:
        if brief_summary_mode:
            return (
                "Read only this file and give a brief summary.\n"
                "Return exactly 3 numbered points.\n"
                "Each point should be one sentence about a real file responsibility.\n"
                "No Evidence, Tag, Review note, Suggested keep, or Suggested review/remove sections.\n"
                "No intro or outro.\n"
                "If coverage is partial, start with `Preliminary summary (partial file coverage).`"
            )
        return (
            "Read only this file and produce a human-review draft.\n"
            "Return 4 to 6 candidate responsibilities.\n"
            "Each candidate needs an `Evidence:` line and a `Tag:` line.\n"
            "Use one tag: likely_core, likely_supporting, or likely_helper.\n"
            "Then add `Review note:`, `Suggested keep:`, and `Suggested review/remove:`.\n"
            "Do not claim these are final truth.\n"
            "If coverage is partial, start with `Preliminary summary (partial file coverage).`\n"
            "Format:\n"
            "Candidate responsibilities for human review:\n"
            "1. <candidate>\n"
            "   Evidence: <names or patterns>\n"
            "   Tag: likely_core / likely_supporting / likely_helper"
        )

    def _format_brief_direct_file_summary(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return stripped

        preliminary_label = ""
        body = stripped
        prefix = "Preliminary summary (partial file coverage)."
        if body.startswith(prefix):
            preliminary_label = prefix
            body = body[len(prefix):].strip()

        bullets = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(("Evidence:", "Tag:", "Review note:", "Suggested keep:", "Suggested review/remove:")):
                continue
            if line.startswith("- "):
                continue
            match = re.match(r"^(\d+)\.\s*(.+)$", line)
            if match:
                bullets.append(match.group(2).strip())
                continue
            if bullets:
                continue
            bullets.append(line)

        if not bullets:
            return stripped

        formatted = "\n\n".join(
            "{0}. {1}".format(index, bullets[index - 1])
            for index in range(1, min(len(bullets), 3) + 1)
        )
        if preliminary_label:
            return preliminary_label + "\n" + formatted
        return formatted

    def _compact_reader_cat_payloads(self, payloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        compacted = []
        for payload in payloads:
            compact_payload = dict(payload)
            if payload.get("tool_name") == "cat":
                compact_payload["content"] = self._compact_reader_cat_content(str(payload.get("content", "")))
            compacted.append(compact_payload)
        return compacted

    def _compact_reader_cat_content(self, content: str) -> str:
        if len(content) <= DIRECT_SUMMARY_CAT_CHAR_BUDGET:
            return content

        lines = content.splitlines()
        if not lines:
            return content

        open_tag = lines[0] if lines and lines[0].startswith("<file ") else ""
        close_tag = ""
        continuation = ""
        body = []
        for line in lines[1:]:
            if line == "</file>":
                close_tag = line
                continue
            if line.startswith("File has more lines."):
                continuation = line
                continue
            body.append(line)

        budget = max(300, DIRECT_SUMMARY_CAT_CHAR_BUDGET - len(open_tag) - len(close_tag) - len(continuation) - 16)
        front = []
        back = []
        front_used = 0
        back_used = 0
        front_index = 0
        back_index = len(body) - 1

        while front_index <= back_index:
            take_front = front_used <= back_used
            candidate = body[front_index] if take_front else body[back_index]
            line_cost = len(candidate) + 1
            if front_used + back_used + line_cost > budget:
                break
            if take_front:
                front.append(candidate)
                front_used += line_cost
                front_index += 1
            else:
                back.append(candidate)
                back_used += line_cost
                back_index -= 1

        omitted = len(body) - len(front) - len(back)
        parts = []
        if open_tag:
            parts.append(open_tag)
        parts.extend(front)
        if omitted > 0:
            parts.append("...[{0} lines omitted for summary context]".format(omitted))
        parts.extend(reversed(back))
        if close_tag:
            parts.append(close_tag)
        if continuation:
            parts.append(continuation)
        return "\n".join(parts)


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


def _tool_use_id_for_cat(arguments: Dict[str, Any]) -> str:
    path = str(arguments.get("path", "")).strip() or "<missing>"
    if arguments.get("full"):
        return "reader-cat-full:{0}".format(path)
    offset = int(arguments.get("offset", 0) or 0)
    limit = int(arguments.get("limit", 0) or 0)
    return "reader-cat:{0}:{1}:{2}".format(path, offset, limit)


def executed_calls_from_turn(turn: AssistantTurn, limit: int = MAX_TOOL_CALLS_PER_ROUND) -> List[ToolCall]:
    if limit <= 0:
        return []
    return turn.tool_calls[:limit]
