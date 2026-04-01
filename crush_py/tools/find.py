from pathlib import Path
from typing import Any, Dict

from .base import BaseTool, ToolError
from .common import ensure_in_workspace, should_skip_path


MAX_RESULTS = 100


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
            matches.append(rel)
            if len(matches) >= MAX_RESULTS:
                break

        if not matches:
            return "No files found."
        output = "\n".join(matches)
        if len(matches) >= MAX_RESULTS:
            output += "\n\nResults truncated at {0} matches. Narrow the directory or pattern.".format(MAX_RESULTS)
        return output
