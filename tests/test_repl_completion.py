import tempfile
import unittest
from pathlib import Path

from crush_py.repl_completion import complete_input, complete_sessions, complete_workspace_paths, escape_completion


class FakeSession:
    def __init__(self, session_id):
        self.id = session_id


class FakeSessionStore:
    def __init__(self, sessions):
        self._sessions = sessions

    def list_sessions(self):
        return list(self._sessions)


class FakeRuntime:
    def __init__(self, workspace_root, sessions=None):
        self.config = type("Config", (), {"workspace_root": workspace_root})()
        self.session_store = FakeSessionStore(sessions or [])


class ReplCompletionTests(unittest.TestCase):
    def test_escape_completion_escapes_spaces(self):
        self.assertEqual(escape_completion("dir with space/file.py"), "dir\\ with\\ space/file.py")

    def test_complete_sessions_filters_by_prefix(self):
        runtime = FakeRuntime(Path("."), sessions=[FakeSession("abc123"), FakeSession("def456")])
        self.assertEqual(complete_sessions(runtime, "ab"), ["abc123"])

    def test_complete_workspace_paths_lists_matching_children(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "src").mkdir()
            (workspace / "src" / "alpha.py").write_text("", encoding="utf-8")
            (workspace / "src" / "beta.py").write_text("", encoding="utf-8")
            runtime = FakeRuntime(workspace)

            matches = complete_workspace_paths(runtime, "src/a")

            self.assertEqual(matches, ["src/alpha.py"])

    def test_complete_input_suggests_commands_at_root(self):
        runtime = FakeRuntime(Path("."))
        matches = complete_input(runtime, "/tr", "/tr")
        self.assertIn("/trace", matches)
        self.assertNotIn("/tool-trace", matches)
        self.assertNotIn("/tools", complete_input(runtime, "/to", "/to"))
        self.assertNotIn("/outline", complete_input(runtime, "/ou", "/ou"))
        self.assertNotIn("/history", complete_input(runtime, "/hi", "/hi"))
        self.assertNotIn("/tree", complete_input(runtime, "/tr", "/tr"))

    def test_complete_input_hides_use_and_sessions_at_root(self):
        runtime = FakeRuntime(Path("."), sessions=[FakeSession("sess-1"), FakeSession("other")])
        self.assertNotIn("/use", complete_input(runtime, "/u", "/u"))
        self.assertNotIn("/sessions", complete_input(runtime, "/s", "/s"))

    def test_complete_input_routes_use_to_sessions(self):
        runtime = FakeRuntime(Path("."), sessions=[FakeSession("sess-1"), FakeSession("other")])
        matches = complete_input(runtime, "/use sess", "sess")
        self.assertEqual(matches, ["sess-1"])

    def test_complete_input_handles_mention_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("", encoding="utf-8")
            (workspace / "src").mkdir()
            runtime = FakeRuntime(workspace)

            matches = complete_input(runtime, "@R", "@R")
            self.assertEqual(matches, ["@README.md"])

            matches = complete_input(runtime, "@", "@")
            self.assertIn("@README.md", matches)
            self.assertIn("@src/", matches)

    def test_complete_workspace_paths_handles_empty_prefix_as_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "a.txt").write_text("", encoding="utf-8")
            runtime = FakeRuntime(workspace)

            self.assertEqual(complete_workspace_paths(runtime, ""), ["a.txt"])

    def test_complete_input_escapes_spaces_in_mentions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "file with space.txt").write_text("", encoding="utf-8")
            runtime = FakeRuntime(workspace)

            matches = complete_input(runtime, "@file", "@file")
            self.assertEqual(matches, ["@file\\ with\\ space.txt"])

    def test_complete_input_cat_still_completes_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("", encoding="utf-8")
            runtime = FakeRuntime(workspace)

            matches = complete_input(runtime, "/cat R", "R")
            self.assertEqual(matches, ["README.md"])

    def test_complete_input_quick_completes_at_prefixed_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("", encoding="utf-8")
            runtime = FakeRuntime(workspace)

            matches = complete_input(runtime, "/quick @R", "@R")
            self.assertEqual(matches, ["@README.md"])


if __name__ == "__main__":
    unittest.main()
