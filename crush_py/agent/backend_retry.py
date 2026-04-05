from typing import Any, Dict, List, Optional

from ..backends.base import AssistantTurn, BackendError
from ..output_sanitize import sanitize_content, sanitize_text


def ask_without_tools(runtime, session_id: str, backend, messages: List[Dict[str, Any]], system_prompt: str, stream: bool = False) -> str:
    if stream:
        final_text = runtime._stream_text_with_retry(backend, system_prompt, messages)
        final_raw_content = [{"type": "text", "text": final_text}] if final_text else []
    else:
        turn = runtime._generate_turn_with_retry(backend, system_prompt, messages, tools=None, stream=False)
        final_text = sanitize_text(turn.text).strip()
        final_raw_content = sanitize_content(turn.raw_content or runtime._assistant_text_blocks(turn))
    runtime.session_store.append_message(
        session_id,
        "assistant",
        final_text,
        metadata={"raw_content": final_raw_content},
    )
    return final_text


def stream_text_with_retry(runtime, backend, system_prompt: str, messages: List[Dict[str, Any]], max_backend_retries: int) -> str:
    errors: List[str] = []
    attempts = [messages]
    fallback_messages = runtime._fallback_messages_for_retry(messages)
    if fallback_messages != messages:
        attempts.append(fallback_messages)
    for retry_index in range(max_backend_retries + 1):
        for candidate_messages in attempts:
            try:
                chunks = []
                runtime._clear_thinking_indicator_line()
                for chunk in backend.stream_generate(system_prompt, candidate_messages, tools=None):
                    chunk_text = str(chunk)
                    if not chunk_text:
                        continue
                    chunks.append(chunk_text)
                    print(chunk_text, end="", flush=True)
                print("")
                return sanitize_text("".join(chunks)).strip()
            except BackendError as exc:
                errors.append(str(exc))
        if retry_index >= max_backend_retries:
            break
    raise BackendError("Backend streaming turn failed after retry/fallback: {0}".format(" | ".join(errors)))


def generate_turn_with_retry(
    runtime,
    backend,
    system_prompt: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[dict]] = None,
    stream: bool = False,
    max_backend_retries: int = 1,
) -> AssistantTurn:
    errors: List[str] = []
    attempts = [messages]
    fallback_messages = runtime._fallback_messages_for_retry(messages)
    if fallback_messages != messages:
        attempts.append(fallback_messages)
    for retry_index in range(max_backend_retries + 1):
        for candidate_messages in attempts:
            try:
                if stream:
                    turn = backend.stream_generate_turn(system_prompt, candidate_messages, tools=tools)
                    text = sanitize_text(turn.text)
                    if text:
                        return AssistantTurn(
                            text=text,
                            tool_calls=turn.tool_calls,
                            raw_content=sanitize_content(turn.raw_content or runtime._assistant_text_blocks(turn)),
                        )
                    if turn.tool_calls:
                        return AssistantTurn(
                            text="",
                            tool_calls=turn.tool_calls,
                            raw_content=sanitize_content(turn.raw_content),
                        )
                turn = backend.generate_turn(system_prompt, candidate_messages, tools=tools)
                text = sanitize_text(turn.text)
                return AssistantTurn(
                    text=text,
                    tool_calls=turn.tool_calls,
                    raw_content=sanitize_content(turn.raw_content or runtime._assistant_text_blocks(turn)),
                )
            except BackendError as exc:
                errors.append(str(exc))
        if retry_index >= max_backend_retries:
            break
    raise BackendError("Backend turn failed after retry/fallback: {0}".format(" | ".join(errors)))


def fallback_messages_for_retry(runtime, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fallback: List[Dict[str, Any]] = []
    changed = False
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, list):
            compact_blocks = []
            for item in content:
                compact_item = dict(item)
                if compact_item.get("type") == "tool_result":
                    compact_content = runtime._compact_retry_tool_result(
                        compact_item.get("tool_name", ""),
                        compact_item.get("content", ""),
                    )
                    if compact_content != compact_item.get("content", ""):
                        changed = True
                    compact_item["content"] = compact_content
                compact_blocks.append(compact_item)
            fallback.append({"role": role, "content": compact_blocks})
            continue
        if isinstance(content, str):
            compact_text = compact_retry_text(content)
            if compact_text != content:
                changed = True
            fallback.append({"role": role, "content": compact_text})
            continue
        fallback.append(message)
    return fallback if changed else messages


def compact_retry_tool_result(tool_name: str, content: str) -> str:
    text = sanitize_text(content)
    if tool_name == "cat":
        return compact_retry_text(text, limit=800)
    if tool_name == "grep":
        return compact_retry_text(text, limit=600)
    return compact_retry_text(text, limit=400)


def compact_retry_text(text: str, limit: int = 800) -> str:
    normalized = sanitize_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "\n...[retry fallback compacted]"
