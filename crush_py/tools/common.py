from pathlib import Path
from typing import Tuple

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


TEXT_ENCODING_CANDIDATES = ("utf-8", "utf-8-sig", "cp950", "big5", "latin-1")


def read_text_with_fallback(path: Path) -> Tuple[str, str]:
    last_error = None
    for encoding in TEXT_ENCODING_CANDIDATES:
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        except OSError as exc:
            raise ToolError("Unable to read file {0}: {1}".format(path, exc))
    raise ToolError("File could not be decoded with supported encodings: {0} ({1})".format(path, last_error))
