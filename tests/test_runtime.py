import tempfile
import unittest
from pathlib import Path

from crush_py.agent.runtime import AgentRuntime
from crush_py.backends.base import AssistantTurn, BaseBackend, ToolCall
from crush_py.config import AppConfig, BackendConfig
from crush_py.repl import _format_history, _format_trace
from crush_py.store.session_store import SessionStore


class FakeToolLoopBackend(BaseBackend):
    def __init__(self):
        self.turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.turn_count += 1
        if self.turn_count == 1:
            return AssistantTurn(
                text="Let me inspect the file first.",
                tool_calls=[
                    ToolCall(
                        id="tool-1",
                        name="view",
                        arguments={"path": "notes.txt"},
                    )
                ],
                raw_content=[
                    {"type": "text", "text": "Let me inspect the file first."},
                    {"type": "tool_use", "id": "tool-1", "name": "view", "input": {"path": "notes.txt"}},
                ],
            )
        return AssistantTurn(text="The file contains three lines.")

    def supports_tool_calls(self):
        return True


class FakeAutoEditBackend(BaseBackend):
    def __init__(self):
        self.turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.turn_count += 1
        if self.turn_count == 1:
            return AssistantTurn(
                text="I will update the file.",
                tool_calls=[
                    ToolCall(
                        id="edit-1",
                        name="edit",
                        arguments={
                            "path": "notes.txt",
                            "old_text": "two",
                            "new_text": "TWO",
                        },
                    )
                ],
                raw_content=[
                    {"type": "text", "text": "I will update the file."},
                    {"type": "tool_use", "id": "edit-1", "name": "edit", "input": {"path": "notes.txt", "old_text": "two", "new_text": "TWO"}},
                ],
            )
        return AssistantTurn(text="Updated the file successfully.")

    def supports_tool_calls(self):
        return True


class FakePlainBackend(BaseBackend):
    def generate(self, system_prompt, messages, tools=None):
        return "Plain final answer."

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())


class FakeAutoBashBackend(BaseBackend):
    def __init__(self):
        self.turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.turn_count += 1
        if self.turn_count == 1:
            return AssistantTurn(
                text="I will run the script now.",
                tool_calls=[
                    ToolCall(
                        id="bash-1",
                        name="bash",
                        arguments={
                            "command": "python script.py",
                            "cwd": ".",
                        },
                    )
                ],
                raw_content=[
                    {"type": "text", "text": "I will run the script now."},
                    {"type": "tool_use", "id": "bash-1", "name": "bash", "input": {"command": "python script.py", "cwd": "."}},
                ],
            )
        return AssistantTurn(text="The command finished.")

    def supports_tool_calls(self):
        return True


class FakeLocateThenViewBackend(BaseBackend):
    def __init__(self):
        self.turn_count = 0
        self.follow_up_messages = None

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.turn_count += 1
        if self.turn_count == 1:
            return AssistantTurn(
                text="I will locate the file first.",
                tool_calls=[
                    ToolCall(
                        id="glob-1",
                        name="glob",
                        arguments={"pattern": "**/notes.txt"},
                    )
                ],
                raw_content=[
                    {"type": "text", "text": "I will locate the file first."},
                    {"type": "tool_use", "id": "glob-1", "name": "glob", "input": {"pattern": "**/notes.txt"}},
                ],
            )
        if self.turn_count == 2:
            self.follow_up_messages = list(messages)
            return AssistantTurn(
                text="Let me read the file before answering.",
                tool_calls=[
                    ToolCall(
                        id="view-1",
                        name="view",
                        arguments={"path": "notes.txt"},
                    )
                ],
                raw_content=[
                    {"type": "text", "text": "Let me read the file before answering."},
                    {"type": "tool_use", "id": "view-1", "name": "view", "input": {"path": "notes.txt"}},
                ],
            )
        return AssistantTurn(text="The file contains three lines.")

    def supports_tool_calls(self):
        return True


class FakeRepeatedBashFailureBackend(BaseBackend):
    def __init__(self):
        self.turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.turn_count += 1
        return AssistantTurn(
            text="I will try bash.",
            tool_calls=[
                ToolCall(
                    id="bash-1",
                    name="bash",
                    arguments={"command": "printf hello"},
                )
            ],
            raw_content=[
                {"type": "text", "text": "I will try bash."},
                {"type": "tool_use", "id": "bash-1", "name": "bash", "input": {"command": "printf hello"}},
            ],
        )

    def supports_tool_calls(self):
        return True


class AgentRuntimeTests(unittest.TestCase):
    def _make_config(self, workspace):
        backend = BackendConfig(
            name="anthropic",
            type="anthropic",
            model="fake-model",
            base_url="https://example.test",
            api_key="fake-key",
            api_key_env=None,
            timeout=30,
            max_tokens=256,
        )
        return AppConfig(
            workspace_root=workspace,
            sessions_dir=workspace / ".crush_py" / "sessions",
            default_backend="anthropic",
            backends={"anthropic": backend},
            ask_on_write=True,
            ask_on_shell=True,
            bash_timeout=60,
        )

    def test_ask_with_tool_loop_persists_tool_trace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir)
            runtime = AgentRuntime(config, store)
            backend = FakeToolLoopBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Summarize notes.txt")
            messages = store.load_messages(runtime.active_session.id)

            self.assertEqual(result, "The file contains three lines.")
            self.assertEqual([message.kind for message in messages], ["message", "tool_use", "tool_result", "message"])
            self.assertEqual(messages[1].metadata["raw_content"][1]["type"], "tool_use")
            self.assertEqual(messages[2].metadata["tool_name"], "view")
            self.assertIn("<file path=\"notes.txt\">", messages[2].content)
            self.assertEqual(messages[3].metadata["raw_content"][0]["text"], "The file contains three lines.")

    def test_format_trace_reads_recent_tool_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir)
            runtime = AgentRuntime(config, store)
            backend = FakeToolLoopBackend()
            runtime._create_backend = lambda backend_cfg: backend

            runtime.ask("Summarize notes.txt")
            text = _format_trace(runtime, limit=10)

            self.assertIn("[tool_use] assistant", text)
            self.assertIn("tool: view", text)
            self.assertIn("[tool_result] user", text)
            self.assertIn("arguments: {'path': 'notes.txt'}", text)
            self.assertIn("stage: assistant_final", text)
            self.assertIn("text: The file contains three lines.", text)

    def test_locator_tools_add_follow_up_view_reminder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir)
            runtime = AgentRuntime(config, store)
            backend = FakeLocateThenViewBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Find notes.txt and summarize it.")

            self.assertEqual(result, "The file contains three lines.")
            self.assertIsNotNone(backend.follow_up_messages)
            self.assertEqual(backend.follow_up_messages[-1]["role"], "user")
            self.assertIn("Use `view` on notes.txt before answering.", backend.follow_up_messages[-1]["content"])

    def test_gemma_4b_profile_uses_more_explicit_prompt_pack(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            backend = BackendConfig(
                name="lm_studio",
                type="openai_compat",
                model="google/gemma-3-4b",
                base_url="http://example.test/v1",
                api_key="not-needed",
                api_key_env=None,
                timeout=30,
                max_tokens=256,
            )
            config = AppConfig(
                workspace_root=workspace,
                sessions_dir=workspace / ".crush_py" / "sessions",
                default_backend="lm_studio",
                backends={"lm_studio": backend},
                ask_on_write=True,
                ask_on_shell=True,
                bash_timeout=60,
            )
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir))

            context_profile = runtime._context_profile_for_backend(backend)

            self.assertEqual(context_profile["name"], "small_model")
            self.assertIn("1) locate, 2) `view`, 3) answer from the file.", context_profile["system_prompt"])
            self.assertIn("Locate is done. Use `view` on", context_profile["view_hint_template"])

    def test_small_model_strict_profile_applies_to_3b_models(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            backend = BackendConfig(
                name="lm_studio",
                type="openai_compat",
                model="qwen2.5-coder-3b",
                base_url="http://example.test/v1",
                api_key="not-needed",
                api_key_env=None,
                timeout=30,
                max_tokens=256,
            )
            config = AppConfig(
                workspace_root=workspace,
                sessions_dir=workspace / ".crush_py" / "sessions",
                default_backend="lm_studio",
                backends={"lm_studio": backend},
                ask_on_write=True,
                ask_on_shell=True,
                bash_timeout=60,
            )
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir))

            context_profile = runtime._context_profile_for_backend(backend)

            self.assertEqual(context_profile["name"], "small_model_strict")
            self.assertEqual(context_profile["max_history_messages"], 3)
            self.assertEqual(context_profile["force_view_after_locator_rounds"], 1)

    def test_force_view_after_repeated_locator_rounds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir))

            self.assertTrue(
                runtime._should_force_view(
                    "Find the file.",
                    ["notes.txt"],
                    locator_rounds_without_view=2,
                )
            )

    def test_small_model_history_trim_is_more_aggressive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            backend = BackendConfig(
                name="lm_studio",
                type="openai_compat",
                model="google/gemma-3-4b",
                base_url="http://example.test/v1",
                api_key="not-needed",
                api_key_env=None,
                timeout=30,
                max_tokens=256,
            )
            config = AppConfig(
                workspace_root=workspace,
                sessions_dir=workspace / ".crush_py" / "sessions",
                default_backend="lm_studio",
                backends={"lm_studio": backend},
                ask_on_write=True,
                ask_on_shell=True,
                bash_timeout=60,
            )
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir))
            profile = runtime._context_profile_for_backend(backend)
            messages = [
                type("Message", (), {"role": "user", "kind": "message"})(),
                type("Message", (), {"role": "assistant", "kind": "message"})(),
                type("Message", (), {"role": "user", "kind": "message"})(),
                type("Message", (), {"role": "assistant", "kind": "tool_use"})(),
                type("Message", (), {"role": "user", "kind": "tool_result"})(),
                type("Message", (), {"role": "assistant", "kind": "message"})(),
                type("Message", (), {"role": "user", "kind": "message"})(),
            ]

            trimmed = runtime._trim_messages_for_backend(messages, profile)

            self.assertEqual(len(trimmed), 2)
            self.assertEqual(trimmed[0].role, "assistant")
            self.assertEqual(trimmed[1].role, "user")

    def test_plain_backend_persists_final_assistant_raw_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir)
            runtime = AgentRuntime(config, store)
            backend = FakePlainBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Say something helpful")
            messages = store.load_messages(runtime.active_session.id)

            self.assertEqual(result, "Plain final answer.")
            self.assertEqual([message.kind for message in messages], ["message", "message"])
            self.assertEqual(messages[1].metadata["raw_content"][0]["text"], "Plain final answer.")

    def test_format_history_reads_recent_conversation_messages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir)
            runtime = AgentRuntime(config, store)
            backend = FakePlainBackend()
            runtime._create_backend = lambda backend_cfg: backend

            runtime.ask("Say something helpful")
            text = _format_history(runtime, limit=10)

            self.assertIn("[user]", text)
            self.assertIn("Say something helpful", text)
            self.assertIn("[assistant]", text)
            self.assertIn("Plain final answer.", text)

    def test_automatic_edit_requires_user_confirmation_and_applies_change(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir)
            runtime = AgentRuntime(config, store)
            backend = FakeAutoEditBackend()
            seen = {}
            runtime._create_backend = lambda backend_cfg: backend

            def allow(tool_name, arguments, preview):
                seen["tool_name"] = tool_name
                seen["arguments"] = dict(arguments)
                seen["preview"] = preview
                return True

            runtime.tool_confirmation_callback = allow

            result = runtime.ask("Please update notes.txt")

            self.assertEqual(result, "Updated the file successfully.")
            self.assertEqual((workspace / "notes.txt").read_text(encoding="utf-8"), "one\nTWO\nthree\n")
            self.assertEqual(seen["tool_name"], "edit")
            self.assertIn("path: notes.txt", seen["preview"])
            self.assertIn("old_text:", seen["preview"])
            self.assertIn("new_text:", seen["preview"])

    def test_automatic_edit_returns_tool_error_when_user_declines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir)
            runtime = AgentRuntime(config, store)
            backend = FakeAutoEditBackend()
            runtime._create_backend = lambda backend_cfg: backend
            runtime.tool_confirmation_callback = lambda tool_name, arguments, preview: False

            result = runtime.ask("Please update notes.txt")
            messages = store.load_messages(runtime.active_session.id)

            self.assertEqual(result, "Edit not applied because the user declined confirmation.")
            self.assertEqual((workspace / "notes.txt").read_text(encoding="utf-8"), "one\ntwo\nthree\n")
            self.assertIn("User declined automatic edit.", messages[2].content)
            self.assertEqual(messages[-1].content, "Edit not applied because the user declined confirmation.")

    def test_automatic_bash_requires_user_confirmation_and_runs_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "script.py").write_text("print('hello from script')\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir)
            runtime = AgentRuntime(config, store)
            backend = FakeAutoBashBackend()
            seen = {}
            runtime._create_backend = lambda backend_cfg: backend

            def allow(tool_name, arguments, preview):
                seen["tool_name"] = tool_name
                seen["arguments"] = dict(arguments)
                seen["preview"] = preview
                return True

            runtime.tool_confirmation_callback = allow

            result = runtime.ask("Run script.py")
            messages = store.load_messages(runtime.active_session.id)

            self.assertEqual(result, "The command finished.")
            self.assertEqual(seen["tool_name"], "bash")
            self.assertIn("cwd: .", seen["preview"])
            self.assertIn("timeout: 60", seen["preview"])
            self.assertIn("command:", seen["preview"])
            self.assertIn("python script.py", seen["preview"])
            self.assertIn("[exit_code] 0", messages[2].content)
            self.assertIn("hello from script", messages[2].content)

    def test_automatic_bash_returns_tool_error_when_user_declines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "script.py").write_text("print('hello from script')\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir)
            runtime = AgentRuntime(config, store)
            backend = FakeAutoBashBackend()
            runtime._create_backend = lambda backend_cfg: backend
            runtime.tool_confirmation_callback = lambda tool_name, arguments, preview: False

            result = runtime.ask("Run script.py")
            messages = store.load_messages(runtime.active_session.id)

            self.assertEqual(result, "The command finished.")
            self.assertIn("User declined automatic bash.", messages[2].content)

    def test_repeated_bash_failures_stop_retry_loop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir)
            runtime = AgentRuntime(config, store)
            backend = FakeRepeatedBashFailureBackend()
            runtime._create_backend = lambda backend_cfg: backend
            runtime.tool_confirmation_callback = lambda tool_name, arguments, preview: False

            result = runtime.ask("Run printf hello")

            self.assertEqual(result, "Stopped after repeated `bash` failures. Rephrase the request or choose a different tool.")


if __name__ == "__main__":
    unittest.main()
