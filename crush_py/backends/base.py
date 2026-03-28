from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


class BackendError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class AssistantTurn:
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw_content: List[Dict[str, Any]] = field(default_factory=list)


class BaseBackend(ABC):
    name = "base"

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[dict]] = None,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def stream_generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[dict]] = None,
    ) -> Iterable[str]:
        raise NotImplementedError

    def generate_turn(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[dict]] = None,
    ) -> AssistantTurn:
        return AssistantTurn(text=self.generate(system_prompt, messages, tools=tools))

    def generate_with_metadata(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[dict]] = None,
    ) -> AssistantTurn:
        turn = self.generate_turn(system_prompt, messages, tools=tools)
        if turn.raw_content:
            return turn
        if not turn.text:
            return turn
        return AssistantTurn(
            text=turn.text,
            tool_calls=turn.tool_calls,
            raw_content=[{"type": "text", "text": turn.text}],
        )

    def supports_tool_calls(self) -> bool:
        return False
