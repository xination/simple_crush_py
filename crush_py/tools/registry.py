from typing import Dict

from ..config import AppConfig
from .base import BaseTool, ToolError
from .cat import CatTool
from .find import FindTool
from .get_outline import GetOutlineTool
from .grep import GrepTool
from .ls import LsTool
from .tree import TreeTool

class ToolRegistry:
    READ_ONLY_TOOL_NAMES = ("ls", "tree", "find", "grep", "get_outline", "cat")

    def __init__(self, config: AppConfig):
        self._tools: Dict[str, BaseTool] = {
            "cat": CatTool(config.workspace_root),
            "find": FindTool(config.workspace_root),
            "get_outline": GetOutlineTool(config.workspace_root),
            "grep": GrepTool(config.workspace_root),
            "ls": LsTool(config.workspace_root),
            "tree": TreeTool(config.workspace_root),
        }

    def names(self):
        return sorted(self._tools.keys())

    def specs(self, names=None):
        if names is None:
            tools = self._tools.values()
        else:
            tools = [self._tools[name] for name in names if name in self._tools]
        return [dict(tool.spec()) for tool in tools]

    def automatic_specs(self):
        return self.specs(self.READ_ONLY_TOOL_NAMES)

    def automatic_specs_for_prompt(self, prompt: str):
        lowered = prompt.lower()
        if "outline" in lowered or "symbol" in lowered:
            return self.specs(("get_outline", "cat"))
        return self.automatic_specs()

    def run(self, name, arguments):
        try:
            tool = self._tools[name]
        except KeyError:
            raise ToolError("Unknown tool `{0}`.".format(name))
        return tool.run(arguments)
