import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crush_py.cli import build_parser, build_summary_prompt, build_trace_prompt, main, prompt_from_args


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


if __name__ == "__main__":
    unittest.main()
