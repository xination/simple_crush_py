import tempfile
import unittest
from pathlib import Path

from crush_py.tools.base import ToolError
from crush_py.tools.edit import EditTool
from crush_py.tools.glob import GlobTool
from crush_py.tools.grep import GrepTool
from crush_py.tools.ls import LsTool
from crush_py.tools.registry import ToolRegistry
from crush_py.tools.view import ViewTool
from crush_py.tools.write import WriteTool
from crush_py.config import AppConfig, BackendConfig


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workspace = Path(self.tempdir.name)
        (self.workspace / "src").mkdir()
        (self.workspace / "tests").mkdir()
        (self.workspace / ".crush_py").mkdir()
        (self.workspace / ".pytest_cache").mkdir()
        (self.workspace / "src" / "demo.py").write_text(
            "alpha\nbeta\nneedle here\n",
            encoding="utf-8",
        )
        (self.workspace / "tests" / "test_demo.py").write_text(
            "needle in tests\n",
            encoding="utf-8",
        )
        (self.workspace / ".crush_py" / "session.json").write_text(
            "needle in session state\n",
            encoding="utf-8",
        )
        (self.workspace / ".pytest_cache" / "cache.txt").write_text(
            "needle in cache\n",
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
        self.assertNotIn(".crush_py/", result)
        self.assertNotIn(".pytest_cache/", result)
        self.assertNotIn("tests/", result)

    def test_glob_returns_matching_paths(self):
        tool = GlobTool(self.workspace)

        result = tool.run({"pattern": "**/*.py", "path": "."})

        self.assertEqual(result.strip(), "src/demo.py")

    def test_grep_returns_matching_lines(self):
        tool = GrepTool(self.workspace)

        result = tool.run({"pattern": "needle", "path": ".", "include": "*.py"})

        self.assertIn("src/demo.py:", result)
        self.assertIn("Line 3, Char 1: needle here", result)
        self.assertNotIn("tests/test_demo.py", result)

    def test_explicit_test_paths_are_still_searchable(self):
        tool = GrepTool(self.workspace)

        result = tool.run({"pattern": "needle", "path": "tests", "include": "*.py"})

        self.assertIn("tests/test_demo.py:", result)

    def test_glob_skips_default_noise_directories(self):
        tool = GlobTool(self.workspace)

        result = tool.run({"pattern": "**/*", "path": "."})

        self.assertIn("notes.txt", result)
        self.assertIn("src/", result)
        self.assertNotIn(".crush_py/", result)
        self.assertNotIn(".pytest_cache/", result)
        self.assertNotIn("tests/", result)

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

    def test_tool_specs_explain_relative_paths_and_search_strategy(self):
        ls_spec = LsTool(self.workspace).spec()
        glob_spec = GlobTool(self.workspace).spec()
        grep_spec = GrepTool(self.workspace).spec()
        view_spec = ViewTool(self.workspace).spec()
        edit_spec = EditTool(self.workspace, ask_for_confirmation=True).spec()

        self.assertIn("workspace-relative", ls_spec["description"])
        self.assertIn("Do not start paths with `/`", ls_spec["description"])
        self.assertIn("file discovery", glob_spec["description"])
        self.assertIn("Do not start paths with `/`", glob_spec["description"])
        self.assertIn("then `view`", grep_spec["description"])
        self.assertIn("exact file path", view_spec["description"])
        self.assertIn("inspected the file with `view`", edit_spec["description"])

    def test_registry_adds_shared_read_only_selection_policy(self):
        backend = BackendConfig(
            name="lm_studio",
            type="openai_compat",
            model="demo-model",
            base_url="http://example.test/v1",
            api_key="not-needed",
            api_key_env=None,
            timeout=30,
            max_tokens=256,
        )
        config = AppConfig(
            workspace_root=self.workspace,
            sessions_dir=self.workspace / ".crush_py" / "sessions",
            default_backend="lm_studio",
            backends={"lm_studio": backend},
            ask_on_write=True,
            ask_on_shell=True,
            bash_timeout=60,
        )

        specs = {spec["name"]: spec for spec in ToolRegistry(config).read_only_specs()}

        self.assertIn("Read-only tool selection policy", specs["ls"]["description"])
        self.assertIn("Use `glob` when you know the filename shape", specs["glob"]["description"])
        self.assertIn("Locate, then `view`, then answer. Do not guess.", specs["grep"]["description"])
        self.assertIn("never start tool paths with `/`", specs["view"]["description"])

    def test_registry_uses_shorter_small_model_policy(self):
        backend = BackendConfig(
            name="lm_studio",
            type="openai_compat",
            model="demo-model",
            base_url="http://example.test/v1",
            api_key="not-needed",
            api_key_env=None,
            timeout=30,
            max_tokens=256,
        )
        config = AppConfig(
            workspace_root=self.workspace,
            sessions_dir=self.workspace / ".crush_py" / "sessions",
            default_backend="lm_studio",
            backends={"lm_studio": backend},
            ask_on_write=True,
            ask_on_shell=True,
            bash_timeout=60,
        )

        specs = {spec["name"]: spec for spec in ToolRegistry(config).read_only_specs(prompt_profile="small_model")}

        self.assertIn("Repo read policy", specs["ls"]["description"])
        self.assertIn("`ls` for area", specs["ls"]["description"])
        self.assertIn("Locate, then `view`, then answer. Do not guess.", specs["view"]["description"])

    def test_registry_uses_read_only_tool_subset_for_read_prompts(self):
        backend = BackendConfig(
            name="lm_studio",
            type="openai_compat",
            model="demo-model",
            base_url="http://example.test/v1",
            api_key="not-needed",
            api_key_env=None,
            timeout=30,
            max_tokens=256,
        )
        config = AppConfig(
            workspace_root=self.workspace,
            sessions_dir=self.workspace / ".crush_py" / "sessions",
            default_backend="lm_studio",
            backends={"lm_studio": backend},
            ask_on_write=True,
            ask_on_shell=True,
            bash_timeout=60,
        )

        specs = {spec["name"] for spec in ToolRegistry(config).automatic_specs_for_prompt("Find where SessionStore is implemented and summarize it.")}

        self.assertEqual(specs, {"glob", "grep", "ls", "view"})

    def test_registry_keeps_mutating_tools_for_edit_requests(self):
        backend = BackendConfig(
            name="lm_studio",
            type="openai_compat",
            model="demo-model",
            base_url="http://example.test/v1",
            api_key="not-needed",
            api_key_env=None,
            timeout=30,
            max_tokens=256,
        )
        config = AppConfig(
            workspace_root=self.workspace,
            sessions_dir=self.workspace / ".crush_py" / "sessions",
            default_backend="lm_studio",
            backends={"lm_studio": backend},
            ask_on_write=True,
            ask_on_shell=True,
            bash_timeout=60,
        )

        specs = {spec["name"] for spec in ToolRegistry(config).automatic_specs_for_prompt("Modify demo.py and run a command.")}

        self.assertIn("edit", specs)
        self.assertIn("bash", specs)


if __name__ == "__main__":
    unittest.main()
