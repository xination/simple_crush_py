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
/ls [PATH] [DEPTH]    list directory tree
/glob PATTERN [PATH]  find files by glob
/grep PATTERN [PATH] [INCLUDE]
/bash COMMAND         run a shell command inside workspace
/history [LIMIT]      show recent conversation messages
/trace [LIMIT]        show recent tool trace entries
/write PATH            overwrite a file with multiline input
/edit PATH             replace one text block with another
/view PATH            read a file inside workspace
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
    "/glob",
    "/grep",
    "/bash",
    "/history",
    "/trace",
    "/write",
    "/edit",
    "/view",
    "/quit",
]


def run_repl(runtime: AgentRuntime, stream: bool = False) -> int:
    runtime.tool_confirmation_callback = _confirm_tool_use
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
            try:
                args = shlex.split(raw)
            except ValueError as exc:
                print("Command parse error: {0}".format(exc))
                continue
            if len(args) > 2:
                print("Usage: /history [LIMIT]")
                continue
            limit = 20
            if len(args) == 2:
                try:
                    limit = int(args[1])
                except ValueError:
                    print("Usage: /history [LIMIT]")
                    continue
            if limit <= 0:
                print("Usage: /history [LIMIT]")
                continue
            print(_format_history(runtime, limit=limit))
            continue
        if raw == "/trace" or raw.startswith("/trace "):
            try:
                args = shlex.split(raw)
            except ValueError as exc:
                print("Command parse error: {0}".format(exc))
                continue
            if len(args) > 2:
                print("Usage: /trace [LIMIT]")
                continue
            limit = 20
            if len(args) == 2:
                try:
                    limit = int(args[1])
                except ValueError:
                    print("Usage: /trace [LIMIT]")
                    continue
            if limit <= 0:
                print("Usage: /trace [LIMIT]")
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
            try:
                args = shlex.split(raw)
            except ValueError as exc:
                print("Command parse error: {0}".format(exc))
                continue
            if len(args) > 3:
                print("Usage: /ls [PATH] [DEPTH]")
                continue
            payload = {}
            if len(args) >= 2:
                payload["path"] = args[1]
            if len(args) >= 3:
                payload["depth"] = args[2]
            try:
                print(runtime.run_tool("ls", payload))
            except ToolError as exc:
                print("Tool error: {0}".format(exc))
            continue
        if raw.startswith("/glob "):
            try:
                args = shlex.split(raw)
            except ValueError as exc:
                print("Command parse error: {0}".format(exc))
                continue
            if len(args) < 2 or len(args) > 3:
                print("Usage: /glob PATTERN [PATH]")
                continue
            payload = {"pattern": args[1]}
            if len(args) >= 3:
                payload["path"] = args[2]
            try:
                print(runtime.run_tool("glob", payload))
            except ToolError as exc:
                print("Tool error: {0}".format(exc))
            continue
        if raw.startswith("/grep "):
            try:
                args = shlex.split(raw)
            except ValueError as exc:
                print("Command parse error: {0}".format(exc))
                continue
            if len(args) < 2 or len(args) > 4:
                print("Usage: /grep PATTERN [PATH] [INCLUDE]")
                continue
            payload = {"pattern": args[1]}
            if len(args) >= 3:
                payload["path"] = args[2]
            if len(args) >= 4:
                payload["include"] = args[3]
            try:
                print(runtime.run_tool("grep", payload))
            except ToolError as exc:
                print("Tool error: {0}".format(exc))
            continue
        if raw.startswith("/bash "):
            command = _normalize_bash_command(raw[len("/bash ") :].strip())
            if not command:
                print("Usage: /bash COMMAND")
                continue
            if not _confirm_action("Run shell command `{0}`?".format(command)):
                print("Shell command cancelled.")
                continue
            try:
                print(
                    runtime.run_tool(
                        "bash",
                        {
                            "command": command,
                            "confirm": True,
                        },
                    )
                )
            except ToolError as exc:
                print("Tool error: {0}".format(exc))
            continue
        if raw.startswith("/view "):
            try:
                args = shlex.split(raw)
            except ValueError as exc:
                print("Command parse error: {0}".format(exc))
                continue
            if len(args) < 2:
                print("Usage: /view PATH [OFFSET] [LIMIT]")
                continue
            if len(args) > 4:
                print("Usage: /view PATH [OFFSET] [LIMIT]")
                continue
            payload = {"path": args[1]}
            if len(args) >= 3:
                payload["offset"] = args[2]
            if len(args) >= 4:
                payload["limit"] = args[3]
            try:
                print(runtime.run_tool("view", payload))
            except ToolError as exc:
                print("Tool error: {0}".format(exc))
            continue
        if raw.startswith("/write "):
            try:
                args = shlex.split(raw)
            except ValueError as exc:
                print("Command parse error: {0}".format(exc))
                continue
            if len(args) != 2:
                print("Usage: /write PATH")
                continue
            content = _read_multiline_block(
                "Enter full file content. Finish with a line containing only `.end`."
            )
            if content is None:
                print("Write cancelled.")
                continue
            if not _confirm_action("Write file `{0}`?".format(args[1])):
                print("Write cancelled.")
                continue
            try:
                print(
                    runtime.run_tool(
                        "write",
                        {"path": args[1], "content": content, "confirm": True},
                    )
                )
            except ToolError as exc:
                print("Tool error: {0}".format(exc))
            continue
        if raw.startswith("/edit "):
            try:
                args = shlex.split(raw)
            except ValueError as exc:
                print("Command parse error: {0}".format(exc))
                continue
            if len(args) != 2:
                print("Usage: /edit PATH")
                continue
            old_text = _read_multiline_block(
                "Enter old text to replace. Finish with a line containing only `.end`."
            )
            if old_text is None:
                print("Edit cancelled.")
                continue
            new_text = _read_multiline_block(
                "Enter new text. Finish with a line containing only `.end`."
            )
            if new_text is None:
                print("Edit cancelled.")
                continue
            replace_all = _confirm_action("Replace all matches?")
            if not _confirm_action("Edit file `{0}`?".format(args[1])):
                print("Edit cancelled.")
                continue
            try:
                print(
                    runtime.run_tool(
                        "edit",
                        {
                            "path": args[1],
                            "old_text": old_text,
                            "new_text": new_text,
                            "replace_all": replace_all,
                            "confirm": True,
                        },
                    )
                )
            except ToolError as exc:
                print("Tool error: {0}".format(exc))
            continue

        try:
            text = runtime.ask(raw, stream=stream)
        except BackendError as exc:
            print("Backend error: {0}".format(exc))
            continue

        if not stream:
            print(text)


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

    if stripped.startswith("/view "):
        prefix = stripped.split(" ", 1)[1]
        return _complete_workspace_paths(runtime, prefix)

    if stripped.startswith("/write "):
        prefix = stripped.split(" ", 1)[1]
        return _complete_workspace_paths(runtime, prefix)

    if stripped.startswith("/edit "):
        prefix = stripped.split(" ", 1)[1]
        return _complete_workspace_paths(runtime, prefix)

    if stripped.startswith("/ls "):
        prefix = stripped.split(" ", 1)[1]
        return _complete_workspace_paths(runtime, prefix)

    if stripped.startswith("/glob "):
        args = _safe_split(stripped)
        if len(args) == 2:
            return _complete_workspace_paths(runtime, "")
        if len(args) >= 3:
            return _complete_workspace_paths(runtime, args[2])

    if stripped.startswith("/grep "):
        args = _safe_split(stripped)
        if len(args) == 2:
            return _complete_workspace_paths(runtime, "")
        if len(args) >= 3:
            return _complete_workspace_paths(runtime, args[2])

    if stripped.startswith("/use "):
        session_prefix = stripped.split(" ", 1)[1]
        return _complete_sessions(runtime, session_prefix)

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


def _normalize_bash_command(command: str) -> str:
    if not command:
        return ""
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if len(parts) == 1:
        return parts[0]
    return command


def _format_trace(runtime: AgentRuntime, limit: int = 20) -> str:
    session = runtime.active_session
    if session is None:
        return "No active session."

    trace_messages = []
    for message in runtime.session_store.load_messages(session.id):
        if message.kind in ("tool_use", "tool_result"):
            trace_messages.append(message)
            continue
        if message.kind == "message" and message.role == "assistant" and message.metadata.get("raw_content"):
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
    lines = [
        "[{0}] {1} ({2})".format(message.kind, message.role, message.created_at),
    ]
    if message.kind == "message":
        text = message.content.strip() if isinstance(message.content, str) else ""
        raw_content = message.metadata.get("raw_content", [])
        lines.append("stage: assistant_final")
        if text:
            lines.append("text: {0}".format(_single_line(text)))
        if raw_content:
            lines.append("raw: {0}".format(_single_line(str(raw_content))))
        return lines

    if message.kind == "tool_use":
        raw_content = message.metadata.get("raw_content", [])
        text = message.content.strip() if isinstance(message.content, str) else ""
        tool_names = []
        for item in raw_content:
            if item.get("type") == "tool_use":
                tool_names.append(item.get("name", ""))
        if tool_names:
            lines.append("tool: {0}".format(", ".join(name for name in tool_names if name)))
        if text:
            lines.append("text: {0}".format(_single_line(text)))
        return lines

    tool_name = message.metadata.get("tool_name", "")
    tool_arguments = message.metadata.get("tool_arguments", {})
    if tool_name:
        lines.append("tool: {0}".format(tool_name))
    if tool_arguments:
        lines.append("arguments: {0}".format(tool_arguments))
    content = message.content if isinstance(message.content, str) else str(message.content)
    lines.append("result: {0}".format(_single_line(content)))
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


def _read_multiline_block(prompt: str):
    print(prompt)
    lines = []
    while True:
        try:
            line = input("... ")
        except EOFError:
            print("")
            return None
        except KeyboardInterrupt:
            print("")
            return None
        if line == ".end":
            return "\n".join(lines)
        lines.append(line)


def _confirm_action(prompt: str) -> bool:
    while True:
        try:
            answer = input("{0} [y/N]: ".format(prompt)).strip().lower()
        except EOFError:
            print("")
            return False
        except KeyboardInterrupt:
            print("")
            return False
        if answer in ("y", "yes"):
            return True
        if answer in ("", "n", "no"):
            return False


def _confirm_tool_use(tool_name: str, arguments: dict, preview: str) -> bool:
    print("[confirm] automatic tool request: {0}".format(tool_name))
    print(preview)
    return _confirm_action("Allow this tool call?")
