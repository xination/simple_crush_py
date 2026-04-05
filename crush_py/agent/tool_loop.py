from typing import Any, Dict, List, Tuple

from ..backends.base import BackendError, ToolCall
from ..output_sanitize import sanitize_content, sanitize_text
from ..tools.base import ToolError


def ask_with_tool_loop(
    runtime,
    session_id: str,
    backend,
    messages: List[Dict[str, Any]],
    prompt: str,
    system_prompt: str,
    decision,
    stream: bool,
    max_tool_rounds: int,
    locator_tool_names: Tuple[str, ...],
) -> str:
    conversation = list(messages)
    intent = runtime._prompt_intent(prompt)
    forced_cat_path = intent.direct_file_path
    reader_completed_paths = set()
    evidence_collected = False
    evidence_retry_used = False

    if forced_cat_path is not None:
        runtime._record_reader_delegate(session_id, forced_cat_path)
        reader_summary = runtime._run_reader_agent(session_id, backend, prompt, forced_cat_path, stream=stream)
        reader_completed_paths.add(forced_cat_path)
        evidence_collected = True
        if intent.direct_file_summary:
            final_text = runtime._finalize_direct_file_summary_output(session_id, prompt, reader_summary.strip())
            runtime._emit_stream_final_text(final_text, stream=stream)
            return runtime._store_final_assistant_text(session_id, final_text)
        if intent.guide_mode or intent.direct_file_trace:
            final_text = reader_summary.strip()
            runtime._emit_stream_final_text(final_text, stream=stream)
            return runtime._store_final_assistant_text(session_id, final_text)
        if runtime._should_accept_reader_summary_directly(prompt, reader_summary):
            final_text = reader_summary.strip()
            runtime._emit_stream_final_text(final_text, stream=stream)
            return runtime._store_final_assistant_text(session_id, final_text)
        conversation = runtime._append_reader_summary_message(conversation, forced_cat_path, reader_summary)

    for _ in range(max_tool_rounds):
        current_tools = runtime.tools.specs(locator_tool_names)
        turn = runtime._generate_turn_with_retry(backend, system_prompt, conversation, tools=current_tools, stream=stream)
        final_text = sanitize_text(turn.text).strip()
        final_raw_content = sanitize_content(turn.raw_content or runtime._assistant_text_blocks(turn))
        if not turn.tool_calls:
            if decision.needs_tools and not evidence_collected:
                if not evidence_retry_used:
                    evidence_retry_used = True
                    conversation.append(
                        {
                            "role": "user",
                            "content": (
                                "Evidence is required before answering this request.\n"
                                "Use at least one local discovery tool first: ls, tree, find, or grep.\n"
                                "Do not answer from prior knowledge alone."
                            ),
                        }
                    )
                    continue
                fallback_text = runtime._repo_evidence_required_message(prompt)
                runtime._emit_stream_final_text(fallback_text, stream=stream)
                return runtime._store_final_assistant_text(session_id, fallback_text)
            if stream and final_text:
                runtime._clear_thinking_indicator_line()
                print(final_text, end="", flush=True)
                print("")
            runtime.session_store.append_message(
                session_id,
                "assistant",
                final_text,
                metadata={"raw_content": final_raw_content},
            )
            return final_text

        assistant_content = runtime._assistant_content_for_tool_turn(turn)
        conversation.append({"role": "assistant", "content": assistant_content})
        executed_calls = executed_calls_from_turn(turn)
        runtime._record_agent_tool_use(session_id, "planner", turn, executed_calls)
        tool_results, candidate_paths = runtime._execute_agent_tool_calls(
            session_id,
            "planner",
            executed_calls,
            collect_candidate_paths=True,
        )
        if executed_calls:
            evidence_collected = True

        conversation.append({"role": "user", "content": tool_results})
        reader_path = runtime._decide_forced_cat(prompt, candidate_paths, tool_results)
        if reader_path and reader_path not in reader_completed_paths:
            runtime._record_reader_delegate(session_id, reader_path)
            reader_summary = runtime._run_reader_agent(session_id, backend, prompt, reader_path)
            reader_completed_paths.add(reader_path)
            evidence_collected = True
            conversation = runtime._append_reader_summary_message(conversation, reader_path, reader_summary)

    raise BackendError("Tool loop exceeded the maximum number of rounds.")


def repo_evidence_required_message(prompt: str) -> str:
    return (
        "I need to inspect local repository files before answering `{0}` reliably. "
        "Please try again, or ask with a concrete file/path such as `summarize README.md`."
    ).format(prompt.strip())


def store_final_assistant_text(runtime, session_id: str, text: str) -> str:
    final_text = sanitize_text(text).strip()
    runtime.session_store.append_message(
        session_id,
        "assistant",
        final_text,
        metadata={"raw_content": [{"type": "text", "text": final_text}]},
    )
    return final_text


def should_accept_reader_summary_directly(runtime, prompt: str, reader_summary: str) -> bool:
    summary = sanitize_text(reader_summary).strip()
    if not summary:
        return False
    intent = runtime._prompt_intent(prompt)
    if not intent.direct_file_path:
        return False
    if intent.repo_trace_hint or intent.guide_mode or intent.direct_file_trace or intent.direct_file_summary:
        return False
    lowered = prompt.lower()
    if lowered.startswith(("find ", "locate ", "grep ", "trace ")):
        return False
    markers = (
        "Confirmed path:",
        "Summary:",
        "According to `",
        "According to ",
        "File flow for human review:",
        "Flow trace for human review:",
        "Variable trace for human review:",
        "Candidate responsibilities for human review:",
        "Checklist:",
        "Beginner summary:",
    )
    if any(marker in summary for marker in markers):
        return True
    return len(summary) >= 120


def emit_stream_final_text(runtime, text: str, stream: bool = False) -> None:
    if not stream or not text:
        return
    runtime._clear_thinking_indicator_line()
    print(text, end="", flush=True)
    print("")


def record_agent_tool_use(runtime, session_id: str, agent: str, turn: Any, executed_calls: List[ToolCall]) -> None:
    runtime.session_store.append_message(
        session_id,
        "assistant",
        runtime._squashed_assistant_text(turn),
        kind="tool_use",
        metadata={
            "agent": agent,
            "tool": executed_calls[0].name if executed_calls else "",
            "tool_names": [tool_call.name for tool_call in executed_calls],
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "arguments": dict(tool_call.arguments),
                }
                for tool_call in executed_calls
            ],
            "assistant_text": runtime._squashed_assistant_text(turn),
        },
    )


def execute_agent_tool_calls(
    runtime,
    session_id: str,
    agent: str,
    executed_calls: List[ToolCall],
    collect_candidate_paths: bool = False,
):
    tool_results: List[Dict[str, Any]] = []
    candidate_paths: List[str] = []
    for tool_call in executed_calls:
        arguments = dict(tool_call.arguments)
        try:
            result = runtime.run_tool(tool_call.name, arguments)
        except ToolError as exc:
            result = "Tool error: {0}".format(exc)
        result = sanitize_text(result)
        summary = runtime._summarize_tool_result(session_id, tool_call.name, arguments, result)
        backend_tool_result = {
            "type": "tool_result",
            "tool_use_id": tool_call.id,
            "tool_name": tool_call.name,
            "content": runtime._backend_tool_result_content(tool_call.name, result, summary),
        }
        tool_results.append(backend_tool_result)
        if collect_candidate_paths:
            candidate_paths.extend(runtime._extract_candidate_paths(tool_call.name, result))
        runtime.session_store.append_message(
            session_id,
            "user",
            backend_tool_result["content"],
            kind="tool_result",
            metadata={
                "agent": agent,
                "tool": tool_call.name,
                "tool_name": tool_call.name,
                "tool_arguments": arguments,
                "tool_use_id": tool_call.id,
                "summary": summary,
                "encoding_used": runtime._tool_result_encoding(tool_call.name, result),
            },
        )
    return tool_results, candidate_paths


def executed_calls_from_turn(turn, limit: int = 2) -> List[ToolCall]:
    if limit <= 0:
        return []
    return turn.tool_calls[:limit]
