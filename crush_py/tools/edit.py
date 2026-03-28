from pathlib import Path
from typing import Any, Dict

from .base import BaseTool, ToolError
from .common import ensure_in_workspace


class EditTool(BaseTool):
    name = "edit"

    def __init__(self, workspace_root: Path, ask_for_confirmation: bool = True):
        self.workspace_root = Path(workspace_root).resolve()
        self.ask_for_confirmation = ask_for_confirmation

    def spec(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": "Edit a file by replacing one text block with another.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                    "confirm": {"type": "boolean", "default": False},
                },
                "required": ["path", "old_text", "new_text"],
            },
        }

    def run(self, arguments: Dict[str, Any]) -> str:
        rel_path = str(arguments.get("path", "")).strip()
        if not rel_path:
            raise ToolError("`path` is required.")

        if "old_text" not in arguments:
            raise ToolError("`old_text` is required.")
        if "new_text" not in arguments:
            raise ToolError("`new_text` is required.")

        old_text = str(arguments.get("old_text"))
        new_text = str(arguments.get("new_text"))
        replace_all = bool(arguments.get("replace_all", False))

        abs_path = (self.workspace_root / rel_path).resolve()
        ensure_in_workspace(self.workspace_root, abs_path)

        if not abs_path.exists():
            raise ToolError("File not found: {0}".format(rel_path))
        if abs_path.is_dir():
            raise ToolError("Path is a directory: {0}".format(rel_path))

        try:
            content = abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolError("File is not valid UTF-8: {0}".format(rel_path))
        except OSError as exc:
            raise ToolError("Unable to read file {0}: {1}".format(rel_path, exc))

        if old_text == "":
            raise ToolError("`old_text` must not be empty for edit.")

        count = content.count(old_text)
        if count == 0:
            raise ToolError("`old_text` was not found in the file.")
        if not replace_all and count > 1:
            raise ToolError(
                "`old_text` appears multiple times. Provide more context or set replace_all=true."
            )

        updated = content.replace(old_text, new_text) if replace_all else content.replace(old_text, new_text, 1)
        if updated == content:
            return "No changes made."

        self._confirm(arguments, rel_path)

        try:
            abs_path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            raise ToolError("Unable to write file {0}: {1}".format(rel_path, exc))

        replaced = "all matches" if replace_all else "1 match"
        return "File edited: {0} ({1})".format(rel_path, replaced)

    def _confirm(self, arguments: Dict[str, Any], rel_path: str) -> None:
        if not self.ask_for_confirmation:
            return
        confirm = arguments.get("confirm", False)
        if confirm is True:
            return
        raise ToolError(
            "Confirmation required to edit `{0}`. Re-run with confirmation.".format(rel_path)
        )
