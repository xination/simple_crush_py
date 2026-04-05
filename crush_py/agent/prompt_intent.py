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
    "簡述",
    "簡單說明",
    "重點整理",
    "三點",
    "3點",
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
    "詳細摘要",
    "詳細說明",
    "完整摘要",
    "職責",
    "證據",
)

SUMMARY_TERMS = (
    "summarize",
    "summary",
    "summarise",
    "explain",
    "what does",
    "responsible for",
    "摘要",
    "總結",
    "概要",
    "簡述",
    "說明",
    "解釋",
    "重點",
    "職責",
    "bullets",
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
    "符號",
    "架構",
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
    "追查",
    "流向",
    "流程",
    "呼叫路徑",
)

FLOW_TRACE_SIGNALS = (
    "trace how",
    " flows",
    " flow ",
    "moves through",
    "handled",
    "追蹤",
    "流向",
    "流程",
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
    "追查變數",
    "從哪裡來",
    "在哪裡設定",
    "被傳到",
    "流向",
)

GUIDE_CHECKLIST_TERMS = (
    "checklist",
    "step-by-step",
    "step by step",
    "action list",
    "檢查清單",
    "逐步",
    "步驟",
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
    "無法",
    "不能用",
    "故障排除",
    "排錯",
)

GUIDE_LEARNING_PATH_TERMS = (
    "read first",
    "start with",
    "reading order",
    "learn first",
    "which docs should i read",
    "onboarding",
    "先讀",
    "先看",
    "閱讀順序",
    "入門",
    "上手",
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
    "精確行號",
    "逐字",
    "引用",
)

GUIDE_REREAD_TERMS = (
    "show me the doc",
    "read the file",
    "reread",
    "re-read",
    "full text",
    "重讀",
    "重新讀",
    "讀檔案",
    "全文",
)

TRACE_VARIABLE_PATTERNS = (
    r"trace the variable\s+([A-Za-z_][A-Za-z0-9_]*)",
    r"trace variable\s+([A-Za-z_][A-Za-z0-9_]*)",
    r"trace how\s+([A-Za-z_][A-Za-z0-9_]*)\s+flows?",
    r"where\s+([A-Za-z_][A-Za-z0-9_]*)\s+is\s+set",
    r"where\s+([A-Za-z_][A-Za-z0-9_]*)\s+comes\s+from",
    r"where\s+([A-Za-z_][A-Za-z0-9_]*)\s+is\s+passed",
    r"追蹤變數\s*([A-Za-z_][A-Za-z0-9_]*)",
    r"追查變數\s*([A-Za-z_][A-Za-z0-9_]*)",
    r"追蹤\s*([A-Za-z_][A-Za-z0-9_]*)\s*的?流向",
    r"追查\s*([A-Za-z_][A-Za-z0-9_]*)\s*的?流向",
    r"([A-Za-z_][A-Za-z0-9_]*)\s*從哪裡來",
    r"([A-Za-z_][A-Za-z0-9_]*)\s*在哪裡設定",
    r"([A-Za-z_][A-Za-z0-9_]*)\s*被傳到哪裡",
)


@dataclass(frozen=True)
class PromptIntent:
    direct_file_path: Optional[str]
    guide_mode: bool
    guide_output_mode: str
    trace_variable: Optional[str]
    direct_file_flow_trace: bool
    direct_file_variable_trace: bool
    direct_file_summary: bool
    brief_summary: bool
    repo_trace_hint: bool

    @property
    def direct_file_trace(self) -> bool:
        return self.direct_file_flow_trace or self.direct_file_variable_trace


def classify_prompt_intent(prompt: str, direct_file_path: Optional[str]) -> PromptIntent:
    lowered = prompt.lower()
    guide_mode = lowered.lstrip().startswith("guide mode:")
    trace_variable = extract_trace_variable(prompt)
    direct_file_flow_trace = bool(direct_file_path and trace_variable and _contains_any(lowered, FLOW_TRACE_SIGNALS))
    direct_file_variable_trace = bool(
        direct_file_path
        and trace_variable
        and not direct_file_flow_trace
        and _contains_any(lowered, VARIABLE_TRACE_SIGNALS)
    )
    has_summary_signal = _contains_any(lowered, SUMMARY_TERMS)
    has_structure_signal = _contains_any(lowered, STRUCTURE_TERMS)
    has_trace_signal = direct_file_flow_trace or direct_file_variable_trace or _contains_any(lowered, REPO_TRACE_HINTS)
    direct_file_summary = bool(direct_file_path and not guide_mode and has_summary_signal and not has_structure_signal and not has_trace_signal)
    brief_summary = bool(direct_file_summary and not _contains_any(lowered, DETAILED_SUMMARY_SIGNALS))
    return PromptIntent(
        direct_file_path=direct_file_path,
        guide_mode=guide_mode,
        guide_output_mode=detect_guide_output_mode(prompt),
        trace_variable=trace_variable,
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
