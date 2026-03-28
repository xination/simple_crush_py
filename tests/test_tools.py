import tempfile
import unittest
from pathlib import Path

from crush_py.tools.base import ToolError
from crush_py.tools.edit import EditTool
from crush_py.tools.glob import GlobTool
from crush_py.tools.grep import GrepTool
from crush_py.tools.ls import LsTool
from crush_py.tools.view import ViewTool
from crush_py.tools.write import WriteTool


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workspace = Path(self.tempdir.name)
        (self.workspace / "src").mkdir()
        (self.workspace / "src" / "demo.py").write_text(
            "alpha\nbeta\nneedle here\n",
            encoding="utf-8",
        )
        (self.workspace / "notes.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")

    def test_view_reads_file_with_line_numbers_and_continuation_hint(self):
        tool = ViewTool(self.workspace)

        result = tool.run({"path": "notes.txt", "offset": 1, "limit": 1})

        self.assertIn("<file path=\"notes.txt\">", result)
        self.assertIn("     2|line2", result)
        self.assertIn("Use offset >= 2 to continue.", result)

    def test_ls_lists_files_and_directories(self):
        tool = LsTool(self.workspace)

        result = tool.run({"path": ".", "depth": 2})

        self.assertIn("- ./", result)
        self.assertIn("  - src/", result)
        self.assertIn("    - demo.py", result)
        self.assertIn("  - notes.txt", result)

    def test_glob_returns_matching_paths(self):
        tool = GlobTool(self.workspace)

        result = tool.run({"pattern": "**/*.py", "path": "."})

        self.assertEqual(result.strip(), "src/demo.py")

    def test_grep_returns_matching_lines(self):
        tool = GrepTool(self.workspace)

        result = tool.run({"pattern": "needle", "path": ".", "include": "*.py"})

        self.assertIn("src/demo.py:", result)
        self.assertIn("Line 3, Char 1: needle here", result)

    def test_write_requires_confirmation_then_writes_file(self):
        tool = WriteTool(self.workspace, ask_for_confirmation=True)

        with self.assertRaises(ToolError):
            tool.run({"path": "created.txt", "content": "hello"})

        result = tool.run({"path": "created.txt", "content": "hello", "confirm": True})

        self.assertEqual((self.workspace / "created.txt").read_text(encoding="utf-8"), "hello")
        self.assertIn("File written: created.txt (created)", result)

    def test_edit_requires_confirmation_then_replaces_text(self):
        tool = EditTool(self.workspace, ask_for_confirmation=True)

        with self.assertRaises(ToolError):
            tool.run(
                {
                    "path": "notes.txt",
                    "old_text": "line2",
                    "new_text": "LINE2",
                }
            )

        result = tool.run(
            {
                "path": "notes.txt",
                "old_text": "line2",
                "new_text": "LINE2",
                "confirm": True,
            }
        )

        self.assertIn("File edited: notes.txt (1 match)", result)
        self.assertEqual(
            (self.workspace / "notes.txt").read_text(encoding="utf-8"),
            "line1\nLINE2\nline3\n",
        )


if __name__ == "__main__":
    unittest.main()
