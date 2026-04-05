import unittest

from .runtime_test_cases import AgentRuntimeTests as SourceTests


def _bind_tests(target_cls, names):
    target_cls._make_config = SourceTests._make_config
    for name in names:
        setattr(target_cls, name, getattr(SourceTests, name))
    return target_cls


class AgentRuntimeSummaryGuideTests(unittest.TestCase):
    pass


_bind_tests(
    AgentRuntimeSummaryGuideTests,
    [
        "test_prompt_named_file_forces_cat_before_any_other_tool",
        "test_direct_file_summary_uses_cat_first_without_outline",
        "test_direct_file_summary_reads_full_file_before_summary",
        "test_direct_file_summary_prompt_defaults_to_brief_instructions",
        "test_assistant_reuses_reader_brief_summary_without_rewriting",
        "test_structure_prompt_can_still_use_outline",
        "test_partial_direct_file_summary_is_marked_preliminary",
        "test_postprocess_direct_file_summary_output_prefixes_partial_label",
        "test_postprocess_direct_file_summary_output_ignores_partial_from_other_file",
        "test_direct_file_summary_defaults_to_brief_mode",
        "test_detects_direct_file_guide_prompt",
        "test_direct_file_guide_uses_reader_fast_path_and_appends_sources",
        "test_guide_prompt_uses_guide_system_prompt_appendix",
        "test_guide_follow_up_on_same_file_reuses_previous_reader_summary_context",
        "test_guide_exact_line_follow_up_rereads_file",
        "test_guide_exact_line_follow_up_returns_local_line_clues",
        "test_guide_partial_previous_summary_forces_reread",
        "test_brief_direct_file_summary_omits_evidence_and_review_scaffolding",
        "test_quickly_summarize_direct_file_uses_brief_mode",
        "test_brief_direct_file_summary_uses_spaced_bullets",
        "test_direct_file_summary_also_omits_review_draft_scaffolding_for_non_brief_wording",
        "test_compact_reader_cat_content_trims_large_payloads",
        "test_partial_brief_direct_file_summary_preserves_preliminary_label",
        "test_key_ideas_prompt_uses_brief_summary_shape",
        "test_direct_file_question_reuses_reader_answer_without_planner_rewrite",
        "test_cat_and_summary_preserve_utf8_chinese_readme_text",
        "test_reader_can_use_up_to_three_tool_calls_before_forced_summary",
        "test_reader_uses_cat_only_for_non_code_files",
        "test_base_system_prompt_distinguishes_discovery_from_evidence_tools",
        "test_traditional_chinese_prompt_intent_detection",
        "test_real_traditional_chinese_trace_prompt_detection",
    ],
)
