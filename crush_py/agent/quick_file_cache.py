from typing import Any, Dict, List, Optional, Tuple

from ..tools.base import ToolError
from ..tools.common import ensure_in_workspace
from .message_builder import single_line


def normalize_quick_file_path(runtime, rel_path: str) -> str:
    normalized = str(rel_path or "").strip().replace("\\", "/")
    if not normalized:
        raise ToolError("`--file` requires a workspace-relative path.")
    abs_path = (runtime.config.workspace_root / normalized).resolve()
    ensure_in_workspace(runtime.config.workspace_root, abs_path)
    if not abs_path.exists():
        raise ToolError("File not found: {0}".format(normalized))
    if abs_path.is_dir():
        raise ToolError("Path is a directory: {0}".format(normalized))
    try:
        return abs_path.relative_to(runtime.config.workspace_root).as_posix()
    except ValueError:
        raise ToolError("Path is outside workspace root: {0}".format(normalized))


def read_quick_file(runtime, rel_path: str, max_quick_file_size: int) -> Tuple[str, Dict[str, str]]:
    abs_path = (runtime.config.workspace_root / rel_path).resolve()
    stat = abs_path.stat()
    if stat.st_size > max_quick_file_size:
        raise ToolError("File is too large for quick mode: {0}".format(rel_path))
    state = runtime._state_for_session(runtime.active_session.id)
    cache_key = (rel_path, stat.st_mtime, stat.st_size)
    if cache_key in state.quick_file_cache:
        return state.quick_file_cache[cache_key], {
            "status": "hit",
            "source": state.quick_file_cache_sources.get(cache_key, "memory_cache"),
        }
    text = runtime.read_text_with_fallback(abs_path)
    state.quick_file_cache[cache_key] = text
    state.quick_file_cache_sources[cache_key] = "disk"
    return text, {"status": "miss", "source": "disk"}


def maybe_cache_quick_file_from_cat(
    runtime,
    session_id: str,
    arguments: Dict[str, Any],
    result: str,
    max_quick_file_size: int,
) -> None:
    rel_path = str(arguments.get("path", "")).strip()
    if not rel_path or "File has more lines." in result:
        return
    normalized_path = rel_path.replace("\\", "/")
    abs_path = (runtime.config.workspace_root / normalized_path).resolve()
    try:
        ensure_in_workspace(runtime.config.workspace_root, abs_path)
    except ToolError:
        return
    if not abs_path.exists() or abs_path.is_dir():
        return
    stat = abs_path.stat()
    if stat.st_size > max_quick_file_size:
        return
    text = extract_text_from_cat_result(result)
    if text is None:
        return
    state = runtime._state_for_session(session_id)
    cache_key = (normalized_path, stat.st_mtime, stat.st_size)
    state.quick_file_cache = {
        key: value for key, value in state.quick_file_cache.items() if not (key[0] == normalized_path and key != cache_key)
    }
    state.quick_file_cache_sources = {
        key: value
        for key, value in state.quick_file_cache_sources.items()
        if not (key[0] == normalized_path and key != cache_key)
    }
    state.quick_file_cache[cache_key] = text
    state.quick_file_cache_sources[cache_key] = "cat_full"


def extract_text_from_cat_result(result: str) -> Optional[str]:
    extracted_lines: List[str] = []
    saw_file_block = False
    for line in result.splitlines():
        if line.startswith("<file "):
            saw_file_block = True
            continue
        if line.startswith("</file>"):
            continue
        if "|" not in line:
            continue
        _, content = line.split("|", 1)
        extracted_lines.append(content)
    if not saw_file_block:
        return None
    return "\n".join(extracted_lines)


def cat_summary_from_cache(runtime, arguments: Dict[str, Any], result: str) -> str:
    rel_path = str(arguments.get("path", "")).strip()
    offset = int(arguments.get("offset", 0) or 0)
    limit = int(arguments.get("limit", 80) or 80)
    full = bool(arguments.get("full", False))
    abs_path = (runtime.config.workspace_root / rel_path).resolve()
    mtime = abs_path.stat().st_mtime if abs_path.exists() else 0.0
    state = state_for_any_session_path(runtime, rel_path)
    cache_key = (rel_path, mtime, offset, limit if not full else -1)
    if cache_key in state.summary_cache:
        return state.summary_cache[cache_key]

    numbered_lines = []
    for line in result.splitlines():
        if "|" not in line or line.startswith("<file") or line.startswith("</file>"):
            continue
        numbered_lines.append(line.strip())
    preview = numbered_lines[:6]
    if full:
        summary = "Read full file `{0}` ({1} line(s)). Key excerpts: {2}".format(
            rel_path or "<missing>",
            max(len(numbered_lines), 1),
            " ; ".join(single_line(item, 80) for item in preview) if preview else "no text captured",
        )
    else:
        summary = "Read `{0}` lines {1}-{2}. Key excerpts: {3}".format(
            rel_path or "<missing>",
            offset + 1,
            offset + max(len(numbered_lines), 1),
            " ; ".join(single_line(item, 80) for item in preview) if preview else "no text captured",
        )
    if not full and "File has more lines." in result:
        summary += " More lines remain."
    state.summary_cache[cache_key] = summary
    return summary


def state_for_any_session_path(runtime, rel_path: str):
    assert runtime.active_session is not None
    return runtime._state_for_session(runtime.active_session.id)
