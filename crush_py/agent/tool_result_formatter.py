import re
from typing import Any, Dict, List, Optional

from .message_builder import single_line


def summarize_tool_result(runtime, session_id: str, tool_name: str, arguments: Dict[str, Any], result: str) -> str:
    state = runtime._state_for_session(session_id)
    if tool_name == "cat":
        runtime._maybe_cache_quick_file_from_cat(session_id, arguments, result)
        summary = runtime._cat_summary_from_cache(arguments, result)
        path = str(arguments.get("path", "")).strip()
        if path:
            if path not in state.confirmed_paths:
                state.confirmed_paths.append(path)
            state.file_summaries[path] = summary
        return summary
    if tool_name == "get_outline":
        return summarize_outline_result(arguments, result)
    if tool_name == "grep":
        summary = summarize_grep_result(arguments, result)
        if "Narrow the search" in result:
            state.unresolved_branches.append("grep for `{0}` is still too broad".format(arguments.get("pattern", "")))
        return summary
    if tool_name == "find":
        return summarize_find_result(result)
    if tool_name in ("tree", "ls"):
        return "Directory overview gathered for `{0}`.".format(arguments.get("path", "."))
    return single_line(result, 240)


def summarize_find_result(result: str) -> str:
    lines = [line.strip() for line in result.splitlines() if line.strip() and not line.startswith("Results truncated")]
    if not lines:
        return "No file candidates found."
    preview = ", ".join(lines[:5])
    return "Find produced {0} candidate(s): {1}".format(len(lines), preview)


def summarize_outline_result(arguments: Dict[str, Any], result: str) -> str:
    path = str(arguments.get("path", "")).strip() or "<missing>"
    outline_lines = []
    for line in result.splitlines():
        if "|" not in line or line.startswith("<outline") or line.startswith("</outline>"):
            continue
        outline_lines.append(line.strip())
    if not outline_lines:
        return "Outline for `{0}` found no clear symbols.".format(path)
    return "Outline for `{0}`: {1}".format(path, " ; ".join(outline_lines[:6]))


def summarize_grep_result(arguments: Dict[str, Any], result: str) -> str:
    files = []
    for line in result.splitlines():
        if line.endswith(":") and not line.startswith("  "):
            files.append(line[:-1])
    if not files:
        return "Grep found no clear file candidates for `{0}`.".format(arguments.get("pattern", ""))
    return "Grep for `{0}` matched {1} file(s): {2}".format(
        arguments.get("pattern", ""),
        len(files),
        ", ".join(files[:5]),
    )


def decide_forced_cat(runtime, prompt: str, candidate_paths: List[str], tool_results: List[Dict[str, Any]]) -> Optional[str]:
    unique_paths = sorted(set(path for path in candidate_paths if path))
    grep_too_broad = any(
        item.get("tool_name") == "grep" and "Narrow the search" in item.get("content", "") for item in tool_results
    )
    if grep_too_broad:
        return None
    if len(unique_paths) == 1:
        return unique_paths[0]
    repo_overview_path = repo_overview_anchor_path(runtime, prompt)
    if repo_overview_path:
        return repo_overview_path
    return None


def repo_overview_anchor_path(runtime, prompt: str) -> Optional[str]:
    lowered = prompt.lower()
    if not any(term in lowered for term in ("repo", "repository", "project", "codebase")):
        return None
    if not any(term in lowered for term in ("what is", "what does", "explain", "describe", "for")):
        return None
    readme_path = runtime.config.workspace_root / "README.md"
    if readme_path.is_file():
        return "README.md"
    return None


def extract_candidate_paths(tool_name: str, result: str) -> List[str]:
    if tool_name == "find":
        return [line.strip() for line in result.splitlines() if line.strip() and not line.startswith("Results truncated")]
    if tool_name == "grep":
        paths = []
        for line in result.splitlines():
            item = line.strip()
            if item.endswith(":") and not item.startswith("Line ") and not item.startswith("Search was capped"):
                paths.append(item[:-1])
        return sorted(set(paths))
    return []


def backend_tool_result_content(tool_name: str, result: str, summary: str, max_inline_cat_result_chars: int) -> str:
    if tool_name in ("cat", "get_outline", "tree", "ls", "find", "grep") and len(result) <= max_inline_cat_result_chars:
        return result
    return summary


def tool_result_encoding(tool_name: str, result: str) -> str:
    if tool_name != "cat":
        return ""
    first_line = result.splitlines()[0] if result.splitlines() else ""
    match = re.search(r'encoding="([^"]+)"', first_line)
    if not match:
        return ""
    return match.group(1)
