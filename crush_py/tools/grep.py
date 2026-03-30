import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .base import BaseTool, ToolError
from .common import ensure_in_workspace, should_skip_path


MAX_MATCHES = 200
MAX_LINE_LENGTH = 500
DEFAULT_INCLUDE = "*"


class GrepTool(BaseTool):
    name = "grep"

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

    def spec(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Search file contents under a workspace-relative directory using regex or literal text. Use this "
                "when you know a symbol, class name, function name, or phrase, but not the exact file. Prefer "
                "`grep` to find candidate files, then `view` to read the best match. Do not start paths with `/`. "
                "Noise directories like `.crush_py`, `.codex`, caches, and `tests` are skipped by default unless "
                "you explicitly search inside them."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern, or exact text when `literal_text=true`.",
                    },
                    "path": {
                        "type": "string",
                        "default": ".",
                        "description": "Workspace-relative directory path that bounds the search.",
                    },
                    "include": {
                        "type": "string",
                        "default": DEFAULT_INCLUDE,
                        "description": "Optional glob filter for filenames, such as `*.py`.",
                    },
                    "literal_text": {
                        "type": "boolean",
                        "default": False,
                        "description": "Set true to match the pattern as plain text instead of regex.",
                    },
                },
                "required": ["pattern"],
            },
        }

    def run(self, arguments: Dict[str, Any]) -> str:
        pattern = str(arguments.get("pattern", "")).strip()
        if not pattern:
            raise ToolError("`pattern` is required. Example: /grep 'SessionStore'")

        rel_path = str(arguments.get("path", ".")).strip() or "."
        include = str(arguments.get("include", DEFAULT_INCLUDE)).strip() or DEFAULT_INCLUDE
        literal_text = bool(arguments.get("literal_text", False))

        search_root = (self.workspace_root / rel_path).resolve()
        ensure_in_workspace(self.workspace_root, search_root)

        if not search_root.exists():
            raise ToolError("Path not found: {0}".format(rel_path))
        if not search_root.is_dir():
            raise ToolError("Path is not a directory: {0}".format(rel_path))

        search_pattern = re.escape(pattern) if literal_text else pattern
        try:
            regex = re.compile(search_pattern)
        except re.error as exc:
            raise ToolError("Invalid regex pattern: {0}".format(exc))

        matches = self._search(search_root, include, regex)
        if not matches:
            return "No matches found."

        lines = []
        current_file = None
        truncated = len(matches) >= MAX_MATCHES
        for rel_file, line_no, char_no, line_text in matches:
            if rel_file != current_file:
                if current_file is not None:
                    lines.append("")
                current_file = rel_file
                lines.append("{0}:".format(rel_file))
            lines.append("  Line {0}, Char {1}: {2}".format(line_no, char_no, line_text))
        if truncated:
            lines.append("")
            lines.append("Results truncated at {0} matches.".format(MAX_MATCHES))
        return "\n".join(lines)

    def _search(self, root: Path, include: str, regex: re.Pattern) -> List[Tuple[str, int, int, str]]:
        results = []
        for path in sorted(root.rglob(include)):
            if should_skip_path(self.workspace_root, root, path):
                continue
            if not path.is_file():
                continue
            try:
                rel_file = path.relative_to(self.workspace_root).as_posix()
            except ValueError:
                continue
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line_no, raw_line in enumerate(handle, start=1):
                        match = regex.search(raw_line)
                        if not match:
                            continue
                        line_text = raw_line.rstrip("\n").rstrip("\r")
                        if len(line_text) > MAX_LINE_LENGTH:
                            line_text = line_text[:MAX_LINE_LENGTH] + "..."
                        results.append((rel_file, line_no, match.start() + 1, line_text))
                        if len(results) >= MAX_MATCHES:
                            return results
            except (UnicodeDecodeError, OSError):
                continue
        return results
