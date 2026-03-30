from pathlib import Path

from .base import ToolError

DEFAULT_IGNORED_DIR_NAMES = (
    ".codex",
    ".crush_py",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "tests",
    "venv",
)


def ensure_in_workspace(workspace_root: Path, path: Path) -> None:
    try:
        path.relative_to(workspace_root)
    except ValueError:
        raise ToolError("Path is outside workspace root: {0}".format(path))


def should_skip_path(workspace_root: Path, search_root: Path, path: Path) -> bool:
    try:
        root_parts = search_root.relative_to(workspace_root).parts
        path_parts = path.relative_to(workspace_root).parts
    except ValueError:
        return True

    if len(path_parts) <= len(root_parts):
        return False

    descendant_parts = path_parts[len(root_parts) :]
    return any(part in DEFAULT_IGNORED_DIR_NAMES for part in descendant_parts)
