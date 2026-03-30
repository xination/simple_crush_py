import subprocess
from pathlib import Path
from typing import Any, Dict

from .base import BaseTool, ToolError
from .common import ensure_in_workspace


DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600
MAX_OUTPUT_CHARS = 20000


class BashTool(BaseTool):
    name = "bash"

    def __init__(
        self,
        workspace_root: Path,
        ask_for_confirmation: bool = True,
        default_timeout: int = DEFAULT_TIMEOUT,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.ask_for_confirmation = ask_for_confirmation
        self.default_timeout = int(default_timeout) if int(default_timeout) > 0 else DEFAULT_TIMEOUT

    def spec(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Run a shell command inside a workspace-relative directory. Use this only when read-only tools are "
                "not enough. Prefer `ls`, `glob`, `grep`, and `view` for repository inspection. Do not start `cwd` "
                "with `/`."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run with `/bin/bash -lc`.",
                    },
                    "cwd": {
                        "type": "string",
                        "default": ".",
                        "description": "Workspace-relative working directory. Use `.` for the workspace root.",
                    },
                    "timeout": {
                        "type": "integer",
                        "default": self.default_timeout,
                        "description": "Maximum runtime in seconds.",
                    },
                    "confirm": {
                        "type": "boolean",
                        "default": False,
                        "description": "Internal confirmation flag. The runtime sets this after user approval.",
                    },
                },
                "required": ["command"],
            },
        }

    def run(self, arguments: Dict[str, Any]) -> str:
        command = str(arguments.get("command", "")).strip()
        if not command:
            raise ToolError("`command` is required.")

        rel_cwd = str(arguments.get("cwd", ".")).strip() or "."
        abs_cwd = (self.workspace_root / rel_cwd).resolve()
        ensure_in_workspace(self.workspace_root, abs_cwd)
        if not abs_cwd.exists():
            raise ToolError("Path not found: {0}".format(rel_cwd))
        if not abs_cwd.is_dir():
            raise ToolError("Path is not a directory: {0}".format(rel_cwd))

        try:
            timeout = int(arguments.get("timeout", self.default_timeout) or self.default_timeout)
        except (TypeError, ValueError):
            raise ToolError("`timeout` must be an integer.")
        if timeout <= 0:
            timeout = self.default_timeout
        if timeout > MAX_TIMEOUT:
            timeout = MAX_TIMEOUT

        self._confirm(arguments, command)

        try:
            completed = subprocess.run(
                ["/bin/bash", "-lc", command],
                cwd=str(abs_cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if not isinstance(stdout, str):
                stdout = stdout.decode("utf-8", errors="replace")
            if not isinstance(stderr, str):
                stderr = stderr.decode("utf-8", errors="replace")
            stderr = (stderr + "\n" if stderr else "") + "Command timed out after {0} seconds.".format(timeout)
            exit_code = 124
        except OSError as exc:
            raise ToolError("Unable to run shell command: {0}".format(exc))

        return self._format_result(abs_cwd, command, exit_code, stdout, stderr)

    def _confirm(self, arguments: Dict[str, Any], command: str) -> None:
        if not self.ask_for_confirmation:
            return
        if arguments.get("confirm") is True:
            return
        raise ToolError(
            "Confirmation required to run shell command `{0}`. Re-run with confirmation.".format(command)
        )

    def _format_result(
        self,
        cwd: Path,
        command: str,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> str:
        return "\n".join(
            [
                "[command] {0}".format(command),
                "[cwd] {0}".format(cwd),
                "[exit_code] {0}".format(exit_code),
                "[stdout]",
                _trim_output(stdout),
                "",
                "[stderr]",
                _trim_output(stderr),
            ]
        )


def _trim_output(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
