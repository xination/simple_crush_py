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
    def _run_direct_file_file_flow_reader(
        self,
        session_id: str,
        backend: BaseBackend,
        prompt: str,
        rel_path: str,
        stream: bool = False,
    ) -> str:
        payloads: List[Dict[str, Any]] = []
        notes: List[str] = []
        try:
            outline_result = self._record_reader_tool(session_id, "get_outline", {"path": rel_path})
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

        cat_payloads, coverage = self._collect_summary_file_reads(session_id, rel_path)
        payloads.extend(cat_payloads)
        compact_payloads = self._compact_reader_cat_payloads(payloads)
        conversation = [
            {
                "role": "user",
                "content": (
                    "User request: {0}\n"
                    "Target file: {1}\n"
                    "Coverage: {2}\n"
                    "{3}"
                ).format(
                    prompt.strip(),
                    rel_path,
                    coverage,
                    self._direct_file_file_flow_reader_instructions(),
                ),
            },
            {"role": "user", "content": compact_payloads},
        ]
        try:
            model_text = self._generate_text_with_optional_streaming(
                backend,
                BASE_READ_HELPER_SYSTEM_PROMPT + READER_APPENDIX,
                conversation,
                stream=stream,
            )
        except BackendError as exc:
            model_text = ""
            notes.append("backend fallback: {0}".format(exc))
        final_text = self._normalize_direct_file_file_flow_output(model_text, rel_path, coverage, payloads, notes)

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
                    "mode": "file_flow_trace",
                },
                "tool_use_id": "reader:{0}".format(rel_path),
                "summary": final_text,
            },
        )
        return final_text

    def _run_direct_file_variable_trace_reader(
        self,
        session_id: str,
        backend: BaseBackend,
        prompt: str,
        rel_path: str,
        stream: bool = False,
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
            model_text = self._generate_text_with_optional_streaming(
                backend,
                BASE_READ_HELPER_SYSTEM_PROMPT + READER_APPENDIX,
                conversation,
                stream=stream,
            )
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
        stream: bool = False,
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
            model_text = self._generate_text_with_optional_streaming(
                backend,
                BASE_READ_HELPER_SYSTEM_PROMPT + READER_APPENDIX,
                conversation,
                stream=stream,
            )
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
        return self._prompt_intent(prompt).direct_file_trace

    def _is_direct_file_flow_trace_prompt(self, prompt: str) -> bool:
        return self._prompt_intent(prompt).direct_file_flow_trace

    def _is_direct_file_variable_trace_prompt(self, prompt: str) -> bool:
        return self._prompt_intent(prompt).direct_file_variable_trace

    def _is_direct_file_file_flow_trace_prompt(self, prompt: str) -> bool:
        return self._prompt_intent(prompt).direct_file_file_flow_trace

    def _prompt_direct_trace_variable(self, prompt: str) -> Optional[str]:
        return self._prompt_intent(prompt).trace_variable

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

    def _direct_file_file_flow_reader_instructions(self) -> str:
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

    def _normalize_direct_file_file_flow_output(
        self,
        text: str,
        rel_path: str,
        coverage: str,
        payloads: List[Dict[str, Any]],
        notes: List[str],
    ) -> str:
        final_text = sanitize_text(text).strip()
        extra_uncertainty_notes = _notes_to_uncertainty_items("; ".join(notes)) if notes else []
        if final_text.startswith("File flow for human review:"):
            return _normalize_trace_output(final_text, extra_uncertainty_notes)
        return self._fallback_direct_file_file_flow_output(rel_path, coverage, payloads, extra_uncertainty_notes)

    def _fallback_direct_file_file_flow_output(
        self,
        rel_path: str,
        coverage: str,
        payloads: List[Dict[str, Any]],
        extra_uncertainty_notes: List[str],
    ) -> str:
        cat_lines = self._cat_code_lines_from_payloads(payloads)
        outline_names = self._merged_callable_names(self._outline_names_from_payloads(payloads), self._callable_names_from_code_lines(cat_lines))
        entry_points = outline_names[:2]
        helper_names = outline_names[2:]
        output_line = self._first_output_line(cat_lines)

        if entry_points and len(entry_points) > 1:
            flow_chain = "Reviewed callables include `{0}` and `{1}`, but the exact handoff chain was not fully proven from the fallback evidence.".format(
                entry_points[0], entry_points[1]
            )
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
            "   Evidence: {0}".format(
                ", ".join("`{0}`".format(name) for name in entry_points) if entry_points else "No confirmed top-level entry point"
            ),
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

    def _outline_names_from_payloads(self, payloads: List[Dict[str, Any]]) -> List[str]:
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

    def _cat_code_lines_from_payloads(self, payloads: List[Dict[str, Any]]) -> List[str]:
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

    def _first_output_line(self, cat_lines: List[str]) -> str:
        for code in cat_lines:
            if code.startswith("return ") and ("join(" in code or "format(" in code or "_single_line" in code):
                return "`{0}`".format(code)
        for code in cat_lines:
            if code.startswith("return "):
                return "`{0}`".format(code)
        return ""

    def _callable_names_from_code_lines(self, cat_lines: List[str]) -> List[str]:
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

    def _merged_callable_names(self, primary: List[str], secondary: List[str]) -> List[str]:
        merged: List[str] = []
        for name in list(primary) + list(secondary):
            if name and name not in merged:
                merged.append(name)
        return merged


