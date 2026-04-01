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


def index_aggregate_cases(payload):
    indexed = {}
    aggregate = payload.get("aggregate", {})
    for item in aggregate.get("cases", []):
        indexed[item["id"]] = item
    return indexed


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
        left_used_cat = bool(left_analysis.get("used_cat", left_analysis.get("used_view")))
        right_used_cat = bool(right_analysis.get("used_cat", right_analysis.get("used_view")))
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
        if left_used_cat != right_used_cat:
            summary["changed_used_view"].append(
                {
                    "id": case_id,
                    "baseline": left_used_cat,
                    "candidate": right_used_cat,
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
        if left_used_cat and not right_used_cat:
            reasons.append("candidate lost `cat`")
        if (not left_used_cat) and right_used_cat:
            reasons.append("candidate gained `cat`")
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


def compare_aggregate_sets(baseline, candidate):
    case_ids = sorted(set(baseline.keys()) | set(candidate.keys()))
    summary = {
        "first_tool_mode_changes": [],
        "used_view_rate_deltas": [],
        "error_rate_deltas": [],
        "avg_tool_call_deltas": [],
        "stability_changes": [],
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

        left_mode = left.get("first_tool_mode", "<none>")
        right_mode = right.get("first_tool_mode", "<none>")
        left_view_rate = float(left.get("used_view_rate", 0.0))
        right_view_rate = float(right.get("used_view_rate", 0.0))
        left_error_rate = float(left.get("error_rate", 0.0))
        right_error_rate = float(right.get("error_rate", 0.0))
        left_avg_tool_calls = float(left.get("avg_tool_call_count", 0.0))
        right_avg_tool_calls = float(right.get("avg_tool_call_count", 0.0))
        left_sequence_variants = int(left.get("tool_sequence_variant_count", 0))
        right_sequence_variants = int(right.get("tool_sequence_variant_count", 0))
        left_answer_variants = int(left.get("answer_variant_count", 0))
        right_answer_variants = int(right.get("answer_variant_count", 0))

        if left_mode != right_mode:
            summary["first_tool_mode_changes"].append(
                {
                    "id": case_id,
                    "baseline": left_mode,
                    "candidate": right_mode,
                }
            )

        view_rate_delta = right_view_rate - left_view_rate
        if view_rate_delta != 0.0:
            summary["used_view_rate_deltas"].append(
                {
                    "id": case_id,
                    "baseline": left_view_rate,
                    "candidate": right_view_rate,
                    "delta": view_rate_delta,
                }
            )

        error_rate_delta = right_error_rate - left_error_rate
        if error_rate_delta != 0.0:
            summary["error_rate_deltas"].append(
                {
                    "id": case_id,
                    "baseline": left_error_rate,
                    "candidate": right_error_rate,
                    "delta": error_rate_delta,
                }
            )

        avg_tool_delta = right_avg_tool_calls - left_avg_tool_calls
        if avg_tool_delta != 0.0:
            summary["avg_tool_call_deltas"].append(
                {
                    "id": case_id,
                    "baseline": left_avg_tool_calls,
                    "candidate": right_avg_tool_calls,
                    "delta": avg_tool_delta,
                }
            )

        if left_sequence_variants != right_sequence_variants or left_answer_variants != right_answer_variants:
            summary["stability_changes"].append(
                {
                    "id": case_id,
                    "baseline": {
                        "tool_sequence_variant_count": left_sequence_variants,
                        "answer_variant_count": left_answer_variants,
                    },
                    "candidate": {
                        "tool_sequence_variant_count": right_sequence_variants,
                        "answer_variant_count": right_answer_variants,
                    },
                }
            )

        reasons = []
        if right_error_rate > left_error_rate:
            reasons.append("candidate error rate increased")
        if right_view_rate < left_view_rate:
            reasons.append("candidate used `cat` less often")
        if left_mode != right_mode:
            reasons.append("first tool mode changed")
        if avg_tool_delta >= 1.0:
            reasons.append("average tool calls increased by >= 1")
        if right_sequence_variants > left_sequence_variants:
            reasons.append("tool routing became less stable")
        if right_answer_variants > left_answer_variants:
            reasons.append("final answers became less stable")
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
    baseline_aggregate = index_aggregate_cases(baseline_payload)
    candidate_aggregate = index_aggregate_cases(candidate_payload)
    if baseline_aggregate and candidate_aggregate:
        comparison_mode = "aggregate"
        comparison = compare_aggregate_sets(baseline_aggregate, candidate_aggregate)
    else:
        comparison_mode = "single_run"
        comparison = compare_result_sets(baseline_results, candidate_results)
    output = {
        "baseline": {
            "path": str(Path(args.baseline)),
            "generated_at": baseline_payload.get("generated_at", ""),
            "backend": baseline_payload.get("backend", ""),
            "case_count": baseline_payload.get("case_count", 0),
            "run_count": baseline_payload.get("completed_runs", 1),
        },
        "candidate": {
            "path": str(Path(args.candidate)),
            "generated_at": candidate_payload.get("generated_at", ""),
            "backend": candidate_payload.get("backend", ""),
            "case_count": candidate_payload.get("case_count", 0),
            "run_count": candidate_payload.get("completed_runs", 1),
        },
        "mode": comparison_mode,
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
