#!/usr/bin/env python3

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crush_py.agent.runtime import AgentRuntime
from crush_py.benchmark import (
    DEFAULT_CASES_PATH,
    load_benchmark_cases,
    run_benchmark_cases,
    save_benchmark_results,
)
from crush_py.config import load_config
from crush_py.store.session_store import SessionStore


def build_parser():
    parser = argparse.ArgumentParser(description="Run small-model benchmark prompts against crush_py.")
    parser.add_argument("--config", help="Path to config.json")
    parser.add_argument("--backend", help="Override backend name")
    parser.add_argument(
        "--cases",
        default=str(DEFAULT_CASES_PATH),
        help="Path to benchmark case JSON",
    )
    parser.add_argument(
        "--output",
        help="Path to save result JSON. Default: benchmark/results/<timestamp>.json",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Run only the specified benchmark case id. Repeatable.",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    config = load_config(config_path=args.config, base_dir=str(Path.cwd()))
    session_store = SessionStore(config.sessions_dir)
    runtime = AgentRuntime(config=config, session_store=session_store)
    cases = load_benchmark_cases(Path(args.cases))
    if args.case_ids:
        wanted = set(args.case_ids)
        cases = [item for item in cases if item["id"] in wanted]
        if not cases:
            parser.exit(status=2, message="No benchmark cases matched --case-id.\n")
    results = run_benchmark_cases(runtime, cases, backend_name=args.backend)

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    output_path = Path(args.output) if args.output else Path("benchmark") / "results" / "{0}.json".format(timestamp)
    payload = {
        "generated_at": timestamp,
        "workspace": str(Path.cwd()),
        "backend": args.backend or config.default_backend,
        "case_count": len(results),
        "results": results,
    }
    save_benchmark_results(output_path, payload)

    summary = {
        "output": str(output_path),
        "case_count": len(results),
        "used_view_count": sum(1 for item in results if item["analysis"]["used_view"]),
        "first_tool_counts": {},
    }
    for item in results:
        first_tool = item["analysis"]["first_tool"] or "<none>"
        summary["first_tool_counts"][first_tool] = summary["first_tool_counts"].get(first_tool, 0) + 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
