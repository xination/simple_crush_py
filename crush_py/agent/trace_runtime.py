import re
from typing import Any, Dict, List, Optional, Tuple

from ..backends.base import BackendError, BaseBackend
from ..output_sanitize import sanitize_text
from ..tools.base import ToolError
from ..tools.get_outline import load_outline_symbols
from ..tools.outline_providers import OutlineSymbol
from .runtime_prompts import BASE_READ_HELPER_SYSTEM_PROMPT, READER_APPENDIX


VARIABLE_TRACE_WINDOW_RADIUS = 6
VARIABLE_TRACE_MAX_WINDOWS = 6
FLOW_TRACE_MAX_WINDOWS = 4
FLOW_TRACE_FALLBACK_RADIUS = 12
FLOW_TRACE_MAX_BLOCK_SPAN = 120


class TraceRuntimeMixin:
    def _run_direct_file_variable_trace_reader(
        self,
        session_id: str,
        backend: BaseBackend,
        prompt: str,
        rel_path: str,
    ) -> str:
        variable_name = self._prompt_direct_trace_variable(prompt)
        if not variable_name:
            raise BackendError("Variable trace prompt is missing a concrete variable name.")

        raw_payloads, coverage, notes = self._collect_variable_trace_reads(session_id, rel_path, variable_name)
        payloads = self._compact_reader_cat_payloads(raw_payloads)
        conversation = [
            {
                "role": "user",
                "content": (
                    "User request: {0}\n"
                    "Target file: {1}\n"
                    "Variable: {2}\n"
                    "Coverage: {3}\n"
                    "Collection notes: {4}\n"
                    "{5}\n"
                    "{6}"
                ).format(
                    prompt.strip(),
                    rel_path,
                    variable_name,
                    coverage,
                    notes,
                    "Read strategy: outline first, grep the variable inside this file, then inspect only the most relevant local windows.",
                    self._direct_file_variable_trace_reader_instructions(),
                ),
            },
            {"role": "user", "content": payloads},
        ]
        try:
            turn = self._generate_turn_with_retry(
                backend,
                BASE_READ_HELPER_SYSTEM_PROMPT + READER_APPENDIX,
                conversation,
                tools=None,
            )
            model_text = sanitize_text(turn.text).strip()
        except BackendError as exc:
            model_text = ""
            coverage = "partial"
            notes = "{0}; backend fallback: {1}".format(notes, exc).strip("; ")
        final_text = self._append_variable_trace_postprocessing(model_text, coverage, variable_name, raw_payloads, notes)

        state = self._state_for_session(session_id)
        state.file_summaries[rel_path] = _single_line(final_text, 240)
        if rel_path and rel_path not in state.confirmed_paths:
            state.confirmed_paths.append(rel_path)
        self.session_store.append_message(
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
                    "mode": "variable_trace",
                    "variable": variable_name,
                },
                "tool_use_id": "reader:{0}".format(rel_path),
                "summary": final_text,
            },
        )
        return final_text

    def _run_direct_file_flow_trace_reader(
        self,
        session_id: str,
        backend: BaseBackend,
        prompt: str,
        rel_path: str,
    ) -> str:
        variable_name = self._prompt_direct_trace_variable(prompt)
        if not variable_name:
            raise BackendError("Flow trace prompt is missing a concrete variable name.")

        raw_payloads, coverage, notes = self._collect_flow_trace_reads(session_id, rel_path, variable_name)
        payloads = self._compact_reader_cat_payloads(raw_payloads)
        conversation = [
            {
                "role": "user",
                "content": (
                    "User request: {0}\n"
                    "Target file: {1}\n"
                    "Tracked name: {2}\n"
                    "Coverage: {3}\n"
                    "Collection notes: {4}\n"
                    "{5}\n"
                    "{6}"
                ).format(
                    prompt.strip(),
                    rel_path,
                    variable_name,
                    coverage,
                    notes,
                    "Read strategy: outline first, locate the containing function or method blocks for the named flow, then inspect those full local blocks.",
                    self._direct_file_flow_trace_reader_instructions(),
                ),
            },
            {"role": "user", "content": payloads},
        ]
        try:
            turn = self._generate_turn_with_retry(
                backend,
                BASE_READ_HELPER_SYSTEM_PROMPT + READER_APPENDIX,
                conversation,
                tools=None,
            )
            model_text = sanitize_text(turn.text).strip()
        except BackendError as exc:
            model_text = ""
            coverage = "partial"
            notes = "{0}; backend fallback: {1}".format(notes, exc).strip("; ")
        final_text = self._append_flow_trace_postprocessing(model_text, coverage, variable_name, raw_payloads, notes)

        state = self._state_for_session(session_id)
        state.file_summaries[rel_path] = _single_line(final_text, 240)
        if rel_path and rel_path not in state.confirmed_paths:
            state.confirmed_paths.append(rel_path)
        self.session_store.append_message(
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
                    "mode": "flow_trace",
                    "variable": variable_name,
                },
                "tool_use_id": "reader:{0}".format(rel_path),
                "summary": final_text,
            },
        )
        return final_text

    def _collect_variable_trace_reads(
        self,
        session_id: str,
        rel_path: str,
        variable_name: str,
    ) -> Tuple[List[Dict[str, Any]], str, str]:
        payloads: List[Dict[str, Any]] = []
        coverage = "local"
        notes: List[str] = []
        outline_symbols: List[OutlineSymbol] = []

        try:
            outline_result = self._record_reader_tool(session_id, "get_outline", {"path": rel_path})
            outline_symbols = load_outline_symbols(self.config.workspace_root, rel_path)
            payloads.append(
                {
                    "type": "tool_result",
                    "tool_use_id": "reader-outline:{0}".format(rel_path),
                    "tool_name": "get_outline",
                    "content": outline_result,
                }
            )
        except ToolError as exc:
            notes.append("outline unavailable: {0}".format(exc))

        search_path, include = _grep_scope_for_file(rel_path)
        grep_result = self._record_reader_tool(
            session_id,
            "grep",
            {
                "pattern": r"\b{0}\b".format(re.escape(variable_name)),
                "path": search_path,
                "include": include,
            },
        )
        payloads.append(
            {
                "type": "tool_result",
                "tool_use_id": "reader-grep:{0}:{1}".format(rel_path, variable_name),
                "tool_name": "grep",
                "content": grep_result,
            }
        )

        matched_lines = _grep_match_line_numbers_for_path(grep_result, rel_path)
        if not matched_lines:
            notes.append("grep found no confirmed in-file occurrences for `{0}`".format(variable_name))
            return payloads, coverage, "; ".join(notes) if notes else "no extra notes"

        windows, truncated = _merged_line_windows(
            matched_lines,
            radius=VARIABLE_TRACE_WINDOW_RADIUS,
            max_windows=VARIABLE_TRACE_MAX_WINDOWS,
        )
        windows = _clip_windows_to_outline_symbols(windows, matched_lines, outline_symbols)
        if truncated or "Search was capped" in grep_result:
            coverage = "partial"
            notes.append("window collection was capped to keep the trace compact")

        for start_line, end_line in windows:
            cat_result = self._record_reader_tool(
                session_id,
                "cat",
                {
                    "path": rel_path,
                    "offset": max(0, start_line - 1),
                    "limit": max(1, end_line - start_line + 1),
                },
            )
            payloads.append(
                {
                    "type": "tool_result",
                    "tool_use_id": "reader-cat:{0}:{1}:{2}".format(rel_path, start_line, end_line),
                    "tool_name": "cat",
                    "content": cat_result,
                }
            )

        if coverage == "local":
            notes.append("only local windows around confirmed matches were read; full-file trace was not verified")
        return payloads, coverage, "; ".join(notes) if notes else "local windows cover every matched line from grep"

    def _collect_flow_trace_reads(
        self,
        session_id: str,
        rel_path: str,
        variable_name: str,
    ) -> Tuple[List[Dict[str, Any]], str, str]:
        payloads: List[Dict[str, Any]] = []
        coverage = "local"
        notes: List[str] = []
        outline_result = ""
        outline_symbols: List[OutlineSymbol] = []

        try:
            outline_result = self._record_reader_tool(session_id, "get_outline", {"path": rel_path})
            outline_symbols = load_outline_symbols(self.config.workspace_root, rel_path)
            payloads.append(
                {
                    "type": "tool_result",
                    "tool_use_id": "reader-outline:{0}".format(rel_path),
                    "tool_name": "get_outline",
                    "content": outline_result,
                }
            )
        except ToolError as exc:
            notes.append("outline unavailable: {0}".format(exc))

        search_path, include = _grep_scope_for_file(rel_path)
        grep_result = self._record_reader_tool(
            session_id,
            "grep",
            {
                "pattern": r"\b{0}\b".format(re.escape(variable_name)),
                "path": search_path,
                "include": include,
            },
        )
        payloads.append(
            {
                "type": "tool_result",
                "tool_use_id": "reader-grep:{0}:{1}".format(rel_path, variable_name),
                "tool_name": "grep",
                "content": grep_result,
            }
        )

        matched_lines = _grep_match_line_numbers_for_path(grep_result, rel_path)
        if not matched_lines:
            notes.append("grep found no confirmed in-file occurrences for `{0}`".format(variable_name))
            return payloads, "partial", "; ".join(notes)

        windows, used_outline, truncated = _flow_trace_windows(outline_symbols, outline_result, matched_lines)
        if not windows:
            windows, truncated = _merged_line_windows(
                matched_lines,
                radius=FLOW_TRACE_FALLBACK_RADIUS,
                max_windows=FLOW_TRACE_MAX_WINDOWS,
            )
            notes.append("fell back to local line windows because no containing function block was confirmed from outline")
        elif used_outline:
            notes.append("read containing function or method blocks for the confirmed matches")

        if truncated or "Search was capped" in grep_result:
            coverage = "partial"
            notes.append("not every candidate flow region was included")

        for start_line, end_line in windows:
            cat_result = self._record_reader_tool(
                session_id,
                "cat",
                {
                    "path": rel_path,
                    "offset": max(0, start_line - 1),
                    "limit": max(1, end_line - start_line + 1),
                },
            )
            payloads.append(
                {
                    "type": "tool_result",
                    "tool_use_id": "reader-cat:{0}:{1}:{2}".format(rel_path, start_line, end_line),
                    "tool_name": "cat",
                    "content": cat_result,
                }
            )

        if coverage == "local":
            notes.append("downstream flow beyond the reviewed local blocks was not verified")
        return payloads, coverage, "; ".join(notes)

    def _is_direct_file_trace_prompt(self, prompt: str) -> bool:
        return self._is_direct_file_flow_trace_prompt(prompt) or self._is_direct_file_variable_trace_prompt(prompt)

    def _is_direct_file_flow_trace_prompt(self, prompt: str) -> bool:
        if not self._prompt_direct_file_path(prompt):
            return False
        variable_name = self._prompt_direct_trace_variable(prompt)
        if not variable_name:
            return False
        lowered = prompt.lower()
        signals = (
            "trace how",
            " flows",
            " flow ",
            "moves through",
            "handled",
        )
        return any(signal in lowered for signal in signals)

    def _is_direct_file_variable_trace_prompt(self, prompt: str) -> bool:
        if not self._prompt_direct_file_path(prompt):
            return False
        variable_name = self._prompt_direct_trace_variable(prompt)
        if not variable_name:
            return False
        if self._is_direct_file_flow_trace_prompt(prompt):
            return False
        lowered = prompt.lower()
        signals = (
            "trace the variable",
            "trace variable",
            "trace how",
            "where ",
            " flows",
            " flow",
            " comes from",
            " is set",
            " is passed",
        )
        return any(signal in lowered for signal in signals)

    def _prompt_direct_trace_variable(self, prompt: str) -> Optional[str]:
        patterns = (
            r"trace the variable\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"trace variable\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"trace how\s+([A-Za-z_][A-Za-z0-9_]*)\s+flows?",
            r"where\s+([A-Za-z_][A-Za-z0-9_]*)\s+is\s+set",
            r"where\s+([A-Za-z_][A-Za-z0-9_]*)\s+comes\s+from",
            r"where\s+([A-Za-z_][A-Za-z0-9_]*)\s+is\s+passed",
        )
        for pattern in patterns:
            match = re.search(pattern, prompt, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _direct_file_variable_trace_reader_instructions(self) -> str:
        return (
            "Stay inside the named file.\n"
            "Do not do repo-wide tracing, alias analysis, or runtime reconstruction.\n"
            "Use the outline, grep matches, and local cat windows as evidence.\n"
            "Prefer fewer exact evidence lines over wider nearby excerpts.\n"
            "Only keep evidence lines that directly use the tracked variable or prove the exact role site.\n"
            "Use the most specific role label the evidence supports.\n"
            "Return this format:\n"
            "Variable trace for human review:\n\n"
            "Variable: <name>\n"
            "Confirmed file: <path>\n\n"
            "1. Defined or first assigned at <location>\n"
            "   Evidence: <line / statement>\n\n"
            "2. Reassigned at <location> or `No confirmed reassignment in reviewed windows`\n"
            "   Evidence: <line / statement>\n\n"
            "3. Passed as an argument at <location> or `No confirmed argument passing in reviewed windows`\n"
            "   Evidence: <line / statement>\n\n"
            "4. Use a specific role label such as `Stored into field`, `Stored into container`, `Used to derive path`, `Returned directly`, or `Used in condition`\n"
            "   Evidence: <line / statement>\n\n"
            "Unresolved uncertainty:\n"
            "- <note>\n"
            "Keep uncertainty explicit and prefer a useful partial trace over guessing."
        )

    def _direct_file_flow_trace_reader_instructions(self) -> str:
        return (
            "Stay inside the named file.\n"
            "Treat this as a local flow trace, not a generic variable-summary template.\n"
            "Prefer claims about entry point, transformation, storage, and downstream calls.\n"
            "Do not claim reassignment unless the evidence line really rebinds the tracked name.\n"
            "Return this format:\n"
            "Flow trace for human review:\n\n"
            "Target: <name>\n"
            "Confirmed file: <path>\n"
            "Coverage: <local / partial>\n\n"
            "1. Entry point\n"
            "   Evidence: <line / statement>\n\n"
            "2. Immediate transformations or normalization\n"
            "   Evidence: <line / statement> or `No confirmed transformation in reviewed blocks`\n\n"
            "3. Storage or state updates\n"
            "   Evidence: <line / statement> or `No confirmed storage in reviewed blocks`\n\n"
            "4. Downstream calls or handoff sites\n"
            "   Evidence: <line / statement> or `No confirmed downstream handoff in reviewed blocks`\n\n"
            "5. Confirmed local flow\n"
            "   Evidence: <short flow chain>\n\n"
            "Unresolved uncertainty:\n"
            "- <note>\n"
            "Prefer a short honest local flow over a broad but shaky trace."
        )

    def _append_trace_coverage_uncertainty(self, text: str, coverage: str) -> str:
        final_text = text.strip()
        lowered = final_text.lower()
        extra_uncertainty_notes: List[str] = []
        if coverage == "partial" and "partial" not in lowered:
            extra_uncertainty_notes.append("The trace is partial because not every candidate region was reviewed.")
        if coverage == "local" and "local flow" not in lowered and "reviewed local" not in lowered and "coverage: local" not in lowered:
            extra_uncertainty_notes.append(
                "The trace is limited to the reviewed local blocks and does not prove full-file downstream flow."
            )
        return _normalize_trace_output(final_text, extra_uncertainty_notes)

    def _append_flow_trace_postprocessing(
        self,
        text: str,
        coverage: str,
        variable_name: str,
        payloads: List[Dict[str, Any]],
        notes: str = "",
    ) -> str:
        final_text = sanitize_text(text).strip()
        extra_uncertainty_notes: List[str] = []
        if coverage == "partial":
            extra_uncertainty_notes.append("This trace is partial because not every candidate region was reviewed.")
        elif coverage == "local":
            extra_uncertainty_notes.append("This trace is limited to the reviewed local blocks.")
            extra_uncertainty_notes.append("It does not yet prove downstream flow beyond the reviewed symbol.")
        if notes:
            extra_uncertainty_notes.extend(_notes_to_uncertainty_items(notes))
        facts = _collect_flow_trace_facts(payloads, variable_name, self.config.workspace_root)
        return _normalize_flow_trace_output(final_text, facts, coverage, extra_uncertainty_notes)

    def _append_variable_trace_postprocessing(
        self,
        text: str,
        coverage: str,
        variable_name: str,
        payloads: List[Dict[str, Any]],
        notes: str = "",
    ) -> str:
        final_text = sanitize_text(text).strip()
        extra_uncertainty_notes: List[str] = []
        if coverage == "partial":
            extra_uncertainty_notes.append("This trace is partial because not every candidate region was reviewed.")
        elif coverage == "local":
            extra_uncertainty_notes.append("This trace is limited to the reviewed local blocks.")
            extra_uncertainty_notes.append("It does not yet prove downstream use in helper methods or later parts of the file.")
        if notes:
            extra_uncertainty_notes.extend(_notes_to_uncertainty_items(notes))
        facts = _collect_variable_trace_facts(payloads, variable_name, self.config.workspace_root)
        return _normalize_variable_trace_output(final_text, facts, coverage, extra_uncertainty_notes)


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
