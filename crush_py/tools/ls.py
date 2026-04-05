from pathlib import Path
from typing import Any, Dict, List

from .base import BaseTool, ToolError
from .common import ensure_in_workspace, should_skip_path


DEFAULT_DEPTH = 2
MAX_ENTRIES = 80


class LsTool(BaseTool):
    name = "ls"

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

    def spec(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "List files and directories under a workspace-relative directory path. Use this for a quick, "
                "shallow view of an area before using `find`, `grep`, or `cat`."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "default": ".",
                        "description": "Workspace-relative directory path. Use `.` for the workspace root.",
                    },
                    "depth": {
                        "type": "integer",
                        "default": DEFAULT_DEPTH,
                        "description": "How many nested directory levels to include.",
                    },
                },
            },
        }

    def run(self, arguments: Dict[str, Any]) -> str:
        rel_path = str(arguments.get("path", ".")).strip() or "."
        try:
            depth = int(arguments.get("depth", DEFAULT_DEPTH) or DEFAULT_DEPTH)
        except (TypeError, ValueError):
            raise ToolError("`depth` must be an integer. Example: /ls src 3")
        if depth < 0:
            raise ToolError("`depth` must be >= 0.")

        root = (self.workspace_root / rel_path).resolve()
        ensure_in_workspace(self.workspace_root, root)

        if not root.exists():
            raise ToolError("Path not found: {0}".format(rel_path))
        if not root.is_dir():
            raise ToolError("Path is not a directory: {0}".format(rel_path))

        lines = ["- {0}/".format(_display_root(rel_path))]
        counter = [0]
        truncated = self._walk(root, root, lines, level=1, max_depth=depth, counter=counter)
        if truncated:
            lines.append("")
            lines.append("Results truncated at {0} entries. Narrow the path or reduce depth.".format(MAX_ENTRIES))
        return "\n".join(lines)

    def _walk(self, search_root: Path, current: Path, lines: List[str], level: int, max_depth: int, counter: List[int]) -> bool:
        if level > max_depth + 1:
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
            try:
                rel = child.relative_to(self.workspace_root).as_posix()
            except ValueError:
                continue
            name = rel + ("/" if child.is_dir() else "")
            lines.append("{0}- {1}".format("  " * level, name))
            if child.is_dir() and level <= max_depth:
                if self._walk(search_root, child, lines, level + 1, max_depth, counter):
                    return True
        return False


def _display_root(rel_path: str) -> str:
    if rel_path in ("", "."):
        return "."
    return rel_path.rstrip("/")
