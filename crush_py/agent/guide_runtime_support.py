import re
from typing import Any, Dict, List

from ..output_sanitize import sanitize_text


def direct_file_guide_reader_instructions(mode: str) -> str:
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


def guide_source_hints(payloads: List[Dict[str, Any]]) -> str:
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


def guide_line_preview(payloads: List[Dict[str, Any]]) -> str:
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


def is_exact_guide_prompt(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(
        marker in lowered
        for marker in (
            "exact line",
            "exact lines",
            "which line",
            "what line",
            "哪一行",
            "哪些行",
            "精確行",
            "逐字",
            "原文",
        )
    )


def exact_guide_line_answer(prompt: str, rel_path: str, payloads: List[Dict[str, Any]], coverage: str) -> str:
    numbered_lines = _numbered_cat_lines(payloads)
    keywords = _prompt_keywords(prompt, rel_path)
    matched = []
    for line_number, line_text in numbered_lines:
        lowered = line_text.lower()
        if keywords and not any(keyword in lowered for keyword in keywords):
            continue
        matched.append((line_number, line_text.strip()))

    if not matched and numbered_lines:
        matched = numbered_lines[: min(3, len(numbered_lines))]

    if matched:
        text = "Exact line clues:\n" + "\n".join(
            "- {0}:{1} `{2}`".format(rel_path, line_number, line_text)
            for line_number, line_text in matched[:3]
        )
        line_numbers = [line_number for line_number, _ in matched[:3]]
        source_hint = "{0}:{1}-{2}".format(rel_path, min(line_numbers), max(line_numbers))
    else:
        text = "Exact line clues:\n- I could not confirm a matching line from the reviewed excerpts."
        source_hint = guide_source_hints(payloads) or rel_path

    text += "\nSources: " + source_hint
    if coverage not in ("complete", "reused"):
        text = "Preliminary guide (partial file coverage).\n" + text
    return text


def fallback_direct_file_guide_output(mode: str, rel_path: str, payloads: List[Dict[str, Any]]) -> str:
    line_preview = guide_line_preview(payloads)
    source_hint = guide_source_hints(payloads) or rel_path
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


def finalize_direct_file_guide_output(
    rel_path: str,
    coverage: str,
    payloads: List[Dict[str, Any]],
    model_text: str,
    fallback_text: str,
) -> str:
    text = sanitize_text(model_text).strip() or fallback_text
    if "sources:" not in text.lower():
        source_hint = guide_source_hints(payloads) or rel_path
        text = text.rstrip() + "\nSources: " + source_hint
    if coverage not in ("complete", "reused") and "partial file coverage" not in text.lower():
        text = "Preliminary guide (partial file coverage).\n" + text
    return text


def _numbered_cat_lines(payloads: List[Dict[str, Any]]) -> List[tuple]:
    rows = []
    for payload in payloads:
        if payload.get("tool_name") != "cat":
            continue
        for raw_line in str(payload.get("content", "")).splitlines():
            match = re.match(r"\s*(\d+)\|(.*)$", raw_line)
            if match:
                rows.append((int(match.group(1)), match.group(2).rstrip()))
    return rows


def _prompt_keywords(prompt: str, rel_path: str) -> List[str]:
    lowered = prompt.lower().replace(rel_path.lower(), " ")
    focused_match = re.search(r"(?:talk about|about|mention|關於|提到)\s+([a-z0-9_-]+)", lowered)
    if focused_match:
        return [focused_match.group(1)]
    if "setup" in lowered:
        return ["setup"]

    stopwords = {
        "guide",
        "mode",
        "user",
        "request",
        "which",
        "what",
        "exact",
        "lines",
        "line",
        "readme",
        "md",
        "talk",
        "about",
        "in",
        "the",
        "do",
        "does",
        "for",
        "a",
        "an",
        "and",
        "to",
        "please",
    }
    keywords = []
    for token in re.findall(r"[a-z0-9_/-]+", lowered):
        if len(token) <= 2 or token in stopwords or "/" in token:
            continue
        keywords.append(token)
    deduped = []
    seen = set()
    for keyword in keywords:
        if keyword in seen:
            continue
        seen.add(keyword)
        deduped.append(keyword)
    return deduped[:4]
