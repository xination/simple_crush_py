from pathlib import Path
from typing import Any, Dict

from .base import BaseTool, ToolError
from .common import ensure_in_workspace, should_skip_path


MAX_RESULTS = 100
MAX_FUZZY_GAP_COST = 1
ANSI_RED = "\033[31m"
ANSI_RESET = "\033[0m"


class FindTool(BaseTool):
    name = "find"

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

    def spec(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Find files by filename or path pattern under a workspace-relative directory. Use this when you "
                "know the file name shape but not the exact location."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Pattern such as `*.py` or `*session*`."},
                    "path": {"type": "string", "default": ".", "description": "Workspace-relative search root."},
                },
                "required": ["pattern"],
            },
        }

    def run(self, arguments: Dict[str, Any]) -> str:
        pattern = str(arguments.get("pattern", "")).strip()
        if not pattern:
            raise ToolError("`pattern` is required. Example: /find '*.py'")

        rel_path = str(arguments.get("path", ".")).strip() or "."
        search_root = (self.workspace_root / rel_path).resolve()
        ensure_in_workspace(self.workspace_root, search_root)
        if not search_root.exists():
            raise ToolError("Path not found: {0}".format(rel_path))
        if not search_root.is_dir():
            raise ToolError("Path is not a directory: {0}".format(rel_path))

        matches = []
        for path in sorted(search_root.rglob(pattern)):
            if should_skip_path(self.workspace_root, search_root, path):
                continue
            if not path.exists():
                continue
            rel = path.relative_to(self.workspace_root).as_posix()
            if path.is_dir():
                rel += "/"
            matches.append(_highlight_contiguous_match(rel, pattern))
            if len(matches) >= MAX_RESULTS:
                break

        if not matches and not any(char in pattern for char in "*?[]"):
            matches = self._fuzzy_matches(search_root, pattern)

        if not matches:
            return "No files found."
        output = "\n".join(matches)
        if len(matches) >= MAX_RESULTS:
            output += "\n\nResults truncated at {0} matches. Narrow the directory or pattern.".format(MAX_RESULTS)
        return output

    def _fuzzy_matches(self, search_root: Path, pattern: str):
        needle = pattern.strip().lower()
        if not needle:
            return []

        scored = []
        for path in sorted(search_root.rglob("*")):
            if should_skip_path(self.workspace_root, search_root, path):
                continue
            if not path.exists():
                continue
            try:
                rel = path.relative_to(self.workspace_root).as_posix()
            except ValueError:
                continue
            haystacks = [path.name.lower(), rel.lower()]
            score = self._best_fuzzy_score(needle, haystacks)
            if score is None:
                continue
            if path.is_dir():
                rel += "/"
            scored.append((score, len(rel), rel, _highlight_fuzzy_match(rel, needle)))

        scored.sort(key=lambda item: (item[0], item[1], item[2]))
        return [highlighted for _, _, _, highlighted in scored[:MAX_RESULTS]]

    def _best_fuzzy_score(self, needle: str, haystacks):
        best_score = None
        for haystack in haystacks:
            score = _subsequence_score(needle, haystack)
            if score is None:
                continue
            if best_score is None or score < best_score:
                best_score = score
        return best_score


def _subsequence_score(needle: str, haystack: str):
    position = -1
    gap_cost = 0
    start_index = None
    for char in needle:
        next_position = haystack.find(char, position + 1)
        if next_position < 0:
            return None
        if start_index is None:
            start_index = next_position
        if position >= 0:
            gap_cost += next_position - position - 1
        position = next_position
    if gap_cost > MAX_FUZZY_GAP_COST:
        return None
    return gap_cost * 10 + (start_index or 0)


def _highlight_contiguous_match(text: str, pattern: str) -> str:
    needle = pattern.strip()
    if not needle or any(char in needle for char in "*?[]"):
        return text
    lowered_text = text.lower()
    lowered_needle = needle.lower()
    start = lowered_text.find(lowered_needle)
    if start < 0:
        return text
    end = start + len(needle)
    return text[:start] + ANSI_RED + text[start:end] + ANSI_RESET + text[end:]


def _highlight_fuzzy_match(text: str, needle: str) -> str:
    if not needle:
        return text
    lowered_text = text.lower()
    positions = []
    index = -1
    for char in needle:
        index = lowered_text.find(char, index + 1)
        if index < 0:
            return text
        positions.append(index)

    parts = []
    position_set = set(positions)
    in_highlight = False
    for idx, char in enumerate(text):
        if idx in position_set and not in_highlight:
            parts.append(ANSI_RED)
            in_highlight = True
        if idx not in position_set and in_highlight:
            parts.append(ANSI_RESET)
            in_highlight = False
        parts.append(char)
    if in_highlight:
        parts.append(ANSI_RESET)
    return "".join(parts)
