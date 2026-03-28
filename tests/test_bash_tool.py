import tempfile
import unittest
from pathlib import Path

from crush_py.tools.bash import BashTool
from crush_py.tools.base import ToolError


class BashToolTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workspace = Path(self.tempdir.name)
        (self.workspace / "nested").mkdir()

    def test_requires_confirmation(self):
        tool = BashTool(self.workspace, ask_for_confirmation=True)

        with self.assertRaises(ToolError):
            tool.run({"command": "printf hello"})

    def test_runs_command_and_captures_stdout(self):
        tool = BashTool(self.workspace, ask_for_confirmation=True)

        result = tool.run({"command": "printf hello", "confirm": True})

        self.assertIn("[exit_code] 0", result)
        self.assertIn("[stdout]\nhello", result)

    def test_rejects_cwd_outside_workspace(self):
        tool = BashTool(self.workspace, ask_for_confirmation=False)

        with self.assertRaises(ToolError):
            tool.run({"command": "pwd", "cwd": "../outside"})

    def test_returns_non_zero_exit_code_and_stderr(self):
        tool = BashTool(self.workspace, ask_for_confirmation=False)

        result = tool.run(
            {
                "command": "printf error >&2; exit 7",
            }
        )

        self.assertIn("[exit_code] 7", result)
        self.assertIn("[stderr]\nerror", result)

    def test_times_out_long_running_command(self):
        tool = BashTool(self.workspace, ask_for_confirmation=False)

        result = tool.run(
            {
                "command": "sleep 2",
                "timeout": 1,
            }
        )

        self.assertIn("[exit_code] 124", result)
        self.assertIn("Command timed out after 1 seconds.", result)


if __name__ == "__main__":
    unittest.main()
