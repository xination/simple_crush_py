import shlex

from .repl_display import format_history, format_trace
from .tools.base import ToolError


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
/summarize PATH       send a direct-file summary request
/guide QUESTION       send a beginner-friendly docs request
/trace REQUEST        send a trace request (same meaning as CLI --trace)
/history [LIMIT]      show recent conversation messages
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
    "/summarize",
    "/guide",
    "/history",
    "/trace",
    "/quit",
]


def try_handle_command(runtime, raw: str, stream: bool = False):
    if raw == "/quit":
        return True, 0
    if raw == "/help":
        print(HELP_TEXT)
        return True, None
    if raw == "/new":
        session = runtime.new_session()
        print("[session] {0} ({1})".format(session.id, session.backend))
        return True, None
    if raw == "/sessions":
        for session in runtime.session_store.list_sessions():
            print("{0}  {1}  {2}".format(session.id, session.backend, session.title))
        return True, None
    if raw == "/backend":
        for name in runtime.available_backends():
            marker = "*" if name == runtime.active_backend_name else " "
            print("{0} {1}".format(marker, name))
        return True, None
    if raw == "/tools":
        for name in runtime.available_tools():
            print(name)
        return True, None
    if raw == "/history" or raw.startswith("/history "):
        args = safe_split(raw)
        if len(args) > 2:
            print("Usage: /history [LIMIT]")
            return True, None
        limit = parse_optional_limit(args[1] if len(args) == 2 else None, "Usage: /history [LIMIT]")
        if limit is None:
            return True, None
        print(format_history(runtime, limit=limit))
        return True, None
    if raw == "/tool-trace" or raw.startswith("/tool-trace "):
        args = safe_split(raw)
        if len(args) > 2:
            print("Usage: /tool-trace [LIMIT]")
            return True, None
        limit = parse_optional_limit(args[1] if len(args) == 2 else None, "Usage: /tool-trace [LIMIT]")
        if limit is None:
            return True, None
        print(format_trace(runtime, limit=limit))
        return True, None
    if raw.startswith("/summarize "):
        from .cli import build_summary_prompt

        request = raw.split(" ", 1)[1].strip()
        if not request:
            print("Usage: /summarize PATH")
            return True, None
        runtime.ask(build_summary_prompt(request), stream=stream)
        return True, None
    if raw.startswith("/guide "):
        from .cli import build_guide_prompt

        request = raw.split(" ", 1)[1].strip()
        if not request:
            print("Usage: /guide QUESTION")
            return True, None
        runtime.ask(build_guide_prompt(request), stream=stream)
        return True, None
    if raw.startswith("/trace "):
        from .cli import build_trace_prompt

        request = raw.split(" ", 1)[1].strip()
        if not request:
            print("Usage: /trace REQUEST")
            return True, None
        runtime.ask(build_trace_prompt(request), stream=stream)
        return True, None
    if raw == "/trace":
        print("Usage: /trace REQUEST")
        return True, None
    if raw.startswith("/use "):
        session_id = raw.split(" ", 1)[1].strip()
        try:
            session = runtime.use_session(session_id)
        except FileNotFoundError:
            print("Session not found: {0}".format(session_id))
            return True, None
        print("[session] {0} ({1})".format(session.id, session.backend))
        return True, None
    if raw == "/ls" or raw.startswith("/ls "):
        args = safe_split(raw)
        if len(args) > 3:
            print("Usage: /ls [PATH] [DEPTH]")
            return True, None
        payload = {}
        if len(args) >= 2:
            payload["path"] = args[1]
        if len(args) >= 3:
            payload["depth"] = args[2]
        run_tool_and_print(runtime, "ls", payload)
        return True, None
    if raw == "/tree" or raw.startswith("/tree "):
        args = safe_split(raw)
        if len(args) > 3:
            print("Usage: /tree [PATH] [DEPTH]")
            return True, None
        payload = {}
        if len(args) >= 2:
            payload["path"] = args[1]
        if len(args) >= 3:
            payload["depth"] = args[2]
        run_tool_and_print(runtime, "tree", payload)
        return True, None
    if raw.startswith("/find "):
        args = safe_split(raw)
        if len(args) < 2 or len(args) > 3:
            print("Usage: /find PATTERN [PATH]")
            return True, None
        payload = {"pattern": args[1]}
        if len(args) >= 3:
            payload["path"] = args[2]
        run_tool_and_print(runtime, "find", payload)
        return True, None
    if raw.startswith("/grep "):
        args = safe_split(raw)
        if len(args) < 2 or len(args) > 4:
            print("Usage: /grep PATTERN [PATH] [INCLUDE]")
            return True, None
        payload = {"pattern": args[1]}
        if len(args) >= 3:
            payload["path"] = args[2]
        if len(args) >= 4:
            payload["include"] = args[3]
        run_tool_and_print(runtime, "grep", payload)
        return True, None
    if raw.startswith("/outline "):
        args = safe_split(raw)
        if len(args) < 2 or len(args) > 3:
            print("Usage: /outline PATH [MAX_ITEMS]")
            return True, None
        payload = {"path": args[1]}
        if len(args) >= 3:
            payload["max_items"] = args[2]
        run_tool_and_print(runtime, "get_outline", payload)
        return True, None
    if raw.startswith("/cat "):
        args = safe_split(raw)
        if len(args) < 2 or len(args) > 4:
            print("Usage: /cat PATH [OFFSET] [LIMIT]")
            return True, None
        payload = {"path": args[1]}
        if len(args) >= 3:
            payload["offset"] = args[2]
        if len(args) >= 4:
            payload["limit"] = args[3]
        run_tool_and_print(runtime, "cat", payload)
        return True, None
    return False, None


def run_tool_and_print(runtime, tool_name: str, payload: dict) -> None:
    try:
        print(runtime.run_tool(tool_name, payload))
    except ToolError as exc:
        print("Tool error: {0}".format(exc))


def parse_optional_limit(value, usage):
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


def safe_split(text: str):
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()
