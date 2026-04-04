import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..agent.messages import Message
from ..output_sanitize import sanitize_content, sanitize_text


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SessionMeta:
    id: str
    title: str
    backend: str
    model: str
    created_at: str
    updated_at: str


class SessionStore:
    def __init__(self, sessions_dir: Path, trace_mode: str = "lean"):
        self.sessions_dir = Path(sessions_dir)
        self.trace_mode = trace_mode
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def create_session(self, backend: str, model: str, title: str = "Untitled Session") -> SessionMeta:
        session_id = str(uuid.uuid4())
        now = utc_now_iso()
        meta = SessionMeta(
            id=session_id,
            title=title,
            backend=backend,
            model=model,
            created_at=now,
            updated_at=now,
        )
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "artifacts").mkdir(exist_ok=True)
        self._write_meta(meta)
        (session_dir / "messages.jsonl").touch()
        return meta

    def list_sessions(self) -> List[SessionMeta]:
        metas = []
        for path in sorted(self.sessions_dir.glob("*/session.json")):
            metas.append(self.load_session(path.parent.name))
        metas.sort(key=lambda item: item.updated_at, reverse=True)
        return metas

    def load_session(self, session_id: str) -> SessionMeta:
        path = self._session_dir(session_id) / "session.json"
        with path.open("r", encoding="utf-8") as handle:
            return SessionMeta(**json.load(handle))

    def append_message(
        self,
        session_id: str,
        role: str,
        content: Any,
        kind: str = "message",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Message:
        safe_content = sanitize_content(content)
        safe_metadata = sanitize_content(metadata or {})
        sanitized_metadata = self._sanitize_metadata(kind, safe_metadata)
        message = Message(
            role=role,
            content=safe_content,
            created_at=utc_now_iso(),
            kind=kind,
            metadata=sanitized_metadata,
        )
        path = self._session_dir(session_id) / "messages.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(message.to_dict(), ensure_ascii=False) + "\n")
        meta = self.load_session(session_id)
        meta.updated_at = utc_now_iso()
        if role == "user" and kind == "message" and meta.title == "Untitled Session":
            meta.title = _derive_title(str(content))
        self._write_meta(meta)
        return message

    def load_messages(self, session_id: str) -> List[Message]:
        path = self._session_dir(session_id) / "messages.jsonl"
        if not path.exists():
            return []
        messages = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                messages.append(Message.from_dict(json.loads(line)))
        return messages

    def _write_meta(self, meta: SessionMeta) -> None:
        path = self._session_dir(meta.id) / "session.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(meta), handle, ensure_ascii=False, indent=2)

    def _session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def _sanitize_metadata(self, kind: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        if self.trace_mode == "debug":
            return dict(metadata)

        if kind == "tool_use":
            tool_name = _first_tool_name(metadata)
            lean = {
                "tool": tool_name,
                "args": _first_tool_args(metadata),
                "agent": metadata.get("agent", ""),
                "text": sanitize_text(
                    metadata.get("assistant_text", "") or _assistant_text_from_raw_content(metadata.get("raw_content", []))
                ),
                "__flat__": True,
            }
            return {key: value for key, value in lean.items() if value not in ("", {}, None)}
        if kind == "tool_result":
            tool_name = metadata.get("tool_name", "") or metadata.get("tool", "")
            tool_arguments = metadata.get("tool_arguments", {}) or metadata.get("args", {})
            lean = {
                "tool": tool_name,
                "summary": sanitize_text(metadata.get("summary", "")),
                "args": dict(tool_arguments) if isinstance(tool_arguments, dict) else {},
                "agent": metadata.get("agent", ""),
                "encoding": metadata.get("encoding_used", ""),
                "__flat__": True,
            }
            if metadata.get("error"):
                lean["error"] = True
            if "duration_ms" in metadata:
                lean["duration_ms"] = metadata["duration_ms"]
            return {key: value for key, value in lean.items() if value not in ("", None)}
        return {}


def _derive_title(content: str) -> str:
    title = " ".join(content.strip().split())
    if not title:
        return "Untitled Session"
    return title[:60]


def _pick_keys(metadata: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    result = {}
    for key in keys:
        if key in metadata:
            result[key] = metadata[key]
    return result


def _assistant_text_from_raw_content(raw_content: Any) -> str:
    if not isinstance(raw_content, list):
        return ""
    for item in raw_content:
        if isinstance(item, dict) and item.get("type") == "text":
            return str(item.get("text", "")).strip()
    return ""


def _first_tool_name(metadata: Dict[str, Any]) -> str:
    tool_name = str(metadata.get("tool_name", "")).strip()
    if tool_name:
        return tool_name
    tool_calls = metadata.get("tool_calls", [])
    if tool_calls:
        return str(tool_calls[0].get("name", "")).strip()
    tool_names = metadata.get("tool_names", [])
    if tool_names:
        return str(tool_names[0]).strip()
    return ""


def _first_tool_args(metadata: Dict[str, Any]) -> Dict[str, Any]:
    tool_arguments = metadata.get("tool_arguments")
    if isinstance(tool_arguments, dict):
        return dict(tool_arguments)
    tool_calls = metadata.get("tool_calls", [])
    if tool_calls and isinstance(tool_calls[0].get("arguments"), dict):
        return dict(tool_calls[0]["arguments"])
    return {}
