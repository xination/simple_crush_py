import argparse
from pathlib import Path
from typing import Optional

from .agent.runtime import AgentRuntime
from .config import ConfigError, load_config
from .repl import run_repl
from .store.session_store import SessionStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-focused repository helper for small local models.")
    parser.add_argument("--config", help="Path to config.json")
    parser.add_argument("--session", help="Resume an existing session ID")
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--prompt", help="Send one prompt and exit")
    prompt_group.add_argument("--trace", help="Send one trace request and exit")
    prompt_group.add_argument("--summarize", help="Summarize one file and exit")
    prompt_group.add_argument("--summarize-brief", help="Summarize one file briefly and exit")
    parser.add_argument("--stream", action="store_true", help="Stream backend output")
    return parser


def build_summary_prompt(path: str, brief: bool = False) -> str:
    normalized = path.strip()
    if brief:
        return "Give a short summary for {0}".format(normalized)
    return "Summarize {0}".format(normalized)


def build_trace_prompt(request: str) -> str:
    normalized = request.strip()
    lowered = normalized.lower()
    if lowered.startswith(("trace ", "where ")):
        return normalized
    return "Trace {0}".format(normalized)


def prompt_from_args(args: argparse.Namespace) -> Optional[str]:
    if args.prompt:
        return args.prompt
    if args.trace:
        return build_trace_prompt(args.trace)
    if args.summarize_brief:
        return build_summary_prompt(args.summarize_brief, brief=True)
    if args.summarize:
        return build_summary_prompt(args.summarize, brief=False)
    return None


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(config_path=args.config, base_dir=str(Path.cwd()))
    except (ConfigError, ValueError, OSError) as exc:
        parser.exit(status=2, message="Config error: {0}\n".format(exc))

    session_store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
    runtime = AgentRuntime(config=config, session_store=session_store)

    if args.session:
        runtime.use_session(args.session)

    prompt = prompt_from_args(args)
    if prompt:
        text = runtime.ask(prompt, stream=args.stream)
        if not args.stream:
            print(text)
        return 0

    return run_repl(runtime, stream=args.stream)
