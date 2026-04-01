from pathlib import Path
from typing import Any, Dict, List

from .base import BaseTool, ToolError
from .common import ensure_in_workspace


DEFAULT_LIMIT = 80
MAX_LIMIT = 400
MAX_LINE_LENGTH = 1600
MAX_FILE_SIZE = 1024 * 1024


class CatTool(BaseTool):
    name = "cat"

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

    def spec(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Read a UTF-8 text file with line numbers. Use this only after you already know the exact "
                "workspace-relative file path. Supports paged reads via `offset` and `limit`."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative file path."},
                    "offset": {"type": "integer", "default": 0},
                    "limit": {"type": "integer", "default": DEFAULT_LIMIT},
                    "full": {
                        "type": "boolean",
                        "default": False,
                        "description": "If true, ignore offset/limit and read the whole file when it is within size limits.",
                    },
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
            limit = int(arguments.get("limit", DEFAULT_LIMIT) or DEFAULT_LIMIT)
        except (TypeError, ValueError):
            raise ToolError("`offset` and `limit` must be integers.")
        full = bool(arguments.get("full", False))

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
        if full and abs_path.stat().st_size > MAX_FILE_SIZE:
            raise ToolError("File is too large: {0}".format(rel_path))

        try:
            with abs_path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except UnicodeDecodeError:
            raise ToolError("File is not valid UTF-8: {0}".format(rel_path))
        except OSError as exc:
            raise ToolError("Unable to read file {0}: {1}".format(rel_path, exc))

        if full:
            offset = 0
            limit = len(lines)
            sliced = lines
        else:
            sliced = lines[offset : offset + limit]
        body = self._format_lines(sliced, start_line=offset + 1)
        parts = [
            '<file path="{0}" offset="{1}" limit="{2}">'.format(rel_path, offset, limit),
            body,
            "</file>",
        ]
        if not full and offset + limit < len(lines):
            parts.append("File has more lines. Use offset >= {0} to continue.".format(offset + limit))
        return "\n".join(part for part in parts if part)

    def _format_lines(self, lines: List[str], start_line: int) -> str:
        formatted = []
        for index, raw_line in enumerate(lines):
            line = raw_line.rstrip("\n").rstrip("\r")
            if len(line) > MAX_LINE_LENGTH:
                line = line[:MAX_LINE_LENGTH] + " ..."
            formatted.append("{0:>6}|{1}".format(start_line + index, line))
        return "\n".join(formatted)
