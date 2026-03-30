import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..backends.anthropic import AnthropicBackend
from ..backends.base import AssistantTurn, BackendError, BaseBackend
from ..backends.hf_local import HuggingFaceLocalBackend
from ..backends.openai_compat import OpenAICompatBackend
from ..config import AppConfig, BackendConfig
from ..store.session_store import SessionMeta, SessionStore
from ..tools.base import ToolError
from ..tools.registry import ToolRegistry


DEFAULT_SYSTEM_PROMPT = """You are crush_py, a coding assistant.

Keep answers concise.
Use only the provided tools.
For repo questions: locate, then `view`, then answer.
Do not guess paths, file contents, or success states.
Use workspace-relative paths only. Never start paths with `/`.
"""

SMALL_MODEL_SYSTEM_PROMPT = """You are crush_py, a coding assistant.

Use only the provided tools.
For repo questions: 1) locate, 2) `view`, 3) answer from the file.
If you have not read the file, inspect it first.
Do not guess paths, file contents, or success states.
Use workspace-relative paths only. Never start paths with `/`.
Keep answers concise.
"""

SMALL_MODEL_STRICT_SYSTEM_PROMPT = """You are crush_py, a coding assistant.

Use only the provided tools.
For repo questions: locate, then `view`, then answer from the file.
Do not answer from memory. If you have not read the file, inspect it first.
Do not guess paths, file contents, or success states.
Use workspace-relative paths only. Never start paths with `/`.
Keep answers concise.
"""

MAX_TOOL_ROUNDS = 8
DEFAULT_HISTORY_MESSAGES = 6
VIEW_ONLY_HINT_TEMPLATE = "Use `view` on {0} before answering."
SMALL_MODEL_VIEW_HINT_TEMPLATE = "Locate is done. Use `view` on {0}, then answer from the file."
SMALL_MODEL_STRICT_VIEW_HINT_TEMPLATE = "Read {0} with `view` now. Then answer only from the file."
REPEATED_TOOL_ERROR_TEMPLATE = "Stopped after repeated `{0}` failures. Rephrase the request or choose a different tool."


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
        context_profile = self._context_profile_for_backend(backend_cfg)
        system_prompt = context_profile["system_prompt"]

        self.session_store.append_message(session.id, "user", prompt)
        messages = self._messages_for_backend(session.id, context_profile)

        if backend.supports_tool_calls():
            text = self._ask_with_tool_loop(session.id, backend, messages, prompt, system_prompt, context_profile)
            self.active_session = self.session_store.load_session(session.id)
            return text
        elif stream:
            chunks = []
            for chunk in backend.stream_generate(system_prompt, messages):
                chunks.append(chunk)
                print(chunk, end="", flush=True)
            print("")
            text = "".join(chunks).strip()
        else:
            turn = backend.generate_with_metadata(system_prompt, messages)
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

    def _messages_for_backend(self, session_id: str, context_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        stored_messages = self._trim_messages_for_backend(
            self.session_store.load_messages(session_id),
            context_profile,
        )
        messages = []
        for message in stored_messages:
            content = message.metadata.get("raw_content", message.content)
            messages.append({"role": message.role, "content": content})
        return messages

    def _trim_messages_for_backend(self, messages: List[Any], context_profile: Dict[str, Any]) -> List[Any]:
        max_history_messages = int(context_profile.get("max_history_messages", DEFAULT_HISTORY_MESSAGES))
        lead_messages_before_last_user = int(context_profile.get("lead_messages_before_last_user", 2))

        if len(messages) <= max_history_messages:
            return messages

        last_user_index = None
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].role == "user" and messages[index].kind == "message":
                last_user_index = index
                break

        if last_user_index is None:
            return messages[-max_history_messages:]

        start = max(0, last_user_index - lead_messages_before_last_user)
        return messages[start:]

    def _ask_with_tool_loop(
        self,
        session_id: str,
        backend: BaseBackend,
        messages: List[Dict[str, Any]],
        prompt: str,
        system_prompt: str,
        context_profile: Dict[str, Any],
    ) -> str:
        conversation = list(messages)
        prompt_profile = context_profile["prompt_profile"]
        base_tools = self.tools.automatic_specs_for_prompt(prompt, prompt_profile=prompt_profile)
        final_text = ""
        final_raw_content = []
        forced_view_path = None
        repeated_failures = {}
        locator_rounds_without_view = 0
        hint_template = context_profile["view_hint_template"]

        for _ in range(MAX_TOOL_ROUNDS):
            current_tools = (
                base_tools
                if forced_view_path is None
                else self.tools.specs(("view",), prompt_profile=prompt_profile)
            )
            if forced_view_path is not None:
                hint = hint_template.format(forced_view_path)
                if not conversation or conversation[-1].get("content") != hint:
                    conversation.append({"role": "user", "content": hint})
            turn = backend.generate_turn(system_prompt, conversation, tools=current_tools)
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
            declined_edit = False
            used_locator_tool = False
            used_view_tool = False
            candidate_paths = []
            for tool_call in turn.tool_calls:
                arguments = dict(tool_call.arguments)
                if tool_call.name in ("ls", "glob", "grep"):
                    used_locator_tool = True
                if tool_call.name == "view":
                    used_view_tool = True
                    forced_view_path = None
                try:
                    if tool_call.name == "edit":
                        self._confirm_automatic_edit(arguments)
                        arguments["confirm"] = True
                    if tool_call.name == "bash":
                        self._confirm_automatic_bash(arguments)
                        arguments["confirm"] = True
                    result = self.run_tool(tool_call.name, arguments)
                    repeated_failures.pop(self._failure_signature(tool_call.name, arguments), None)
                except ToolError as exc:
                    result = "Tool error: {0}".format(exc)
                    if tool_call.name == "edit" and "User declined automatic edit." in str(exc):
                        declined_edit = True
                    signature = self._failure_signature(tool_call.name, arguments)
                    repeated_failures[signature] = repeated_failures.get(signature, 0) + 1
                    if tool_call.name in ("bash", "edit") and repeated_failures[signature] >= 2:
                        final_text = REPEATED_TOOL_ERROR_TEMPLATE.format(tool_call.name)
                        final_raw_content = self._assistant_text_blocks(AssistantTurn(text=final_text))
                        self.session_store.append_message(
                            session_id,
                            "assistant",
                            final_text,
                            metadata={"raw_content": final_raw_content},
                        )
                        return final_text
                candidate_paths.extend(self._extract_candidate_paths(tool_call.name, result))
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "tool_name": tool_call.name,
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
            if declined_edit:
                final_text = "Edit not applied because the user declined confirmation."
                final_raw_content = self._assistant_text_blocks(AssistantTurn(text=final_text))
                self.session_store.append_message(
                    session_id,
                    "assistant",
                    final_text,
                    metadata={"raw_content": final_raw_content},
                )
                return final_text
            conversation.append({"role": "user", "content": tool_results})
            if used_view_tool:
                locator_rounds_without_view = 0
                forced_view_path = None
            elif used_locator_tool:
                locator_rounds_without_view += 1
            if used_locator_tool and not used_view_tool and self._should_force_view(
                prompt,
                candidate_paths,
                locator_rounds_without_view,
                context_profile,
            ):
                forced_view_path = candidate_paths[0]

        raise BackendError("Tool loop exceeded the maximum number of rounds.")

    def _should_force_view(
        self,
        prompt: str,
        candidate_paths: List[str],
        locator_rounds_without_view: int = 1,
        context_profile: Optional[Dict[str, Any]] = None,
    ) -> bool:
        context_profile = context_profile or self._default_context_profile()
        unique_paths = sorted(set(candidate_paths))
        if len(unique_paths) != 1:
            return False
        prompt_lower = (prompt or "").lower()
        markers = (
            "summarize",
            "summary",
            "explain",
            "explanation",
            "implementation",
            "implemented",
            "what does",
            "what is",
            "how does",
            "how ",
            "read ",
            "inspect",
            "find where",
            "show me",
        )
        if any(marker in prompt_lower for marker in markers):
            return True
        return locator_rounds_without_view >= int(context_profile.get("force_view_after_locator_rounds", 2))

    def _extract_candidate_paths(self, tool_name: str, result: str) -> List[str]:
        if tool_name == "glob":
            paths = []
            for line in result.splitlines():
                item = line.strip()
                if not item or item.endswith("/") or item.startswith("Results truncated"):
                    continue
                paths.append(item)
            return paths
        if tool_name == "grep":
            paths = []
            for line in result.splitlines():
                item = line.strip()
                if item.endswith(":") and not item.startswith("Line "):
                    paths.append(item[:-1])
            return sorted(set(paths))
        return []

    def _failure_signature(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[str, str]:
        return tool_name, repr(sorted(arguments.items()))

    def _system_prompt_for_backend(self, backend_cfg: BackendConfig) -> str:
        return self._context_profile_for_backend(backend_cfg)["system_prompt"]

    def _view_hint_template_for_backend(self, backend_cfg: BackendConfig) -> str:
        return self._context_profile_for_backend(backend_cfg)["view_hint_template"]

    def _context_profile_for_backend(self, backend_cfg: BackendConfig) -> Dict[str, Any]:
        model_name = (backend_cfg.model or "").lower()
        profile_name = self._model_prompt_profile_name(model_name)
        profiles = {
            "default": self._default_context_profile(),
            "small_model": {
                "name": "small_model",
                "prompt_profile": "small_model",
                "system_prompt": SMALL_MODEL_SYSTEM_PROMPT,
                "view_hint_template": SMALL_MODEL_VIEW_HINT_TEMPLATE,
                "max_history_messages": 4,
                "lead_messages_before_last_user": 1,
                "force_view_after_locator_rounds": 2,
            },
            "small_model_strict": {
                "name": "small_model_strict",
                "prompt_profile": "small_model_strict",
                "system_prompt": SMALL_MODEL_STRICT_SYSTEM_PROMPT,
                "view_hint_template": SMALL_MODEL_STRICT_VIEW_HINT_TEMPLATE,
                "max_history_messages": 3,
                "lead_messages_before_last_user": 1,
                "force_view_after_locator_rounds": 1,
            },
        }
        return profiles[profile_name]

    def _default_context_profile(self) -> Dict[str, Any]:
        return {
            "name": "default",
            "prompt_profile": "default",
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "view_hint_template": VIEW_ONLY_HINT_TEMPLATE,
            "max_history_messages": DEFAULT_HISTORY_MESSAGES,
            "lead_messages_before_last_user": 2,
            "force_view_after_locator_rounds": 2,
        }

    def _model_prompt_profile_name(self, model_name: str) -> str:
        if self._is_small_model_strict(model_name):
            return "small_model_strict"
        if self._is_small_model(model_name):
            return "small_model"
        return "default"

    def _is_small_model(self, model_name: str) -> bool:
        if self._matches_model_size(model_name, ("4",)):
            return True
        return self._is_small_model_strict(model_name)

    def _is_small_model_strict(self, model_name: str) -> bool:
        if self._matches_model_size(model_name, ("1", "2", "3")):
            return True
        markers = (" mini", "-mini", "_mini", "small", "tiny", "smol")
        return any(marker in model_name for marker in markers)

    def _matches_model_size(self, model_name: str, sizes: Tuple[str, ...]) -> bool:
        pattern = r"(^|[^0-9])({0})b([^0-9]|$)".format("|".join(re.escape(size) for size in sizes))
        return re.search(pattern, model_name) is not None

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
