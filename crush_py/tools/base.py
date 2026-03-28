from abc import ABC, abstractmethod
from typing import Any, Dict


class ToolError(Exception):
    pass


class BaseTool(ABC):
    name = "base"

    @abstractmethod
    def spec(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def run(self, arguments: Dict[str, Any]) -> str:
        raise NotImplementedError
