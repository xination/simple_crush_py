from typing import Any, Dict, List, Optional, Tuple

from ..tools.outline_providers import OutlineSymbol
from .trace_runtime_support_common import (
    _contains_variable_token,
    _dedupe_preserve_order,
    _fact_entry,
    _fact_location,
    _flow_trace_windows,
    _grep_match_line_numbers_for_path,
    _innermost_symbol_name_for_line,
    _innermost_symbol_qualname_for_line,
    _iter_numbered_code_lines,
    _outline_symbols_from_payloads,
    _render_unknown_section,
    _section_label_separator,
    _symbol_can_bound_block,
    _trace_confidence,
    _trace_status,
)
import re
def _normalize_flow_trace_output(
    text: str,
    facts: Dict[str, Any],
    coverage: str,
    extra_uncertainty_notes: Optional[List[str]] = None,
) -> str:
    lines = text.splitlines()
    target_name = ""
    confirmed_file = ""
    model_uncertainty: List[str] = []
    collecting_uncertainty = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Target:"):
            target_name = stripped.partition(":")[2].strip()
            continue
        if stripped.startswith("Confirmed file:"):
            confirmed_file = stripped.partition(":")[2].strip()
            continue
        if stripped.startswith("Unresolved uncertainty:"):
            collecting_uncertainty = True
            remainder = stripped.partition(":")[2].strip()
            if remainder:
                model_uncertainty.append(remainder)
            continue
        if collecting_uncertainty:
            if not stripped:
                continue
            if stripped.startswith("-"):
                model_uncertainty.append(stripped[1:].strip())
            else:
                model_uncertainty.append(stripped)

    tracked_name = str(facts.get("variable_name", "")).strip() or target_name
    reviewed_symbol = str(facts.get("reviewed_symbol_qualname", "")).strip()
    reviewed_span = facts.get("reviewed_span")

    section_lines = [
        "Flow trace for human review:",
        "",
        "Target: {0}".format(tracked_name or "<unknown>"),
        "Confirmed file: {0}".format(confirmed_file or "<unknown>"),
        "Trace status: {0}".format(_trace_status(coverage)),
        "Confidence: {0}".format(_trace_confidence(coverage)),
    ]
    section_lines.extend(_render_flow_coverage_lines(coverage, facts))
    if reviewed_symbol:
        section_lines.append("Reviewed symbol: {0}".format(reviewed_symbol))
    if reviewed_span:
        section_lines.append("Reviewed lines: {0}-{1}".format(reviewed_span[0], reviewed_span[1]))
    section_lines.append("")

    section_lines.extend(_render_flow_trace_section(1, "Entry point", facts.get("entry"), "No confirmed entry point in the reviewed block."))
    section_lines.append("")
    section_lines.extend(
        _render_flow_trace_section(
            2,
            "Immediate transform",
            facts.get("transformation"),
            "No confirmed local transformation in the reviewed block.",
        )
    )
    section_lines.append("")
    section_lines.extend(
        _render_flow_trace_section(
            3,
            "Stored or logged",
            _best_storage_or_persistence_fact(facts),
            "No confirmed storage or persistence call in the reviewed block.",
        )
    )
    section_lines.append("")
    section_lines.extend(
        _render_flow_trace_section(
            4,
            "Hand-off",
            facts.get("downstream"),
            "No confirmed downstream handoff in the reviewed block.",
        )
    )
    section_lines.append("")
    section_lines.extend(
        _render_unknown_section(
            5,
            _useful_flow_uncertainty_notes(model_uncertainty),
            extra_uncertainty_notes or [],
        )
    )

    return "\n".join(section_lines).strip()

def _collect_flow_trace_facts(payloads: List[Dict[str, Any]], variable_name: str, workspace_root: Any) -> Dict[str, Any]:
    outline_symbols = _outline_symbols_from_payloads(payloads, workspace_root)
    cat_lines = _cat_numbered_lines_from_payloads(payloads)
    grep_matches = _grep_lines_from_payloads(payloads)
    reviewed_symbol = _reviewed_symbol_for_lines(outline_symbols, grep_matches)
    reviewed_span = _reviewed_span(reviewed_symbol, cat_lines)

    facts: Dict[str, Any] = {
        "variable_name": variable_name,
        "reviewed_symbol_name": reviewed_symbol.name if reviewed_symbol else "",
        "reviewed_symbol_qualname": reviewed_symbol.qualname if reviewed_symbol else "",
        "reviewed_span": reviewed_span,
        "entry": None,
        "transformation": None,
        "state_update": None,
        "persistence": None,
        "downstream": None,
    }

    for line_number, code in cat_lines:
        stripped = code.strip()
        if not stripped:
            continue
        context_name = _innermost_symbol_name_for_line(outline_symbols, line_number)
        qualname = _innermost_symbol_qualname_for_line(outline_symbols, line_number)
        if facts["entry"] is None and reviewed_symbol and line_number == reviewed_symbol.start_line:
            facts["entry"] = _fact_entry(line_number, code, context_name, role_label="", qualname=qualname)
            continue
        if not _contains_variable_token(code, variable_name):
            continue
        if facts["transformation"] is None and _looks_like_flow_transformation(code, variable_name):
            facts["transformation"] = _fact_entry(line_number, code, context_name, qualname=qualname)
            continue
        if facts["state_update"] is None and _looks_like_flow_state_update(code, variable_name):
            facts["state_update"] = _fact_entry(
                line_number,
                code,
                context_name,
                role_label="Confirmed state field update",
                qualname=qualname,
            )
            continue
        if facts["persistence"] is None and _looks_like_flow_persistence(code, variable_name):
            facts["persistence"] = _fact_entry(
                line_number,
                code,
                context_name,
                role_label="Confirmed persistence call",
                qualname=qualname,
            )
            continue
        if facts["downstream"] is None and _looks_like_flow_handoff(code, variable_name):
            facts["downstream"] = _fact_entry(line_number, code, context_name, qualname=qualname)
            continue
    if facts["downstream"] is None:
        facts["downstream"] = _best_flow_handoff(cat_lines, outline_symbols, variable_name)
    return facts

def _cat_numbered_lines_from_payloads(payloads: List[Dict[str, Any]]) -> List[Tuple[int, str]]:
    items: List[Tuple[int, str]] = []
    for payload in payloads:
        if payload.get("tool_name") != "cat":
            continue
        items.extend(_iter_numbered_code_lines(str(payload.get("content", ""))))
    return items

def _grep_lines_from_payloads(payloads: List[Dict[str, Any]]) -> List[int]:
    for payload in payloads:
        if payload.get("tool_name") != "grep":
            continue
        tool_use_id = str(payload.get("tool_use_id", ""))
        parts = tool_use_id.split(":")
        rel_path = parts[1] if len(parts) > 1 else ""
        return _grep_match_line_numbers_for_path(str(payload.get("content", "")), rel_path)
    return []

def _reviewed_symbol_for_lines(outline_symbols: List[OutlineSymbol], line_numbers: List[int]) -> Optional[OutlineSymbol]:
    if not outline_symbols:
        return None
    function_like = [symbol for symbol in outline_symbols if symbol.kind in {"function", "method"}]
    candidates = function_like or [symbol for symbol in outline_symbols if _symbol_can_bound_block(symbol)]
    related = [
        symbol
        for symbol in candidates
        if any(symbol.start_line <= line <= max(symbol.start_line, symbol.end_line) for line in line_numbers)
    ]
    if not related:
        return candidates[0] if candidates else None
    related.sort(key=lambda symbol: (symbol.end_line - symbol.start_line, symbol.start_line))
    return related[0]

def _reviewed_span(reviewed_symbol: Optional[OutlineSymbol], cat_lines: List[Tuple[int, str]]) -> Optional[Tuple[int, int]]:
    if reviewed_symbol:
        return (reviewed_symbol.start_line, max(reviewed_symbol.start_line, reviewed_symbol.end_line))
    if not cat_lines:
        return None
    line_numbers = [line_number for line_number, _ in cat_lines]
    return (min(line_numbers), max(line_numbers))

def _best_flow_handoff(
    cat_lines: List[Tuple[int, str]],
    outline_symbols: List[OutlineSymbol],
    variable_name: str,
) -> Optional[Dict[str, Any]]:
    for line_number, code in cat_lines:
        if not _looks_like_flow_handoff(code, variable_name):
            continue
        context_name = _innermost_symbol_name_for_line(outline_symbols, line_number)
        qualname = _innermost_symbol_qualname_for_line(outline_symbols, line_number)
        return _fact_entry(line_number, code, context_name, qualname=qualname)
    return None

def _looks_like_flow_transformation(code: str, variable_name: str) -> bool:
    stripped = code.strip()
    if not _contains_variable_token(code, variable_name):
        return False
    if "=" not in code:
        return False
    lowered = stripped.lower()
    return any(token in lowered for token in (".strip(", ".lower(", ".upper(", ".format(", "json.dumps", "json.loads"))

def _looks_like_flow_state_update(code: str, variable_name: str) -> bool:
    if not _contains_variable_token(code, variable_name):
        return False
    if "=" in code:
        left, _, _ = code.partition("=")
        if re.search(r"(state|self|session|meta|messages?)[\.\[]", left):
            return True
    return False

def _looks_like_flow_persistence(code: str, variable_name: str) -> bool:
    if not _contains_variable_token(code, variable_name):
        return False
    lowered = code.lower()
    if re.search(r"\bappend_message\b", lowered):
        return True
    if re.search(r"(state|session|meta|messages?)\.(append|extend|update|write)", lowered):
        return True
    return bool(re.search(r"\b(write|save|persist|dump|append|store)\w*\s*\(", lowered))

def _looks_like_flow_handoff(code: str, variable_name: str) -> bool:
    if not _contains_variable_token(code, variable_name):
        return False
    stripped = code.strip()
    if stripped.startswith(("def ", "class ", "@")):
        return False
    if _looks_like_flow_state_update(code, variable_name) or _looks_like_flow_persistence(code, variable_name):
        return False
    return bool(re.search(r"[A-Za-z_][A-Za-z0-9_\.]*\s*\(", code))

def _flow_trace_coverage_text(coverage: str, facts: Dict[str, Any]) -> str:
    if coverage == "local":
        reviewed = str(facts.get("reviewed_symbol_qualname", "")).strip()
        if reviewed:
            return "local (reviewed `{0}` block only)".format(reviewed)
        return "local"
    if coverage:
        return coverage
    return ""

def _render_flow_coverage_lines(coverage: str, facts: Dict[str, Any]) -> List[str]:
    scope = _flow_trace_coverage_text(coverage, facts) or "local"
    selection = "containing symbol blocks" if facts.get("reviewed_symbol_qualname") else "grep-confirmed local windows"
    return [
        "Coverage:",
        "- scope: {0}".format(scope),
        "- selection: {0}".format(selection),
        "- full file: {0}".format("yes" if coverage == "complete" else "no"),
        "- cross file: no",
    ]

def _render_flow_trace_section(
    number: int,
    label: str,
    fact: Optional[Dict[str, Any]],
    fallback_text: str,
) -> List[str]:
    if fact:
        return [
            "{0}. {1}{2}{3}".format(number, label, _section_label_separator(label), _fact_location(fact)),
            "   Evidence: {0}".format(fact["evidence"]),
        ]
    return [
        "{0}. {1}".format(number, label),
        "   {0}".format(fallback_text),
    ]

def _confirmed_flow_steps(facts: Dict[str, Any], variable_name: str) -> List[str]:
    lines: List[str] = []
    reviewed = str(facts.get("reviewed_symbol_qualname", "")).strip()
    if reviewed:
        lines.append("Reviewed scope: `{0}`.".format(reviewed))
    if facts.get("entry"):
        lines.append("`{0}` enters the reviewed symbol at the confirmed entry point.".format(variable_name))
    if facts.get("transformation"):
        lines.append("A local transformation is confirmed in the reviewed block.")
    if facts.get("state_update"):
        lines.append("A state field update using `{0}` is confirmed in the reviewed block.".format(variable_name))
    if facts.get("persistence"):
        lines.append("A storage or persistence call using `{0}` is confirmed in the reviewed block.".format(variable_name))
    if facts.get("downstream"):
        lines.append("A downstream call or handoff using `{0}` is confirmed in the reviewed block.".format(variable_name))
    return lines

def _best_storage_or_persistence_fact(facts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return facts.get("persistence") or facts.get("state_update")

def _useful_flow_uncertainty_notes(notes: List[str]) -> List[str]:
    useful: List[str] = []
    for note in notes:
        cleaned = note.strip()
        if not cleaned:
            continue
        lowered = cleaned.lower().rstrip(".")
        if lowered in {
            "none",
            "the trace is limited to the reviewed local blocks",
            "the trace is limited to the reviewed local blocks and does not prove full-file downstream flow",
        }:
            continue
        useful.append(cleaned)
    return useful
