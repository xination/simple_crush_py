import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from .agent.runtime import AgentRuntime
from .config import ConfigError, load_config
from .repl import run_repl
from .store.session_store import SessionStore


def configure_utf8_stdio() -> None:
    if os.name != "nt":
        return
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-focused repository helper for small local models.",
        epilog=(
            "When to use these modes:\n"
            "  --file PATH        Quick mode for one text file. Read it fully and answer from that file only.\n"
            "  --summarize PATH   Use when you want a short file summary in 3 concise points.\n"
            "  --trace REQUEST    Use when you want to follow how a variable, value, or flow moves through code.\n"
            "  --guide QUESTION   Use when you want beginner-friendly help from workspace docs, steps, or setup notes."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--config", help="Path to config.json")
    parser.add_argument("--session", help="Resume an existing session ID")
    parser.add_argument(
        "--file",
        help="Quick file mode. Read one workspace file fully and answer only from that file.",
    )
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--prompt", help="Send one prompt and exit")
    prompt_group.add_argument(
        "--trace",
        help="Trace code flow for a variable, symbol, or request. Use this to answer where something is passed, stored, or transformed.",
    )
    prompt_group.add_argument(
        "--guide",
        help="Ask docs-based, beginner-friendly questions. Use this for setup, checklists, troubleshooting, or plain-language explanations.",
    )
    prompt_group.add_argument(
        "--summarize",
        help="Summarize one file in a short 3-point overview. Use this when you want the file's main responsibilities, not a code trace.",
    )
    parser.add_argument("--stream", action="store_true", help="Stream backend output")
    return parser


def build_summary_prompt(path: str) -> str:
    normalized = path.strip()
    return "Give a short summary for {0}".format(normalized)


def build_trace_prompt(request: str) -> str:
    normalized = request.strip()
    lowered = normalized.lower()
    if lowered.startswith(("trace ", "where ")):
        return normalized
    return "Trace {0}".format(normalized)


def build_guide_prompt(request: str) -> str:
    normalized = request.strip()
    return (
        "Guide mode:\n"
        "User request: {0}\n"
        "Guide expectations:\n"
        "- answer from workspace docs when possible\n"
        "- explain for a beginner in plain language\n"
        "- prefer an action-oriented structure\n"
        "- include source file hints and section or line clues when available\n"
        "- be explicit about uncertainty when the docs are incomplete"
    ).format(normalized)


def prompt_from_args(args: argparse.Namespace) -> Optional[str]:
    if args.prompt:
        return args.prompt
    if args.trace:
        return build_trace_prompt(args.trace)
    if args.guide:
        return build_guide_prompt(args.guide)
    if args.summarize:
        return build_summary_prompt(args.summarize)
    return None


def launch_base_dir() -> Path:
    caller_cwd = os.environ.get("CRUSH_PY_CALLER_CWD", "").strip()
    if caller_cwd:
        return Path(caller_cwd).resolve()
    return Path.cwd()


def main(argv=None) -> int:
    configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.file and not args.prompt:
        parser.error("--file requires --prompt.")
    if args.file and any((args.trace, args.guide, args.summarize)):
        parser.error("--file only works with --prompt.")

    try:
        config = load_config(config_path=args.config, base_dir=str(launch_base_dir()))
    except (ConfigError, ValueError, OSError) as exc:
        parser.exit(status=2, message="Config error: {0}\n".format(exc))

    session_store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
    runtime = AgentRuntime(config=config, session_store=session_store)

    if args.session:
        runtime.use_session(args.session)

    if args.file:
        text = runtime.ask_quick_file(args.file, args.prompt, stream=args.stream)
        if not args.stream:
            print(text)
        return 0

    prompt = prompt_from_args(args)
    if prompt:
        text = runtime.ask(prompt, stream=args.stream)
        if not args.stream:
            print(text)
        return 0

    return run_repl(runtime, stream=args.stream)
