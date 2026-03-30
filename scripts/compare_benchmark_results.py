#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path


def build_parser():
    parser = argparse.ArgumentParser(description="Compare two small-model benchmark result JSON files.")
    parser.add_argument("baseline", help="Baseline benchmark result JSON")
    parser.add_argument("candidate", help="Candidate benchmark result JSON")
    parser.add_argument("--output", help="Optional path to save comparison JSON")
    return parser


def load_results(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    results = payload.get("results", [])
    indexed = {}
    for item in results:
        indexed[item["id"]] = item
    return payload, indexed


def compare_result_sets(baseline, candidate):
    case_ids = sorted(set(baseline.keys()) | set(candidate.keys()))
    summary = {
        "changed_first_tool": [],
        "changed_used_view": [],
        "tool_call_deltas": [],
        "needs_manual_review": [],
        "missing_in_baseline": [],
        "missing_in_candidate": [],
    }

    for case_id in case_ids:
        left = baseline.get(case_id)
        right = candidate.get(case_id)
        if left is None:
            summary["missing_in_baseline"].append(case_id)
            continue
        if right is None:
            summary["missing_in_candidate"].append(case_id)
            continue

        left_analysis = left.get("analysis", {})
        right_analysis = right.get("analysis", {})
        left_error = (left.get("error") or "").strip()
        right_error = (right.get("error") or "").strip()
        left_first_tool = left_analysis.get("first_tool", "")
        right_first_tool = right_analysis.get("first_tool", "")
        left_used_view = bool(left_analysis.get("used_view"))
        right_used_view = bool(right_analysis.get("used_view"))
        left_tool_calls = int(left_analysis.get("tool_call_count", 0))
        right_tool_calls = int(right_analysis.get("tool_call_count", 0))
        delta = right_tool_calls - left_tool_calls

        if left_first_tool != right_first_tool:
            summary["changed_first_tool"].append(
                {
                    "id": case_id,
                    "baseline": left_first_tool or "<none>",
                    "candidate": right_first_tool or "<none>",
                }
            )
        if left_used_view != right_used_view:
            summary["changed_used_view"].append(
                {
                    "id": case_id,
                    "baseline": left_used_view,
                    "candidate": right_used_view,
                }
            )
        if delta != 0:
            summary["tool_call_deltas"].append(
                {
                    "id": case_id,
                    "baseline": left_tool_calls,
                    "candidate": right_tool_calls,
                    "delta": delta,
                }
            )
        if left_error != right_error:
            summary["needs_manual_review"].append(
                {
                    "id": case_id,
                    "reasons": ["error status changed"],
                }
            )
            continue

        reasons = []
        if left_used_view and not right_used_view:
            reasons.append("candidate lost `view`")
        if (not left_used_view) and right_used_view:
            reasons.append("candidate gained `view`")
        if left_first_tool != right_first_tool:
            reasons.append("first tool changed")
        if abs(delta) >= 2:
            reasons.append("tool call count changed by >= 2")
        if left.get("answer", "").strip() != right.get("answer", "").strip():
            reasons.append("final answer changed")
        if reasons:
            summary["needs_manual_review"].append(
                {
                    "id": case_id,
                    "reasons": reasons,
                }
            )
    return summary


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    baseline_payload, baseline_results = load_results(args.baseline)
    candidate_payload, candidate_results = load_results(args.candidate)
    comparison = compare_result_sets(baseline_results, candidate_results)
    output = {
        "baseline": {
            "path": str(Path(args.baseline)),
            "generated_at": baseline_payload.get("generated_at", ""),
            "backend": baseline_payload.get("backend", ""),
            "case_count": baseline_payload.get("case_count", 0),
        },
        "candidate": {
            "path": str(Path(args.candidate)),
            "generated_at": candidate_payload.get("generated_at", ""),
            "backend": candidate_payload.get("backend", ""),
            "case_count": candidate_payload.get("case_count", 0),
        },
        "comparison": comparison,
    }

    text = json.dumps(output, ensure_ascii=False, indent=2)
    print(text)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
