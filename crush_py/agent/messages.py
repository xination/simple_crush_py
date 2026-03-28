from dataclasses import asdict, dataclass, field
from typing import Any, Dict


@dataclass
class Message:
    role: str
    content: Any
    created_at: str
    kind: str = "message"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
