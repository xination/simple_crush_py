def format_trace(runtime, limit: int = 20) -> str:
    session = runtime.active_session
    if session is None:
        return "No active session."

    trace_messages = []
    for message in runtime.session_store.load_messages(session.id):
        if message.kind in ("tool_use", "tool_result"):
            trace_messages.append(message)
            continue
        if message.kind == "message" and message.role == "assistant":
            trace_messages.append(message)
    if not trace_messages:
        return "No tool trace entries for this session."

    selected = trace_messages[-limit:]
    lines = []
    for index, message in enumerate(selected, start=1):
        if index > 1:
            lines.append("")
        lines.extend(format_trace_message(message))
    return "\n".join(lines)


def format_trace_message(message) -> list:
    header_parts = ["[{0}]".format(message.kind)]
    if message.role:
        header_parts.append(message.role)
    if message.created_at:
        header_parts.append("({0})".format(message.created_at))
    lines = [" ".join(header_parts)]
    agent = message.metadata.get("agent", "")
    if agent:
        lines.append("agent: {0}".format(agent))
    if message.kind == "message":
        text = message.content.strip() if isinstance(message.content, str) else ""
        lines.append("stage: assistant_final")
        if text:
            lines.append("text: {0}".format(_single_line(text)))
        return lines

    if message.kind == "tool_use":
        text = message.content.strip() if isinstance(message.content, str) else ""
        tool_names = list(message.metadata.get("tool_names", []))
        if not tool_names and message.metadata.get("tool"):
            tool_names = [message.metadata.get("tool", "")]
        if not tool_names:
            raw_content = message.metadata.get("raw_content", [])
            for item in raw_content:
                if item.get("type") == "tool_use":
                    tool_names.append(item.get("name", ""))
        if not text:
            text = str(message.metadata.get("assistant_text", "") or message.metadata.get("text", "")).strip()
        tool_args = message.metadata.get("tool_arguments", {})
        if not tool_args:
            tool_args = message.metadata.get("args", {})
        if tool_names:
            lines.append("tool: {0}".format(", ".join(name for name in tool_names if name)))
        if tool_args:
            lines.append("arguments: {0}".format(tool_args))
        if text:
            lines.append("text: {0}".format(_single_line(text)))
        return lines

    tool_name = message.metadata.get("tool_name", "") or message.metadata.get("tool", "")
    tool_arguments = message.metadata.get("tool_arguments", {}) or message.metadata.get("args", {})
    if tool_name:
        lines.append("tool: {0}".format(tool_name))
    if tool_arguments:
        lines.append("arguments: {0}".format(tool_arguments))
    result_text = message.metadata.get("summary", message.content)
    lines.append("result: {0}".format(_single_line(result_text)))
    return lines


def format_history(runtime, limit: int = 20) -> str:
    session = runtime.active_session
    if session is None:
        return "No active session."

    history_messages = [
        message
        for message in runtime.session_store.load_messages(session.id)
        if message.kind == "message"
    ]
    if not history_messages:
        return "No conversation messages for this session."

    selected = history_messages[-limit:]
    lines = []
    for index, message in enumerate(selected, start=1):
        if index > 1:
            lines.append("")
        lines.extend(format_history_message(message))
    return "\n".join(lines)


def format_history_message(message) -> list:
    role = message.role
    lines = ["[{0}] ({1})".format(role, message.created_at)]
    content = message.content if isinstance(message.content, str) else str(message.content)
    lines.append(_single_line(content))
    return lines


def _single_line(text: str, max_length: int = 160) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + " ..."
