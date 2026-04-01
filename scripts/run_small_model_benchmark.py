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
    aggregate_run_results,
    build_run_summary,
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
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="How many times to run the full benchmark set. Default: 1",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.runs <= 0:
        parser.exit(status=2, message="--runs must be >= 1.\n")

    config = load_config(config_path=args.config, base_dir=str(Path.cwd()))
    session_store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
    runtime = AgentRuntime(config=config, session_store=session_store)
    cases = load_benchmark_cases(Path(args.cases))
    if args.case_ids:
        wanted = set(args.case_ids)
        cases = [item for item in cases if item["id"] in wanted]
        if not cases:
            parser.exit(status=2, message="No benchmark cases matched --case-id.\n")

    runs = []
    for run_index in range(1, args.runs + 1):
        results = run_benchmark_cases(runtime, cases, backend_name=args.backend)
        runs.append(
            {
                "run_index": run_index,
                "results": results,
                "summary": build_run_summary(results),
            }
        )

    latest_results = runs[-1]["results"] if runs else []
    aggregate = aggregate_run_results(runs)

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    output_path = Path(args.output) if args.output else Path("benchmark") / "results" / "{0}.json".format(timestamp)
    payload = {
        "generated_at": timestamp,
        "workspace": str(Path.cwd()),
        "backend": args.backend or config.default_backend,
        "case_count": len(cases),
        "requested_runs": args.runs,
        "completed_runs": len(runs),
        "results": latest_results,
        "runs": runs,
        "aggregate": aggregate,
    }
    save_benchmark_results(output_path, payload)

    summary = {
        "output": str(output_path),
        "run_count": len(runs),
        "case_count": len(cases),
        "latest_run": runs[-1]["summary"] if runs else {},
        "aggregate_overall": aggregate["overall"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
