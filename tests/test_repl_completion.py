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

    def test_complete_input_routes_use_to_sessions(self):
        runtime = FakeRuntime(Path("."), sessions=[FakeSession("sess-1"), FakeSession("other")])
        matches = complete_input(runtime, "/use sess", "sess")
        self.assertEqual(matches, ["sess-1"])


if __name__ == "__main__":
    unittest.main()
