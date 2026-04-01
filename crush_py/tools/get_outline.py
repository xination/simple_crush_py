import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .base import BaseTool, ToolError
from .common import ensure_in_workspace


DEFAULT_MAX_ITEMS = 80
MAX_FILE_SIZE = 1024 * 1024
SUPPORTED_SUFFIXES = {".py", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".hxx"}


class GetOutlineTool(BaseTool):
    name = "get_outline"

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()

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
        if not rel_path:
            raise ToolError("`path` is required.")

        try:
            max_items = int(arguments.get("max_items", DEFAULT_MAX_ITEMS) or DEFAULT_MAX_ITEMS)
        except (TypeError, ValueError):
            raise ToolError("`max_items` must be an integer.")
        if max_items <= 0:
            max_items = DEFAULT_MAX_ITEMS

        abs_path = (self.workspace_root / rel_path).resolve()
        ensure_in_workspace(self.workspace_root, abs_path)
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

        suffix = abs_path.suffix.lower()
        if suffix == ".py":
            items = self._python_outline(text)
        else:
            items = self._cpp_outline(text)

        if not items:
            return "No outline symbols found in {0}.".format(rel_path)

        lines = ['<outline path="{0}">'.format(rel_path)]
        for line_no, label in items[:max_items]:
            lines.append("{0:>6}|{1}".format(line_no, label))
        lines.append("</outline>")
        if len(items) > max_items:
            lines.append("Outline truncated at {0} items. Use `cat` for details.".format(max_items))
        return "\n".join(lines)

    def _python_outline(self, text: str) -> List[Tuple[int, str]]:
        items = []
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            stripped = raw_line.lstrip()
            indent = len(raw_line) - len(stripped)
            prefix = "  " * (indent // 4)
            if stripped.startswith("class "):
                match = re.match(r"class\s+([A-Za-z_][A-Za-z0-9_]*)", stripped)
                if match:
                    items.append((line_no, "{0}class {1}".format(prefix, match.group(1))))
            elif stripped.startswith("def ") or stripped.startswith("async def "):
                match = re.match(r"(async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)", stripped)
                if match:
                    signature = _trim_signature("{0} {1}({2}".format(match.group(1), match.group(2), match.group(3)))
                    items.append((line_no, "{0}{1}".format(prefix, signature)))
        return items

    def _cpp_outline(self, text: str) -> List[Tuple[int, str]]:
        items = []
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(("//", "/*", "*", "#")):
                continue
            if re.match(r"(class|struct|enum)\s+[A-Za-z_][A-Za-z0-9_:<>,]*", stripped):
                items.append((line_no, _trim_signature(stripped.rstrip("{").strip())))
                continue
            if "(" not in stripped or ")" not in stripped:
                continue
            if stripped.endswith(";") or stripped.endswith("{"):
                if "::" in stripped or re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(", stripped):
                    items.append((line_no, _trim_signature(stripped.rstrip("{").rstrip(";").strip())))
        return items


def _trim_signature(text: str, max_length: int = 120) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + " ..."
