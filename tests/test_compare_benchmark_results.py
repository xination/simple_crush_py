import unittest

from scripts.compare_benchmark_results import compare_result_sets


class CompareBenchmarkResultsTests(unittest.TestCase):
    def test_compare_result_sets_reports_changed_tools_and_view_usage(self):
        baseline = {
            "case_a": {
                "answer": "alpha",
                "analysis": {
                    "first_tool": "grep",
                    "used_view": True,
                    "tool_call_count": 2,
                },
            }
        }
        candidate = {
            "case_a": {
                "answer": "beta",
                "analysis": {
                    "first_tool": "glob",
                    "used_view": False,
                    "tool_call_count": 4,
                },
            }
        }

        comparison = compare_result_sets(baseline, candidate)

        self.assertEqual(comparison["changed_first_tool"][0]["id"], "case_a")
        self.assertEqual(comparison["changed_used_view"][0]["id"], "case_a")
        self.assertEqual(comparison["tool_call_deltas"][0]["delta"], 2)
        self.assertIn("candidate lost `view`", comparison["needs_manual_review"][0]["reasons"])
        self.assertIn("final answer changed", comparison["needs_manual_review"][0]["reasons"])


if __name__ == "__main__":
    unittest.main()
