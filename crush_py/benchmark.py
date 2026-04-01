import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agent.runtime import AgentRuntime


DEFAULT_CASES_PATH = Path("benchmark") / "small_model_cases.json"


def load_benchmark_cases(path: Path) -> List[Dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("`cases` must be a list.")
    normalized = []
    for item in cases:
        if not isinstance(item, dict):
            raise ValueError("Each benchmark case must be an object.")
        case_id = str(item.get("id", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        if not case_id or not prompt:
            raise ValueError("Each benchmark case requires non-empty `id` and `prompt`.")
        normalized.append(item)
    return normalized


def analyze_session_messages(messages: List[Any]) -> Dict[str, Any]:
    tool_sequence = []
    assistant_final = ""
    used_cat = False
    first_cat_index = None
    locator_tool_count = 0
    for message in messages:
        if message.kind == "tool_use":
            raw_content = message.metadata.get("raw_content", [])
            for block in raw_content:
                if block.get("type") != "tool_use":
                    continue
                tool_name = block.get("name", "")
                tool_sequence.append(tool_name)
                if tool_name in ("ls", "tree", "find", "grep"):
                    locator_tool_count += 1
                if tool_name == "cat" and first_cat_index is None:
                    first_cat_index = len(tool_sequence) - 1
                    used_cat = True
        elif message.kind == "message" and message.role == "assistant":
            assistant_final = str(message.content)

    return {
        "tool_sequence": tool_sequence,
        "first_tool": tool_sequence[0] if tool_sequence else "",
        "tool_call_count": len(tool_sequence),
        "locator_tool_count": locator_tool_count,
        "used_cat": used_cat,
        "first_cat_tool_index": first_cat_index,
        "cat_before_final": used_cat,
        "assistant_final": assistant_final,
    }


def run_benchmark_cases(
    runtime: AgentRuntime,
    cases: List[Dict[str, Any]],
    backend_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    results = []
    for case in cases:
        session = runtime.new_session(
            backend_name=backend_name,
            title="benchmark:{0}".format(case["id"]),
        )
        error_text = ""
        answer = ""
        try:
            answer = runtime.ask(case["prompt"])
        except Exception as exc:
            error_text = "{0}: {1}".format(type(exc).__name__, exc)
        messages = runtime.session_store.load_messages(session.id)
        analysis = analyze_session_messages(messages)
        results.append(
            {
                "id": case["id"],
                "prompt": case["prompt"],
                "tags": list(case.get("tags", [])),
                "expected_flow": case.get("expected_flow", []),
                "notes": case.get("notes", ""),
                "session_id": session.id,
                "backend": runtime.active_session.backend if runtime.active_session else "",
                "model": runtime.active_session.model if runtime.active_session else "",
                "answer": answer,
                "error": error_text,
                "analysis": analysis,
            }
        )
    return results


def build_run_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {
        "case_count": len(results),
        "error_count": 0,
        "used_view_count": 0,
        "first_tool_counts": {},
    }
    first_tool_counts = Counter()
    for item in results:
        if item.get("error"):
            summary["error_count"] += 1
        analysis = item.get("analysis", {})
        if analysis.get("used_cat"):
            summary["used_view_count"] += 1
        first_tool = analysis.get("first_tool", "") or "<none>"
        first_tool_counts[first_tool] += 1
    summary["first_tool_counts"] = dict(first_tool_counts)
    return summary


def aggregate_run_results(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    case_runs = {}
    for run in runs:
        for item in run.get("results", []):
            case_runs.setdefault(item["id"], []).append(item)

    aggregate_cases = []
    overall = {
        "run_count": len(runs),
        "case_count": len(case_runs),
        "total_case_executions": 0,
        "success_count": 0,
        "error_count": 0,
        "error_rate": 0.0,
        "used_view_count": 0,
        "used_view_rate": 0.0,
        "avg_tool_call_count": 0.0,
        "avg_locator_tool_count": 0.0,
    }
    total_tool_calls = 0
    total_locator_tool_calls = 0

    for case_id in sorted(case_runs.keys()):
        items = case_runs[case_id]
        run_count = len(items)
        success_count = 0
        error_count = 0
        used_view_count = 0
        first_tool_counts = Counter()
        tool_call_total = 0
        locator_tool_total = 0
        answers = Counter()
        tool_sequences = Counter()

        for item in items:
            analysis = item.get("analysis", {})
            error_text = (item.get("error") or "").strip()
            if error_text:
                error_count += 1
            else:
                success_count += 1
            if analysis.get("used_cat"):
                used_view_count += 1
            first_tool = analysis.get("first_tool", "") or "<none>"
            first_tool_counts[first_tool] += 1
            tool_call_total += int(analysis.get("tool_call_count", 0))
            locator_tool_total += int(analysis.get("locator_tool_count", 0))
            answers[(item.get("answer") or "").strip()] += 1
            tool_sequences[tuple(analysis.get("tool_sequence", []))] += 1

        case_payload = {
            "id": case_id,
            "run_count": run_count,
            "success_count": success_count,
            "error_count": error_count,
            "error_rate": float(error_count) / float(run_count) if run_count else 0.0,
            "used_view_count": used_view_count,
            "used_view_rate": float(used_view_count) / float(run_count) if run_count else 0.0,
            "first_tool_counts": dict(first_tool_counts),
            "first_tool_mode": first_tool_counts.most_common(1)[0][0] if first_tool_counts else "<none>",
            "avg_tool_call_count": float(tool_call_total) / float(run_count) if run_count else 0.0,
            "avg_locator_tool_count": float(locator_tool_total) / float(run_count) if run_count else 0.0,
            "answer_variant_count": len(answers),
            "tool_sequence_variant_count": len(tool_sequences),
        }
        aggregate_cases.append(case_payload)

        overall["total_case_executions"] += run_count
        overall["success_count"] += success_count
        overall["error_count"] += error_count
        overall["used_view_count"] += used_view_count
        total_tool_calls += tool_call_total
        total_locator_tool_calls += locator_tool_total

    total_executions = overall["total_case_executions"]
    if total_executions:
        overall["error_rate"] = float(overall["error_count"]) / float(total_executions)
        overall["used_view_rate"] = float(overall["used_view_count"]) / float(total_executions)
        overall["avg_tool_call_count"] = float(total_tool_calls) / float(total_executions)
        overall["avg_locator_tool_count"] = float(total_locator_tool_calls) / float(total_executions)

    return {
        "overall": overall,
        "cases": aggregate_cases,
    }


def save_benchmark_results(path: Path, payload: Dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
