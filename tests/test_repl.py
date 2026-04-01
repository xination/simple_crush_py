import unittest
from dataclasses import dataclass

from crush_py.repl import _format_history_message, _format_trace_message


@dataclass
class FakeMessage:
    role: str
    content: str
    created_at: str
    kind: str
    metadata: dict


class ReplTests(unittest.TestCase):
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
