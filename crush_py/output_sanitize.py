import re
from typing import Any


_CONTROL_TOKEN_RE = re.compile(r"<\|tool_(?:call|response)\|>|<tool_(?:call|response)\|>|<\|\"\|>")
_LEAKED_CALL_RE = re.compile(
    r"call:(?:unknown_tool|crush_py:[A-Za-z_][A-Za-z0-9_-]*)\{.*?\}(?=(?:<\|tool_(?:call|response)\|>|<tool_(?:call|response)\|>|$))",
    flags=re.DOTALL,
)
_HEADER_MARKERS = (
    "Variable trace for human review:",
    "Flow trace for human review:",
    "Candidate responsibilities for human review:",
    "Confirmed path:",
    "Summary:",
)


def sanitize_text(text: Any) -> str:
    if text is None:
        return ""
    cleaned = str(text)
    cleaned = _trim_to_human_readable_start(cleaned)
    previous = None
    while cleaned != previous:
        previous = cleaned
        cleaned = _LEAKED_CALL_RE.sub("", cleaned)
        cleaned = _CONTROL_TOKEN_RE.sub("", cleaned)
    return cleaned.strip()


def sanitize_content(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [sanitize_content(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_content(item) for item in value)
    if isinstance(value, dict):
        return {key: sanitize_content(item) for key, item in value.items()}
    return value


def _trim_to_human_readable_start(text: str) -> str:
    for marker in _HEADER_MARKERS:
        index = text.find(marker)
        if index > 0 and _looks_like_leaked_prefix(text[:index]):
            return text[index:]
    return text


def _looks_like_leaked_prefix(prefix: str) -> bool:
    if not prefix.strip():
        return False
    return "<|" in prefix or "call:unknown_tool" in prefix or "call:crush_py:" in prefix
