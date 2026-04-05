import unittest

from .runtime_test_cases import AgentRuntimeTests as SourceTests


def _bind_tests(target_cls, names):
    target_cls._make_config = SourceTests._make_config
    for name in names:
        setattr(target_cls, name, getattr(SourceTests, name))
    return target_cls


class AgentRuntimeTraceTests(unittest.TestCase):
    pass


_bind_tests(
    AgentRuntimeTraceTests,
    [
        "test_detects_direct_file_variable_trace_prompt",
        "test_detects_direct_file_file_flow_trace_prompt",
        "test_direct_file_variable_trace_uses_outline_grep_and_local_cat_reads",
        "test_direct_file_flow_trace_reads_containing_function_blocks",
        "test_flow_trace_uses_qualname_and_separates_persistence_from_handoff",
        "test_direct_file_file_flow_trace_uses_reader_and_fallback_output",
        "test_trace_prompt_does_not_use_direct_summary_fast_path",
        "test_trace_shows_planner_and_reader_agents",
        "test_trace_system_prompt_strengthens_uncertainty_language",
        "test_real_traditional_chinese_trace_prompt_detection",
    ],
)
