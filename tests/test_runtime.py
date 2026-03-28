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

            self.assertEqual(result, "Updated the file successfully.")
            self.assertEqual((workspace / "notes.txt").read_text(encoding="utf-8"), "one\ntwo\nthree\n")
            self.assertIn("User declined automatic edit.", messages[2].content)

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


if __name__ == "__main__":
    unittest.main()
