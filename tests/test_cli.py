import io
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from crush_py.agent.runtime import AgentRuntime
from crush_py.backends.base import BaseBackend
from crush_py.cli import (
    build_guide_prompt,
    build_parser,
    build_summary_prompt,
    build_trace_prompt,
    configure_utf8_stdio,
    launch_base_dir,
    main,
    prompt_from_args,
    resolve_writable_sessions_dir,
)
from crush_py.repl import run_repl


class FakeRuntime:
    def __init__(self, config=None, session_store=None):
        self.prompts = []
        self.quick_file_calls = []
        self.streams = []
        self.show_thinking_flags = []
        self.session_ids = []
        self.active_session = None

    def use_session(self, session_id):
        self.session_ids.append(session_id)

    def new_session(self):
        session = SimpleNamespace(id="session-1", backend="demo")
        self.active_session = session
        return session

    def ask(self, prompt, stream=False, show_thinking=False):
        self.prompts.append(prompt)
        self.streams.append(stream)
        self.show_thinking_flags.append(show_thinking)
        return "ok"

    def ask_quick_file(self, path, prompt, stream=False):
        self.quick_file_calls.append((path, prompt))
        self.streams.append(stream)
        return "ok"


class CliTests(unittest.TestCase):
    class FakeQuickFileEchoBackend(BaseBackend):
        def generate(self, system_prompt, messages, tools=None):
            return "unused"

        def stream_generate(self, system_prompt, messages, tools=None):
            file_message = next(
                message["content"]
                for message in messages
                if isinstance(message.get("content"), str)
                and message["content"].startswith("File content from `README.md`:\n")
            )
            file_body = file_message.split("\n", 1)[1]
            if file_body.startswith("(") and "', 'utf-8')" in file_body:
                file_body = file_body.split("', 'utf-8')", 1)[0].lstrip("(").strip("'")
            first_line = next((line.strip() for line in file_body.splitlines() if line.strip()), "")
            yield "E2E quick file saw: {0}".format(first_line)

    def test_configure_utf8_stdio_reconfigures_windows_streams(self):
        calls = []

        class FakeStream:
            def reconfigure(self, **kwargs):
                calls.append(kwargs)

        with patch("crush_py.cli.os.name", "nt"):
            with patch("crush_py.cli.sys.stdout", FakeStream()):
                with patch("crush_py.cli.sys.stderr", FakeStream()):
                    configure_utf8_stdio()

        self.assertEqual(
            calls,
            [
                {"encoding": "utf-8", "errors": "replace"},
                {"encoding": "utf-8", "errors": "replace"},
            ],
        )

    def test_configure_utf8_stdio_skips_non_windows(self):
        fake_stdout = SimpleNamespace()
        fake_stderr = SimpleNamespace()

        with patch("crush_py.cli.os.name", "posix"):
            with patch("crush_py.cli.sys.stdout", fake_stdout):
                with patch("crush_py.cli.sys.stderr", fake_stderr):
                    configure_utf8_stdio()

        self.assertFalse(hasattr(fake_stdout, "encoding"))

    def test_build_summary_prompt_defaults_to_brief_mode(self):
        self.assertEqual(build_summary_prompt("README.md"), "Give a short summary for README.md")

    def test_build_trace_prompt_adds_trace_prefix(self):
        self.assertEqual(
            build_trace_prompt("how prompt flows inside crush_py/agent/runtime.py"),
            "Trace how prompt flows inside crush_py/agent/runtime.py",
        )

    def test_build_trace_prompt_preserves_existing_trace_wording(self):
        self.assertEqual(
            build_trace_prompt("Trace the variable session_id in crush_py/store/session_store.py"),
            "Trace the variable session_id in crush_py/store/session_store.py",
        )

    def test_build_guide_prompt_wraps_request_with_docs_expectations(self):
        prompt = build_guide_prompt("turn README.md into a checklist")

        self.assertIn("Guide mode:", prompt)
        self.assertIn("User request: turn README.md into a checklist", prompt)
        self.assertIn("answer from workspace docs when possible", prompt)
        self.assertIn("include source file hints", prompt)

    def test_launch_base_dir_prefers_original_caller_cwd_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("crush_py.cli.os.environ", {"CRUSH_PY_CALLER_CWD": tmpdir}, clear=False):
                self.assertEqual(launch_base_dir(), Path(tmpdir).resolve())

    def test_launch_base_dir_falls_back_to_current_working_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("crush_py.cli.os.environ", {}, clear=True):
                with patch("crush_py.cli.Path.cwd", return_value=Path(tmpdir)):
                    self.assertEqual(launch_base_dir(), Path(tmpdir).resolve())

    def test_resolve_writable_sessions_dir_prefers_configured_path_when_writable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            configured = Path(tmpdir) / "sessions"
            config = SimpleNamespace(sessions_dir=configured)

            resolved = resolve_writable_sessions_dir(config)

            self.assertEqual(resolved, configured.resolve())

    def test_resolve_writable_sessions_dir_falls_back_to_home_then_temp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = SimpleNamespace(sessions_dir=root / "configured" / "sessions")
            home_candidate = (root / "home" / ".crush_py" / "sessions").resolve()
            temp_candidate = (root / "temp" / ".crush_py" / "sessions").resolve()

            def fake_is_writable(path):
                return path == temp_candidate

            with patch("crush_py.cli.Path.home", return_value=root / "home"):
                with patch("crush_py.cli.tempfile.gettempdir", return_value=str(root / "temp")):
                    with patch("crush_py.cli._is_writable_sessions_dir", side_effect=fake_is_writable):
                        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                            resolved = resolve_writable_sessions_dir(config)

        self.assertEqual(resolved, temp_candidate)
        self.assertNotEqual(home_candidate, temp_candidate)
        self.assertIn(str(temp_candidate), stderr.getvalue())

    def test_resolve_writable_sessions_dir_raises_when_all_candidates_fail(self):
        config = SimpleNamespace(sessions_dir=Path("/configured/sessions"))

        with patch("crush_py.cli._is_writable_sessions_dir", return_value=False):
            with self.assertRaisesRegex(Exception, "No writable sessions_dir available"):
                resolve_writable_sessions_dir(config)

    def test_prompt_from_args_prefers_explicit_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["--prompt", "hello"])

        self.assertEqual(prompt_from_args(args), "hello")

    def test_prompt_from_args_builds_summary_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["--summarize", "README.md"])

        self.assertEqual(prompt_from_args(args), "Give a short summary for README.md")

    def test_prompt_from_args_builds_trace_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["--trace", "how prompt flows inside crush_py/agent/runtime.py"])

        self.assertEqual(prompt_from_args(args), "Trace how prompt flows inside crush_py/agent/runtime.py")

    def test_prompt_from_args_builds_guide_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["--guide", "turn README.md into a checklist"])

        self.assertIn("Guide mode:", prompt_from_args(args))
        self.assertIn("turn README.md into a checklist", prompt_from_args(args))

    def test_parser_rejects_multiple_prompt_modes(self):
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["--prompt", "hello", "--summarize", "README.md"])

    def test_parser_help_explains_trace_summarize_and_guide(self):
        parser = build_parser()

        help_text = parser.format_help()

        self.assertIn("Quick mode for one text file.", help_text)
        self.assertIn("Trace code flow for a variable, symbol, or request.", help_text)
        self.assertIn("Summarize one file in a short 3-point overview.", help_text)
        self.assertIn("Ask docs-based, beginner-friendly questions.", help_text)
        self.assertIn("When to use these modes:", help_text)
        self.assertIn("--file PATH", help_text)
        self.assertIn("--summarize PATH", help_text)
        self.assertIn("--trace REQUEST", help_text)
        self.assertIn("--guide QUESTION", help_text)

    def test_main_uses_quick_file_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config_path = workspace / "config.json"
            config_path.write_text(
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
            fake_runtime = FakeRuntime()

            with patch("crush_py.cli.AgentRuntime", return_value=fake_runtime):
                with patch("builtins.print") as print_mock:
                    exit_code = main(
                        [
                            "--config",
                            str(config_path),
                            "--file",
                            "README.md",
                            "--prompt",
                            "show me how to start",
                            "--stream",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            self.assertEqual(fake_runtime.quick_file_calls, [("README.md", "show me how to start")])
            self.assertEqual(fake_runtime.streams, [True])
            print_mock.assert_not_called()

    def test_main_uses_original_caller_cwd_for_config_base_dir(self):
        fake_runtime = FakeRuntime()
        fake_config = SimpleNamespace(sessions_dir=Path("sessions"), trace_mode="lean")

        with tempfile.TemporaryDirectory() as caller_tmpdir:
            with patch.dict("crush_py.cli.os.environ", {"CRUSH_PY_CALLER_CWD": caller_tmpdir}, clear=False):
                with patch("crush_py.cli.load_config", return_value=fake_config) as load_config_mock:
                    with patch("crush_py.cli.SessionStore"):
                        with patch("crush_py.cli.AgentRuntime", return_value=fake_runtime):
                            with patch("crush_py.cli.run_repl", return_value=0):
                                exit_code = main([])

        self.assertEqual(exit_code, 0)
        load_config_mock.assert_called_once_with(config_path=None, base_dir=str(Path(caller_tmpdir).resolve()))

    def test_main_replaces_sessions_dir_with_writable_fallback_before_store_init(self):
        fake_runtime = FakeRuntime()
        fake_config = SimpleNamespace(sessions_dir=Path("/configured/sessions"), trace_mode="lean")
        fallback_dir = Path("/fallback/sessions")

        with patch("crush_py.cli.load_config", return_value=fake_config):
            with patch("crush_py.cli.resolve_writable_sessions_dir", return_value=fallback_dir):
                with patch("crush_py.cli.SessionStore") as session_store_mock:
                    with patch("crush_py.cli.AgentRuntime", return_value=fake_runtime):
                        with patch("crush_py.cli.run_repl", return_value=0):
                            exit_code = main([])

        self.assertEqual(exit_code, 0)
        session_store_mock.assert_called_once_with(fallback_dir, trace_mode="lean")

    def test_main_repl_quick_command_reads_readme_from_original_caller_cwd_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            caller_root = Path(tmpdir) / "outside"
            caller_root.mkdir()
            (caller_root / "README.md").write_text(
                "# OUTER README\nThis should win.\n",
                encoding="utf-8",
            )

            with patch.dict("crush_py.cli.os.environ", {"CRUSH_PY_CALLER_CWD": str(caller_root)}, clear=False):
                with patch.object(
                    AgentRuntime,
                    "_create_backend",
                    return_value=self.FakeQuickFileEchoBackend(),
                ):
                    with patch("builtins.input", side_effect=["/quick @README.md, show me the key facts", "/quit"]):
                        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                            exit_code = main([])

        rendered = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("E2E quick file saw: # OUTER README", rendered)

    def test_main_uses_trace_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config_path = workspace / "config.json"
            config_path.write_text(
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
            fake_runtime = FakeRuntime()

            with patch("crush_py.cli.AgentRuntime", return_value=fake_runtime):
                with patch("builtins.print") as print_mock:
                    exit_code = main(["--config", str(config_path), "--trace", "how prompt flows inside crush_py/agent/runtime.py"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(fake_runtime.prompts, ["Trace how prompt flows inside crush_py/agent/runtime.py"])
            self.assertEqual(fake_runtime.streams, [False])
            print_mock.assert_called_once_with("ok")

    def test_main_uses_guide_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config_path = workspace / "config.json"
            config_path.write_text(
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
            fake_runtime = FakeRuntime()

            with patch("crush_py.cli.AgentRuntime", return_value=fake_runtime):
                with patch("builtins.print") as print_mock:
                    exit_code = main(["--config", str(config_path), "--guide", "turn README.md into a checklist"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(fake_runtime.streams, [False])
            self.assertEqual(len(fake_runtime.prompts), 1)
            self.assertIn("Guide mode:", fake_runtime.prompts[0])
            self.assertIn("turn README.md into a checklist", fake_runtime.prompts[0])
            print_mock.assert_called_once_with("ok")

    def test_main_uses_existing_session_for_guide_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config_path = workspace / "config.json"
            config_path.write_text(
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
            fake_runtime = FakeRuntime()

            with patch("crush_py.cli.AgentRuntime", return_value=fake_runtime):
                with patch("builtins.print") as print_mock:
                    exit_code = main(
                        [
                            "--config",
                            str(config_path),
                            "--session",
                            "demo-session",
                            "--guide",
                            "I am stuck during setup in README.md",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            self.assertEqual(fake_runtime.session_ids, ["demo-session"])
            self.assertEqual(len(fake_runtime.prompts), 1)
            self.assertIn("Guide mode:", fake_runtime.prompts[0])
            self.assertIn("I am stuck during setup in README.md", fake_runtime.prompts[0])
            print_mock.assert_called_once_with("ok")


    def test_main_runs_repl_without_streaming_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config_path = workspace / "config.json"
            config_path.write_text(
                '{"workspace_root": ".", "sessions_dir": ".crush_py/sessions", "default_backend": "lm_studio", "backends": {"lm_studio": {"type": "openai_compat", "model": "demo", "base_url": "http://example.test/v1", "api_key": "not-needed"}}}',
                encoding="utf-8",
            )
            fake_runtime = FakeRuntime()

            with patch("crush_py.cli.AgentRuntime", return_value=fake_runtime):
                with patch("crush_py.cli.run_repl", return_value=0) as run_repl_mock:
                    exit_code = main(["--config", str(config_path)])

            self.assertEqual(exit_code, 0)
            run_repl_mock.assert_called_once_with(fake_runtime, stream=False)

    def test_run_repl_prints_plain_prompt_result(self):
        runtime = FakeRuntime()

        with patch("builtins.input", side_effect=["summarize README.md", "/quit"]):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = run_repl(runtime, stream=False)

        self.assertEqual(exit_code, 0)
        self.assertIn("ok", stdout.getvalue())
        self.assertEqual(runtime.show_thinking_flags, [True])
