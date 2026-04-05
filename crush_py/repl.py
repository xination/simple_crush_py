from .agent.runtime import AgentRuntime
from .backends.base import BackendError
from .repl_completion import setup_readline
from .repl_commands import try_handle_command
from .repl_display import (
    format_history,
    format_history_message,
    format_trace,
    format_trace_message,
)


def run_repl(runtime: AgentRuntime, stream: bool = False) -> int:
    setup_readline(runtime)

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
        handled, exit_code = try_handle_command(runtime, raw, stream=stream)
        if handled:
            if exit_code is not None:
                return exit_code
            continue

        try:
            text = runtime.ask(raw, stream=stream, show_thinking=True)
            if not stream and text:
                print(text)
        except BackendError as exc:
            print("Backend error: {0}".format(exc))
            continue
_format_trace = format_trace
_format_trace_message = format_trace_message
_format_history = format_history
_format_history_message = format_history_message
