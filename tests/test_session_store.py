import json
import tempfile
import unittest
from pathlib import Path

from crush_py.store.session_store import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_create_session_creates_expected_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir) / "sessions")

            session = store.create_session(backend="anthropic", model="demo-model")
            session_dir = Path(tmpdir) / "sessions" / session.id

            self.assertTrue((session_dir / "session.json").exists())
            self.assertTrue((session_dir / "messages.jsonl").exists())
            self.assertTrue((session_dir / "artifacts").is_dir())
            self.assertEqual(session.title, "Untitled Session")

    def test_append_and_load_messages_updates_title(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir) / "sessions")
            session = store.create_session(backend="anthropic", model="demo-model")

            store.append_message(session.id, "user", "   hello    from   crush_py   ")
            store.append_message(
                session.id,
                "assistant",
                "tool output",
                kind="tool_use",
                metadata={"raw_content": [{"type": "text", "text": "tool output"}]},
            )

            reloaded = store.load_session(session.id)
            messages = store.load_messages(session.id)

            self.assertEqual(reloaded.title, "hello from crush_py")
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0].role, "user")
            self.assertEqual(messages[1].kind, "tool_use")
            self.assertEqual(messages[1].metadata["raw_content"][0]["text"], "tool output")

    def test_list_sessions_sorts_by_updated_at_descending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir) / "sessions")
            first = store.create_session(backend="anthropic", model="model-a", title="first")
            second = store.create_session(backend="anthropic", model="model-b", title="second")
            self._set_updated_at(store, first.id, "2026-01-01T00:00:00+00:00")
            self._set_updated_at(store, second.id, "2026-01-01T00:00:01+00:00")

            sessions = store.list_sessions()

            self.assertEqual(sessions[0].id, second.id)
            self.assertEqual(sessions[1].id, first.id)

    def _set_updated_at(self, store, session_id, updated_at):
        session_path = store.sessions_dir / session_id / "session.json"
        payload = json.loads(session_path.read_text(encoding="utf-8"))
        payload["updated_at"] = updated_at
        session_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
