from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Message:
    role: str = ""
    content: Any = ""
    created_at: str = ""
    kind: str = "message"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        if self.kind == "tool_use" and self.metadata.get("__flat__"):
            payload = {"kind": "tool_use"}
            tool_name = self.metadata.get("tool")
            if tool_name:
                payload["tool"] = tool_name
            args = self.metadata.get("args")
            if args:
                payload["args"] = args
            agent = self.metadata.get("agent")
            if agent:
                payload["agent"] = agent
            text = self.metadata.get("text")
            if text:
                payload["text"] = text
            return payload
        if self.kind == "tool_result" and self.metadata.get("__flat__"):
            payload = {"kind": "tool_result"}
            tool_name = self.metadata.get("tool")
            if tool_name:
                payload["tool"] = tool_name
            summary = self.metadata.get("summary")
            if summary:
                payload["summary"] = summary
            args = self.metadata.get("args")
            if args:
                payload["args"] = args
            agent = self.metadata.get("agent")
            if agent:
                payload["agent"] = agent
            encoding_used = self.metadata.get("encoding")
            if encoding_used:
                payload["encoding"] = encoding_used
            if self.metadata.get("error"):
                payload["error"] = True
            if "duration_ms" in self.metadata:
                payload["duration_ms"] = self.metadata["duration_ms"]
            return payload
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "kind": self.kind,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Message":
        kind = payload.get("kind", "message")
        if kind == "tool_use" and "role" not in payload:
            metadata = {
                "tool": payload.get("tool", ""),
                "args": dict(payload.get("args", {}) or {}),
                "agent": payload.get("agent", ""),
                "text": payload.get("text", ""),
                "__flat__": True,
            }
            metadata = {key: value for key, value in metadata.items() if value not in ("", {}, None)}
            return cls(kind="tool_use", content="", metadata=metadata)
        if kind == "tool_result" and "role" not in payload:
            metadata = {
                "tool": payload.get("tool", ""),
                "summary": payload.get("summary", ""),
                "args": dict(payload.get("args", {}) or {}),
                "agent": payload.get("agent", ""),
                "encoding": payload.get("encoding", ""),
                "error": payload.get("error", False),
                "__flat__": True,
            }
            if "duration_ms" in payload:
                metadata["duration_ms"] = payload["duration_ms"]
            metadata = {
                key: value
                for key, value in metadata.items()
                if value not in ("", None, False)
            }
            return cls(kind="tool_result", content="", metadata=metadata)
        return cls(
            role=payload.get("role", ""),
            content=payload.get("content", ""),
            created_at=payload.get("created_at", ""),
            kind=kind,
            metadata=dict(payload.get("metadata", {}) or {}),
        )
