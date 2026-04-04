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
            self.assertEqual(messages[1].metadata["text"], "tool output")

    def test_lean_mode_discards_heavy_trace_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir) / "sessions", trace_mode="lean")
            session = store.create_session(backend="anthropic", model="demo-model")

            store.append_message(
                session.id,
                "user",
                "summary only",
                kind="tool_result",
                metadata={
                    "tool_name": "cat",
                    "tool_arguments": {"path": "notes.txt"},
                    "tool_use_id": "tool-1",
                    "summary": "Read notes.txt lines 1-3.",
                    "encoding_used": "cp950",
                    "raw_content": [{"type": "tool_result", "content": "full text"}],
                    "backend_content": [{"type": "tool_result", "content": "full text"}],
                },
            )

            messages = store.load_messages(session.id)

            self.assertEqual(messages[0].metadata["tool"], "cat")
            self.assertNotIn("agent", messages[0].metadata)
            self.assertEqual(messages[0].metadata["summary"], "Read notes.txt lines 1-3.")
            self.assertEqual(messages[0].metadata["encoding"], "cp950")
            self.assertNotIn("raw_content", messages[0].metadata)
            self.assertNotIn("backend_content", messages[0].metadata)

    def test_lean_mode_keeps_agent_trace_label(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir) / "sessions", trace_mode="lean")
            session = store.create_session(backend="anthropic", model="demo-model")

            store.append_message(
                session.id,
                "assistant",
                "delegating",
                kind="tool_use",
                metadata={
                    "agent": "planner",
                    "tool_names": ["reader"],
                    "assistant_text": "delegating",
                },
            )

            messages = store.load_messages(session.id)

            self.assertEqual(messages[0].metadata["agent"], "planner")
            self.assertEqual(messages[0].metadata["tool"], "reader")

    def test_lean_mode_persists_flat_event_shape_on_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir) / "sessions", trace_mode="lean")
            session = store.create_session(backend="anthropic", model="demo-model")

            store.append_message(
                session.id,
                "assistant",
                "",
                kind="tool_use",
                metadata={
                    "agent": "planner",
                    "tool_calls": [{"id": "tool-1", "name": "grep", "arguments": {"pattern": "needle"}}],
                },
            )
            store.append_message(
                session.id,
                "user",
                "",
                kind="tool_result",
                metadata={
                    "agent": "planner",
                    "tool_name": "grep",
                    "summary": "No clear file candidates for `needle`.",
                },
            )

            payloads = [
                json.loads(line)
                for line in (store.sessions_dir / session.id / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(
                payloads[0],
                {"kind": "tool_use", "role": "assistant", "tool": "grep", "args": {"pattern": "needle"}, "agent": "planner"},
            )
            self.assertEqual(
                payloads[1],
                {
                    "kind": "tool_result",
                    "role": "user",
                    "tool": "grep",
                    "summary": "No clear file candidates for `needle`.",
                    "agent": "planner",
                },
            )

    def test_lean_mode_persists_tool_result_encoding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir) / "sessions", trace_mode="lean")
            session = store.create_session(backend="anthropic", model="demo-model")

            store.append_message(
                session.id,
                "user",
                "",
                kind="tool_result",
                metadata={
                    "agent": "reader",
                    "tool_name": "cat",
                    "summary": "Read cp950 file.",
                    "encoding_used": "cp950",
                },
            )

            payload = json.loads((store.sessions_dir / session.id / "messages.jsonl").read_text(encoding="utf-8").splitlines()[0])

            self.assertEqual(payload["encoding"], "cp950")

    def test_debug_mode_keeps_full_trace_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir) / "sessions", trace_mode="debug")
            session = store.create_session(backend="anthropic", model="demo-model")

            store.append_message(
                session.id,
                "assistant",
                "tool output",
                kind="tool_use",
                metadata={
                    "assistant_text": "tool output",
                    "raw_content": [{"type": "text", "text": "tool output"}],
                },
            )

            messages = store.load_messages(session.id)

            self.assertIn("raw_content", messages[0].metadata)

    def test_append_message_sanitizes_control_tokens_before_logging(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SessionStore(Path(tmpdir) / "sessions", trace_mode="debug")
            session = store.create_session(backend="anthropic", model="demo-model")

            store.append_message(
                session.id,
                "assistant",
                '<|tool_response|>Flow trace for human review:\n\nTarget: prompt',
                metadata={
                    "assistant_text": '<|tool_call|>call:unknown_tool{value:"x"}<|tool_response|>clean text',
                    "raw_content": [{"type": "text", "text": '<|tool_response|>clean text'}],
                },
            )

            messages = store.load_messages(session.id)
            payload = json.loads((store.sessions_dir / session.id / "messages.jsonl").read_text(encoding="utf-8").splitlines()[0])

            self.assertEqual(messages[0].content, "Flow trace for human review:\n\nTarget: prompt")
            self.assertEqual(messages[0].metadata["assistant_text"], "clean text")
            self.assertNotIn("<|tool_response|>", json.dumps(payload, ensure_ascii=False))
            self.assertNotIn("call:unknown_tool", json.dumps(payload, ensure_ascii=False))

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
