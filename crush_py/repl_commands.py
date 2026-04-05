import shlex

from .repl_display import format_history, format_trace
from .tools.base import ToolError


HELP_TEXT = """Commands:
/new                  create a new session
/backend              show available backends
/model [NAME]         show or set the current session model
/ls [PATH] [DEPTH]    quick listing for a directory area
/find PATTERN [PATH]  locate files by filename/path pattern, with fuzzy fallback
/grep PATTERN [PATH] [INCLUDE]
/cat PATH [OFFSET] [LIMIT]
/summarize PATH       send a direct-file summary request
/guide QUESTION       send a beginner-friendly docs request
/trace REQUEST        send a trace request (same meaning as CLI --trace)
/quit                 exit
"""

VISIBLE_COMMANDS = [
    "/help",
    "/new",
    "/backend",
    "/model",
    "/ls",
    "/find",
    "/grep",
    "/cat",
    "/summarize",
    "/guide",
    "/trace",
    "/quit",
]

COMMANDS = VISIBLE_COMMANDS + [
    "/sessions",
    "/use",
    "/tools",
    "/outline",
    "/history",
    "/tree",
]


def print_command_hint(raw: str) -> None:
    print("\033[1m{0}\033[0m".format(raw))


def try_handle_command(runtime, raw: str, stream: bool = False):
    if raw == "/quit":
        return True, 0
    if raw == "/help":
        print_command_hint(raw)
        print(HELP_TEXT)
        return True, None
    if raw == "/new":
        print_command_hint(raw)
        session = runtime.new_session()
        print("[session] {0} ({1})".format(session.id, session.backend))
        return True, None
    if raw == "/sessions":
        print_command_hint(raw)
        for session in runtime.session_store.list_sessions():
            print("{0}  {1}  {2}".format(session.id, session.backend, session.title))
        return True, None
    if raw == "/backend":
        print_command_hint(raw)
        for name in runtime.available_backends():
            marker = "*" if name == runtime.active_backend_name else " "
            print("{0} {1}".format(marker, name))
        return True, None
    if raw == "/model":
        print_command_hint(raw)
        session = getattr(runtime, "active_session", None)
        if session is None:
            session = runtime.new_session()
        print(getattr(session, "model", ""))
        return True, None
    if raw.startswith("/model "):
        print_command_hint(raw)
        model = raw.split(" ", 1)[1].strip()
        if not model:
            print("Usage: /model MODEL_NAME")
            return True, None
        session = runtime.set_session_model(model)
        print("[session] {0} model={1}".format(session.id, session.model))
        return True, None
    if raw == "/tools":
        print_command_hint(raw)
        for name in runtime.available_tools():
            print(name)
        return True, None
    if raw == "/history" or raw.startswith("/history "):
        print_command_hint(raw)
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
        print_command_hint(raw)
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
        print_command_hint(raw)
        from .cli import build_summary_prompt

        request = raw.split(" ", 1)[1].strip()
        if not request:
            print("Usage: /summarize PATH")
            return True, None
        text = runtime.ask(build_summary_prompt(request), stream=stream, show_thinking=True)
        if not stream and text:
            print(text)
        return True, None
    if raw.startswith("/guide "):
        print_command_hint(raw)
        from .cli import build_guide_prompt

        request = raw.split(" ", 1)[1].strip()
        if not request:
            print("Usage: /guide QUESTION")
            return True, None
        text = runtime.ask(build_guide_prompt(request), stream=stream, show_thinking=True)
        if not stream and text:
            print(text)
        return True, None
    if raw.startswith("/trace "):
        print_command_hint(raw)
        from .cli import build_trace_prompt

        request = raw.split(" ", 1)[1].strip()
        if not request:
            print("Usage: /trace REQUEST")
            return True, None
        text = runtime.ask(build_trace_prompt(request), stream=stream, show_thinking=True)
        if not stream and text:
            print(text)
        return True, None
    if raw == "/trace":
        print_command_hint(raw)
        print("Usage: /trace REQUEST")
        return True, None
    if raw.startswith("/use "):
        print_command_hint(raw)
        session_id = raw.split(" ", 1)[1].strip()
        try:
            session = runtime.use_session(session_id)
        except FileNotFoundError:
            print("Session not found: {0}".format(session_id))
            return True, None
        print("[session] {0} ({1})".format(session.id, session.backend))
        return True, None
    if raw == "/ls" or raw.startswith("/ls "):
        print_command_hint(raw)
        if runtime.active_session:
            runtime.session_store.append_message(runtime.active_session.id, "user", raw)
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
        print_command_hint(raw)
        if runtime.active_session:
            runtime.session_store.append_message(runtime.active_session.id, "user", raw)
        args = safe_split(raw)
        if len(args) > 3:
            print("Usage: /tree [PATH] [DEPTH]")
            return True, None
        payload = {}
        if len(args) >= 2:
            payload["path"] = args[1]
        if len(args) >= 3:
            payload["depth"] = args[2]
        run_tool_and_print(runtime, "ls", payload)
        return True, None
    if raw.startswith("/find "):
        print_command_hint(raw)
        if runtime.active_session:
            runtime.session_store.append_message(runtime.active_session.id, "user", raw)
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
        print_command_hint(raw)
        if runtime.active_session:
            runtime.session_store.append_message(runtime.active_session.id, "user", raw)
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
        print_command_hint(raw)
        if runtime.active_session:
            runtime.session_store.append_message(runtime.active_session.id, "user", raw)
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
        print_command_hint(raw)
        if runtime.active_session:
            runtime.session_store.append_message(runtime.active_session.id, "user", raw)
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
    session = runtime.active_session
    if session:
        runtime.session_store.append_message(
            session.id,
            "assistant",
            "",
            kind="tool_use",
            metadata={
                "tool": tool_name,
                "args": payload,
                "agent": "planner",
                "__flat__": True,
            },
        )
    try:
        result = runtime.run_tool(tool_name, payload)
        print(result)
        if session:
            summary = runtime._summarize_tool_result(session.id, tool_name, payload, result)
            runtime.session_store.append_message(
                session.id,
                "user",
                runtime._backend_tool_result_content(tool_name, result, summary),
                kind="tool_result",
                metadata={
                    "tool": tool_name,
                    "args": payload,
                    "summary": summary,
                    "agent": "planner",
                    "__flat__": True,
                },
            )
    except ToolError as exc:
        print("Tool error: {0}".format(exc))
        if session:
            runtime.session_store.append_message(
                session.id,
                "user",
                "Tool error: {0}".format(exc),
                kind="tool_result",
                metadata={
                    "tool": tool_name,
                    "args": payload,
                    "error": True,
                    "agent": "planner",
                    "__flat__": True,
                },
            )


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
