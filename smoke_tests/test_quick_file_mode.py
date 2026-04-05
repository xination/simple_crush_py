import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from crush_py.agent.runtime import AgentRuntime
from crush_py.backends.base import AssistantTurn, BaseBackend
from crush_py.cli import main


class FakeQuickSmokeBackend(BaseBackend):
    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        return AssistantTurn(text="1. Start with `python -m crush_py --help`.")


class QuickFileModeSmokeTests(unittest.TestCase):
    def test_cli_quick_file_mode_reads_single_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text(
                "# Demo\nUse `python -m crush_py --help` to get started.\n",
                encoding="utf-8",
            )
            (workspace / "config.json").write_text(
                (
                    '{\n'
                    '  "workspace_root": ".",\n'
                    '  "sessions_dir": ".crush_py/sessions",\n'
                    '  "default_backend": "lm_studio",\n'
                    '  "trace_mode": "lean",\n'
                    '  "backends": {\n'
                    '    "lm_studio": {\n'
                    '      "type": "openai_compat",\n'
                    '      "model": "demo",\n'
                    '      "base_url": "http://example.test/v1",\n'
                    '      "api_key": "not-needed"\n'
                    "    }\n"
                    "  }\n"
                    "}\n"
                ),
                encoding="utf-8",
            )

            with patch.object(AgentRuntime, "_create_backend", return_value=FakeQuickSmokeBackend()):
                with redirect_stdout(io.StringIO()) as stdout:
                    exit_code = main(
                        [
                            "--config",
                            str(workspace / "config.json"),
                            "--file",
                            "README.md",
                            "--prompt",
                            "show me how to start, prefer in a list format",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            self.assertIn("python -m crush_py --help", stdout.getvalue())

    def test_quick_file_mode_reuses_cached_text_without_history_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text(
                "# Demo\nUse `python -m crush_py --help` to get started.\n",
                encoding="utf-8",
            )
            (workspace / "config.json").write_text(
                (
                    '{\n'
                    '  "workspace_root": ".",\n'
                    '  "sessions_dir": ".crush_py/sessions",\n'
                    '  "default_backend": "lm_studio",\n'
                    '  "trace_mode": "lean",\n'
                    '  "backends": {\n'
                    '    "lm_studio": {\n'
                    '      "type": "openai_compat",\n'
                    '      "model": "demo",\n'
                    '      "base_url": "http://example.test/v1",\n'
                    '      "api_key": "not-needed"\n'
                    "    }\n"
                    "  }\n"
                    "}\n"
                ),
                encoding="utf-8",
            )
            session_store = None
            with patch.object(AgentRuntime, "_create_backend", return_value=FakeQuickSmokeBackend()):
                runtime = None
                with patch("crush_py.agent.runtime.read_text_with_fallback") as read_mock:
                    read_mock.return_value = ("# Demo\nUse `python -m crush_py --help` to get started.\n", "utf-8")
                    from crush_py.config import load_config
                    from crush_py.store.session_store import SessionStore

                    config = load_config(config_path=str(workspace / "config.json"), base_dir=str(workspace))
                    session_store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
                    runtime = AgentRuntime(config, session_store)
                    runtime.ask_quick_file("README.md", "first pass", stream=False)
                    runtime.ask_quick_file("README.md", "second pass", stream=False)

                self.assertEqual(read_mock.call_count, 1)
                messages = session_store.load_messages(runtime.active_session.id)
                self.assertEqual([message.role for message in messages], ["user", "assistant", "user", "assistant"])

    def test_quick_file_mode_debug_trace_shows_cache_hit_and_miss(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text(
                "# Demo\nUse `python -m crush_py --help` to get started.\n",
                encoding="utf-8",
            )
            (workspace / "config.json").write_text(
                (
                    '{\n'
                    '  "workspace_root": ".",\n'
                    '  "sessions_dir": ".crush_py/sessions",\n'
                    '  "default_backend": "lm_studio",\n'
                    '  "trace_mode": "debug",\n'
                    '  "backends": {\n'
                    '    "lm_studio": {\n'
                    '      "type": "openai_compat",\n'
                    '      "model": "demo",\n'
                    '      "base_url": "http://example.test/v1",\n'
                    '      "api_key": "not-needed"\n'
                    "    }\n"
                    "  }\n"
                    "}\n"
                ),
                encoding="utf-8",
            )
            with patch.object(AgentRuntime, "_create_backend", return_value=FakeQuickSmokeBackend()):
                from crush_py.config import load_config
                from crush_py.store.session_store import SessionStore

                config = load_config(config_path=str(workspace / "config.json"), base_dir=str(workspace))
                session_store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
                runtime = AgentRuntime(config, session_store)
                runtime.ask_quick_file("README.md", "first pass", stream=False)
                runtime.ask_quick_file("README.md", "second pass", stream=False)

            messages = session_store.load_messages(runtime.active_session.id)
            user_messages = [message for message in messages if message.role == "user"]
            self.assertEqual(user_messages[0].metadata.get("quick_file_cache_status"), "miss")
            self.assertEqual(user_messages[1].metadata.get("quick_file_cache_status"), "hit")


if __name__ == "__main__":
    unittest.main()
