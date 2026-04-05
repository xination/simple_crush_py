from typing import Any, Dict, List

from ..backends.base import AssistantTurn, ToolCall


def single_line(text: str, max_length: int = 160) -> str:
    normalized = " ".join(str(text).strip().split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + " ..."


def tool_use_id_for_cat(arguments: Dict[str, Any]) -> str:
    path = str(arguments.get("path", "")).strip() or "<missing>"
    if arguments.get("full"):
        return "reader-cat-full:{0}".format(path)
    offset = int(arguments.get("offset", 0) or 0)
    limit = int(arguments.get("limit", 0) or 0)
    return "reader-cat:{0}:{1}:{2}".format(path, offset, limit)


def tool_use_id_for_reader_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    if tool_name == "cat":
        return tool_use_id_for_cat(arguments)
    path = str(arguments.get("path", "")).strip() or "<missing>"
    if tool_name == "grep":
        pattern = str(arguments.get("pattern", "")).strip() or "<missing>"
        include = str(arguments.get("include", "")).strip() or "*"
        return "reader-grep:{0}:{1}:{2}".format(path, include, pattern)
    return "reader-{0}:{1}".format(tool_name, path)


def executed_calls_from_turn(turn: AssistantTurn, limit: int) -> List[ToolCall]:
    if limit <= 0:
        return []
    return turn.tool_calls[:limit]
