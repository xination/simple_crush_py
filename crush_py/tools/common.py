from pathlib import Path

from .base import ToolError


def ensure_in_workspace(workspace_root: Path, path: Path) -> None:
    try:
        path.relative_to(workspace_root)
    except ValueError:
        raise ToolError("Path is outside workspace root: {0}".format(path))
