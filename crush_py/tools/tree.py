from pathlib import Path
from typing import Any, Dict, List

from .base import BaseTool, ToolError
from .common import ensure_in_workspace, should_skip_path


DEFAULT_DEPTH = 3
MAX_ENTRIES = 200


class TreeTool(BaseTool):
    name = "tree"

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

    def spec(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Show a compact directory tree under a workspace-relative path. Use this to understand the repo area "
                "before searching for symbols."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": ".", "description": "Workspace-relative directory path."},
                    "depth": {"type": "integer", "default": DEFAULT_DEPTH, "description": "Maximum tree depth."},
                },
            },
        }

    def run(self, arguments: Dict[str, Any]) -> str:
        rel_path = str(arguments.get("path", ".")).strip() or "."
        try:
            depth = int(arguments.get("depth", DEFAULT_DEPTH) or DEFAULT_DEPTH)
        except (TypeError, ValueError):
            raise ToolError("`depth` must be an integer.")
        if depth < 0:
            raise ToolError("`depth` must be >= 0.")

        root = (self.workspace_root / rel_path).resolve()
        ensure_in_workspace(self.workspace_root, root)
        if not root.exists():
            raise ToolError("Path not found: {0}".format(rel_path))
        if not root.is_dir():
            raise ToolError("Path is not a directory: {0}".format(rel_path))

        lines = [_display_root(rel_path) + "/"]
        counter = [0]
        truncated = self._walk(root, root, lines, 0, depth, counter)
        if truncated:
            lines.append("... Results truncated at {0} entries. Narrow the path.".format(MAX_ENTRIES))
        return "\n".join(lines)

    def _walk(self, search_root: Path, current: Path, lines: List[str], level: int, max_depth: int, counter: List[int]) -> bool:
        if level > max_depth:
            return False
        try:
            children = sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError as exc:
            raise ToolError("Unable to list directory {0}: {1}".format(current, exc))
        for child in children:
            if should_skip_path(self.workspace_root, search_root, child):
                continue
            counter[0] += 1
            if counter[0] > MAX_ENTRIES:
                return True
            marker = child.name + ("/" if child.is_dir() else "")
            lines.append("{0}{1}".format("  " * (level + 1), marker))
            if child.is_dir() and self._walk(search_root, child, lines, level + 1, max_depth, counter):
                return True
        return False


def _display_root(rel_path: str) -> str:
    return "." if rel_path in ("", ".") else rel_path.rstrip("/")
