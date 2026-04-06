import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .repl_display import format_history, format_trace
from .tools.base import ToolError


@dataclass(frozen=True)
class CommandSpec:
    matches: Callable[[str], bool]
    handler: Callable[[object, str, bool], tuple[bool, Optional[int]]]


def exact(command: str) -> Callable[[str], bool]:
    return lambda raw: raw == command


def prefix(command: str) -> Callable[[str], bool]:
    return lambda raw: raw.startswith(command)


def exact_or_prefix(command: str) -> Callable[[str], bool]:
    return lambda raw: raw == command or raw.startswith(command + " ")


def print_command_hint(raw: str) -> None:
    print("\033[1m{0}\033[0m".format(raw))


def handle_quit(runtime, raw: str, stream: bool = False):
    return True, 0


def handle_help(runtime, raw: str, stream: bool = False):
    from .repl_commands import HELP_TEXT

    print_command_hint(raw)
    print(HELP_TEXT)
    return True, None


def handle_new(runtime, raw: str, stream: bool = False):
    print_command_hint(raw)
    session = runtime.new_session()
    print("[session] {0} ({1})".format(session.id, session.backend))
    return True, None


def handle_sessions(runtime, raw: str, stream: bool = False):
    print_command_hint(raw)
    for session in runtime.session_store.list_sessions():
        print("{0}  {1}  {2}".format(session.id, session.backend, session.title))
    return True, None


def handle_backend(runtime, raw: str, stream: bool = False):
    print_command_hint(raw)
    for name in runtime.available_backends():
        marker = "*" if name == runtime.active_backend_name else " "
        print("{0} {1}".format(marker, name))
    return True, None


def handle_info(runtime, raw: str, stream: bool = False):
    print_command_hint(raw)
    session = getattr(runtime, "active_session", None)
    print("Session: {0}".format(getattr(session, "id", "")))
    print("Backend: {0}".format(getattr(runtime, "active_backend_name", "")))
    print("Model: {0}".format(getattr(session, "model", "")))
    workspace_root = getattr(getattr(runtime, "config", None), "workspace_root", "")
    sessions_dir = getattr(getattr(runtime, "config", None), "sessions_dir", "")
    if isinstance(workspace_root, Path):
        workspace_root = workspace_root.as_posix()
    if isinstance(sessions_dir, Path):
        sessions_dir = sessions_dir.as_posix()
    print("Workspace Root: {0}".format(workspace_root))
    print("Sessions Dir: {0}".format(sessions_dir))
    print("Trace Mode: {0}".format(getattr(getattr(runtime, "session_store", None), "trace_mode", "")))
    return True, None


def handle_tools(runtime, raw: str, stream: bool = False):
    print_command_hint(raw)
    for name in runtime.available_tools():
        print(name)
    return True, None


def handle_history(runtime, raw: str, stream: bool = False):
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


def handle_tool_trace(runtime, raw: str, stream: bool = False):
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


def handle_summary(runtime, raw: str, stream: bool = False):
    from .cli import build_summary_prompt

    return _handle_prompt_command(runtime, raw, stream, "/summarize ", "Usage: /summarize PATH", build_summary_prompt)


def handle_guide(runtime, raw: str, stream: bool = False):
    from .cli import build_guide_prompt

    return _handle_prompt_command(runtime, raw, stream, "/guide ", "Usage: /guide QUESTION", build_guide_prompt)


def handle_trace(runtime, raw: str, stream: bool = False):
    from .cli import build_trace_prompt

    return _handle_prompt_command(runtime, raw, stream, "/trace ", "Usage: /trace REQUEST", build_trace_prompt)


def handle_quick(runtime, raw: str, stream: bool = False):
    print_command_hint(raw)
    parsed = parse_quick_command(raw)
    if parsed is None:
        print("Usage: /quick @PATH, PROMPT")
        return True, None
    path, request = parsed
    runtime.ask_quick_file(path, request, stream=True)
    return True, None


def handle_quick_usage(runtime, raw: str, stream: bool = False):
    print_command_hint(raw)
    print("Usage: /quick @PATH, PROMPT")
    return True, None


def handle_trace_usage(runtime, raw: str, stream: bool = False):
    print_command_hint(raw)
    print("Usage: /trace REQUEST")
    return True, None


def handle_use(runtime, raw: str, stream: bool = False):
    print_command_hint(raw)
    session_id = raw.split(" ", 1)[1].strip()
    try:
        session = runtime.use_session(session_id)
    except FileNotFoundError:
        print("Session not found: {0}".format(session_id))
        return True, None
    print("[session] {0} ({1})".format(session.id, session.backend))
    return True, None


def handle_ls(runtime, raw: str, stream: bool = False):
    return _handle_tool_command(runtime, raw, "ls", min_args=0, max_args=2, usage="Usage: /ls [PATH] [DEPTH]")


def handle_tree(runtime, raw: str, stream: bool = False):
    return _handle_tool_command(runtime, raw, "ls", min_args=0, max_args=2, usage="Usage: /tree [PATH] [DEPTH]")


def handle_find(runtime, raw: str, stream: bool = False):
    return _handle_tool_command(
        runtime,
        raw,
        "find",
        min_args=1,
        max_args=2,
        usage="Usage: /find PATTERN [PATH]",
        arg_names=["pattern", "path"],
    )


def handle_grep(runtime, raw: str, stream: bool = False):
    return _handle_tool_command(
        runtime,
        raw,
        "grep",
        min_args=1,
        max_args=3,
        usage="Usage: /grep PATTERN [PATH] [INCLUDE]",
        arg_names=["pattern", "path", "include"],
    )


def handle_outline(runtime, raw: str, stream: bool = False):
    return _handle_tool_command(
        runtime,
        raw,
        "get_outline",
        min_args=1,
        max_args=2,
        usage="Usage: /outline PATH [MAX_ITEMS]",
        arg_names=["path", "max_items"],
    )


def handle_cat(runtime, raw: str, stream: bool = False):
    return _handle_tool_command(
        runtime,
        raw,
        "cat",
        min_args=1,
        max_args=3,
        usage="Usage: /cat PATH [OFFSET] [LIMIT]",
        arg_names=["path", "offset", "limit"],
    )


def _handle_prompt_command(runtime, raw: str, stream: bool, command_prefix: str, usage: str, builder):
    print_command_hint(raw)
    request = raw.split(" ", 1)[1].strip()
    if not request:
        print(usage)
        return True, None
    text = runtime.ask(builder(request), stream=stream, show_thinking=True)
    if not stream and text:
        print(text)
    return True, None


def _handle_tool_command(
    runtime,
    raw: str,
    tool_name: str,
    min_args: int,
    max_args: int,
    usage: str,
    arg_names: Optional[list[str]] = None,
):
    print_command_hint(raw)
    if runtime.active_session:
        runtime.session_store.append_message(runtime.active_session.id, "user", raw)
    args = safe_split(raw)
    values = args[1:]
    if len(values) < min_args or len(values) > max_args:
        print(usage)
        return True, None
    payload = {}
    if arg_names is None:
        arg_names = ["path", "depth"]
    for key, value in zip(arg_names, values):
        payload[key] = value
    run_tool_and_print(runtime, tool_name, payload)
    return True, None


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


def parse_quick_command(raw: str):
    body = raw[len("/quick ") :].strip()
    if not body or "," not in body:
        return None
    path_part, prompt_part = body.split(",", 1)
    normalized_path = path_part.strip()
    if normalized_path.startswith("@"):
        normalized_path = normalized_path[1:].strip()
    prompt = prompt_part.strip()
    if not normalized_path or not prompt:
        return None
    return normalized_path, prompt


COMMAND_SPECS = [
    CommandSpec(exact("/quit"), handle_quit),
    CommandSpec(exact("/help"), handle_help),
    CommandSpec(exact("/new"), handle_new),
    CommandSpec(exact("/sessions"), handle_sessions),
    CommandSpec(exact("/info"), handle_info),
    CommandSpec(exact("/tools"), handle_tools),
    CommandSpec(exact_or_prefix("/history"), handle_history),
    CommandSpec(exact_or_prefix("/tool-trace"), handle_tool_trace),
    CommandSpec(prefix("/summarize "), handle_summary),
    CommandSpec(prefix("/guide "), handle_guide),
    CommandSpec(prefix("/trace "), handle_trace),
    CommandSpec(prefix("/quick "), handle_quick),
    CommandSpec(exact("/quick"), handle_quick_usage),
    CommandSpec(exact("/trace"), handle_trace_usage),
    CommandSpec(prefix("/use "), handle_use),
    CommandSpec(exact_or_prefix("/ls"), handle_ls),
    CommandSpec(exact_or_prefix("/tree"), handle_tree),
    CommandSpec(prefix("/find "), handle_find),
    CommandSpec(prefix("/grep "), handle_grep),
    CommandSpec(prefix("/outline "), handle_outline),
    CommandSpec(prefix("/cat "), handle_cat),
]
