import unittest
from dataclasses import dataclass

from crush_py.repl import (
    _format_history_message,
    _format_trace_message,
    _normalize_bash_command,
)


@dataclass
class FakeMessage:
    role: str
    content: str
    created_at: str
    kind: str
    metadata: dict


class ReplTests(unittest.TestCase):
    def test_normalize_bash_command_unwraps_single_quoted_command(self):
        self.assertEqual(_normalize_bash_command('"printf hello"'), "printf hello")

    def test_normalize_bash_command_keeps_shell_quotes_when_needed(self):
        self.assertEqual(
            _normalize_bash_command('printf "hello world"'),
            'printf "hello world"',
        )

    def test_format_trace_message_for_tool_use(self):
        message = FakeMessage(
            role="assistant",
            content="Let me inspect the file first.",
            created_at="2026-03-28T00:00:00+00:00",
            kind="tool_use",
            metadata={
                "raw_content": [
                    {"type": "text", "text": "Let me inspect the file first."},
                    {"type": "tool_use", "name": "view", "input": {"path": "notes.txt"}},
                ]
            },
        )

        lines = _format_trace_message(message)

        self.assertIn("[tool_use] assistant (2026-03-28T00:00:00+00:00)", lines[0])
        self.assertIn("tool: view", lines[1])
        self.assertIn("text: Let me inspect the file first.", lines[2])

    def test_format_trace_message_for_tool_result(self):
        message = FakeMessage(
            role="user",
            content='<file path="notes.txt"> one two three </file>',
            created_at="2026-03-28T00:00:01+00:00",
            kind="tool_result",
            metadata={
                "tool_name": "view",
                "tool_arguments": {"path": "notes.txt"},
            },
        )

        lines = _format_trace_message(message)

        self.assertIn("[tool_result] user (2026-03-28T00:00:01+00:00)", lines[0])
        self.assertIn("tool: view", lines[1])
        self.assertIn("arguments: {'path': 'notes.txt'}", lines[2])
        self.assertIn('result: <file path="notes.txt"> one two three </file>', lines[3])

    def test_format_trace_message_for_final_assistant_message(self):
        message = FakeMessage(
            role="assistant",
            content="The file contains three lines.",
            created_at="2026-03-28T00:00:02+00:00",
            kind="message",
            metadata={
                "raw_content": [
                    {"type": "text", "text": "The file contains three lines."},
                ]
            },
        )

        lines = _format_trace_message(message)

        self.assertIn("[message] assistant (2026-03-28T00:00:02+00:00)", lines[0])
        self.assertIn("stage: assistant_final", lines[1])
        self.assertIn("text: The file contains three lines.", lines[2])

    def test_format_history_message_for_user_message(self):
        message = FakeMessage(
            role="user",
            content="Summarize notes.txt",
            created_at="2026-03-28T00:00:03+00:00",
            kind="message",
            metadata={},
        )

        lines = _format_history_message(message)

        self.assertIn("[user] (2026-03-28T00:00:03+00:00)", lines[0])
        self.assertEqual(lines[1], "Summarize notes.txt")


if __name__ == "__main__":
    unittest.main()
