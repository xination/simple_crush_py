import tempfile
import unittest
from pathlib import Path

from crush_py.benchmark import (
    aggregate_run_results,
    analyze_session_messages,
    build_run_summary,
    load_benchmark_cases,
    run_benchmark_cases,
)
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
                        {"type": "tool_use", "name": "cat", "id": "tool-2", "input": {"path": "crush_py/store/session_store.py"}},
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

        self.assertEqual(analysis["tool_sequence"], ["grep", "cat"])
        self.assertEqual(analysis["first_tool"], "grep")
        self.assertEqual(analysis["tool_call_count"], 2)
        self.assertTrue(analysis["used_cat"])
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

    def test_build_run_summary_counts_errors_view_and_first_tool(self):
        results = [
            {
                "error": "",
                "analysis": {
                    "used_cat": True,
                    "first_tool": "grep",
                },
            },
            {
                "error": "RuntimeError: demo",
                "analysis": {
                    "used_cat": False,
                    "first_tool": "",
                },
            },
        ]

        summary = build_run_summary(results)

        self.assertEqual(summary["case_count"], 2)
        self.assertEqual(summary["error_count"], 1)
        self.assertEqual(summary["used_view_count"], 1)
        self.assertEqual(summary["first_tool_counts"]["grep"], 1)
        self.assertEqual(summary["first_tool_counts"]["<none>"], 1)

    def test_aggregate_run_results_summarizes_multi_run_case_metrics(self):
        runs = [
            {
                "run_index": 1,
                "results": [
                    {
                        "id": "case_a",
                        "answer": "alpha",
                        "error": "",
                        "analysis": {
                            "used_cat": True,
                            "first_tool": "grep",
                            "tool_call_count": 2,
                            "locator_tool_count": 1,
                            "tool_sequence": ["grep", "cat"],
                        },
                    }
                ],
            },
            {
                "run_index": 2,
                "results": [
                    {
                        "id": "case_a",
                        "answer": "beta",
                        "error": "RuntimeError: demo",
                        "analysis": {
                            "used_cat": False,
                            "first_tool": "find",
                            "tool_call_count": 4,
                            "locator_tool_count": 2,
                            "tool_sequence": ["find", "grep", "cat", "cat"],
                        },
                    }
                ],
            },
        ]

        aggregate = aggregate_run_results(runs)
        case_a = aggregate["cases"][0]

        self.assertEqual(aggregate["overall"]["run_count"], 2)
        self.assertEqual(aggregate["overall"]["total_case_executions"], 2)
        self.assertEqual(case_a["run_count"], 2)
        self.assertEqual(case_a["success_count"], 1)
        self.assertEqual(case_a["error_count"], 1)
        self.assertEqual(case_a["used_view_rate"], 0.5)
        self.assertEqual(case_a["first_tool_counts"]["grep"], 1)
        self.assertEqual(case_a["first_tool_counts"]["find"], 1)
        self.assertEqual(case_a["avg_tool_call_count"], 3.0)
        self.assertEqual(case_a["avg_locator_tool_count"], 1.5)
        self.assertEqual(case_a["answer_variant_count"], 2)
        self.assertEqual(case_a["tool_sequence_variant_count"], 2)


if __name__ == "__main__":
    unittest.main()
