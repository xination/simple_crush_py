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

READ_ONLY_SELECTION_POLICY = (
    "Read-only tool selection policy: "
    "1) Use `ls` when you do not know the area yet. "
    "2) Use `glob` when you know the filename shape. "
    "3) Use `grep` when you know a symbol or text. "
    "4) Use `view` only after you know the exact path. "
    "5) Locate, then `view`, then answer. Do not guess. "
    "6) Use workspace-relative paths only; never start tool paths with `/`."
)
SMALL_MODEL_READ_ONLY_SELECTION_POLICY = (
    "Repo read policy: "
    "`ls` for area, `glob` for filename, `grep` for text, `view` for exact path. "
    "Locate, then `view`, then answer. Do not guess. "
    "Use workspace-relative paths only."
)


class ToolRegistry:
    READ_ONLY_TOOL_NAMES = ("glob", "grep", "ls", "view")
    AUTOMATIC_TOOL_NAMES = ("glob", "grep", "ls", "view", "edit", "bash")
    MUTATING_TOOL_NAMES = ("edit", "bash")

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

    def specs(self, names=None, prompt_profile: str = "default"):
        if names is None:
            tools = self._tools.values()
        else:
            tools = [self._tools[name] for name in names if name in self._tools]
        specs = []
        for tool in tools:
            spec = dict(tool.spec())
            if spec["name"] in self.READ_ONLY_TOOL_NAMES:
                policy = self._selection_policy_for_prompt_profile(prompt_profile)
                spec["description"] = "{0}\n\n{1}".format(spec.get("description", ""), policy)
            specs.append(spec)
        return specs

    def read_only_specs(self, prompt_profile: str = "default"):
        return self.specs(self.READ_ONLY_TOOL_NAMES, prompt_profile=prompt_profile)

    def automatic_specs(self, prompt_profile: str = "default"):
        return self.specs(self.AUTOMATIC_TOOL_NAMES, prompt_profile=prompt_profile)

    def automatic_specs_for_prompt(self, prompt: str, prompt_profile: str = "default"):
        prompt_lower = (prompt or "").lower()
        if _looks_mutating(prompt_lower):
            return self.automatic_specs(prompt_profile=prompt_profile)
        return self.read_only_specs(prompt_profile=prompt_profile)

    def run(self, name, arguments):
        try:
            tool = self._tools[name]
        except KeyError:
            raise ToolError("Unknown tool `{0}`.".format(name))
        return tool.run(arguments)

    def _selection_policy_for_prompt_profile(self, prompt_profile: str) -> str:
        if prompt_profile in ("small_model", "small_model_strict"):
            return SMALL_MODEL_READ_ONLY_SELECTION_POLICY
        return READ_ONLY_SELECTION_POLICY


def _looks_mutating(prompt_lower: str) -> bool:
    keywords = (
        "write",
        "create",
        "modify",
        "edit",
        "update",
        "rewrite",
        "replace",
        "run",
        "bash",
        "command",
        "fix",
    )
    return any(keyword in prompt_lower for keyword in keywords)
