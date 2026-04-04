import re
from typing import Any, Dict, List, Optional, Tuple

from ..backends.base import BackendError, BaseBackend
from ..tools.base import ToolError
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

        payloads, coverage, notes = self._collect_variable_trace_reads(session_id, rel_path, variable_name)
        payloads = self._compact_reader_cat_payloads(payloads)
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
        turn = backend.generate_turn(BASE_READ_HELPER_SYSTEM_PROMPT + READER_APPENDIX, conversation, tools=None)
        final_text = self._append_trace_coverage_uncertainty(turn.text.strip(), coverage)

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

        payloads, coverage, notes = self._collect_flow_trace_reads(session_id, rel_path, variable_name)
        payloads = self._compact_reader_cat_payloads(payloads)
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
        turn = backend.generate_turn(BASE_READ_HELPER_SYSTEM_PROMPT + READER_APPENDIX, conversation, tools=None)
        final_text = self._append_trace_coverage_uncertainty(turn.text.strip(), coverage)

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

        windows, used_outline, truncated = _flow_trace_windows(outline_result, matched_lines)
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
            "4. Used in condition, return, or storage at <location> or `No confirmed use of that kind in reviewed windows`\n"
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
        if coverage == "partial" and "partial" not in lowered:
            final_text += "\n\nUnresolved uncertainty:\n- The trace is partial because not every candidate region was reviewed."
        if coverage == "local" and "local flow" not in lowered and "reviewed local" not in lowered and "coverage: local" not in lowered:
            final_text += "\n\nUnresolved uncertainty:\n- The trace is limited to the reviewed local blocks and does not prove full-file downstream flow."
        return final_text


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


def _flow_trace_windows(outline_result: str, line_numbers: List[int]) -> Tuple[List[Tuple[int, int]], bool, bool]:
    outline_blocks = _outline_blocks(outline_result)
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
