import unittest

from .runtime_test_cases import AgentRuntimeTests as SourceTests


def _bind_tests(target_cls, names):
    target_cls._make_config = SourceTests._make_config
    for name in names:
        setattr(target_cls, name, getattr(SourceTests, name))
    return target_cls


class AgentRuntimeToolLoopTests(unittest.TestCase):
    pass


_bind_tests(
    AgentRuntimeToolLoopTests,
    [
        "test_ask_with_tool_loop_persists_tool_trace",
        "test_streaming_tool_turn_keeps_tool_loop_instead_of_returning_early",
        "test_streaming_only_prints_final_answer_not_tool_loop_text",
        "test_format_trace_reads_recent_tool_entries",
        "test_find_single_candidate_forces_cat_before_answering",
        "test_broad_grep_keeps_answer_uncertain",
        "test_small_cat_result_is_forwarded_to_backend_in_full",
        "test_small_find_result_is_forwarded_to_backend_in_full",
        "test_small_grep_result_is_forwarded_to_backend_in_full",
        "test_reader_raw_tool_payload_is_excluded_from_planner_history",
        "test_repl_ls_command_history_is_available_to_next_natural_language_turn",
        "test_repl_ls_then_prompt_runs_planner_search_and_reader_cat",
        "test_duplicate_tool_result_is_deduped_in_session_store",
        "test_plain_backend_persists_final_assistant_raw_content",
        "test_plain_backend_retries_and_sanitizes_final_output",
        "test_format_history_reads_recent_conversation_messages",
        "test_summary_cache_reuses_cat_summary_for_same_slice",
    ],
)
