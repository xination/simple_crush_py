import re
from typing import Any, Dict, List, Optional

from .trace_runtime_support_common import (
    _argument_fact_score,
    _contains_variable_token,
    _dedupe_preserve_order,
    _fact_entry,
    _fact_location,
    _innermost_symbol_name_for_line,
    _innermost_symbol_qualname_for_line,
    _iter_numbered_code_lines,
    _looks_like_argument_passing,
    _looks_like_assignment_to_variable,
    _looks_like_path_derivation,
    _looks_like_storage_site,
    _normalize_trace_output,
    _normalized_uncertainty_notes,
    _outline_symbols_from_payloads,
    _prefer_argument_fact,
    _render_unknown_section,
    _section_label_separator,
    _trace_confidence,
    _trace_status,
)
def _normalize_variable_trace_output(
    text: str,
    facts: Dict[str, Any],
    coverage: str,
    extra_uncertainty_notes: Optional[List[str]] = None,
) -> str:
    lines = text.splitlines()
    variable_name = ""
    confirmed_file = ""
    model_uncertainty: List[str] = []
    collecting_uncertainty = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Variable:"):
            variable_name = stripped.partition(":")[2].strip()
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

    tracked_name = variable_name or str(facts.get("variable_name", "")).strip()
    section_lines = [
        "Variable trace for human review:",
        "",
        "Variable: {0}".format(tracked_name or "<unknown>"),
        "Confirmed file: {0}".format(confirmed_file or "<unknown>"),
        "Trace status: {0}".format(_trace_status(coverage)),
        "Confidence: {0}".format(_trace_confidence(coverage)),
    ]
    section_lines.extend(_render_variable_coverage_lines(coverage, facts))
    section_lines.append("")

    definition = facts.get("definition")
    reassignment = facts.get("reassignment")
    argument = facts.get("argument")
    role = _best_confirmed_role(facts)

    section_lines.extend(_render_variable_trace_section(1, "Defined at", definition, "No confirmed definition in the reviewed block."))
    section_lines.append("")
    section_lines.extend(_render_variable_trace_section(2, "Reassigned at", reassignment, "No confirmed reassignment in the reviewed block."))
    section_lines.append("")
    section_lines.extend(_render_variable_trace_section(3, "Passed to", argument, "No confirmed argument-passing site was found in the reviewed block."))
    section_lines.append("")
    section_lines.extend(_render_variable_trace_role_section(4, role))
    section_lines.append("")
    section_lines.extend(
        _render_unknown_section(
            5,
            _useful_model_uncertainty_notes(model_uncertainty),
            extra_uncertainty_notes or [],
        )
    )

    return "\n".join(section_lines).strip()

def _collect_variable_trace_facts(payloads: List[Dict[str, Any]], variable_name: str, workspace_root: Any) -> Dict[str, Any]:
    facts: Dict[str, Any] = {
        "variable_name": variable_name,
        "definition": None,
        "reassignment": None,
        "argument": None,
        "storage": None,
        "path": None,
        "returned": None,
        "condition": None,
        "function_name": "",
    }
    seen_assignment = False
    outline_symbols = _outline_symbols_from_payloads(payloads, workspace_root)

    for payload in payloads:
        if payload.get("tool_name") != "cat":
            continue
        content = str(payload.get("content", ""))
        for line_number, code in _iter_numbered_code_lines(content):
            stripped = code.strip()
            if not stripped:
                continue
            current_function = _innermost_symbol_name_for_line(outline_symbols, line_number)
            current_qualname = _innermost_symbol_qualname_for_line(outline_symbols, line_number)
            if not _contains_variable_token(code, variable_name):
                continue
            if _looks_like_assignment_to_variable(code, variable_name):
                key = "definition" if not seen_assignment else "reassignment"
                seen_assignment = True
                if facts.get(key) is None:
                    facts[key] = _fact_entry(line_number, code, current_function, qualname=current_qualname)
                continue
            if facts["storage"] is None and _looks_like_storage_site(code, variable_name):
                facts["storage"] = _fact_entry(
                    line_number,
                    code,
                    current_function,
                    role_label="Stored into field",
                    qualname=current_qualname,
                )
            if _looks_like_argument_passing(code, variable_name):
                candidate_argument = _fact_entry(line_number, code, current_function, qualname=current_qualname)
                if _prefer_argument_fact(candidate_argument, facts.get("argument"), variable_name):
                    facts["argument"] = candidate_argument
            if facts["path"] is None and _looks_like_path_derivation(code, variable_name):
                facts["path"] = _fact_entry(
                    line_number,
                    code,
                    current_function,
                    role_label="Used to derive path",
                    qualname=current_qualname,
                )
            if facts["returned"] is None and re.search(r"\breturn\b", stripped) and _contains_variable_token(code, variable_name):
                facts["returned"] = _fact_entry(
                    line_number,
                    code,
                    current_function,
                    role_label="Returned directly",
                    qualname=current_qualname,
                )
            if facts["condition"] is None and re.search(r"^\s*(if|elif|while|assert)\b", code):
                facts["condition"] = _fact_entry(
                    line_number,
                    code,
                    current_function,
                    role_label="Used in condition",
                    qualname=current_qualname,
                )
    facts["function_name"] = _preferred_context_name(facts)
    return facts

def _variable_trace_coverage_text(coverage: str, facts: Dict[str, Any]) -> str:
    if coverage == "local":
        function_name = str(facts.get("function_name", "")).strip()
        if function_name:
            return "local (reviewed `{0}` block only)".format(function_name)
        return "local"
    if coverage:
        return coverage
    return ""

def _render_variable_coverage_lines(coverage: str, facts: Dict[str, Any]) -> List[str]:
    scope = _variable_trace_coverage_text(coverage, facts) or "local"
    return [
        "Coverage:",
        "- scope: {0}".format(scope),
        "- selection: grep-confirmed local windows",
        "- full file: {0}".format("yes" if coverage == "complete" else "no"),
        "- cross file: no",
    ]

def _render_variable_trace_section(
    number: int,
    label: str,
    fact: Optional[Dict[str, Any]],
    fallback_text: str,
) -> List[str]:
    if fact:
        location = _fact_location(fact)
        return [
            "{0}. {1}{2}{3}".format(number, label, _section_label_separator(label), location),
            "   Evidence: {0}".format(fact["evidence"]),
        ]
    return [
        "{0}. {1}".format(number, label),
        "   {0}".format(fallback_text),
    ]

def _render_variable_trace_role_section(number: int, fact: Optional[Dict[str, Any]]) -> List[str]:
    if fact:
        role_label = str(fact.get("role_label", "")).strip() or "Other confirmed local role"
        return [
            "{0}. Used in".format(number),
            "   Role: {0} at {1}".format(role_label, _fact_location(fact)),
            "   Evidence: {0}".format(fact["evidence"]),
        ]
    return [
        "{0}. Used in".format(number),
        "   No additional confirmed local role was found in the reviewed block.",
    ]

def _best_confirmed_role(facts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for key in ("storage", "path", "returned", "condition"):
        fact = facts.get(key)
        if fact:
            return fact
    return None

def _confirmed_local_flow_lines(facts: Dict[str, Any], variable_name: str) -> List[str]:
    lines: List[str] = []
    if facts.get("definition"):
        lines.append("`{0}` is created or first assigned in the reviewed block.".format(variable_name))
    storage = facts.get("storage")
    if storage:
        lines.append("It is stored into a field or constructor argument in the reviewed block.")
    argument = facts.get("argument")
    if argument:
        if facts.get("path"):
            lines.append("It is passed into a helper call to derive a path or directory.")
        else:
            lines.append("It is passed as an argument to another call in the reviewed block.")
    elif facts.get("path"):
        lines.append("It is used to derive a local path or directory value.")
    returned = facts.get("returned")
    if returned:
        lines.append("It contributes directly to a returned value in the reviewed block.")
    condition = facts.get("condition")
    if condition:
        lines.append("It participates in a local condition check.")
    return lines

def _useful_model_uncertainty_notes(notes: List[str]) -> List[str]:
    useful: List[str] = []
    for note in notes:
        cleaned = note.strip()
        if not cleaned:
            continue
        lowered = cleaned.lower().rstrip(".")
        if lowered in {
            "none",
            "the exact usage of session_id is not fully clear from the provided code snippet",
            "the exact role of the session_id is not fully clear beyond its initial assignment and use within the sessionmeta object",
        }:
            continue
        useful.append(cleaned)
    return useful

def _preferred_context_name(facts: Dict[str, Any]) -> str:
    for key in ("definition", "argument", "storage", "path", "returned", "condition", "reassignment"):
        fact = facts.get(key)
        if not fact:
            continue
        function_name = str(fact.get("qualname", "") or fact.get("function_name", "")).strip()
        if function_name:
            return function_name
    return str(facts.get("function_name", "")).strip()
