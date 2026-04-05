from pathlib import Path
from typing import Any, Dict, List, Optional

from ..backends.base import AssistantTurn, BackendError, BaseBackend, ToolCall
from ..output_sanitize import sanitize_text
from ..tools.outline_providers import SUPPORTED_SUFFIXES
from .runtime_prompts import BASE_READ_HELPER_SYSTEM_PROMPT, READER_APPENDIX


READER_TOOL_NAMES = ("get_outline", "cat")
MAX_READER_ROUNDS = 4
MAX_READER_TOOL_CALLS = 3


class ReaderRuntimeMixin:
    def _run_reader_agent(self, session_id: str, backend: BaseBackend, prompt: str, rel_path: str, stream: bool = False) -> str:
        intent = self._prompt_intent(prompt)
        if intent.guide_mode and intent.direct_file_path and not intent.direct_file_trace:
            return self._run_direct_file_guide_reader(session_id, backend, prompt, rel_path, stream=stream)
        if intent.direct_file_flow_trace:
            return self._run_direct_file_flow_trace_reader(session_id, backend, prompt, rel_path, stream=stream)
        if intent.direct_file_variable_trace:
            return self._run_direct_file_variable_trace_reader(session_id, backend, prompt, rel_path, stream=stream)
        if intent.direct_file_summary:
            return self._run_direct_file_summary_reader(session_id, backend, prompt, rel_path, stream=stream)
        reader_tool_names = self._reader_tool_names_for_path(rel_path)
        strategy = (
            "This is a direct-file summary request. Prefer `cat` first. Use `get_outline` only if the user explicitly asks about structure, classes, methods, functions, symbols, or architecture."
            if intent.direct_file_summary
            else (
                "This target is a non-code file. Use `cat` only. Do not use `get_outline`."
                if self._prefer_cat_only_for_path(rel_path)
                else "This is a code file. Use `get_outline` first if it helps narrow structure, then use `cat` for confirming evidence."
            )
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
            turn = self._generate_turn_with_retry(
                backend,
                BASE_READ_HELPER_SYSTEM_PROMPT + READER_APPENDIX,
                conversation,
                tools=self.tools.specs(reader_tool_names) if remaining_tool_calls > 0 else None,
                stream=stream,
            )
            final_text = sanitize_text(turn.text).strip()
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
                        "tool": "reader",
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
            self._record_agent_tool_use(session_id, "reader", turn, executed_calls)
            tool_results, _ = self._execute_agent_tool_calls(session_id, "reader", executed_calls)
            conversation.append({"role": "user", "content": tool_results})

        raise BackendError("Reader agent exceeded the maximum number of rounds.")

    def _prefer_cat_only_for_path(self, rel_path: str) -> bool:
        return Path(rel_path).suffix.lower() not in SUPPORTED_SUFFIXES

    def _reader_tool_names_for_path(self, rel_path: str):
        if self._prefer_cat_only_for_path(rel_path):
            return ("cat",)
        return READER_TOOL_NAMES

    def _record_reader_cat_tool(self, session_id: str, arguments: Dict[str, Any]) -> str:
        return self._record_reader_tool(session_id, "cat", arguments)

    def _record_reader_tool(self, session_id: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        tool_use_id = _tool_use_id_for_reader_tool(tool_name, arguments)
        self.session_store.append_message(
            session_id,
            "assistant",
            "",
            kind="tool_use",
            metadata={
                "agent": "reader",
                "tool": tool_name,
                "tool_names": [tool_name],
                "tool_calls": [{"id": tool_use_id, "name": tool_name, "arguments": dict(arguments)}],
                "assistant_text": "",
            },
        )
        result = self.run_tool(tool_name, arguments)
        result = sanitize_text(result)
        summary = self._summarize_tool_result(session_id, tool_name, arguments, result)
        self.session_store.append_message(
            session_id,
            "user",
            self._backend_tool_result_content(tool_name, result, summary),
            kind="tool_result",
            metadata={
                "agent": "reader",
                "tool": tool_name,
                "tool_name": tool_name,
                "tool_arguments": dict(arguments),
                "tool_use_id": tool_use_id,
                "summary": summary,
                "encoding_used": self._tool_result_encoding(tool_name, result),
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
                "tool": "reader",
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

    def _generate_text_with_optional_streaming(
        self,
        backend: BaseBackend,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        stream: bool = False,
    ) -> str:
        if stream:
            chunks = []
            for chunk in backend.stream_generate(system_prompt, messages):
                chunks.append(chunk)
                print(chunk, end="", flush=True)
            print("")
            return sanitize_text("".join(chunks)).strip()

        turn = self._generate_turn_with_retry(backend, system_prompt, messages)
        return sanitize_text(turn.text).strip()

    def _reader_summary_history_content(self, message: Any) -> str:
        rel_path = str(message.metadata.get("tool_arguments", {}).get("path", "")).strip()
        if not rel_path:
            rel_path = str(message.metadata.get("args", {}).get("path", "")).strip()
        if rel_path:
            return "Reader summary for `{0}`:\n{1}".format(rel_path, message.content or message.metadata.get("summary", ""))
        return "Reader summary:\n{0}".format(message.content or message.metadata.get("summary", ""))


def _single_line(text: str, max_length: int = 160) -> str:
    normalized = " ".join(str(text).strip().split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + " ..."


def _tool_use_id_for_cat(arguments: Dict[str, Any]) -> str:
    path = str(arguments.get("path", "")).strip() or "<missing>"
    if arguments.get("full"):
        return "reader-cat-full:{0}".format(path)
    offset = int(arguments.get("offset", 0) or 0)
    limit = int(arguments.get("limit", 0) or 0)
    return "reader-cat:{0}:{1}:{2}".format(path, offset, limit)


def _tool_use_id_for_reader_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    if tool_name == "cat":
        return _tool_use_id_for_cat(arguments)
    path = str(arguments.get("path", "")).strip() or "<missing>"
    if tool_name == "grep":
        pattern = str(arguments.get("pattern", "")).strip() or "<missing>"
        include = str(arguments.get("include", "")).strip() or "*"
        return "reader-grep:{0}:{1}:{2}".format(path, include, pattern)
    return "reader-{0}:{1}".format(tool_name, path)


def executed_calls_from_turn(turn: AssistantTurn, limit: int) -> List[ToolCall]:
    if limit <= 0:
        return []
    return turn.tool_calls[:limit]
