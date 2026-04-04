import re
from typing import Any, Dict, List, Optional, Tuple

from ..backends.base import BackendError, BaseBackend
from ..output_sanitize import sanitize_text
from ..tools.base import ToolError
from ..tools.get_outline import load_outline_symbols
from ..tools.outline_providers import OutlineSymbol
from .runtime_prompts import BASE_READ_HELPER_SYSTEM_PROMPT, READER_APPENDIX
from .trace_runtime_support import (
    _clip_windows_to_outline_symbols,
    _collect_flow_trace_facts,
    _collect_variable_trace_facts,
    _flow_trace_windows,
    _grep_match_line_numbers_for_path,
    _grep_scope_for_file,
    _merged_line_windows,
    _normalize_flow_trace_output,
    _normalize_trace_output,
    _normalize_variable_trace_output,
    _notes_to_uncertainty_items,
    _single_line,
    _trace_confidence,
    _trace_status,
)


VARIABLE_TRACE_WINDOW_RADIUS = 6
VARIABLE_TRACE_MAX_WINDOWS = 6
FLOW_TRACE_MAX_WINDOWS = 4
FLOW_TRACE_FALLBACK_RADIUS = 12


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


