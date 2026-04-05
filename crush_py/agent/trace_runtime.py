import re
from typing import Any, Dict, List, Optional, Tuple

from ..backends.base import BackendError, BaseBackend
from .trace_runtime_file_flow import (
    callable_names_from_code_lines,
    cat_code_lines_from_payloads,
    direct_file_file_flow_reader_instructions,
    fallback_direct_file_file_flow_output,
    first_output_line,
    merged_callable_names,
    normalize_direct_file_file_flow_output,
    outline_names_from_payloads,
    run_direct_file_file_flow_reader,
)
from .trace_runtime_named import (
    append_flow_trace_postprocessing,
    append_trace_coverage_uncertainty,
    append_variable_trace_postprocessing,
    collect_flow_trace_reads,
    collect_variable_trace_reads,
    direct_file_flow_trace_reader_instructions,
    direct_file_variable_trace_reader_instructions,
    run_direct_file_flow_trace_reader,
    run_direct_file_variable_trace_reader,
)


class TraceRuntimeMixin:
    def _run_direct_file_file_flow_reader(
        self,
        session_id: str,
        backend: BaseBackend,
        prompt: str,
        rel_path: str,
        stream: bool = False,
    ) -> str:
        return run_direct_file_file_flow_reader(self, session_id, backend, prompt, rel_path, stream=stream)

    def _run_direct_file_variable_trace_reader(
        self,
        session_id: str,
        backend: BaseBackend,
        prompt: str,
        rel_path: str,
        stream: bool = False,
    ) -> str:
        return run_direct_file_variable_trace_reader(self, session_id, backend, prompt, rel_path, stream=stream)

    def _run_direct_file_flow_trace_reader(
        self,
        session_id: str,
        backend: BaseBackend,
        prompt: str,
        rel_path: str,
        stream: bool = False,
    ) -> str:
        return run_direct_file_flow_trace_reader(self, session_id, backend, prompt, rel_path, stream=stream)

    def _collect_variable_trace_reads(
        self,
        session_id: str,
        rel_path: str,
        variable_name: str,
    ) -> Tuple[List[Dict[str, Any]], str, str]:
        return collect_variable_trace_reads(self, session_id, rel_path, variable_name)

    def _collect_flow_trace_reads(
        self,
        session_id: str,
        rel_path: str,
        variable_name: str,
    ) -> Tuple[List[Dict[str, Any]], str, str]:
        return collect_flow_trace_reads(self, session_id, rel_path, variable_name)

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
        return direct_file_variable_trace_reader_instructions()

    def _direct_file_flow_trace_reader_instructions(self) -> str:
        return direct_file_flow_trace_reader_instructions()

    def _direct_file_file_flow_reader_instructions(self) -> str:
        return direct_file_file_flow_reader_instructions()

    def _append_trace_coverage_uncertainty(self, text: str, coverage: str) -> str:
        return append_trace_coverage_uncertainty(text, coverage)

    def _append_flow_trace_postprocessing(
        self,
        text: str,
        coverage: str,
        variable_name: str,
        payloads: List[Dict[str, Any]],
        notes: str = "",
    ) -> str:
        return append_flow_trace_postprocessing(self, text, coverage, variable_name, payloads, notes)

    def _append_variable_trace_postprocessing(
        self,
        text: str,
        coverage: str,
        variable_name: str,
        payloads: List[Dict[str, Any]],
        notes: str = "",
    ) -> str:
        return append_variable_trace_postprocessing(self, text, coverage, variable_name, payloads, notes)

    def _normalize_direct_file_file_flow_output(
        self,
        text: str,
        rel_path: str,
        coverage: str,
        payloads: List[Dict[str, Any]],
        notes: List[str],
    ) -> str:
        return normalize_direct_file_file_flow_output(self, text, rel_path, coverage, payloads, notes)

    def _fallback_direct_file_file_flow_output(
        self,
        rel_path: str,
        coverage: str,
        payloads: List[Dict[str, Any]],
        extra_uncertainty_notes: List[str],
    ) -> str:
        return fallback_direct_file_file_flow_output(rel_path, coverage, payloads, extra_uncertainty_notes)

    def _outline_names_from_payloads(self, payloads: List[Dict[str, Any]]) -> List[str]:
        return outline_names_from_payloads(payloads)

    def _cat_code_lines_from_payloads(self, payloads: List[Dict[str, Any]]) -> List[str]:
        return cat_code_lines_from_payloads(payloads)

    def _first_output_line(self, cat_lines: List[str]) -> str:
        return first_output_line(cat_lines)

    def _callable_names_from_code_lines(self, cat_lines: List[str]) -> List[str]:
        return callable_names_from_code_lines(cat_lines)

    def _merged_callable_names(self, primary: List[str], secondary: List[str]) -> List[str]:
        return merged_callable_names(primary, secondary)
