import unittest

from scripts.compare_benchmark_results import compare_aggregate_sets, compare_result_sets


class CompareBenchmarkResultsTests(unittest.TestCase):
    def test_compare_result_sets_reports_changed_tools_and_cat_usage(self):
        baseline = {
            "case_a": {
                "answer": "alpha",
                "analysis": {
                    "first_tool": "grep",
                    "used_cat": True,
                    "tool_call_count": 2,
                },
            }
        }
        candidate = {
            "case_a": {
                "answer": "beta",
                "analysis": {
                    "first_tool": "find",
                    "used_cat": False,
                    "tool_call_count": 4,
                },
            }
        }

        comparison = compare_result_sets(baseline, candidate)

        self.assertEqual(comparison["changed_first_tool"][0]["id"], "case_a")
        self.assertEqual(comparison["changed_used_view"][0]["id"], "case_a")
        self.assertEqual(comparison["tool_call_deltas"][0]["delta"], 2)
        self.assertIn("candidate lost `cat`", comparison["needs_manual_review"][0]["reasons"])
        self.assertIn("final answer changed", comparison["needs_manual_review"][0]["reasons"])

    def test_compare_aggregate_sets_reports_rate_and_stability_changes(self):
        baseline = {
            "case_a": {
                "first_tool_mode": "grep",
                "used_view_rate": 1.0,
                "error_rate": 0.0,
                "avg_tool_call_count": 2.0,
                "tool_sequence_variant_count": 1,
                "answer_variant_count": 1,
            }
        }
        candidate = {
            "case_a": {
                "first_tool_mode": "glob",
                "used_view_rate": 0.5,
                "error_rate": 0.5,
                "avg_tool_call_count": 3.2,
                "tool_sequence_variant_count": 2,
                "answer_variant_count": 3,
            }
        }

        comparison = compare_aggregate_sets(baseline, candidate)

        self.assertEqual(comparison["first_tool_mode_changes"][0]["id"], "case_a")
        self.assertEqual(comparison["used_view_rate_deltas"][0]["delta"], -0.5)
        self.assertEqual(comparison["error_rate_deltas"][0]["delta"], 0.5)
        self.assertEqual(comparison["avg_tool_call_deltas"][0]["id"], "case_a")
        self.assertEqual(comparison["stability_changes"][0]["id"], "case_a")
        self.assertIn("candidate error rate increased", comparison["needs_manual_review"][0]["reasons"])
        self.assertIn("tool routing became less stable", comparison["needs_manual_review"][0]["reasons"])


if __name__ == "__main__":
    unittest.main()
