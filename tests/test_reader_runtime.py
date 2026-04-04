import unittest
from dataclasses import dataclass

from crush_py.agent.reader_runtime import (
    ReaderRuntimeMixin,
    _tool_use_id_for_reader_tool,
    executed_calls_from_turn,
)
from crush_py.backends.base import AssistantTurn, ToolCall


@dataclass
class FakeMessage:
    role: str
    content: str
    created_at: str
    kind: str
    metadata: dict


class ReaderRuntimeDirectTests(unittest.TestCase):
    def test_tool_use_id_for_reader_tool_uses_cat_shape(self):
        tool_use_id = _tool_use_id_for_reader_tool("cat", {"path": "notes.txt", "offset": 10, "limit": 20})
        self.assertEqual(tool_use_id, "reader-cat:notes.txt:10:20")

    def test_tool_use_id_for_reader_tool_uses_grep_shape(self):
        tool_use_id = _tool_use_id_for_reader_tool("grep", {"path": "src", "include": "*.py", "pattern": "needle"})
        self.assertEqual(tool_use_id, "reader-grep:src:*.py:needle")

    def test_executed_calls_from_turn_applies_limit(self):
        turn = AssistantTurn(
            text="",
            tool_calls=[
                ToolCall(id="1", name="cat", arguments={"path": "a"}),
                ToolCall(id="2", name="grep", arguments={"pattern": "x"}),
            ],
        )

        calls = executed_calls_from_turn(turn, limit=1)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].id, "1")

    def test_append_reader_summary_message_adds_user_message(self):
        messages = [{"role": "assistant", "content": "planner"}]

        updated = ReaderRuntimeMixin._append_reader_summary_message(object(), messages, "notes.txt", "summary text")

        self.assertEqual(updated[-1]["role"], "user")
        self.assertIn("Reader agent summary for `notes.txt`", updated[-1]["content"])

    def test_skip_message_for_planner_history_keeps_only_reader_summary(self):
        class DummyReaderRuntime(ReaderRuntimeMixin):
            pass

        runtime = DummyReaderRuntime()
        summary_message = FakeMessage(
            role="assistant",
            content="summary",
            created_at="",
            kind="tool_result",
            metadata={"agent": "reader", "tool_name": "reader"},
        )
        raw_reader_message = FakeMessage(
            role="assistant",
            content="raw cat result",
            created_at="",
            kind="tool_result",
            metadata={"agent": "reader", "tool_name": "cat"},
        )

        self.assertFalse(runtime._skip_message_for_planner_history(summary_message))
        self.assertTrue(runtime._skip_message_for_planner_history(raw_reader_message))

    def test_reader_summary_history_content_prefers_path(self):
        class DummyReaderRuntime(ReaderRuntimeMixin):
            pass

        runtime = DummyReaderRuntime()
        message = FakeMessage(
            role="assistant",
            content="Summary body",
            created_at="",
            kind="tool_result",
            metadata={"tool_arguments": {"path": "notes.txt"}},
        )

        rendered = runtime._reader_summary_history_content(message)

        self.assertEqual(rendered, "Reader summary for `notes.txt`:\nSummary body")


if __name__ == "__main__":
    unittest.main()
