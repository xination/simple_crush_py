from pathlib import Path
import shlex

from .agent.runtime import AgentRuntime
from .backends.base import BackendError
from .tools.base import ToolError

try:
    import readline
except ImportError:  # pragma: no cover
    readline = None


HELP_TEXT = """Commands:
/new                  create a new session
/sessions             list sessions
/use <session_id>     switch to an existing session
/backend              show available backends
/tools                show available tools
/ls [PATH] [DEPTH]    quick listing for a directory area
/tree [PATH] [DEPTH]  compact tree view for a directory area
/find PATTERN [PATH]  locate files by filename/path pattern
/grep PATTERN [PATH] [INCLUDE]
/outline PATH         compact symbol outline for one code file
/cat PATH [OFFSET] [LIMIT]
/history [LIMIT]      show recent conversation messages
/trace [LIMIT]        show recent tool trace entries
/quit                 exit
"""

COMMANDS = [
    "/help",
    "/new",
    "/sessions",
    "/use",
    "/backend",
    "/tools",
    "/ls",
    "/tree",
    "/find",
    "/grep",
    "/outline",
    "/cat",
    "/history",
    "/trace",
    "/quit",
]


def run_repl(runtime: AgentRuntime, stream: bool = False) -> int:
    _setup_readline(runtime)

    if runtime.active_session is None:
        session = runtime.new_session()
        print("[session] {0} ({1})".format(session.id, session.backend))

    print("crush_py REPL. Type /quit to exit. Type /help for commands.")
    while True:
        try:
            raw = input("crush_py> ").strip()
        except EOFError:
            print("")
            return 0
        except KeyboardInterrupt:
            print("")
            continue

        if not raw:
            continue
        if raw == "/quit":
            return 0
        if raw == "/help":
            print(HELP_TEXT)
            continue
        if raw == "/new":
            session = runtime.new_session()
            print("[session] {0} ({1})".format(session.id, session.backend))
            continue
        if raw == "/sessions":
            for session in runtime.session_store.list_sessions():
                print("{0}  {1}  {2}".format(session.id, session.backend, session.title))
            continue
        if raw == "/backend":
            for name in runtime.available_backends():
                marker = "*" if name == runtime.active_backend_name else " "
                print("{0} {1}".format(marker, name))
            continue
        if raw == "/tools":
            for name in runtime.available_tools():
                print(name)
            continue
        if raw == "/history" or raw.startswith("/history "):
            args = _safe_split(raw)
            if len(args) > 2:
                print("Usage: /history [LIMIT]")
                continue
            limit = _parse_optional_limit(args[1] if len(args) == 2 else None, "Usage: /history [LIMIT]")
            if limit is None:
                continue
            print(_format_history(runtime, limit=limit))
            continue
        if raw == "/trace" or raw.startswith("/trace "):
            args = _safe_split(raw)
            if len(args) > 2:
                print("Usage: /trace [LIMIT]")
                continue
            limit = _parse_optional_limit(args[1] if len(args) == 2 else None, "Usage: /trace [LIMIT]")
            if limit is None:
                continue
            print(_format_trace(runtime, limit=limit))
            continue
        if raw.startswith("/use "):
            session_id = raw.split(" ", 1)[1].strip()
            try:
                session = runtime.use_session(session_id)
            except FileNotFoundError:
                print("Session not found: {0}".format(session_id))
                continue
            print("[session] {0} ({1})".format(session.id, session.backend))
            continue
        if raw == "/ls" or raw.startswith("/ls "):
            args = _safe_split(raw)
            if len(args) > 3:
                print("Usage: /ls [PATH] [DEPTH]")
                continue
            payload = {}
            if len(args) >= 2:
                payload["path"] = args[1]
            if len(args) >= 3:
                payload["depth"] = args[2]
            _run_tool_and_print(runtime, "ls", payload)
            continue
        if raw == "/tree" or raw.startswith("/tree "):
            args = _safe_split(raw)
            if len(args) > 3:
                print("Usage: /tree [PATH] [DEPTH]")
                continue
            payload = {}
            if len(args) >= 2:
                payload["path"] = args[1]
            if len(args) >= 3:
                payload["depth"] = args[2]
            _run_tool_and_print(runtime, "tree", payload)
            continue
        if raw.startswith("/find "):
            args = _safe_split(raw)
            if len(args) < 2 or len(args) > 3:
                print("Usage: /find PATTERN [PATH]")
                continue
            payload = {"pattern": args[1]}
            if len(args) >= 3:
                payload["path"] = args[2]
            _run_tool_and_print(runtime, "find", payload)
            continue
        if raw.startswith("/grep "):
            args = _safe_split(raw)
            if len(args) < 2 or len(args) > 4:
                print("Usage: /grep PATTERN [PATH] [INCLUDE]")
                continue
            payload = {"pattern": args[1]}
            if len(args) >= 3:
                payload["path"] = args[2]
            if len(args) >= 4:
                payload["include"] = args[3]
            _run_tool_and_print(runtime, "grep", payload)
            continue
        if raw.startswith("/outline "):
            args = _safe_split(raw)
            if len(args) < 2 or len(args) > 3:
                print("Usage: /outline PATH [MAX_ITEMS]")
                continue
            payload = {"path": args[1]}
            if len(args) >= 3:
                payload["max_items"] = args[2]
            _run_tool_and_print(runtime, "get_outline", payload)
            continue
        if raw.startswith("/cat "):
            args = _safe_split(raw)
            if len(args) < 2 or len(args) > 4:
                print("Usage: /cat PATH [OFFSET] [LIMIT]")
                continue
            payload = {"path": args[1]}
            if len(args) >= 3:
                payload["offset"] = args[2]
            if len(args) >= 4:
                payload["limit"] = args[3]
            _run_tool_and_print(runtime, "cat", payload)
            continue

        try:
            text = runtime.ask(raw, stream=stream)
        except BackendError as exc:
            print("Backend error: {0}".format(exc))
            continue

        if not stream:
            print(text)


def _run_tool_and_print(runtime: AgentRuntime, tool_name: str, payload: dict) -> None:
    try:
        print(runtime.run_tool(tool_name, payload))
    except ToolError as exc:
        print("Tool error: {0}".format(exc))


def _parse_optional_limit(value, usage):
    if value is None:
        return 20
    try:
        limit = int(value)
    except ValueError:
        print(usage)
        return None
    if limit <= 0:
        print(usage)
        return None
    return limit


def _setup_readline(runtime: AgentRuntime) -> None:
    if readline is None:
        return
    readline.parse_and_bind("tab: complete")
    readline.set_completer_delims(" \t\n")
    readline.set_completer(_build_completer(runtime))


def _build_completer(runtime: AgentRuntime):
    def completer(text, state):
        buffer_text = readline.get_line_buffer()
        matches = _complete_input(runtime, buffer_text, text)
        if state < len(matches):
            return matches[state]
        return None

    return completer


def _complete_input(runtime: AgentRuntime, buffer_text: str, text: str):
    stripped = buffer_text.lstrip()
    if not stripped or (stripped.startswith("/") and " " not in stripped):
        return [item for item in COMMANDS if item.startswith(text)]

    if stripped.startswith("/cat "):
        return _complete_workspace_paths(runtime, stripped.split(" ", 1)[1])
    if stripped.startswith("/ls "):
        return _complete_workspace_paths(runtime, stripped.split(" ", 1)[1])
    if stripped.startswith("/tree "):
        return _complete_workspace_paths(runtime, stripped.split(" ", 1)[1])
    if stripped.startswith("/find "):
        args = _safe_split(stripped)
        if len(args) >= 3:
            return _complete_workspace_paths(runtime, args[2])
    if stripped.startswith("/grep "):
        args = _safe_split(stripped)
        if len(args) >= 3:
            return _complete_workspace_paths(runtime, args[2])
    if stripped.startswith("/use "):
        return _complete_sessions(runtime, stripped.split(" ", 1)[1])
    return []


def _complete_workspace_paths(runtime: AgentRuntime, prefix: str):
    workspace_root = runtime.config.workspace_root
    normalized = prefix.replace("\\ ", " ")
    base_path = (workspace_root / normalized).resolve()

    if prefix.endswith("/"):
        parent = base_path
        fragment = ""
    else:
        parent = base_path.parent
        fragment = base_path.name

    if not parent.exists() or not parent.is_dir():
        return []

    matches = []
    for child in sorted(parent.iterdir(), key=lambda item: item.name):
        if fragment and not child.name.startswith(fragment):
            continue
        try:
            relative = child.relative_to(workspace_root).as_posix()
        except ValueError:
            continue
        if child.is_dir():
            relative += "/"
        matches.append(_escape_completion(relative))
    return matches


def _complete_sessions(runtime: AgentRuntime, prefix: str):
    return [
        session.id
        for session in runtime.session_store.list_sessions()
        if session.id.startswith(prefix)
    ]


def _escape_completion(value: str) -> str:
    return value.replace(" ", "\\ ")


def _safe_split(text: str):
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _format_trace(runtime: AgentRuntime, limit: int = 20) -> str:
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
        lines.extend(_format_trace_message(message))
    return "\n".join(lines)


def _format_trace_message(message) -> list:
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


def _format_history(runtime: AgentRuntime, limit: int = 20) -> str:
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
        lines.extend(_format_history_message(message))
    return "\n".join(lines)


def _format_history_message(message) -> list:
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
