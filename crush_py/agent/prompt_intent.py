import re
from dataclasses import dataclass
from typing import Optional


BRIEF_SUMMARY_SIGNALS = (
    "briefly",
    "brief summary",
    "quickly summarize",
    "quick summary",
    "short summary",
    "just give me",
    "3 bullets",
    "three bullets",
    "short",
    "摘要",
    "重點",
    "簡述",
    "簡短",
    "簡單說",
    "3點",
    "??",
    "蝪∟膩",
    "蝪∪隤芣?",
    "???渡?",
    "銝?",
)

DETAILED_SUMMARY_SIGNALS = (
    "detailed summary",
    "detailed",
    "full summary",
    "human-review draft",
    "human review draft",
    "candidate responsibilities",
    "responsible for",
    "evidence:",
    "tag:",
    "suggested keep",
    "suggested review/remove",
    "詳細",
    "完整摘要",
    "人類審查",
    "證據",
    "標記",
    "閰喟敦??",
    "閰喟敦隤芣?",
    "摰??",
    "?瑁痊",
    "霅?",
)

SUMMARY_TERMS = (
    "summarize",
    "summary",
    "summarise",
    "explain",
    "what does",
    "responsible for",
    "摘要",
    "簡述",
    "說明",
    "解釋",
    "重點",
    "隤芣?",
    "bullets",
    "??",
    "蝮賜?",
    "璁?",
    "蝪∟膩",
    "閫??",
    "??",
    "?瑁痊",
)

STRUCTURE_TERMS = (
    "class",
    "classes",
    "function",
    "functions",
    "method",
    "methods",
    "structure",
    "outline",
    "symbol",
    "architecture",
    "類別",
    "函式",
    "方法",
    "結構",
    "大綱",
    "架構",
    "symbol",
    "憿",
    "?賢?",
    "?寞?",
    "蝯?",
    "憭抒雇",
    "蝚西?",
    "?嗆?",
)

REPO_TRACE_HINTS = (
    "trace",
    "tracing",
    "call path",
    "used",
    "where ",
    "flow",
    "flows",
    "moves through",
    "追蹤",
    "追踪",
    "流向",
    "流程",
    "傳遞",
    "餈質馱",
    "餈賣",
    "瘚?",
    "瘚?",
    "?澆頝臬?",
)

FLOW_TRACE_SIGNALS = (
    "trace how",
    " flows",
    " flow ",
    "moves through",
    "handled",
    "流向",
    "流程",
    "餈質馱",
    "瘚?",
    "瘚?",
)

VARIABLE_TRACE_SIGNALS = (
    "trace the variable",
    "trace variable",
    "trace how",
    "where ",
    " flows",
    " flow",
    " comes from",
    " is set",
    " is passed",
    "追蹤變數",
    "追踪變數",
    "追蹤 ",
    "追踪 ",
    "流向",
    "在哪裡",
    "傳到哪",
    "怎麼傳",
    "餈質馱霈",
    "餈賣霈",
    "敺鋆∩?",
    "?典鋆∟身摰?",
    "鋡怠??",
    "瘚?",
)

FILE_FLOW_TRACE_SIGNALS = (
    "trace the flow",
    "trace flow",
    "flow for",
    "flow of",
    "control flow",
    "execution flow",
)

GUIDE_CHECKLIST_TERMS = (
    "checklist",
    "step-by-step",
    "step by step",
    "action list",
    "檢查清單",
    "步驟",
    "逐步",
    "瑼Ｘ皜",
    "?郊",
    "甇仿?",
)

GUIDE_TROUBLESHOOTING_TERMS = (
    "stuck",
    "failed",
    "does not work",
    "doesn't work",
    "not work",
    "error",
    "step failed",
    "卡住",
    "失敗",
    "錯誤",
    "不能用",
    "疑難排解",
    "?∩?",
    "憭望?",
    "?航炊",
    "?⊥?",
    "銝??",
    "???",
    "?",
)

GUIDE_LEARNING_PATH_TERMS = (
    "read first",
    "start with",
    "reading order",
    "learn first",
    "which docs should i read",
    "onboarding",
    "先讀",
    "從哪開始",
    "閱讀順序",
    "學習路線",
    "??",
    "??",
    "?梯???",
    "?仿?",
    "銝?",
)

GUIDE_EXACT_EVIDENCE_TERMS = (
    "exact line",
    "exact lines",
    "which line",
    "what line",
    "quote",
    "quoted",
    "verbatim",
    "哪一行",
    "哪些行",
    "精確行",
    "逐字",
    "原文",
    "?芯?銵?",
    "蝎曄Ⅱ銵?",
    "??",
    "撘",
)

GUIDE_REREAD_TERMS = (
    "show me the doc",
    "read the file",
    "reread",
    "re-read",
    "full text",
    "重讀",
    "重新讀",
    "全文",
    "??",
    "?霈",
    "霈瑼?",
    "?冽?",
)

TRACE_VARIABLE_PATTERNS = (
    r"trace the variable\s+([A-Za-z_][A-Za-z0-9_]*)",
    r"trace variable\s+([A-Za-z_][A-Za-z0-9_]*)",
    r"trace how\s+([A-Za-z_][A-Za-z0-9_]*)\s+flows?",
    r"where\s+([A-Za-z_][A-Za-z0-9_]*)\s+is\s+set",
    r"where\s+([A-Za-z_][A-Za-z0-9_]*)\s+comes\s+from",
    r"where\s+([A-Za-z_][A-Za-z0-9_]*)\s+is\s+passed",
    r"追蹤\s*([A-Za-z_][A-Za-z0-9_]*)",
    r"追踪\s*([A-Za-z_][A-Za-z0-9_]*)",
    r"([A-Za-z_][A-Za-z0-9_]*)\s*在\s*.+?\s*的流向",
    r"([A-Za-z_][A-Za-z0-9_]*)\s*流向",
    r"餈質馱霈\s*([A-Za-z_][A-Za-z0-9_]*)",
    r"餈賣霈\s*([A-Za-z_][A-Za-z0-9_]*)",
    r"餈質馱\s*([A-Za-z_][A-Za-z0-9_]*)",
    r"餈賣\s*([A-Za-z_][A-Za-z0-9_]*)",
)


@dataclass(frozen=True)
class PromptIntent:
    direct_file_path: Optional[str]
    guide_mode: bool
    guide_output_mode: str
    trace_variable: Optional[str]
    direct_file_file_flow_trace: bool
    direct_file_flow_trace: bool
    direct_file_variable_trace: bool
    direct_file_summary: bool
    brief_summary: bool
    repo_trace_hint: bool

    @property
    def direct_file_trace(self) -> bool:
        return self.direct_file_file_flow_trace or self.direct_file_flow_trace or self.direct_file_variable_trace


def classify_prompt_intent(prompt: str, direct_file_path: Optional[str]) -> PromptIntent:
    lowered = prompt.lower()
    guide_mode = lowered.lstrip().startswith("guide mode:")
    trace_variable = extract_trace_variable(prompt)
    direct_file_file_flow_trace = bool(
        direct_file_path and not trace_variable and _contains_any(lowered, FILE_FLOW_TRACE_SIGNALS)
    )
    direct_file_flow_trace = bool(direct_file_path and trace_variable and _contains_any(lowered, FLOW_TRACE_SIGNALS))
    direct_file_variable_trace = bool(
        direct_file_path
        and trace_variable
        and not direct_file_flow_trace
        and _contains_any(lowered, VARIABLE_TRACE_SIGNALS)
    )
    has_summary_signal = _contains_any(lowered, SUMMARY_TERMS)
    has_structure_signal = _contains_any(lowered, STRUCTURE_TERMS)
    has_trace_signal = (
        direct_file_file_flow_trace
        or direct_file_flow_trace
        or direct_file_variable_trace
        or _contains_any(lowered, REPO_TRACE_HINTS)
    )
    direct_file_summary = bool(
        direct_file_path and not guide_mode and has_summary_signal and not has_structure_signal and not has_trace_signal
    )
    brief_summary = bool(direct_file_summary and not _contains_any(lowered, DETAILED_SUMMARY_SIGNALS))
    return PromptIntent(
        direct_file_path=direct_file_path,
        guide_mode=guide_mode,
        guide_output_mode=detect_guide_output_mode(prompt),
        trace_variable=trace_variable,
        direct_file_file_flow_trace=direct_file_file_flow_trace,
        direct_file_flow_trace=direct_file_flow_trace,
        direct_file_variable_trace=direct_file_variable_trace,
        direct_file_summary=direct_file_summary,
        brief_summary=brief_summary,
        repo_trace_hint=has_trace_signal,
    )


def detect_guide_output_mode(prompt: str) -> str:
    lowered = prompt.lower()
    if _contains_any(lowered, GUIDE_CHECKLIST_TERMS):
        return "checklist"
    if _contains_any(lowered, GUIDE_TROUBLESHOOTING_TERMS):
        return "troubleshooting"
    if _contains_any(lowered, GUIDE_LEARNING_PATH_TERMS):
        return "learning_path"
    return "beginner_summary"


def should_reread_guide_prompt(prompt: str) -> bool:
    lowered = prompt.lower()
    return _contains_any(lowered, GUIDE_EXACT_EVIDENCE_TERMS) or _contains_any(lowered, GUIDE_REREAD_TERMS)


def extract_trace_variable(prompt: str) -> Optional[str]:
    for pattern in TRACE_VARIABLE_PATTERNS:
        match = re.search(pattern, prompt, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _contains_any(text: str, terms) -> bool:
    return any(term in text for term in terms)
