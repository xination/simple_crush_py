import tempfile
import unittest
from pathlib import Path

from crush_py.benchmark import analyze_session_messages, load_benchmark_cases, run_benchmark_cases
from crush_py.agent.messages import Message


class BenchmarkTests(unittest.TestCase):
    def test_load_benchmark_cases_requires_id_and_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cases.json"
            path.write_text(
                '{"cases": [{"id": "demo", "prompt": "Read README.md"}]}',
                encoding="utf-8",
            )

            cases = load_benchmark_cases(path)

            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]["id"], "demo")

    def test_analyze_session_messages_extracts_tool_sequence(self):
        messages = [
            Message(
                role="assistant",
                content="I will inspect it.",
                created_at="2026-01-01T00:00:00+00:00",
                kind="tool_use",
                metadata={
                    "raw_content": [
                        {"type": "text", "text": "I will inspect it."},
                        {"type": "tool_use", "name": "grep", "id": "tool-1", "input": {"pattern": "SessionStore"}},
                        {"type": "tool_use", "name": "view", "id": "tool-2", "input": {"path": "crush_py/store/session_store.py"}},
                    ]
                },
            ),
            Message(
                role="assistant",
                content="SessionStore handles session persistence.",
                created_at="2026-01-01T00:00:01+00:00",
                kind="message",
                metadata={},
            ),
        ]

        analysis = analyze_session_messages(messages)

        self.assertEqual(analysis["tool_sequence"], ["grep", "view"])
        self.assertEqual(analysis["first_tool"], "grep")
        self.assertEqual(analysis["tool_call_count"], 2)
        self.assertTrue(analysis["used_view"])
        self.assertEqual(analysis["assistant_final"], "SessionStore handles session persistence.")

    def test_run_benchmark_cases_records_error_without_aborting(self):
        class FakeRuntime:
            def __init__(self):
                self._session_index = 0
                self.active_session = None
                self.session_store = self

            def new_session(self, backend_name=None, title=""):
                self._session_index += 1
                self.active_session = type(
                    "Session",
                    (),
                    {"id": "session-{0}".format(self._session_index), "backend": "lm_studio", "model": "demo-model"},
                )()
                return self.active_session

            def ask(self, prompt):
                raise RuntimeError("demo failure")

            def load_messages(self, session_id):
                return []

        runtime = FakeRuntime()
        cases = [{"id": "demo", "prompt": "Read README.md"}]

        results = run_benchmark_cases(runtime, cases)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["error"], "RuntimeError: demo failure")
        self.assertEqual(results[0]["answer"], "")


if __name__ == "__main__":
    unittest.main()
