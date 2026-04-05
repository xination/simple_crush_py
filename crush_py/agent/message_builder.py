from typing import Any, Dict, List

from ..output_sanitize import sanitize_content, sanitize_text


def messages_for_backend(runtime, session_id: str, max_recent_messages: int) -> List[Dict[str, Any]]:
    state = runtime._state_for_session(session_id)
    stored_messages = runtime.session_store.load_messages(session_id)
    recent_messages = stored_messages[-max_recent_messages:]
    earlier_messages = stored_messages[:-max_recent_messages]
    history_summary = build_history_summary(state, earlier_messages)

    messages: List[Dict[str, Any]] = []
    if history_summary:
        messages.append({"role": "user", "content": "Conversation summary:\n{0}".format(history_summary)})

    for message in recent_messages:
        if runtime._skip_message_for_planner_history(message):
            continue
        if message.kind == "message":
            messages.append({"role": message.role, "content": message.content})
            continue
        if message.kind == "tool_use":
            messages.append({"role": message.role, "content": stored_tool_use_content(runtime, message)})
            continue
        if message.kind == "tool_result":
            if runtime._is_reader_summary_message(message):
                messages.append({"role": "user", "content": runtime._reader_summary_history_content(message)})
                continue
            messages.append({"role": "user", "content": stored_tool_result_content(runtime, message)})
    return messages


def build_history_summary(state: Any, earlier_messages: List[Any]) -> str:
    lines = []
    if state.entry_point:
        lines.append("entry point: {0}".format(single_line(state.entry_point, 180)))
    if state.confirmed_paths:
        lines.append("confirmed files: {0}".format(", ".join(state.confirmed_paths[-5:])))
    if state.file_summaries:
        items = []
        for path in sorted(state.file_summaries.keys())[-3:]:
            items.append("{0}: {1}".format(path, single_line(state.file_summaries[path], 140)))
        lines.append("file summaries: {0}".format(" | ".join(items)))
    unresolved = state.unresolved_branches[-3:]
    if unresolved:
        lines.append("unresolved branches: {0}".format(" | ".join(single_line(item, 140) for item in unresolved)))
    if earlier_messages:
        lines.append("older message count: {0}".format(len(earlier_messages)))
    return "\n".join(lines)


def stored_tool_use_content(runtime, message: Any) -> Any:
    tool_calls = message.metadata.get("tool_calls", [])
    if not tool_calls:
        tool_name = str(message.metadata.get("tool", "")).strip()
        tool_args = message.metadata.get("args", {})
        if tool_name:
            tool_calls = [{"id": "", "name": tool_name, "arguments": dict(tool_args) if isinstance(tool_args, dict) else {}}]
    assistant_text = str(message.metadata.get("assistant_text", "") or message.metadata.get("text", "")).strip()
    if runtime.session_store.trace_mode == "debug":
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


def stored_tool_result_content(runtime, message: Any) -> Any:
    if runtime.session_store.trace_mode == "debug":
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


def assistant_text_blocks(turn: Any) -> List[Dict[str, str]]:
    if not turn.text:
        return []
    return [{"type": "text", "text": sanitize_text(turn.text)}]


def assistant_content_for_tool_turn(runtime, turn: Any) -> List[Dict[str, Any]]:
    raw_content = sanitize_content(turn.raw_content or runtime._assistant_text_blocks(turn))
    if not turn.tool_calls:
        return raw_content
    return [item for item in raw_content if item.get("type") != "text"]


def squashed_assistant_text(turn: Any) -> str:
    if turn.tool_calls:
        return ""
    return sanitize_text(turn.text).strip()


def single_line(text: str, max_length: int = 160) -> str:
    normalized = " ".join(str(text).strip().split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + " ..."
