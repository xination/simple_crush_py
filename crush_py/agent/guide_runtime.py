from typing import Any, Dict, List, Tuple

from ..backends.base import BackendError, BaseBackend
from ..output_sanitize import sanitize_text
from .guide_runtime_support import (
    direct_file_guide_reader_instructions,
    exact_guide_line_answer,
    fallback_direct_file_guide_output,
    finalize_direct_file_guide_output,
    guide_line_preview,
    guide_source_hints,
    is_exact_guide_prompt,
)
from .prompt_intent import detect_guide_output_mode, should_reread_guide_prompt
from .reader_runtime_support import single_line as _single_line
from .runtime_prompts import BASE_READ_HELPER_SYSTEM_PROMPT, GUIDE_APPENDIX, READER_APPENDIX


class GuideRuntimeMixin:
    def _run_direct_file_guide_reader(self, session_id: str, backend: BaseBackend, prompt: str, rel_path: str, stream: bool = False) -> str:
        mode = self._guide_output_mode(prompt)
        previous_summary, previous_coverage = self._latest_guide_reader_result(session_id, rel_path)
        if previous_summary and self._should_reuse_guide_summary(prompt, previous_summary, previous_coverage):
            return self._answer_from_reused_guide_summary(
                session_id,
                backend,
                prompt,
                rel_path,
                mode,
                previous_summary,
                stream=stream,
            )

        payloads, coverage = self._collect_guide_file_reads(session_id, rel_path)
        compact_payloads = self._compact_reader_cat_payloads(payloads)
        exact_line_prompt = is_exact_guide_prompt(prompt)
        context_note = ""
        if previous_summary:
            context_note = "Previous guide summary for {0}:\n{1}\n".format(rel_path, previous_summary)
        conversation = [
            {
                "role": "user",
                "content": (
                    "User request: {0}\n"
                    "Target doc: {1}\n"
                    "Coverage: {2}\n"
                    "{3}"
                    "{4}"
                ).format(
                    prompt.strip(),
                    rel_path,
                    coverage,
                    context_note,
                    self._direct_file_guide_reader_instructions(mode),
                ),
            },
            {"role": "user", "content": compact_payloads},
        ]

        try:
            model_text = self._generate_text_with_optional_streaming(
                backend,
                BASE_READ_HELPER_SYSTEM_PROMPT + GUIDE_APPENDIX + READER_APPENDIX,
                conversation,
                stream=stream,
            )
        except BackendError:
            model_text = ""

        if exact_line_prompt:
            final_text = exact_guide_line_answer(prompt, rel_path, payloads, coverage)
        else:
            final_text = self._finalize_direct_file_guide_output(prompt, rel_path, coverage, payloads, model_text)
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
                "tool_arguments": {"path": rel_path, "coverage": coverage, "mode": "guide"},
                "tool_use_id": "reader:{0}".format(rel_path),
                "summary": final_text,
            },
        )
        return final_text

    def _answer_from_reused_guide_summary(
        self,
        session_id: str,
        backend: BaseBackend,
        prompt: str,
        rel_path: str,
        mode: str,
        previous_summary: str,
        stream: bool = False,
    ) -> str:
        conversation = [
            {
                "role": "user",
                "content": (
                    "User request: {0}\n"
                    "Target doc: {1}\n"
                    "Reuse the previous guide summary first instead of rereading the full doc.\n"
                    "{2}"
                    "Previous guide summary for {1}:\n{3}\n"
                ).format(
                    prompt.strip(),
                    rel_path,
                    self._direct_file_guide_reader_instructions(mode),
                    previous_summary,
                ),
            }
        ]
        try:
            model_text = self._generate_text_with_optional_streaming(
                backend,
                BASE_READ_HELPER_SYSTEM_PROMPT + GUIDE_APPENDIX + READER_APPENDIX,
                conversation,
                stream=stream,
            )
        except BackendError:
            model_text = ""

        final_text = self._finalize_direct_file_guide_output(prompt, rel_path, "reused", [], model_text or previous_summary)
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
                "tool_arguments": {"path": rel_path, "coverage": "reused", "mode": "guide"},
                "tool_use_id": "reader:{0}".format(rel_path),
                "summary": final_text,
            },
        )
        return final_text

    def _collect_guide_file_reads(self, session_id: str, rel_path: str) -> Tuple[List[Dict[str, Any]], str]:
        return self._collect_summary_file_reads(session_id, rel_path)

    def _is_guide_prompt(self, prompt: str) -> bool:
        return self._prompt_intent(prompt).guide_mode

    def _is_direct_file_guide_prompt(self, prompt: str) -> bool:
        intent = self._prompt_intent(prompt)
        return intent.guide_mode and bool(intent.direct_file_path) and not intent.direct_file_trace

    def _guide_output_mode(self, prompt: str) -> str:
        return detect_guide_output_mode(prompt)

    def _direct_file_guide_reader_instructions(self, mode: str) -> str:
        return direct_file_guide_reader_instructions(mode)

    def _finalize_direct_file_guide_output(
        self,
        prompt: str,
        rel_path: str,
        coverage: str,
        payloads: List[Dict[str, Any]],
        model_text: str,
    ) -> str:
        fallback_text = self._fallback_direct_file_guide_output(prompt, rel_path, payloads)
        return finalize_direct_file_guide_output(rel_path, coverage, payloads, model_text, fallback_text)

    def _fallback_direct_file_guide_output(self, prompt: str, rel_path: str, payloads: List[Dict[str, Any]]) -> str:
        mode = self._guide_output_mode(prompt)
        return fallback_direct_file_guide_output(mode, rel_path, payloads)

    def _should_reuse_guide_summary(self, prompt: str, previous_summary: str, previous_coverage: str) -> bool:
        if should_reread_guide_prompt(prompt):
            return False
        if previous_coverage not in ("complete", "reused"):
            return False
        if not previous_summary.strip():
            return False
        return True

    def _latest_guide_reader_result(self, session_id: str, rel_path: str) -> Tuple[str, str]:
        for message in reversed(self.session_store.load_messages(session_id)):
            if not self._is_reader_summary_message(message):
                continue
            args = message.metadata.get("tool_arguments", {}) or message.metadata.get("args", {}) or {}
            if str(args.get("mode", "")).strip() != "guide":
                continue
            if str(args.get("path", "")).strip() != rel_path:
                continue
            summary = sanitize_text(message.content or message.metadata.get("summary", "")).strip()
            coverage = str(args.get("coverage", "")).strip() or "unknown"
            return summary, coverage
        return "", "unknown"

    def _guide_source_hints(self, payloads: List[Dict[str, Any]]) -> str:
        return guide_source_hints(payloads)

    def _guide_line_preview(self, payloads: List[Dict[str, Any]]) -> str:
        return guide_line_preview(payloads)
