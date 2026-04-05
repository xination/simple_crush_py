import re
from typing import Any, Dict, List, Tuple

from ..backends.base import BackendError, BaseBackend
from ..output_sanitize import sanitize_text
from .prompt_intent import detect_guide_output_mode, should_reread_guide_prompt
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
        common = (
            "Stay inside the named workspace doc.\n"
            "Explain for a beginner and keep claims grounded in the provided file excerpts.\n"
            "Always end with `Sources:` and include the file path plus line clues.\n"
            "Mention uncertainty if the doc excerpts are incomplete.\n"
        )
        if mode == "checklist":
            return common + (
                "Return this shape:\n"
                "Checklist:\n"
                "1. Step 1\n"
                "2. Step 2\n"
                "3. Step 3\n"
                "Success check: <what success looks like>\n"
                "Sources: <path and line clues>"
            )
        if mode == "troubleshooting":
            return common + (
                "Return this shape:\n"
                "Troubleshooting:\n"
                "- Likely current step: <step>\n"
                "- Relevant source section: <section or lines>\n"
                "- Possible causes: <plain-language causes>\n"
                "- What to check first: <first checks>\n"
                "- What to do next: <next action>\n"
                "Sources: <path and line clues>"
            )
        if mode == "learning_path":
            return common + (
                "Return this shape:\n"
                "Learning path:\n"
                "- Start here: <first thing to read in this file>\n"
                "- Read next: <next part>\n"
                "- Read later: <later part>\n"
                "- Why this order makes sense: <brief reason>\n"
                "Sources: <path and line clues>"
            )
        return common + (
            "Return this shape:\n"
            "Beginner summary:\n"
            "- Goal: <goal of the doc>\n"
            "- You will accomplish: <outcome>\n"
            "- Prepare first: <prerequisites>\n"
            "- Main steps: <short steps>\n"
            "- Common beginner confusion: <pitfalls>\n"
            "Sources: <path and line clues>"
        )

    def _finalize_direct_file_guide_output(
        self,
        prompt: str,
        rel_path: str,
        coverage: str,
        payloads: List[Dict[str, Any]],
        model_text: str,
    ) -> str:
        text = sanitize_text(model_text).strip()
        if not text:
            text = self._fallback_direct_file_guide_output(prompt, rel_path, payloads)
        if "sources:" not in text.lower():
            source_hint = self._guide_source_hints(payloads) or rel_path
            text = text.rstrip() + "\nSources: " + source_hint
        if coverage not in ("complete", "reused") and "partial file coverage" not in text.lower():
            text = "Preliminary guide (partial file coverage).\n" + text
        return text

    def _fallback_direct_file_guide_output(self, prompt: str, rel_path: str, payloads: List[Dict[str, Any]]) -> str:
        mode = self._guide_output_mode(prompt)
        line_preview = self._guide_line_preview(payloads)
        source_hint = self._guide_source_hints(payloads) or rel_path
        if mode == "checklist":
            return (
                "Checklist:\n"
                "1. Read the opening section to understand the goal and scope.\n"
                "2. Follow the procedure in order and do not skip prerequisites.\n"
                "3. Compare your result with the success cues mentioned in the doc.\n"
                "Success check: your outcome should match the documented examples or completion cues.\n"
                "Sources: {0}".format(source_hint)
            )
        if mode == "troubleshooting":
            return (
                "Troubleshooting:\n"
                "- Likely current step: review the most recent procedure step mentioned in the doc.\n"
                "- Relevant source section: {0}\n"
                "- Possible causes: a missed prerequisite, a skipped step, or a misunderstood term.\n"
                "- What to check first: compare your current state against the documented sequence.\n"
                "- What to do next: re-run the previous step carefully and confirm the expected result before moving on.\n"
                "Sources: {0}".format(source_hint)
            )
        if mode == "learning_path":
            return (
                "Learning path:\n"
                "- Start here: read the opening overview and purpose first.\n"
                "- Read next: move to the main procedure or usage section.\n"
                "- Read later: revisit details, warnings, and edge cases after the basics make sense.\n"
                "- Why this order makes sense: it builds context before details.\n"
                "Sources: {0}".format(source_hint)
            )
        return (
            "Beginner summary:\n"
            "- Goal: this doc explains a repo-local workflow or instruction set.\n"
            "- You will accomplish: understand the main task and the order of actions.\n"
            "- Prepare first: gather the prerequisites and read the opening context.\n"
            "- Main steps: follow the documented sequence and compare each step with the examples.\n"
            "- Common beginner confusion: hidden prerequisites or skipped assumptions can be easy to miss.\n"
            "- Doc glimpse: {0}\n"
            "Sources: {1}".format(line_preview or "see the cited lines for the exact wording", source_hint)
        )

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
        hints = []
        seen = set()
        for payload in payloads:
            if payload.get("tool_name") != "cat":
                continue
            content = str(payload.get("content", ""))
            match = re.search(r'<file path="([^"]+)"(?: offset="(\d+)")?(?: limit="(\d+)")?[^>]*>', content)
            if not match:
                continue
            path = match.group(1)
            start_line = int(match.group(2) or 0) + 1
            line_numbers = []
            for raw_line in content.splitlines():
                numbered = re.match(r"\s*(\d+)\|", raw_line)
                if numbered:
                    line_numbers.append(int(numbered.group(1)))
            end_line = max(line_numbers) if line_numbers else start_line
            hint = "{0}:{1}-{2}".format(path, start_line, end_line)
            if hint in seen:
                continue
            seen.add(hint)
            hints.append(hint)
        return "; ".join(hints[:3])

    def _guide_line_preview(self, payloads: List[Dict[str, Any]]) -> str:
        previews = []
        for payload in payloads:
            if payload.get("tool_name") != "cat":
                continue
            for raw_line in str(payload.get("content", "")).splitlines():
                if "|" not in raw_line or raw_line.startswith("<file") or raw_line.startswith("</file>"):
                    continue
                parts = raw_line.split("|", 1)
                previews.append(parts[1].strip())
                if len(previews) >= 2:
                    return " / ".join(previews)
        return ""


def _single_line(text: str, max_length: int = 160) -> str:
    normalized = " ".join(str(text).strip().split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + " ..."
