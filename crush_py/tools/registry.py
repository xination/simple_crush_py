from typing import Dict

from ..config import AppConfig
from .bash import BashTool
from .base import BaseTool, ToolError
from .edit import EditTool
from .glob import GlobTool
from .grep import GrepTool
from .ls import LsTool
from .view import ViewTool
from .write import WriteTool


class ToolRegistry:
    READ_ONLY_TOOL_NAMES = ("glob", "grep", "ls", "view")
    AUTOMATIC_TOOL_NAMES = ("glob", "grep", "ls", "view", "edit", "bash")

    def __init__(self, config: AppConfig):
        self._tools: Dict[str, BaseTool] = {
            "bash": BashTool(
                config.workspace_root,
                ask_for_confirmation=config.ask_on_shell,
                default_timeout=config.bash_timeout,
            ),
            "edit": EditTool(config.workspace_root, ask_for_confirmation=config.ask_on_write),
            "glob": GlobTool(config.workspace_root),
            "grep": GrepTool(config.workspace_root),
            "ls": LsTool(config.workspace_root),
            "view": ViewTool(config.workspace_root),
            "write": WriteTool(config.workspace_root, ask_for_confirmation=config.ask_on_write),
        }

    def names(self):
        return sorted(self._tools.keys())

    def specs(self, names=None):
        if names is None:
            tools = self._tools.values()
        else:
            tools = [self._tools[name] for name in names if name in self._tools]
        return [tool.spec() for tool in tools]

    def read_only_specs(self):
        return self.specs(self.READ_ONLY_TOOL_NAMES)

    def automatic_specs(self):
        return self.specs(self.AUTOMATIC_TOOL_NAMES)

    def run(self, name, arguments):
        try:
            tool = self._tools[name]
        except KeyError:
            raise ToolError("Unknown tool `{0}`.".format(name))
        return tool.run(arguments)
