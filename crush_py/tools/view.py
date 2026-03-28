from pathlib import Path
from typing import Any, Dict, List

from .base import BaseTool, ToolError
from .common import ensure_in_workspace


DEFAULT_LIMIT = 200
MAX_LIMIT = 2000
MAX_LINE_LENGTH = 2000
MAX_FILE_SIZE = 1024 * 1024


class ViewTool(BaseTool):
    name = "view"

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

    def spec(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": "Read a UTF-8 text file with line numbers.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "default": 0},
                    "limit": {"type": "integer", "default": DEFAULT_LIMIT},
                },
                "required": ["path"],
            },
        }

    def run(self, arguments: Dict[str, Any]) -> str:
        rel_path = str(arguments.get("path", "")).strip()
        if not rel_path:
            raise ToolError("`path` is required.")

        try:
            offset = int(arguments.get("offset", 0) or 0)
        except (TypeError, ValueError):
            raise ToolError("`offset` must be an integer. Example: /view FILE 200 50")

        try:
            limit = int(arguments.get("limit", DEFAULT_LIMIT) or DEFAULT_LIMIT)
        except (TypeError, ValueError):
            raise ToolError("`limit` must be an integer. Example: /view FILE 200 50")

        if offset < 0:
            raise ToolError("`offset` must be >= 0.")
        if limit <= 0:
            limit = DEFAULT_LIMIT
        if limit > MAX_LIMIT:
            limit = MAX_LIMIT

        abs_path = (self.workspace_root / rel_path).resolve()
        ensure_in_workspace(self.workspace_root, abs_path)

        if not abs_path.exists():
            raise ToolError("File not found: {0}".format(rel_path))
        if abs_path.is_dir():
            raise ToolError("Path is a directory: {0}".format(rel_path))
        if abs_path.stat().st_size > MAX_FILE_SIZE:
            raise ToolError("File is too large: {0}".format(rel_path))

        try:
            with abs_path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except UnicodeDecodeError:
            raise ToolError("File is not valid UTF-8: {0}".format(rel_path))
        except OSError as exc:
            raise ToolError("Unable to read file {0}: {1}".format(rel_path, exc))

        sliced = lines[offset : offset + limit]
        has_more = offset + limit < len(lines)
        body = self._format_lines(sliced, start_line=offset + 1)
        parts = ["<file path=\"{0}\">".format(rel_path), body, "</file>"]
        if has_more:
            parts.append(
                "File has more lines. Use offset >= {0} to continue.".format(offset + limit)
            )
        return "\n".join(part for part in parts if part)

    def _format_lines(self, lines: List[str], start_line: int) -> str:
        formatted = []
        for index, raw_line in enumerate(lines):
            line = raw_line.rstrip("\n").rstrip("\r")
            if len(line) > MAX_LINE_LENGTH:
                line = line[:MAX_LINE_LENGTH] + " ..."
            formatted.append("{0:>6}|{1}".format(start_line + index, line))
        return "\n".join(formatted)
