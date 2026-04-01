import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .base import BaseTool, ToolError
from .common import ensure_in_workspace, should_skip_path


MAX_MATCHES = 60
MAX_FILES = 12
MAX_MATCHES_PER_FILE = 3
MAX_OUTPUT_CHARS = 3500
MAX_LINE_LENGTH = 240
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
                "when you know a symbol, class name, function name, or phrase, but not the exact file. Output is "
                "intentionally capped for small-model context safety."
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

        matches, file_count = self._search(search_root, include, regex)
        if not matches:
            return "No matches found."

        lines = []
        current_file = None
        per_file_counts = OrderedDict()
        for rel_file, line_no, char_no, line_text in matches:
            per_file_counts.setdefault(rel_file, 0)
            per_file_counts[rel_file] += 1
            if rel_file != current_file:
                if current_file is not None:
                    lines.append("")
                current_file = rel_file
                lines.append("{0}:".format(rel_file))
            lines.append("  Line {0}, Char {1}: {2}".format(line_no, char_no, line_text))
        summary_bits = []
        if file_count >= MAX_FILES:
            summary_bits.append("file count reached the cap of {0}".format(MAX_FILES))
        if len(matches) >= MAX_MATCHES:
            summary_bits.append("match count reached the cap of {0}".format(MAX_MATCHES))
        output = "\n".join(lines)
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
            summary_bits.append("output size reached the small-model budget")
        if summary_bits:
            output += (
                "\n\nSearch was capped because {0}. Narrow the search by file extension, folder, or a more "
                "specific symbol before using `cat`.".format(", ".join(summary_bits))
            )
        return output

    def _search(self, root: Path, include: str, regex: re.Pattern) -> Tuple[List[Tuple[str, int, int, str]], int]:
        results = []
        files_with_matches = OrderedDict()
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
                file_match_count = 0
                with path.open("r", encoding="utf-8") as handle:
                    for line_no, raw_line in enumerate(handle, start=1):
                        match = regex.search(raw_line)
                        if not match:
                            continue
                        line_text = raw_line.rstrip("\n").rstrip("\r")
                        if len(line_text) > MAX_LINE_LENGTH:
                            line_text = line_text[:MAX_LINE_LENGTH] + "..."
                        files_with_matches.setdefault(rel_file, True)
                        results.append((rel_file, line_no, match.start() + 1, line_text))
                        file_match_count += 1
                        if len(files_with_matches) >= MAX_FILES:
                            return results, len(files_with_matches)
                        if file_match_count >= MAX_MATCHES_PER_FILE:
                            break
                        if len(results) >= MAX_MATCHES:
                            return results, len(files_with_matches)
            except (UnicodeDecodeError, OSError):
                continue
        return results, len(files_with_matches)
