import unittest

from .runtime_test_cases import AgentRuntimeTests as SourceTests


def _bind_tests(target_cls, names):
    target_cls._make_config = SourceTests._make_config
    for name in names:
        setattr(target_cls, name, getattr(SourceTests, name))
    return target_cls


class AgentRuntimeQuickRouterTests(unittest.TestCase):
    pass


_bind_tests(
    AgentRuntimeQuickRouterTests,
    [
        "test_tiny_intent_router_routes_direct_file_doc_qa",
        "test_tiny_intent_router_falls_back_when_json_is_invalid",
        "test_no_tool_conversation_prompt_skips_planner_tool_loop",
        "test_quick_file_mode_reads_one_file_without_tools",
        "test_quick_file_mode_rejects_large_files",
        "test_quick_file_mode_stream_prints_incrementally",
        "test_quick_file_mode_stays_stateless_even_after_prior_turns",
        "test_quick_file_mode_reuses_cached_file_text_on_repeat",
        "test_quick_file_mode_records_cache_debug_metadata_in_debug_trace_mode",
        "test_full_cat_result_populates_quick_file_cache",
        "test_repo_question_still_enters_tool_loop_when_router_requires_evidence",
        "test_repo_question_requires_evidence_before_accepting_planner_answer",
        "test_repo_question_falls_back_safely_if_planner_refuses_tools_twice",
        "test_repo_question_can_anchor_to_readme_after_initial_discovery",
        "test_sanitize_text_removes_ansi_escape_codes",
        "test_single_doc_workspace_fast_path_avoids_config_drift",
        "test_session_model_override_is_used_for_backend_creation",
    ],
)
