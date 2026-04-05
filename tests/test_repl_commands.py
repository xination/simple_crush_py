import io
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass

from crush_py.repl_commands import HELP_TEXT, parse_optional_limit, parse_quick_command, safe_split, try_handle_command


@dataclass
class FakeSession:
    id: str
    backend: str
    title: str = "Demo"
    model: str = "demo-model"


class FakeSessionStore:
    def __init__(self, sessions=None):
        self._sessions = sessions or []

    def list_sessions(self):
        return list(self._sessions)


class FakeRuntime:
    def __init__(self):
        self.active_backend_name = "demo"
        self.active_session = None
        self.session_store = FakeSessionStore([FakeSession("s-1", "demo", "First session")])
        self.tool_calls = []
        self.used_sessions = []
        self.new_session_calls = 0
        self.prompts = []
        self.quick_prompts = []
        self.show_thinking_flags = []
        self.models = []

    def new_session(self):
        self.new_session_calls += 1
        return FakeSession("new-session", "demo")

    def set_session_model(self, model):
        self.models.append(model)
        session = FakeSession("new-session", "demo", model=model)
        self.active_session = session
        return session

    def available_backends(self):
        return ["demo", "other"]

    def available_tools(self):
        return ["cat", "grep"]

    def use_session(self, session_id):
        if session_id == "missing":
            raise FileNotFoundError(session_id)
        self.used_sessions.append(session_id)
        return FakeSession(session_id, "demo")

    def run_tool(self, name, payload):
        self.tool_calls.append((name, payload))
        return "tool-result:{0}:{1}".format(name, payload)

    def ask(self, prompt, stream=False, show_thinking=False):
        self.prompts.append((prompt, stream))
        self.show_thinking_flags.append(show_thinking)
        return "assistant-result:{0}".format(prompt)

    def ask_quick_file(self, path, prompt, stream=False):
        self.quick_prompts.append((path, prompt, stream))
        if stream:
            print("quick-result:{0}:{1}".format(path, prompt), end="")
        return "quick-result:{0}:{1}".format(path, prompt)


class ReplCommandsTests(unittest.TestCase):
    def test_safe_split_falls_back_when_quotes_are_unbalanced(self):
        self.assertEqual(safe_split('/find "unterminated notes.txt'), ["/find", '"unterminated', "notes.txt"])

    def test_parse_optional_limit_uses_default(self):
        self.assertEqual(parse_optional_limit(None, "usage"), 20)

    def test_parse_optional_limit_rejects_non_positive_values(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = parse_optional_limit("0", "Usage: /history [LIMIT]")
        self.assertIsNone(result)
        self.assertIn("Usage: /history [LIMIT]", stdout.getvalue())

    def test_parse_quick_command_extracts_path_and_prompt(self):
        self.assertEqual(
            parse_quick_command("/quick @README.md, show me how to start"),
            ("README.md", "show me how to start"),
        )

    def test_try_handle_command_runs_tool_command(self):
        runtime = FakeRuntime()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            handled, exit_code = try_handle_command(runtime, "/grep needle src *.py")

        self.assertTrue(handled)
        self.assertIsNone(exit_code)
        self.assertEqual(runtime.tool_calls, [("grep", {"pattern": "needle", "path": "src", "include": "*.py"})])
        self.assertIn("tool-result:grep", stdout.getvalue())

    def test_try_handle_command_handles_unknown_session(self):
        runtime = FakeRuntime()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            handled, exit_code = try_handle_command(runtime, "/use missing")

        self.assertTrue(handled)
        self.assertIsNone(exit_code)
        self.assertIn("Session not found: missing", stdout.getvalue())

    def test_try_handle_command_returns_exit_for_quit(self):
        runtime = FakeRuntime()
        handled, exit_code = try_handle_command(runtime, "/quit")

        self.assertTrue(handled)
        self.assertEqual(exit_code, 0)

    def test_try_handle_command_returns_not_handled_for_plain_prompt(self):
        runtime = FakeRuntime()
        handled, exit_code = try_handle_command(runtime, "summarize runtime.py")

        self.assertFalse(handled)
        self.assertIsNone(exit_code)

    def test_trace_command_sends_trace_prompt(self):
        runtime = FakeRuntime()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            handled, exit_code = try_handle_command(runtime, "/trace how prompt flows inside crush_py/agent/runtime.py")

        self.assertTrue(handled)
        self.assertIsNone(exit_code)
        self.assertEqual(
            runtime.prompts,
            [("Trace how prompt flows inside crush_py/agent/runtime.py", False)],
        )
        self.assertIn("\x1b[1m/trace how prompt flows inside crush_py/agent/runtime.py\x1b[0m", stdout.getvalue())

    def test_guide_command_sends_guide_prompt(self):
        runtime = FakeRuntime()
        handled, exit_code = try_handle_command(runtime, "/guide summarize README.md for a beginner")

        self.assertTrue(handled)
        self.assertIsNone(exit_code)
        self.assertEqual(len(runtime.prompts), 1)
        self.assertIn("Guide mode:", runtime.prompts[0][0])
        self.assertIn("summarize README.md for a beginner", runtime.prompts[0][0])

    def test_summarize_command_sends_summary_prompt(self):
        runtime = FakeRuntime()
        handled, exit_code = try_handle_command(runtime, "/summarize README.md")

        self.assertTrue(handled)
        self.assertIsNone(exit_code)
        self.assertEqual(runtime.prompts, [("Give a short summary for README.md", False)])
        self.assertEqual(runtime.show_thinking_flags, [True])

    def test_summarize_command_prints_result(self):
        runtime = FakeRuntime()
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            handled, exit_code = try_handle_command(runtime, "/summarize README.md")

        self.assertTrue(handled)
        self.assertIsNone(exit_code)
        self.assertIn("\x1b[1m/summarize README.md\x1b[0m", stdout.getvalue())
        self.assertIn("assistant-result:Give a short summary for README.md", stdout.getvalue())

    def test_help_text_hides_advanced_commands(self):
        self.assertNotIn("/sessions", HELP_TEXT)
        self.assertNotIn("/use <session_id>", HELP_TEXT)
        self.assertNotIn("/outline", HELP_TEXT)
        self.assertNotIn("/history", HELP_TEXT)
        self.assertNotIn("/tools", HELP_TEXT)
        self.assertNotIn("/tree", HELP_TEXT)

    def test_help_text_shows_quick_command(self):
        self.assertIn("/quick @PATH, PROMPT", HELP_TEXT)
        self.assertIn("always streams", HELP_TEXT)
        self.assertIn("first comma", HELP_TEXT)
        self.assertIn("everything after the first comma", HELP_TEXT)

    def test_tool_trace_command_shows_trace_log(self):
        runtime = FakeRuntime()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            handled, exit_code = try_handle_command(runtime, "/tool-trace 5")

        self.assertTrue(handled)
        self.assertIsNone(exit_code)
        self.assertEqual(runtime.prompts, [])
        self.assertIn("No active session.", stdout.getvalue())


    def test_summarize_command_passes_stream_to_ask(self):
        runtime = FakeRuntime()
        handled, exit_code = try_handle_command(runtime, "/summarize README.md", stream=True)

        self.assertTrue(handled)
        self.assertEqual(runtime.prompts, [("Give a short summary for README.md", True)])
        self.assertEqual(runtime.show_thinking_flags, [True])

    def test_model_command_sets_session_model(self):
        runtime = FakeRuntime()
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            handled, exit_code = try_handle_command(runtime, "/model google/gemma-3-4b")

        self.assertTrue(handled)
        self.assertIsNone(exit_code)
        self.assertEqual(runtime.models, ["google/gemma-3-4b"])
        self.assertIn("model=google/gemma-3-4b", stdout.getvalue())

    def test_quick_command_uses_quick_file_mode(self):
        runtime = FakeRuntime()
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            handled, exit_code = try_handle_command(
                runtime,
                "/quick @README.md, show me how to start, prefer in a list format",
            )

        self.assertTrue(handled)
        self.assertIsNone(exit_code)
        self.assertEqual(
            runtime.quick_prompts,
            [("README.md", "show me how to start, prefer in a list format", True)],
        )
        self.assertEqual(stdout.getvalue().count("quick-result:README.md:show me how to start"), 1)

    def test_quick_command_forces_stream_even_when_repl_stream_is_false(self):
        runtime = FakeRuntime()

        handled, exit_code = try_handle_command(
            runtime,
            "/quick @README.md, show me how to start",
            stream=False,
        )

        self.assertTrue(handled)
        self.assertIsNone(exit_code)
        self.assertEqual(runtime.quick_prompts, [("README.md", "show me how to start", True)])
