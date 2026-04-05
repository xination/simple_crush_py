from typing import Any, Dict, List

from ..backends.base import BackendError
from ..output_sanitize import sanitize_text
from .runtime_prompts import BASE_READ_HELPER_SYSTEM_PROMPT, READER_APPENDIX
from .trace_runtime_support import (
    _notes_to_uncertainty_items,
    _normalize_trace_output,
    _single_line,
    _trace_confidence,
    _trace_status,
)


def run_direct_file_file_flow_reader(runtime, session_id: str, backend, prompt: str, rel_path: str, stream: bool = False) -> str:
    payloads: List[Dict[str, Any]] = []
    notes: List[str] = []
    try:
        outline_result = runtime._record_reader_tool(session_id, "get_outline", {"path": rel_path})
        payloads.append(
            {"type": "tool_result", "tool_use_id": "reader-outline:{0}".format(rel_path), "tool_name": "get_outline", "content": outline_result}
        )
    except Exception as exc:
        notes.append("outline unavailable: {0}".format(exc))

    cat_payloads, coverage = runtime._collect_summary_file_reads(session_id, rel_path)
    payloads.extend(cat_payloads)
    compact_payloads = runtime._compact_reader_cat_payloads(payloads)
    conversation = [
        {
            "role": "user",
            "content": ("User request: {0}\nTarget file: {1}\nCoverage: {2}\n{3}").format(
                prompt.strip(), rel_path, coverage, runtime._direct_file_file_flow_reader_instructions()
            ),
        },
        {"role": "user", "content": compact_payloads},
    ]
    try:
        model_text = runtime._generate_text_with_optional_streaming(
            backend, BASE_READ_HELPER_SYSTEM_PROMPT + READER_APPENDIX, conversation, stream=stream
        )
    except BackendError as exc:
        model_text = ""
        notes.append("backend fallback: {0}".format(exc))
    final_text = runtime._normalize_direct_file_file_flow_output(model_text, rel_path, coverage, payloads, notes)

    state = runtime._state_for_session(session_id)
    state.file_summaries[rel_path] = _single_line(final_text, 240)
    if rel_path and rel_path not in state.confirmed_paths:
        state.confirmed_paths.append(rel_path)
    runtime.session_store.append_message(
        session_id,
        "assistant",
        final_text,
        kind="tool_result",
        metadata={
            "agent": "reader",
            "tool_name": "reader",
            "tool_arguments": {
                "path": rel_path,
                "coverage": coverage,
                "trace_status": _trace_status(coverage),
                "confidence": _trace_confidence(coverage),
                "mode": "file_flow_trace",
            },
            "tool_use_id": "reader:{0}".format(rel_path),
            "summary": final_text,
        },
    )
    return final_text


def direct_file_file_flow_reader_instructions() -> str:
    return (
        "Stay inside the named file.\n"
        "Treat this as a file-level flow walkthrough.\n"
        "Describe how the main functions connect, what each stage consumes, and what the caller gets back.\n"
        "Do not narrate your thinking or hidden reasoning.\n"
        "Return this format:\n"
        "File flow for human review:\n\n"
        "File: <path>\n"
        "Trace status: <complete / partial>\n"
        "Confidence: <file-only / local-only>\n\n"
        "1. Main entry points\n"
        "   Evidence: <function or method names>\n\n"
        "2. Core flow chain\n"
        "   Evidence: <how one function hands off to another>\n\n"
        "3. Supporting helpers\n"
        "   Evidence: <helper names or `No confirmed helper`>\n\n"
        "4. Caller-visible output\n"
        "   Evidence: <return shape or final rendered form>\n\n"
        "Unresolved uncertainty:\n"
        "- <note>"
    )


def normalize_direct_file_file_flow_output(runtime, text: str, rel_path: str, coverage: str, payloads: List[Dict[str, Any]], notes: List[str]) -> str:
    final_text = sanitize_text(text).strip()
    extra_uncertainty_notes = _notes_to_uncertainty_items("; ".join(notes)) if notes else []
    if final_text.startswith("File flow for human review:"):
        return _normalize_trace_output(final_text, extra_uncertainty_notes)
    return fallback_direct_file_file_flow_output(rel_path, coverage, payloads, extra_uncertainty_notes)


def fallback_direct_file_file_flow_output(rel_path: str, coverage: str, payloads: List[Dict[str, Any]], extra_uncertainty_notes: List[str]) -> str:
    cat_lines = cat_code_lines_from_payloads(payloads)
    outline_names = merged_callable_names(outline_names_from_payloads(payloads), callable_names_from_code_lines(cat_lines))
    entry_points = outline_names[:2]
    helper_names = outline_names[2:]
    output_line = first_output_line(cat_lines)

    if entry_points and len(entry_points) > 1:
        flow_chain = "Reviewed callables include `{0}` and `{1}`, but the exact handoff chain was not fully proven from the fallback evidence.".format(entry_points[0], entry_points[1])
    elif entry_points:
        flow_chain = "`{0}` is the main reviewed entry point in this file.".format(entry_points[0])
    else:
        flow_chain = "No confirmed function chain was recovered from the reviewed outline."

    helper_text = ", ".join("`{0}`".format(name) for name in helper_names[:4]) if helper_names else "No confirmed helper"
    output_text = output_line or "No confirmed return shape was recovered from the reviewed file content."

    body = [
        "File flow for human review:",
        "",
        "File: {0}".format(rel_path),
        "Trace status: {0}".format(_trace_status(coverage)),
        "Confidence: {0}".format(_trace_confidence(coverage)),
        "",
        "1. Main entry points",
        "   Evidence: {0}".format(", ".join("`{0}`".format(name) for name in entry_points) if entry_points else "No confirmed top-level entry point"),
        "",
        "2. Core flow chain",
        "   Evidence: {0}".format(flow_chain),
        "",
        "3. Supporting helpers",
        "   Evidence: {0}".format(helper_text),
        "",
        "4. Caller-visible output",
        "   Evidence: {0}".format(output_text),
    ]
    return _normalize_trace_output("\n".join(body), extra_uncertainty_notes)


def outline_names_from_payloads(payloads: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for payload in payloads:
        if payload.get("tool_name") != "get_outline":
            continue
        for item in str(payload.get("content", "")).split(" ; "):
            if "|" not in item:
                continue
            _, _, label = item.partition("|")
            label = label.strip()
            if "(" in label:
                label = label.split("(", 1)[0].strip()
            if label.startswith("def "):
                names.append(label[4:].strip())
            elif label.startswith("class "):
                names.append(label[6:].strip())
    deduped: List[str] = []
    for name in names:
        if name and name not in deduped:
            deduped.append(name)
    return deduped


def cat_code_lines_from_payloads(payloads: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for payload in payloads:
        if payload.get("tool_name") != "cat":
            continue
        for raw_line in str(payload.get("content", "")).splitlines():
            if "|" not in raw_line:
                continue
            _, _, code = raw_line.partition("|")
            stripped = code.strip()
            if stripped:
                lines.append(stripped)
    return lines


def first_output_line(cat_lines: List[str]) -> str:
    for code in cat_lines:
        if code.startswith("return ") and ("join(" in code or "format(" in code or "_single_line" in code):
            return "`{0}`".format(code)
    for code in cat_lines:
        if code.startswith("return "):
            return "`{0}`".format(code)
    return ""


def callable_names_from_code_lines(cat_lines: List[str]) -> List[str]:
    names: List[str] = []
    for code in cat_lines:
        if code.startswith("def "):
            names.append(code[4:].split("(", 1)[0].strip())
        elif code.startswith("class "):
            names.append(code[6:].split("(", 1)[0].split(":", 1)[0].strip())
    deduped: List[str] = []
    for name in names:
        if name and name not in deduped:
            deduped.append(name)
    return deduped


def merged_callable_names(primary: List[str], secondary: List[str]) -> List[str]:
    merged: List[str] = []
    for name in list(primary) + list(secondary):
        if name and name not in merged:
            merged.append(name)
    return merged
