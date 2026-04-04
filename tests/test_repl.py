import unittest
from dataclasses import dataclass

from crush_py.repl import _format_history_message, _format_trace_message
from crush_py.repl_display import format_history, format_trace


@dataclass
class FakeMessage:
    role: str
    content: str
    created_at: str
    kind: str
    metadata: dict


class ReplTests(unittest.TestCase):
    def test_format_trace_returns_empty_message_when_no_active_session(self):
        runtime = type("Runtime", (), {"active_session": None})()
        self.assertEqual(format_trace(runtime), "No active session.")

    def test_format_history_returns_empty_message_when_no_active_session(self):
        runtime = type("Runtime", (), {"active_session": None})()
        self.assertEqual(format_history(runtime), "No active session.")

    def test_format_trace_message_for_tool_use(self):
        message = FakeMessage(
            role="assistant",
            content="Let me inspect the file first.",
            created_at="2026-03-28T00:00:00+00:00",
            kind="tool_use",
            metadata={
                "tool_names": ["cat"],
                "assistant_text": "Let me inspect the file first.",
            },
        )

        lines = _format_trace_message(message)

        self.assertIn("[tool_use] assistant (2026-03-28T00:00:00+00:00)", lines[0])
        self.assertIn("tool: cat", lines[-2])
        self.assertIn("text: Let me inspect the file first.", lines[-1])

    def test_format_trace_message_for_tool_result_prefers_summary(self):
        message = FakeMessage(
            role="user",
            content='<file path="notes.txt"> one two three </file>',
            created_at="2026-03-28T00:00:01+00:00",
            kind="tool_result",
            metadata={
                "tool_name": "cat",
                "tool_arguments": {"path": "notes.txt"},
                "summary": "Read notes.txt lines 1-3. Key excerpts: 1|one ; 2|two ; 3|three",
            },
        )

        lines = _format_trace_message(message)

        self.assertIn("[tool_result] user (2026-03-28T00:00:01+00:00)", lines[0])
        self.assertIn("tool: cat", lines[-3])
        self.assertIn("arguments: {'path': 'notes.txt'}", lines[-2])
        self.assertIn("Read notes.txt lines 1-3.", lines[-1])

    def test_format_trace_message_for_final_assistant_message(self):
        message = FakeMessage(
            role="assistant",
            content="Confirmed path: notes.txt",
            created_at="2026-03-28T00:00:02+00:00",
            kind="message",
            metadata={},
        )

        lines = _format_trace_message(message)

        self.assertIn("[message] assistant (2026-03-28T00:00:02+00:00)", lines[0])
        self.assertIn("stage: assistant_final", lines[-2])
        self.assertIn("text: Confirmed path: notes.txt", lines[-1])

    def test_format_trace_renders_multiple_entries(self):
        session = type("Session", (), {"id": "s-1"})()
        messages = [
            FakeMessage(
                role="assistant",
                content="Inspecting",
                created_at="2026-03-28T00:00:00+00:00",
                kind="tool_use",
                metadata={"tool_names": ["cat"], "assistant_text": "Inspecting"},
            ),
            FakeMessage(
                role="user",
                content="Read file",
                created_at="2026-03-28T00:00:01+00:00",
                kind="tool_result",
                metadata={"tool_name": "cat", "summary": "Read notes.txt"},
            ),
        ]
        store = type("Store", (), {"load_messages": lambda self, session_id: messages})()
        runtime = type("Runtime", (), {"active_session": session, "session_store": store})()

        rendered = format_trace(runtime, limit=10)

        self.assertIn("[tool_use] assistant", rendered)
        self.assertIn("[tool_result] user", rendered)

    def test_format_history_renders_recent_messages(self):
        session = type("Session", (), {"id": "s-1"})()
        messages = [
            FakeMessage(
                role="user",
                content="Trace session_id",
                created_at="2026-03-28T00:00:00+00:00",
                kind="message",
                metadata={},
            ),
            FakeMessage(
                role="assistant",
                content="Confirmed path: session_store.py",
                created_at="2026-03-28T00:00:01+00:00",
                kind="message",
                metadata={},
            ),
        ]
        store = type("Store", (), {"load_messages": lambda self, session_id: messages})()
        runtime = type("Runtime", (), {"active_session": session, "session_store": store})()

        rendered = format_history(runtime, limit=10)

        self.assertIn("[user]", rendered)
        self.assertIn("Confirmed path: session_store.py", rendered)

    def test_format_history_message_for_user_message(self):
        message = FakeMessage(
            role="user",
            content="Trace how SessionStore is used",
            created_at="2026-03-28T00:00:03+00:00",
            kind="message",
            metadata={},
        )

        lines = _format_history_message(message)

        self.assertIn("[user] (2026-03-28T00:00:03+00:00)", lines[0])
        self.assertEqual(lines[1], "Trace how SessionStore is used")


if __name__ == "__main__":
    unittest.main()
