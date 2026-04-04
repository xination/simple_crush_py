from pathlib import Path
from typing import Any, Dict, List

from .base import BaseTool, ToolError
from .common import ensure_in_workspace
from .outline_providers import OutlineSymbol, SUPPORTED_SUFFIXES, default_outline_provider_chain


DEFAULT_MAX_ITEMS = 80
MAX_FILE_SIZE = 1024 * 1024


class GetOutlineTool(BaseTool):
    name = "get_outline"

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.provider_chain = default_outline_provider_chain()

    def spec(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": "Return a compact symbol outline for one code file before using `cat`.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative file path."},
                    "max_items": {"type": "integer", "default": DEFAULT_MAX_ITEMS},
                },
                "required": ["path"],
            },
        }

    def run(self, arguments: Dict[str, Any]) -> str:
        rel_path = str(arguments.get("path", "")).strip()
        try:
            max_items = int(arguments.get("max_items", DEFAULT_MAX_ITEMS) or DEFAULT_MAX_ITEMS)
        except (TypeError, ValueError):
            raise ToolError("`max_items` must be an integer.")
        if max_items <= 0:
            max_items = DEFAULT_MAX_ITEMS
        items = load_outline_symbols(self.workspace_root, rel_path, self.provider_chain)
        if not items:
            return "No outline symbols found in {0}.".format(rel_path)

        lines = ['<outline path="{0}">'.format(rel_path)]
        for symbol in items[:max_items]:
            lines.append("{0:>6}|{1}".format(symbol.start_line, symbol.display))
        lines.append("</outline>")
        if len(items) > max_items:
            lines.append("Outline truncated at {0} items. Use `cat` for details.".format(max_items))
        return "\n".join(lines)


def load_outline_symbols(
    workspace_root: Path,
    rel_path: str,
    provider_chain=None,
) -> List[OutlineSymbol]:
    rel_path = str(rel_path or "").strip()
    if not rel_path:
        raise ToolError("`path` is required.")

    workspace_root = Path(workspace_root).resolve()
    abs_path = (workspace_root / rel_path).resolve()
    ensure_in_workspace(workspace_root, abs_path)
    if not abs_path.exists():
        raise ToolError("File not found: {0}".format(rel_path))
    if abs_path.is_dir():
        raise ToolError("Path is a directory: {0}".format(rel_path))
    if abs_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ToolError("Outline is only supported for Python/C/C++ source files: {0}".format(rel_path))
    if abs_path.stat().st_size > MAX_FILE_SIZE:
        raise ToolError("File is too large: {0}".format(rel_path))

    try:
        text = abs_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ToolError("File is not valid UTF-8: {0}".format(rel_path))
    except OSError as exc:
        raise ToolError("Unable to read file {0}: {1}".format(rel_path, exc))

    active_chain = provider_chain or default_outline_provider_chain()
    return active_chain.extract(text, abs_path)
