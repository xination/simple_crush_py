import json
from dataclasses import dataclass
from typing import Optional

from ..backends.base import BackendError, BaseBackend
from ..output_sanitize import sanitize_text
from .prompt_intent import PromptIntent


SUPPORTED_INTENTS = (
    "direct_file_summary",
    "direct_file_doc_qa",
    "direct_file_trace",
    "guide",
    "repo_search",
    "general_qa",
)

SUPPORTED_CONFIDENCE = ("low", "medium", "high")


@dataclass(frozen=True)
class IntentDecision:
    intent: str
    confidence: str
    target_path: Optional[str]
    needs_full_cat: bool
    source: str


def route_intent_with_llm(
    backend: BaseBackend,
    prompt: str,
    direct_file_path: Optional[str],
    is_code_file: bool,
) -> Optional[IntentDecision]:
    if not direct_file_path:
        return None

    system_prompt = (
        "Intent router:\n"
        "You classify the user's request for runtime routing.\n"
        "Return strict JSON only.\n"
        "Do not answer the user's question.\n"
        "Allowed intents: direct_file_summary, direct_file_doc_qa, direct_file_trace, guide, repo_search, general_qa.\n"
        "Confidence must be one of: low, medium, high."
    )
    messages = [
        {
            "role": "user",
            "content": (
                "Classify this request.\n"
                "user_prompt: {0}\n"
                "direct_file_path: {1}\n"
                "file_kind: {2}\n"
                "Respond with JSON matching:\n"
                '{{"intent":"...","confidence":"...","target_path":"...","needs_full_cat":true}}\n'
                "Use direct_file_doc_qa when the user asks what the file says or what it is for.\n"
                "Use direct_file_summary when the user explicitly wants a summary.\n"
                "Use direct_file_trace when the user wants flow, tracing, origin, usage, or movement through code."
            ).format(
                prompt.strip(),
                direct_file_path,
                "code" if is_code_file else "non_code",
            ),
        }
    ]
    try:
        raw = sanitize_text(backend.generate(system_prompt, messages)).strip()
    except BackendError:
        return None
    return _parse_router_json(raw, direct_file_path)


def heuristic_intent_decision(
    prompt: str,
    direct_file_path: Optional[str],
    is_code_file: bool,
    prompt_intent: PromptIntent,
) -> IntentDecision:
    lowered = prompt.lower()
    if prompt_intent.guide_mode:
        intent = "guide"
    elif prompt_intent.direct_file_trace:
        intent = "direct_file_trace"
    elif prompt_intent.direct_file_summary:
        intent = "direct_file_summary"
    elif (
        direct_file_path
        and not is_code_file
        and not prompt_intent.repo_trace_hint
        and ("according to " in lowered or "based on " in lowered)
    ):
        intent = "direct_file_doc_qa"
    elif direct_file_path:
        intent = "general_qa"
    elif prompt_intent.repo_trace_hint:
        intent = "repo_search"
    else:
        intent = "general_qa"
    return IntentDecision(
        intent=intent,
        confidence="medium",
        target_path=direct_file_path,
        needs_full_cat=bool(direct_file_path and not is_code_file),
        source="heuristic",
    )


def merge_intent_decision(
    llm_decision: Optional[IntentDecision],
    fallback: IntentDecision,
) -> IntentDecision:
    if llm_decision is None:
        return fallback
    if llm_decision.confidence == "low":
        return fallback
    if llm_decision.intent not in SUPPORTED_INTENTS:
        return fallback
    return llm_decision


def _parse_router_json(raw: str, direct_file_path: Optional[str]) -> Optional[IntentDecision]:
    text = raw.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.startswith("```")]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    intent = str(payload.get("intent", "")).strip()
    confidence = str(payload.get("confidence", "")).strip().lower()
    target_path = str(payload.get("target_path", "")).strip() or direct_file_path
    needs_full_cat = bool(payload.get("needs_full_cat", False))
    if intent not in SUPPORTED_INTENTS:
        return None
    if confidence not in SUPPORTED_CONFIDENCE:
        return None
    return IntentDecision(
        intent=intent,
        confidence=confidence,
        target_path=target_path,
        needs_full_cat=needs_full_cat,
        source="llm",
    )
