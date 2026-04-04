import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from crush_py.cli import (
    build_guide_prompt,
    build_parser,
    build_summary_prompt,
    build_trace_prompt,
    configure_utf8_stdio,
    main,
    prompt_from_args,
)


class FakeRuntime:
    def __init__(self, config=None, session_store=None):
        self.prompts = []
        self.streams = []
        self.session_ids = []

    def use_session(self, session_id):
        self.session_ids.append(session_id)

    def ask(self, prompt, stream=False):
        self.prompts.append(prompt)
        self.streams.append(stream)
        return "ok"


class CliTests(unittest.TestCase):
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

    def test_build_summary_prompt_for_review_mode(self):
        self.assertEqual(build_summary_prompt("README.md"), "Summarize README.md")

    def test_build_summary_prompt_for_brief_mode(self):
        self.assertEqual(
            build_summary_prompt("README.md", brief=True),
            "Give a short summary for README.md",
        )

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

    def test_prompt_from_args_prefers_explicit_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["--prompt", "hello"])

        self.assertEqual(prompt_from_args(args), "hello")

    def test_prompt_from_args_builds_summary_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["--summarize", "README.md"])

        self.assertEqual(prompt_from_args(args), "Summarize README.md")

    def test_prompt_from_args_builds_trace_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["--trace", "how prompt flows inside crush_py/agent/runtime.py"])

        self.assertEqual(prompt_from_args(args), "Trace how prompt flows inside crush_py/agent/runtime.py")

    def test_prompt_from_args_builds_guide_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["--guide", "turn README.md into a checklist"])

        self.assertIn("Guide mode:", prompt_from_args(args))
        self.assertIn("turn README.md into a checklist", prompt_from_args(args))

    def test_prompt_from_args_builds_brief_summary_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["--summarize-brief", "README.md"])

        self.assertEqual(prompt_from_args(args), "Give a short summary for README.md")

    def test_parser_rejects_multiple_prompt_modes(self):
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["--prompt", "hello", "--summarize", "README.md"])

    def test_main_uses_summarize_brief_prompt(self):
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
                    exit_code = main(["--config", str(config_path), "--summarize-brief", "README.md"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(fake_runtime.prompts, ["Give a short summary for README.md"])
            self.assertEqual(fake_runtime.streams, [False])
            print_mock.assert_called_once_with("ok")

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


    def test_main_runs_repl_with_streaming_by_default(self):
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
            run_repl_mock.assert_called_once_with(fake_runtime, stream=True)
