import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..agent.messages import Message


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
    def __init__(self, sessions_dir: Path):
        self.sessions_dir = Path(sessions_dir)
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
        message = Message(
            role=role,
            content=content,
            created_at=utc_now_iso(),
            kind=kind,
            metadata=metadata or {},
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
                messages.append(Message(**json.loads(line)))
        return messages

    def _write_meta(self, meta: SessionMeta) -> None:
        path = self._session_dir(meta.id) / "session.json"
        with path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(meta), handle, ensure_ascii=False, indent=2)

    def _session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id


def _derive_title(content: str) -> str:
    title = " ".join(content.strip().split())
    if not title:
        return "Untitled Session"
    return title[:60]
