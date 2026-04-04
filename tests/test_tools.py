import tempfile
import unittest
from pathlib import Path

from crush_py.config import AppConfig, BackendConfig
from crush_py.tools.cat import CatTool
from crush_py.tools.find import FindTool
from crush_py.tools.get_outline import GetOutlineTool
from crush_py.tools.grep import GrepTool
from crush_py.tools.ls import LsTool
from crush_py.tools.outline_providers import PythonAstOutlineProvider, RegexOutlineProvider, default_outline_provider_chain
from crush_py.tools.registry import ToolRegistry
from crush_py.tools.tree import TreeTool


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.workspace = Path(self.tempdir.name)
        (self.workspace / "src" / "nested").mkdir(parents=True)
        (self.workspace / "tests").mkdir()
        (self.workspace / ".crush_py").mkdir()
        (self.workspace / ".pytest_cache").mkdir()
        (self.workspace / "src" / "demo.py").write_text(
            "def alpha():\n    return 'alpha'\n\nneedle here\n",
            encoding="utf-8",
        )
        (self.workspace / "src" / "nested" / "demo.cpp").write_text(
            "void trace_me() {}\ntrace_me();\n",
            encoding="utf-8",
        )
        (self.workspace / "tests" / "test_demo.py").write_text("needle in tests\n", encoding="utf-8")
        (self.workspace / ".crush_py" / "session.json").write_text("noise\n", encoding="utf-8")
        (self.workspace / "notes.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")

    def test_cat_reads_file_with_line_numbers_and_continuation_hint(self):
        tool = CatTool(self.workspace)

        result = tool.run({"path": "notes.txt", "offset": 1, "limit": 1})

        self.assertIn('<file path="notes.txt" offset="1" limit="1" encoding="utf-8">', result)
        self.assertIn("     2|line2", result)
        self.assertIn("Use offset >= 2 to continue.", result)

    def test_cat_spec_uses_smaller_default_limit(self):
        tool = CatTool(self.workspace)

        spec = tool.spec()

        self.assertEqual(spec["input_schema"]["properties"]["limit"]["default"], 80)

    def test_cat_can_read_full_file(self):
        tool = CatTool(self.workspace)

        result = tool.run({"path": "notes.txt", "full": True})

        self.assertIn('<file path="notes.txt" offset="0" limit="3" encoding="utf-8">', result)
        self.assertIn("     1|line1", result)
        self.assertIn("     3|line3", result)
        self.assertNotIn("File has more lines.", result)

    def test_cat_uses_encoding_fallback_for_cp950_files(self):
        tool = CatTool(self.workspace)
        text = "第一行\n第二行\n"
        (self.workspace / "cp950_notes.txt").write_bytes(text.encode("cp950"))

        result = tool.run({"path": "cp950_notes.txt", "full": True})

        self.assertIn('encoding="cp950"', result)
        self.assertIn("第一行", result)
        self.assertIn("第二行", result)

    def test_ls_lists_files_and_directories(self):
        tool = LsTool(self.workspace)

        result = tool.run({"path": ".", "depth": 1})

        self.assertIn("- ./", result)
        self.assertIn("  - src/", result)
        self.assertIn("  - notes.txt", result)
        self.assertNotIn(".crush_py/", result)
        self.assertNotIn("tests/", result)

    def test_tree_lists_nested_structure(self):
        tool = TreeTool(self.workspace)

        result = tool.run({"path": "src", "depth": 2})

        self.assertIn("src/", result)
        self.assertIn("  demo.py", result)
        self.assertIn("  nested/", result)
        self.assertIn("    demo.cpp", result)

    def test_find_returns_matching_paths(self):
        tool = FindTool(self.workspace)

        result = tool.run({"pattern": "*.py", "path": "src"})

        self.assertIn("src/demo.py", result)
        self.assertNotIn("tests/test_demo.py", result)

    def test_grep_returns_matching_lines(self):
        tool = GrepTool(self.workspace)

        result = tool.run({"pattern": "needle", "path": ".", "include": "*.py"})

        self.assertIn("src/demo.py:", result)
        self.assertIn("Line 4, Char 1: needle here", result)
        self.assertNotIn("tests/test_demo.py", result)

    def test_explicit_test_paths_are_still_searchable(self):
        tool = GrepTool(self.workspace)

        result = tool.run({"pattern": "needle", "path": "tests", "include": "*.py"})

        self.assertIn("tests/test_demo.py:", result)

    def test_grep_caps_noisy_results_with_narrowing_hint(self):
        tool = GrepTool(self.workspace)
        for index in range(20):
            (self.workspace / "src" / "nested" / "f{0}.py".format(index)).write_text(
                "needle = 1\nneedle = 2\nneedle = 3\nneedle = 4\n",
                encoding="utf-8",
            )

        result = tool.run({"pattern": "needle", "path": "src", "include": "*.py"})

        self.assertIn("Search was capped because", result)
        self.assertIn("Narrow the search", result)

    def test_get_outline_returns_python_symbols(self):
        tool = GetOutlineTool(self.workspace)

        result = tool.run({"path": "src/demo.py"})

        self.assertIn('<outline path="src/demo.py">', result)
        self.assertIn("def alpha()", result)

    def test_get_outline_ast_provider_keeps_nested_python_structure(self):
        (self.workspace / "src" / "nested_demo.py").write_text(
            "\n".join(
                [
                    "class Outer:",
                    "    def method(self):",
                    "        def helper():",
                    "            return 1",
                    "        return helper()",
                    "",
                    "async def worker():",
                    "    return 2",
                ]
            ),
            encoding="utf-8",
        )

        result = GetOutlineTool(self.workspace).run({"path": "src/nested_demo.py"})

        self.assertIn("class Outer", result)
        self.assertIn("def method(...)", result)
        self.assertIn("def helper(...)", result)
        self.assertIn("async def worker(...)", result)

    def test_python_ast_provider_exposes_qualnames_and_parent_spans(self):
        source = "\n".join(
            [
                "class Outer:",
                "    def method(self):",
                "        def helper():",
                "            return 1",
                "        return helper()",
            ]
        )

        symbols = PythonAstOutlineProvider().extract(source, self.workspace / "demo.py")

        self.assertEqual([symbol.qualname for symbol in symbols[:3]], ["Outer", "Outer.method", "Outer.method.helper"])
        self.assertEqual([symbol.parent for symbol in symbols[:3]], [None, "Outer", "method"])
        self.assertTrue(all(symbol.end_line >= symbol.start_line for symbol in symbols))

    def test_regex_provider_remains_available_as_fallback(self):
        source = "def alpha(x,\n"

        symbols = RegexOutlineProvider().extract(source, self.workspace / "broken.py")

        self.assertEqual([symbol.display for symbol in symbols], ["def alpha(x,"])

    def test_default_outline_provider_chain_prefers_ast_and_falls_back_on_syntax_error(self):
        chain = default_outline_provider_chain()

        ast_symbols = chain.extract("class Demo:\n    def run(self):\n        return 1\n", self.workspace / "demo.py")
        fallback_symbols = chain.extract("def alpha(x,\n", self.workspace / "broken.py")

        self.assertEqual(ast_symbols[0].qualname, "Demo")
        self.assertEqual(fallback_symbols[0].display, "def alpha(x,")

    def test_registry_exposes_only_read_tools(self):
        backend = BackendConfig(
            name="lm_studio",
            type="openai_compat",
            model="demo-3b",
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
            trace_mode="lean",
            backends={"lm_studio": backend},
        )

        specs = {spec["name"]: spec for spec in ToolRegistry(config).automatic_specs()}

        self.assertEqual(set(specs.keys()), {"ls", "tree", "find", "grep", "get_outline", "cat"})
        self.assertEqual(
            specs["get_outline"]["description"],
            "Return a compact symbol outline for one code file before using `cat`.",
        )


if __name__ == "__main__":
    unittest.main()
