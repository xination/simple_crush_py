from typing import Any, Callable, Dict, List, Optional

from ..backends.anthropic import AnthropicBackend
from ..backends.base import AssistantTurn, BackendError, BaseBackend
from ..backends.hf_local import HuggingFaceLocalBackend
from ..backends.openai_compat import OpenAICompatBackend
from ..config import AppConfig, BackendConfig
from ..store.session_store import SessionMeta, SessionStore
from ..tools.base import ToolError
from ..tools.registry import ToolRegistry


DEFAULT_SYSTEM_PROMPT = """You are crush_py, a coding assistant for older and constrained environments.

Keep answers practical and concise. Prefer safe, incremental changes.
Do not assume tools are available unless the runtime says so.
When you need to inspect the repository, prefer the available read-only tools
instead of guessing.
"""

MAX_TOOL_ROUNDS = 8


class AgentRuntime:
    def __init__(self, config: AppConfig, session_store: SessionStore):
        self.config = config
        self.session_store = session_store
        self.active_session: Optional[SessionMeta] = None
        self.active_backend_name = config.default_backend
        self.tools = ToolRegistry(config)
        self.tool_confirmation_callback: Optional[Callable[[str, Dict[str, Any], str], bool]] = None

    def new_session(self, backend_name: Optional[str] = None, title: str = "Untitled Session") -> SessionMeta:
        backend_cfg = self._get_backend_config(backend_name)
        session = self.session_store.create_session(
            backend=backend_cfg.name,
            model=backend_cfg.model,
            title=title,
        )
        self.active_session = session
        self.active_backend_name = backend_cfg.name
        return session

    def use_session(self, session_id: str) -> SessionMeta:
        session = self.session_store.load_session(session_id)
        self.active_session = session
        self.active_backend_name = session.backend
        return session

    def ask(self, prompt: str, stream: bool = False) -> str:
        if self.active_session is None:
            self.new_session()
        assert self.active_session is not None

        session = self.active_session
        backend_cfg = self._get_backend_config(session.backend)
        backend = self._create_backend(backend_cfg)

        self.session_store.append_message(session.id, "user", prompt)
        messages = self._messages_for_backend(session.id)

        if backend.supports_tool_calls():
            text = self._ask_with_tool_loop(session.id, backend, messages)
            self.active_session = self.session_store.load_session(session.id)
            return text
        elif stream:
            chunks = []
            for chunk in backend.stream_generate(DEFAULT_SYSTEM_PROMPT, messages):
                chunks.append(chunk)
                print(chunk, end="", flush=True)
            print("")
            text = "".join(chunks).strip()
        else:
            turn = backend.generate_with_metadata(DEFAULT_SYSTEM_PROMPT, messages)
            text = turn.text.strip()
            self.session_store.append_message(
                session.id,
                "assistant",
                text,
                metadata={"raw_content": turn.raw_content},
            )
            self.active_session = self.session_store.load_session(session.id)
            return text

        self.session_store.append_message(session.id, "assistant", text)
        self.active_session = self.session_store.load_session(session.id)
        return text

    def available_backends(self) -> List[str]:
        return sorted(self.config.backends.keys())

    def available_tools(self) -> List[str]:
        return self.tools.names()

    def run_tool(self, name: str, arguments: Dict[str, object]) -> str:
        try:
            return self.tools.run(name, arguments)
        except ToolError:
            raise

    def _messages_for_backend(self, session_id: str) -> List[Dict[str, Any]]:
        messages = []
        for message in self.session_store.load_messages(session_id):
            content = message.metadata.get("raw_content", message.content)
            messages.append({"role": message.role, "content": content})
        return messages

    def _ask_with_tool_loop(self, session_id: str, backend: BaseBackend, messages: List[Dict[str, Any]]) -> str:
        conversation = list(messages)
        tools = self.tools.automatic_specs()
        final_text = ""
        final_raw_content = []

        for _ in range(MAX_TOOL_ROUNDS):
            turn = backend.generate_turn(DEFAULT_SYSTEM_PROMPT, conversation, tools=tools)
            final_text = turn.text.strip()
            final_raw_content = turn.raw_content or self._assistant_text_blocks(turn)
            if not turn.tool_calls:
                self.session_store.append_message(
                    session_id,
                    "assistant",
                    final_text,
                    metadata={"raw_content": final_raw_content},
                )
                return final_text

            assistant_content = turn.raw_content or self._assistant_text_blocks(turn)
            conversation.append({"role": "assistant", "content": assistant_content})
            self.session_store.append_message(
                session_id,
                "assistant",
                turn.text,
                kind="tool_use",
                metadata={"raw_content": assistant_content},
            )
            tool_results = []
            for tool_call in turn.tool_calls:
                arguments = dict(tool_call.arguments)
                try:
                    if tool_call.name == "edit":
                        self._confirm_automatic_edit(arguments)
                        arguments["confirm"] = True
                    if tool_call.name == "bash":
                        self._confirm_automatic_bash(arguments)
                        arguments["confirm"] = True
                    result = self.run_tool(tool_call.name, arguments)
                except ToolError as exc:
                    result = "Tool error: {0}".format(exc)
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": result,
                }
                tool_results.append(
                    tool_result_block
                )
                self.session_store.append_message(
                    session_id,
                    "user",
                    result,
                    kind="tool_result",
                    metadata={
                        "tool_name": tool_call.name,
                        "tool_arguments": arguments if 'arguments' in locals() else tool_call.arguments,
                        "tool_use_id": tool_call.id,
                        "raw_content": [tool_result_block],
                    },
                )
            conversation.append({"role": "user", "content": tool_results})

        raise BackendError("Tool loop exceeded the maximum number of rounds.")

    def _assistant_text_blocks(self, turn: AssistantTurn) -> List[Dict[str, str]]:
        if not turn.text:
            return []
        return [{"type": "text", "text": turn.text}]

    def _confirm_automatic_edit(self, arguments: Dict[str, Any]) -> None:
        preview = self._build_edit_preview(arguments)
        if self.tool_confirmation_callback is None:
            raise ToolError("Automatic `edit` requires interactive confirmation, but no confirmation handler is available.")
        if not self.tool_confirmation_callback("edit", arguments, preview):
            raise ToolError("User declined automatic edit.")

    def _confirm_automatic_bash(self, arguments: Dict[str, Any]) -> None:
        preview = self._build_bash_preview(arguments)
        if self.tool_confirmation_callback is None:
            raise ToolError("Automatic `bash` requires interactive confirmation, but no confirmation handler is available.")
        if not self.tool_confirmation_callback("bash", arguments, preview):
            raise ToolError("User declined automatic bash.")

    def _build_edit_preview(self, arguments: Dict[str, Any]) -> str:
        path = str(arguments.get("path", "")).strip() or "<missing>"
        old_text = str(arguments.get("old_text", ""))
        new_text = str(arguments.get("new_text", ""))
        replace_all = bool(arguments.get("replace_all", False))
        return "\n".join(
            [
                "Model wants to edit a file.",
                "path: {0}".format(path),
                "replace_all: {0}".format("true" if replace_all else "false"),
                "old_text:",
                _preview_text_block(old_text),
                "new_text:",
                _preview_text_block(new_text),
            ]
        )

    def _build_bash_preview(self, arguments: Dict[str, Any]) -> str:
        command = str(arguments.get("command", "")).strip() or "<missing>"
        cwd = str(arguments.get("cwd", ".")).strip() or "."
        timeout = arguments.get("timeout", self.config.bash_timeout)
        return "\n".join(
            [
                "Model wants to run a shell command.",
                "cwd: {0}".format(cwd),
                "timeout: {0}".format(timeout),
                "command:",
                _preview_text_block(command),
            ]
        )

    def _get_backend_config(self, backend_name: Optional[str]) -> BackendConfig:
        name = backend_name or self.active_backend_name or self.config.default_backend
        try:
            return self.config.backends[name]
        except KeyError:
            raise BackendError("Unknown backend `{0}`.".format(name))

    def _create_backend(self, backend_cfg: BackendConfig) -> BaseBackend:
        if backend_cfg.type == "anthropic":
            return AnthropicBackend(
                model=backend_cfg.model,
                api_key=backend_cfg.api_key,
                base_url=backend_cfg.base_url,
                timeout=backend_cfg.timeout,
                max_tokens=backend_cfg.max_tokens,
            )
        if backend_cfg.type == "openai_compat":
            return OpenAICompatBackend(
                model=backend_cfg.model,
                api_key=backend_cfg.api_key,
                base_url=backend_cfg.base_url,
                timeout=backend_cfg.timeout,
                max_tokens=backend_cfg.max_tokens,
            )
        if backend_cfg.type == "hf_local":
            return HuggingFaceLocalBackend(
                model=backend_cfg.model,
                api_key=backend_cfg.api_key,
                base_url=backend_cfg.base_url,
                timeout=backend_cfg.timeout,
                max_tokens=backend_cfg.max_tokens,
            )
        raise BackendError("Unsupported backend type `{0}`.".format(backend_cfg.type))


def _preview_text_block(text: str, max_lines: int = 8, max_chars: int = 400) -> str:
    normalized = text if len(text) <= max_chars else text[:max_chars] + "\n...[truncated]"
    lines = normalized.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["...[truncated]"]
    if not lines:
        return "(empty)"
    return "\n".join("    {0}".format(line) for line in lines)
