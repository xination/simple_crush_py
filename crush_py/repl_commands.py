from .repl_command_handlers import (
    COMMAND_SPECS,
    parse_optional_limit,
    parse_quick_command,
    print_command_hint,
    run_tool_and_print,
    safe_split,
)


HELP_TEXT = """Commands:
/new                  create a new session
/backend              show available backends
/model [NAME]         show or set the current session model
/ls [PATH] [DEPTH]    quick listing for a directory area
/find PATTERN [PATH]  locate files by filename/path pattern, with fuzzy fallback
/grep PATTERN [PATH] [INCLUDE]
/cat PATH [OFFSET] [LIMIT]
/quick @PATH, PROMPT  single-file quick mode; always streams; skips intent detection and tools
                      format: use the first comma to separate file path and prompt
                      prompt rule: everything after the first comma is treated as the prompt
                      example: /quick @README.md, show me how to start in Traditional Chinese
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
    "/quick",
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
    for spec in COMMAND_SPECS:
        if spec.matches(raw):
            return spec.handler(runtime, raw, stream)
    return False, None
