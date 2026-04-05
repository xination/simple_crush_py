import re
from typing import Any, Dict, List, Tuple

from ..backends.base import BaseBackend
from ..tools.base import ToolError
from .runtime_prompts import BASE_READ_HELPER_SYSTEM_PROMPT, READER_APPENDIX


SUMMARY_CHUNK_LIMIT = 400
MAX_SUMMARY_CHUNKS = 3
DIRECT_SUMMARY_CAT_CHAR_BUDGET = 1400


class SummaryRuntimeMixin:
    def _run_direct_file_summary_reader(self, session_id: str, backend: BaseBackend, prompt: str, rel_path: str, stream: bool = False) -> str:
        cat_payloads, coverage = self._collect_summary_file_reads(session_id, rel_path)
        cat_payloads = self._compact_reader_cat_payloads(cat_payloads)
        coverage_line = "Coverage: {0}".format(coverage)
        brief_summary_mode = self._is_brief_summary_prompt(prompt)
        request_instructions = self._direct_file_summary_reader_instructions(brief_summary_mode)
        conversation = [
            {
                "role": "user",
                "content": (
                    "User request: {0}\n"
                    "Target file: {1}\n"
                    "{2}\n"
                    "{3}"
                ).format(prompt.strip(), rel_path, coverage_line, request_instructions),
            },
            {"role": "user", "content": cat_payloads},
        ]
        final_text = self._generate_text_with_optional_streaming(
            backend,
            BASE_READ_HELPER_SYSTEM_PROMPT + READER_APPENDIX,
            conversation,
            stream=stream,
        )
        if coverage != "complete" and "Preliminary summary" not in final_text:
            final_text = "Preliminary summary (partial file coverage).\n" + final_text

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
                "tool_arguments": {"path": rel_path, "coverage": coverage, "mode": "summary"},
                "tool_use_id": "reader:{0}".format(rel_path),
                "summary": final_text,
            },
        )
        return final_text

    def _collect_summary_file_reads(self, session_id: str, rel_path: str) -> Tuple[List[Dict[str, Any]], str]:
        payloads: List[Dict[str, Any]] = []
        try:
            result = self._record_reader_cat_tool(session_id, {"path": rel_path, "full": True})
            payloads.append(
                {
                    "type": "tool_result",
                    "tool_use_id": "reader-cat-full:{0}".format(rel_path),
                    "tool_name": "cat",
                    "content": result,
                }
            )
            return payloads, "complete"
        except ToolError:
            offset = 0
            for _ in range(MAX_SUMMARY_CHUNKS):
                result = self._record_reader_cat_tool(
                    session_id,
                    {"path": rel_path, "offset": offset, "limit": SUMMARY_CHUNK_LIMIT},
                )
                payloads.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": "reader-cat-page:{0}:{1}".format(rel_path, offset),
                        "tool_name": "cat",
                        "content": result,
                    }
                )
                next_offset = _next_cat_offset(result)
                if next_offset is None:
                    return payloads, "complete"
                offset = next_offset
            return payloads, "partial"

    def _latest_reader_coverage(self, session_id: str, rel_path: str, allowed_modes: Tuple[str, ...] = ("summary", "")) -> str:
        for message in reversed(self.session_store.load_messages(session_id)):
            if not self._is_reader_summary_message(message):
                continue
            args = message.metadata.get("tool_arguments", {}) or message.metadata.get("args", {}) or {}
            if str(args.get("path", "")).strip() != rel_path:
                continue
            mode = str(args.get("mode", "")).strip()
            if allowed_modes and mode not in allowed_modes:
                continue
            coverage = str(args.get("coverage", "")).strip()
            if coverage:
                return coverage
        return "unknown"

    def _has_partial_reader_summary_for_path(self, session_id: str, rel_path: str) -> bool:
        coverage = self._latest_reader_coverage(session_id, rel_path)
        return bool(coverage and coverage not in ("complete", "unknown"))

    def _postprocess_direct_file_summary_output(self, session_id: str, prompt: str, text: str) -> str:
        return self._finalize_direct_file_summary_output(session_id, prompt, text)

    def _finalize_direct_file_summary_output(self, session_id: str, prompt: str, text: str) -> str:
        if not self._is_direct_file_summary_prompt(prompt):
            return text
        processed = text
        if self._is_brief_summary_prompt(prompt):
            processed = self._format_brief_direct_file_summary(processed)
        rel_path = self._prompt_direct_file_path(prompt)
        if not rel_path or not self._has_partial_reader_summary_for_path(session_id, rel_path):
            return processed
        if "Preliminary summary (partial file coverage)." in processed:
            return processed
        return "Preliminary summary (partial file coverage).\n" + processed

    def _is_direct_file_summary_prompt(self, prompt: str) -> bool:
        return self._prompt_intent(prompt).direct_file_summary

    def _is_brief_summary_prompt(self, prompt: str) -> bool:
        return self._prompt_intent(prompt).brief_summary

    def _direct_file_summary_reader_instructions(self, brief_summary_mode: bool) -> str:
        if brief_summary_mode:
            return (
                "Read only this file and give a brief summary.\n"
                "Return exactly 3 numbered points.\n"
                "Each point should be one sentence about a real file responsibility.\n"
                "No Evidence, Tag, Review note, Suggested keep, or Suggested review/remove sections.\n"
                "No intro or outro.\n"
                "If coverage is partial, start with `Preliminary summary (partial file coverage).`"
            )
        return (
            "Read only this file and produce a human-review draft.\n"
            "Return 4 to 6 candidate responsibilities.\n"
            "Each candidate needs an `Evidence:` line and a `Tag:` line.\n"
            "Use one tag: likely_core, likely_supporting, or likely_helper.\n"
            "Then add `Review note:`, `Suggested keep:`, and `Suggested review/remove:`.\n"
            "Do not claim these are final truth.\n"
            "If coverage is partial, start with `Preliminary summary (partial file coverage).`\n"
            "Format:\n"
            "Candidate responsibilities for human review:\n"
            "1. <candidate>\n"
            "   Evidence: <names or patterns>\n"
            "   Tag: likely_core / likely_supporting / likely_helper"
        )

    def _format_brief_direct_file_summary(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return stripped

        preliminary_label = ""
        body = stripped
        prefix = "Preliminary summary (partial file coverage)."
        if body.startswith(prefix):
            preliminary_label = prefix
            body = body[len(prefix):].strip()

        bullets = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line in ("Candidate responsibilities for human review:",):
                continue
            if line.startswith(("Evidence:", "Tag:", "Review note:", "Suggested keep:", "Suggested review/remove:")):
                continue
            if line.startswith("- "):
                continue
            match = re.match(r"^(\d+)\.\s*(.+)$", line)
            if match:
                bullets.append(match.group(2).strip())
                continue
            if bullets:
                continue
            bullets.append(line)

        if not bullets:
            return stripped

        formatted = "\n\n".join(
            "{0}. {1}".format(index, bullets[index - 1])
            for index in range(1, min(len(bullets), 3) + 1)
        )
        if preliminary_label:
            return preliminary_label + "\n" + formatted
        return formatted

    def _compact_reader_cat_payloads(self, payloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        compacted = []
        for payload in payloads:
            compact_payload = dict(payload)
            if payload.get("tool_name") == "cat":
                compact_payload["content"] = self._compact_reader_cat_content(str(payload.get("content", "")))
            compacted.append(compact_payload)
        return compacted

    def _compact_reader_cat_content(self, content: str) -> str:
        if len(content) <= DIRECT_SUMMARY_CAT_CHAR_BUDGET:
            return content

        lines = content.splitlines()
        if not lines:
            return content

        open_tag = lines[0] if lines and lines[0].startswith("<file ") else ""
        close_tag = ""
        continuation = ""
        body = []
        for line in lines[1:]:
            if line == "</file>":
                close_tag = line
                continue
            if line.startswith("File has more lines."):
                continuation = line
                continue
            body.append(line)

        budget = max(300, DIRECT_SUMMARY_CAT_CHAR_BUDGET - len(open_tag) - len(close_tag) - len(continuation) - 16)
        front = []
        back = []
        front_used = 0
        back_used = 0
        front_index = 0
        back_index = len(body) - 1

        while front_index <= back_index:
            take_front = front_used <= back_used
            candidate = body[front_index] if take_front else body[back_index]
            line_cost = len(candidate) + 1
            if front_used + back_used + line_cost > budget:
                break
            if take_front:
                front.append(candidate)
                front_used += line_cost
                front_index += 1
            else:
                back.append(candidate)
                back_used += line_cost
                back_index -= 1

        omitted = len(body) - len(front) - len(back)
        parts = []
        if open_tag:
            parts.append(open_tag)
        parts.extend(front)
        if omitted > 0:
            parts.append("...[{0} lines omitted for summary context]".format(omitted))
        parts.extend(reversed(back))
        if close_tag:
            parts.append(close_tag)
        if continuation:
            parts.append(continuation)
        return "\n".join(parts)


def _single_line(text: str, max_length: int = 160) -> str:
    normalized = " ".join(str(text).strip().split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + " ..."


def _next_cat_offset(result: str):
    match = re.search(r"Use offset >= (\d+) to continue\.", result)
    if not match:
        return None
    return int(match.group(1))
