import re
from typing import Any, Dict, List, Optional, Tuple

from ..tools.base import ToolError
from ..tools.get_outline import load_outline_symbols
from ..tools.outline_providers import OutlineSymbol


FLOW_TRACE_MAX_BLOCK_SPAN = 120
FLOW_TRACE_MAX_WINDOWS = 4
def _single_line(text: str, max_length: int = 160) -> str:
    normalized = " ".join(str(text).strip().split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + " ..."

def _grep_scope_for_file(rel_path: str) -> Tuple[str, str]:
    normalized = rel_path.strip().replace("\\", "/")
    if "/" not in normalized:
        return ".", normalized
    parent, _, filename = normalized.rpartition("/")
    return parent or ".", filename

def _grep_match_line_numbers_for_path(result: str, rel_path: str) -> List[int]:
    matched = False
    line_numbers: List[int] = []
    for raw_line in result.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line == "{0}:".format(rel_path):
            matched = True
            continue
        if matched and not line.startswith("  "):
            break
        if not matched:
            continue
        match = re.search(r"Line\s+(\d+),\s+Char\s+\d+:", line)
        if match:
            line_numbers.append(int(match.group(1)))
    return sorted(set(line_numbers))

def _merged_line_windows(line_numbers: List[int], radius: int, max_windows: int) -> Tuple[List[Tuple[int, int]], bool]:
    if not line_numbers:
        return [], False

    windows: List[Tuple[int, int]] = []
    for line_number in sorted(set(line_numbers)):
        start = max(1, line_number - radius)
        end = line_number + radius
        if windows and start <= windows[-1][1] + 1:
            previous_start, previous_end = windows[-1]
            windows[-1] = (previous_start, max(previous_end, end))
            continue
        windows.append((start, end))

    if len(windows) <= max_windows:
        return windows, False
    return windows[:max_windows], True

def _flow_trace_windows(
    outline_symbols: List[OutlineSymbol],
    outline_result: str,
    line_numbers: List[int],
) -> Tuple[List[Tuple[int, int]], bool, bool]:
    outline_blocks = _outline_blocks_from_symbols(outline_symbols) or _outline_blocks(outline_result)
    if not outline_blocks:
        return [], False, False

    windows: List[Tuple[int, int]] = []
    for line_number in sorted(set(line_numbers)):
        block = None
        for start_line, end_line in outline_blocks:
            if start_line <= line_number <= end_line:
                block = (start_line, end_line)
                break
        if block is None:
            continue
        start_line, end_line = block
        capped_end = min(end_line, start_line + FLOW_TRACE_MAX_BLOCK_SPAN - 1)
        if windows and start_line <= windows[-1][1] + 1:
            previous_start, previous_end = windows[-1]
            windows[-1] = (previous_start, max(previous_end, capped_end))
            continue
        windows.append((start_line, capped_end))

    if not windows:
        return [], False, False
    if len(windows) <= FLOW_TRACE_MAX_WINDOWS:
        return windows, True, False
    return windows[:FLOW_TRACE_MAX_WINDOWS], True, True

def _clip_windows_to_outline_symbols(
    windows: List[Tuple[int, int]],
    matched_lines: List[int],
    outline_symbols: List[OutlineSymbol],
) -> List[Tuple[int, int]]:
    if not windows or not outline_symbols:
        return windows

    clipped: List[Tuple[int, int]] = []
    for start_line, end_line in windows:
        related_symbols = [
            symbol
            for symbol in outline_symbols
            if _symbol_can_bound_block(symbol) and any(symbol.start_line <= line <= symbol.end_line for line in matched_lines)
        ]
        if not related_symbols:
            clipped.append((start_line, end_line))
            continue
        block_start = min(symbol.start_line for symbol in related_symbols)
        block_end = max(symbol.end_line for symbol in related_symbols)
        clipped.append((max(start_line, block_start), min(end_line, block_end)))
    return clipped

def _outline_blocks(outline_result: str) -> List[Tuple[int, int]]:
    starts: List[int] = []
    for raw_line in outline_result.split(" ; "):
        match = re.match(r"\s*(\d+)\|\s*(.+)$", raw_line.strip())
        if not match:
            continue
        line_number = int(match.group(1))
        label = match.group(2).strip()
        if label.startswith(("def ", "class ")) or label.startswith(("def\t", "class\t")):
            starts.append(line_number)
            continue
        if label.startswith(("def__", "class__")):
            starts.append(line_number)
            continue
        if label.startswith(("def", "class")) and " " in label:
            starts.append(line_number)

    starts = sorted(set(starts))
    if not starts:
        return []

    blocks: List[Tuple[int, int]] = []
    for index, start_line in enumerate(starts):
        next_start = starts[index + 1] if index + 1 < len(starts) else start_line + FLOW_TRACE_MAX_BLOCK_SPAN - 1
        blocks.append((start_line, max(start_line, next_start - 1)))
    return blocks

def _outline_blocks_from_symbols(outline_symbols: List[OutlineSymbol]) -> List[Tuple[int, int]]:
    blocks: List[Tuple[int, int]] = []
    for symbol in outline_symbols:
        if not _symbol_can_bound_block(symbol):
            continue
        blocks.append((symbol.start_line, max(symbol.start_line, symbol.end_line)))
    return sorted(set(blocks))

def _symbol_can_bound_block(symbol: OutlineSymbol) -> bool:
    return symbol.kind in {"class", "function", "method"}

def _normalize_trace_output(text: str, extra_uncertainty_notes: Optional[List[str]] = None) -> str:
    return _merge_uncertainty_sections(text, extra_uncertainty_notes or [])

def _nearest_evidence_text(lines: List[str], header_index: int) -> str:
    for next_index in range(header_index + 1, len(lines)):
        stripped = lines[next_index].strip()
        if re.match(r"^\d+\.\s+", stripped) or stripped.startswith("Unresolved uncertainty:"):
            break
        if stripped.startswith("Evidence:"):
            return stripped.partition(":")[2].strip()
    return ""

def _filter_trace_evidence(header_text: str, evidence_text: str, variable_name: str) -> str:
    evidence = evidence_text.strip()
    if not evidence:
        return "`No direct role site retained from reviewed windows`"
    lowered = evidence.lower().strip("`")
    if lowered in {
        "none",
        "none.",
        "no confirmed reassignment in reviewed windows",
        "no confirmed argument passing in reviewed windows",
        "no confirmed use of that kind in reviewed windows",
        "no confirmed storage in reviewed windows",
    }:
        if lowered.startswith("none"):
            return "`No direct role site retained from reviewed windows`"
        return evidence
    if _contains_variable_token(evidence, variable_name):
        return evidence
    if "No confirmed" in header_text:
        return "`No direct role site retained from reviewed windows`"
    return "`No direct role site retained from reviewed windows`"

def _refined_usage_header(header_text: str, evidence_text: str, variable_name: str) -> str:
    evidence = evidence_text.strip().strip("`")
    lowered = evidence.lower()
    if not evidence or lowered.startswith("no confirmed"):
        return "Other confirmed local roles at `No confirmed role-specific use in reviewed windows`"
    if re.search(r"\b(return|yield)\b", lowered) and _contains_variable_token(evidence, variable_name):
        return "Returned directly at <location>"
    if re.search(r"\b(if|elif|while|assert)\b", lowered) and _contains_variable_token(evidence, variable_name):
        return "Used in condition at <location>"
    if _looks_like_storage_site(evidence, variable_name):
        if re.search(r"[\.\[]", evidence.split("=", 1)[0]):
            return "Stored into field or container at <location>"
        return "Stored for later use at <location>"
    if _looks_like_path_derivation(evidence, variable_name):
        return "Used to derive path at <location>"
    return "Other confirmed local roles at <location>"

def _contains_variable_token(text: str, variable_name: str) -> bool:
    if not variable_name:
        return False
    return bool(re.search(r"\b{0}\b".format(re.escape(variable_name)), text))

def _looks_like_storage_site(evidence_text: str, variable_name: str) -> bool:
    if "=" not in evidence_text or not _contains_variable_token(evidence_text, variable_name):
        return False
    left, _, right = evidence_text.partition("=")
    return _contains_variable_token(right, variable_name) and not _contains_variable_token(left, variable_name)

def _looks_like_path_derivation(evidence_text: str, variable_name: str) -> bool:
    if not _contains_variable_token(evidence_text, variable_name):
        return False
    lowered = evidence_text.lower()
    return any(token in lowered for token in ("/", "\\", "path", "dir", "join"))

def _merge_uncertainty_sections(text: str, extra_notes: List[str]) -> str:
    kept_lines: List[str] = []
    note_candidates: List[str] = []
    in_uncertainty = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("Unresolved uncertainty:"):
            in_uncertainty = True
            remainder = stripped.partition(":")[2].strip()
            if remainder:
                note_candidates.append(remainder)
            continue
        if in_uncertainty:
            if re.match(r"^\d+\.\s+", stripped) or stripped.startswith(("Variable:", "Confirmed file:", "Coverage:", "Target:")):
                in_uncertainty = False
                kept_lines.append(raw_line)
                continue
            if not stripped:
                continue
            if stripped.startswith("-"):
                note_candidates.append(stripped[1:].strip())
            else:
                note_candidates.append(stripped)
            continue
        kept_lines.append(raw_line)

    note_candidates.extend(extra_notes)
    notes = _normalized_uncertainty_notes(note_candidates)
    body = "\n".join(kept_lines).strip()
    uncertainty_block = "Unresolved uncertainty:\n" + "\n".join("- {0}".format(note) for note in notes)
    if not body:
        return uncertainty_block
    return body + "\n\n" + uncertainty_block

def _notes_to_uncertainty_items(notes: str) -> List[str]:
    items: List[str] = []
    for part in notes.split(";"):
        cleaned = part.strip()
        if cleaned:
            items.append(cleaned)
    return items

def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    kept: List[str] = []
    for item in items:
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        kept.append(cleaned)
    return kept

def _trace_status(coverage: str) -> str:
    if coverage == "complete":
        return "complete"
    return "partial"

def _trace_confidence(coverage: str) -> str:
    if coverage == "complete":
        return "file-only"
    return "local-only"

def _normalized_uncertainty_notes(notes: List[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    real_notes_present = False

    for note in notes:
        cleaned = note.strip()
        if not cleaned:
            continue
        lowered = cleaned.lower().rstrip(".")
        if lowered in {"none", "`none`"}:
            cleaned = "None"
        else:
            real_notes_present = True
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned)

    if real_notes_present:
        normalized = [note for note in normalized if note.lower() != "none"]
    if not normalized:
        return ["None"]
    return normalized

def _outline_symbols_from_payloads(payloads: List[Dict[str, Any]], workspace_root: Any) -> List[OutlineSymbol]:
    for payload in payloads:
        if payload.get("tool_name") != "get_outline":
            continue
        tool_use_id = str(payload.get("tool_use_id", ""))
        rel_path = tool_use_id.partition(":")[2].strip()
        if not rel_path:
            continue
        try:
            return load_outline_symbols(workspace_root, rel_path)
        except ToolError:
            return []
    return []

def _innermost_symbol_name_for_line(outline_symbols: List[OutlineSymbol], line_number: int) -> str:
    candidates = [
        symbol
        for symbol in outline_symbols
        if _symbol_can_bound_block(symbol) and symbol.start_line <= line_number <= max(symbol.start_line, symbol.end_line)
    ]
    if not candidates:
        return ""
    function_like = [symbol for symbol in candidates if symbol.kind in {"function", "method"}]
    ranked = function_like or candidates
    ranked.sort(key=lambda symbol: (symbol.end_line - symbol.start_line, symbol.start_line))
    return ranked[0].name

def _iter_numbered_code_lines(content: str) -> List[Tuple[int, str]]:
    items: List[Tuple[int, str]] = []
    for raw_line in content.splitlines():
        match = re.match(r"\s*(\d+)\|(.*)$", raw_line)
        if not match:
            continue
        items.append((int(match.group(1)), match.group(2).rstrip()))
    return items

def _fact_entry(
    line_number: int,
    code: str,
    function_name: str,
    role_label: str = "",
    qualname: str = "",
) -> Dict[str, Any]:
    return {
        "line": line_number,
        "evidence": "`{0}`".format(code.strip()),
        "function_name": function_name,
        "qualname": qualname,
        "role_label": role_label,
    }

def _looks_like_assignment_to_variable(code: str, variable_name: str) -> bool:
    stripped = code.strip()
    return bool(re.match(r"{0}\s*=".format(re.escape(variable_name)), stripped))

def _looks_like_argument_passing(code: str, variable_name: str) -> bool:
    if not _contains_variable_token(code, variable_name):
        return False
    lowered = code.strip().lower()
    if lowered.startswith(("def ", "class ", "@")):
        return False
    for match in re.finditer(r"\(([^)]*)\)", code):
        arguments = match.group(1)
        if _contains_variable_token(arguments, variable_name):
            return True
    return False

def _prefer_argument_fact(candidate: Dict[str, Any], existing: Optional[Dict[str, Any]], variable_name: str) -> bool:
    if existing is None:
        return True
    candidate_score = _argument_fact_score(candidate, variable_name)
    existing_score = _argument_fact_score(existing, variable_name)
    if candidate_score != existing_score:
        return candidate_score > existing_score
    return int(candidate.get("line", 0)) < int(existing.get("line", 0))

def _argument_fact_score(fact: Dict[str, Any], variable_name: str) -> int:
    code = str(fact.get("evidence", "")).strip("`")
    score = 0
    if _looks_like_path_derivation(code, variable_name):
        score += 3
    if re.search(r"\breturn\b", code):
        score += 2
    if _looks_like_storage_site(code, variable_name):
        score -= 2
    if re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*=\s*[A-Za-z_][A-Za-z0-9_]*\(", code):
        score += 1
    return score

def _render_unknown_section(number: int, model_notes: List[str], extra_notes: List[str]) -> List[str]:
    notes = _dedupe_preserve_order(model_notes + extra_notes)
    lines = ["{0}. Unknown / not proven".format(number)]
    if notes:
        for note in notes:
            lines.append("   - {0}".format(note))
        return lines
    return lines + ["   Nothing else was proven from the reviewed evidence."]

def _section_label_separator(label: str) -> str:
    normalized = label.strip().lower()
    if normalized.endswith(("at", "to", "in")):
        return " "
    return " at "

def _fact_location(fact: Dict[str, Any]) -> str:
    line_number = fact.get("line")
    function_name = str(fact.get("qualname", "") or fact.get("function_name", "")).strip()
    if function_name:
        return "line {0} inside `{1}`".format(line_number, function_name)
    return "line {0}".format(line_number)

def _innermost_symbol_qualname_for_line(outline_symbols: List[OutlineSymbol], line_number: int) -> str:
    candidates = [
        symbol
        for symbol in outline_symbols
        if _symbol_can_bound_block(symbol) and symbol.start_line <= line_number <= max(symbol.start_line, symbol.end_line)
    ]
    if not candidates:
        return ""
    function_like = [symbol for symbol in candidates if symbol.kind in {"function", "method"}]
    ranked = function_like or candidates
    ranked.sort(key=lambda symbol: (symbol.end_line - symbol.start_line, symbol.start_line))
    return ranked[0].qualname
