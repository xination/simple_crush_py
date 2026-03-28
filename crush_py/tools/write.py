from pathlib import Path
from typing import Any, Dict, Optional

from .base import BaseTool, ToolError
from .common import ensure_in_workspace


class WriteTool(BaseTool):
    name = "write"

    def __init__(self, workspace_root: Path, ask_for_confirmation: bool = True):
        self.workspace_root = Path(workspace_root).resolve()
        self.ask_for_confirmation = ask_for_confirmation

    def spec(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": "Write complete content to a file, replacing any existing content.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "confirm": {"type": "boolean", "default": False},
                },
                "required": ["path", "content"],
            },
        }

    def run(self, arguments: Dict[str, Any]) -> str:
        rel_path = str(arguments.get("path", "")).strip()
        if not rel_path:
            raise ToolError("`path` is required.")

        if "content" not in arguments:
            raise ToolError("`content` is required.")
        content = str(arguments.get("content"))

        abs_path = (self.workspace_root / rel_path).resolve()
        ensure_in_workspace(self.workspace_root, abs_path)

        if abs_path.exists() and abs_path.is_dir():
            raise ToolError("Path is a directory: {0}".format(rel_path))

        old_content: Optional[str] = None
        if abs_path.exists():
            try:
                old_content = abs_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raise ToolError("Existing file is not valid UTF-8: {0}".format(rel_path))
            except OSError as exc:
                raise ToolError("Unable to read existing file {0}: {1}".format(rel_path, exc))
            if old_content == content:
                return "No changes made. File already has the requested content."

        self._confirm(arguments, rel_path, old_content is not None)

        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise ToolError("Unable to write file {0}: {1}".format(rel_path, exc))

        if old_content is None:
            return "File written: {0} (created)".format(rel_path)
        return "File written: {0} (replaced entire content)".format(rel_path)

    def _confirm(self, arguments: Dict[str, Any], rel_path: str, existed: bool) -> None:
        if not self.ask_for_confirmation:
            return
        confirm = arguments.get("confirm", False)
        if confirm is True:
            return
        action = "overwrite" if existed else "create"
        raise ToolError(
            "Confirmation required to {0} `{1}`. Re-run with confirmation.".format(action, rel_path)
        )
