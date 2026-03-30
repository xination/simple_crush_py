import json
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
    used_view = False
    first_view_index = None
    locator_tool_count = 0
    for message in messages:
        if message.kind == "tool_use":
            raw_content = message.metadata.get("raw_content", [])
            for block in raw_content:
                if block.get("type") != "tool_use":
                    continue
                tool_name = block.get("name", "")
                tool_sequence.append(tool_name)
                if tool_name in ("ls", "glob", "grep"):
                    locator_tool_count += 1
                if tool_name == "view" and first_view_index is None:
                    first_view_index = len(tool_sequence) - 1
                    used_view = True
        elif message.kind == "message" and message.role == "assistant":
            assistant_final = str(message.content)

    return {
        "tool_sequence": tool_sequence,
        "first_tool": tool_sequence[0] if tool_sequence else "",
        "tool_call_count": len(tool_sequence),
        "locator_tool_count": locator_tool_count,
        "used_view": used_view,
        "first_view_tool_index": first_view_index,
        "view_before_final": used_view,
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


def save_benchmark_results(path: Path, payload: Dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
