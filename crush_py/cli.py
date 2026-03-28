import argparse
from pathlib import Path

from .agent.runtime import AgentRuntime
from .config import ConfigError, load_config
from .repl import run_repl
from .store.session_store import SessionStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal Python 3.9 coding assistant.")
    parser.add_argument("--config", help="Path to config.json")
    parser.add_argument("--session", help="Resume an existing session ID")
    parser.add_argument("--prompt", help="Send one prompt and exit")
    parser.add_argument("--stream", action="store_true", help="Stream backend output")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(config_path=args.config, base_dir=str(Path.cwd()))
    except (ConfigError, ValueError, OSError) as exc:
        parser.exit(status=2, message="Config error: {0}\n".format(exc))

    session_store = SessionStore(config.sessions_dir)
    runtime = AgentRuntime(config=config, session_store=session_store)

    if args.session:
        runtime.use_session(args.session)

    if args.prompt:
        text = runtime.ask(args.prompt, stream=args.stream)
        if not args.stream:
            print(text)
        return 0

    return run_repl(runtime, stream=args.stream)
