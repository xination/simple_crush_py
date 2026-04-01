import json
import tempfile
import unittest
from pathlib import Path

from crush_py.agent.runtime import AgentRuntime
from crush_py.backends.base import AssistantTurn, BaseBackend, ToolCall
from crush_py.config import AppConfig, BackendConfig
from crush_py.repl import _format_history, _format_trace
from crush_py.store.session_store import SessionStore


class FakeCatLoopBackend(BaseBackend):
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
                text="I will read the file first.",
                tool_calls=[ToolCall(id="tool-1", name="cat", arguments={"path": "notes.txt"})],
                raw_content=[
                    {"type": "text", "text": "I will read the file first."},
                    {"type": "tool_use", "id": "tool-1", "name": "cat", "input": {"path": "notes.txt"}},
                ],
            )
        return AssistantTurn(text="Confirmed path: notes.txt\nUnconfirmed branches: none\nNext step: none")

    def supports_tool_calls(self):
        return True


class FakeFindThenCatBackend(BaseBackend):
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
                tool_calls=[ToolCall(id="find-1", name="find", arguments={"pattern": "*notes.txt"})],
                raw_content=[
                    {"type": "text", "text": "I will locate the file first."},
                    {"type": "tool_use", "id": "find-1", "name": "find", "input": {"pattern": "*notes.txt"}},
                ],
            )
        if self.turn_count == 2:
            self.follow_up_messages = list(messages)
            return AssistantTurn(
                text="Now I can read it.",
                tool_calls=[ToolCall(id="cat-1", name="cat", arguments={"path": "notes.txt"})],
                raw_content=[
                    {"type": "text", "text": "Now I can read it."},
                    {"type": "tool_use", "id": "cat-1", "name": "cat", "input": {"path": "notes.txt"}},
                ],
            )
        return AssistantTurn(text="Confirmed path: notes.txt\nUnconfirmed branches: none\nNext step: none")

    def supports_tool_calls(self):
        return True


class FakePromptDirectCatBackend(BaseBackend):
    def __init__(self):
        self.reader_turn_count = 0
        self.planner_turn_count = 0
        self.reader_first_messages = None
        self.planner_first_messages = None
        self.reader_system_prompt = None
        self.planner_system_prompt = None
        self.planner_tools_seen = []

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_turn_count += 1
            if tools is None:
                self.reader_system_prompt = system_prompt
                self.reader_first_messages = list(messages)
                return AssistantTurn(
                    text=(
                        "Candidate responsibilities for human review:\n"
                        "1. Manage each session's persisted metadata and lifecycle state.\n"
                        "   Evidence: SessionMeta; create_session; _write_meta.\n"
                        "   Tag: likely_core\n"
                        "2. Persist and reload session message history for later reuse.\n"
                        "   Evidence: append_message; load_messages; messages.jsonl.\n"
                        "   Tag: likely_core\n"
                        "3. Organize session files and storage folders.\n"
                        "   Evidence: list_sessions; load_session; _session_dir.\n"
                        "   Tag: likely_supporting\n"
                        "4. Normalize metadata before writing trace records.\n"
                        "   Evidence: _sanitize_metadata; _first_tool_name; _first_tool_args.\n"
                        "   Tag: likely_helper\n"
                        "Review note: Item 4 looks more like an implementation helper than a top-level file responsibility.\n"
                        "Suggested keep:\n"
                        "- Manage each session's persisted metadata and lifecycle state.\n"
                        "- Persist and reload session message history for later reuse.\n"
                        "- Organize session files and storage folders.\n"
                        "Suggested review/remove:\n"
                        "- Normalize metadata before writing trace records."
                    )
                )
            if self.reader_turn_count == 1:
                self.reader_system_prompt = system_prompt
                self.reader_first_messages = list(messages)
                return AssistantTurn(
                    text="I will inspect the requested file for the planner.",
                    tool_calls=[ToolCall(id="cat-1", name="cat", arguments={"path": "crush_py/store/session_store.py"})],
                    raw_content=[
                        {"type": "text", "text": "I will inspect the requested file for the planner."},
                        {
                            "type": "tool_use",
                            "id": "cat-1",
                            "name": "cat",
                            "input": {"path": "crush_py/store/session_store.py"},
                        },
                    ],
                )
            return AssistantTurn(
                text=(
                    "Confirmed path: crush_py/store/session_store.py\n"
                    "Summary: it stores session metadata and message history.\n"
                    "Evidence: class SessionStore; create_session; append_message.\n"
                    "Unresolved uncertainty: none"
                )
            )
        self.planner_turn_count += 1
        if self.planner_turn_count == 1:
            self.planner_system_prompt = system_prompt
            self.planner_first_messages = list(messages)
            self.planner_tools_seen.append(tools)
        return AssistantTurn(
            text=(
                "Candidate responsibilities for human review:\n"
                "1. Manage each session's persisted metadata and lifecycle state.\n"
                "   Evidence: SessionMeta; create_session; _write_meta.\n"
                "   Tag: likely_core\n"
                "2. Persist and reload session message history for later reuse.\n"
                "   Evidence: append_message; load_messages; messages.jsonl.\n"
                "   Tag: likely_core\n"
                "3. Organize session files and storage folders.\n"
                "   Evidence: list_sessions; load_session; _session_dir.\n"
                "   Tag: likely_supporting\n"
                "4. Normalize metadata before writing trace records.\n"
                "   Evidence: _sanitize_metadata; _first_tool_name; _first_tool_args.\n"
                "   Tag: likely_helper\n"
                "Review note: Item 4 looks more like an implementation helper than a top-level file responsibility.\n"
                "Suggested keep:\n"
                "- Manage each session's persisted metadata and lifecycle state.\n"
                "- Persist and reload session message history for later reuse.\n"
                "- Organize session files and storage folders.\n"
                "Suggested review/remove:\n"
                "- Normalize metadata before writing trace records."
            )
        )

    def supports_tool_calls(self):
        return True


class FakeInlineCatResultBackend(BaseBackend):
    def __init__(self):
        self.reader_turn_count = 0
        self.second_turn_messages = None

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_turn_count += 1
            if self.reader_turn_count == 1:
                return AssistantTurn(
                    text="I will read the file first.",
                    tool_calls=[ToolCall(id="cat-1", name="cat", arguments={"path": "notes.txt"})],
                    raw_content=[
                        {"type": "text", "text": "I will read the file first."},
                        {"type": "tool_use", "id": "cat-1", "name": "cat", "input": {"path": "notes.txt"}},
                    ],
                )
            self.second_turn_messages = list(messages)
            return AssistantTurn(
                text="Confirmed path: notes.txt\nSummary: note file\nEvidence: 1|one ; 2|two ; 3|three\nUnresolved uncertainty: none"
            )
        return AssistantTurn(text="Confirmed path: notes.txt\nUnconfirmed branches: none\nNext step: none")

    def supports_tool_calls(self):
        return True


class FakePlannerReaderBackend(BaseBackend):
    def __init__(self):
        self.planner_turn_count = 0
        self.reader_turn_count = 0
        self.planner_second_messages = None
        self.reader_first_messages = None

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_turn_count += 1
            if self.reader_turn_count == 1:
                self.reader_first_messages = list(messages)
                return AssistantTurn(
                    text="I will inspect the chosen file for the planner.",
                    tool_calls=[ToolCall(id="cat-r1", name="cat", arguments={"path": "notes.txt"})],
                    raw_content=[
                        {"type": "text", "text": "I will inspect the chosen file for the planner."},
                        {"type": "tool_use", "id": "cat-r1", "name": "cat", "input": {"path": "notes.txt"}},
                    ],
                )
            return AssistantTurn(
                text=(
                    "Confirmed path: notes.txt\n"
                    "Summary: the file contains three lines of note text.\n"
                    "Evidence: 1|one ; 2|two ; 3|three\n"
                    "Unresolved uncertainty: none"
                )
            )

        self.planner_turn_count += 1
        if self.planner_turn_count == 1:
            return AssistantTurn(
                text="I will locate the file first.",
                tool_calls=[ToolCall(id="find-1", name="find", arguments={"pattern": "*notes.txt"})],
                raw_content=[
                    {"type": "text", "text": "I will locate the file first."},
                    {"type": "tool_use", "id": "find-1", "name": "find", "input": {"pattern": "*notes.txt"}},
                ],
            )
        self.planner_second_messages = list(messages)
        return AssistantTurn(text="Confirmed path: notes.txt\nUnconfirmed branches: none\nNext step: none")

    def supports_tool_calls(self):
        return True


class FakeReaderThreeCallBackend(BaseBackend):
    def __init__(self):
        self.reader_turn_count = 0
        self.reader_tools_seen = []
        self.final_reader_tools = None

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_turn_count += 1
            self.reader_tools_seen.append([tool["name"] for tool in (tools or [])])
            if self.reader_turn_count == 1:
                return AssistantTurn(
                    text="Let me inspect the outline first.",
                    tool_calls=[ToolCall(id="outline-1", name="get_outline", arguments={"path": "notes.txt"})],
                    raw_content=[
                        {"type": "text", "text": "Let me inspect the outline first."},
                        {"type": "tool_use", "id": "outline-1", "name": "get_outline", "input": {"path": "notes.txt"}},
                    ],
                )
            if self.reader_turn_count == 2:
                return AssistantTurn(
                    text="Now I will read the first chunk.",
                    tool_calls=[ToolCall(id="cat-1", name="cat", arguments={"path": "notes.txt", "offset": 0, "limit": 2})],
                    raw_content=[
                        {"type": "text", "text": "Now I will read the first chunk."},
                        {"type": "tool_use", "id": "cat-1", "name": "cat", "input": {"path": "notes.txt", "offset": 0, "limit": 2}},
                    ],
                )
            if self.reader_turn_count == 3:
                return AssistantTurn(
                    text="I need one more chunk.",
                    tool_calls=[ToolCall(id="cat-2", name="cat", arguments={"path": "notes.txt", "offset": 2, "limit": 2})],
                    raw_content=[
                        {"type": "text", "text": "I need one more chunk."},
                        {"type": "tool_use", "id": "cat-2", "name": "cat", "input": {"path": "notes.txt", "offset": 2, "limit": 2}},
                    ],
                )
            self.final_reader_tools = tools
            return AssistantTurn(
                text=(
                    "Confirmed path: notes.txt\n"
                    "Summary: the file spans four lines gathered across two cat pages.\n"
                    "Evidence: 1|one ; 2|two ; 3|three ; 4|four\n"
                    "Unresolved uncertainty: none"
                )
            )
        return AssistantTurn(text="Confirmed path: notes.txt\nUnconfirmed branches: none\nNext step: none")

    def supports_tool_calls(self):
        return True


class FakeBroadGrepBackend(BaseBackend):
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
                text="I will search broadly first.",
                tool_calls=[ToolCall(id="grep-1", name="grep", arguments={"pattern": "needle", "path": ".", "include": "*.py"})],
                raw_content=[
                    {"type": "text", "text": "I will search broadly first."},
                    {"type": "tool_use", "id": "grep-1", "name": "grep", "input": {"pattern": "needle", "path": ".", "include": "*.py"}},
                ],
            )
        return AssistantTurn(
            text="Confirmed path: none yet\nUnconfirmed branches: many matching files\nNext step: narrow the grep by folder or extension"
        )

    def supports_tool_calls(self):
        return True


class FakePlainBackend(BaseBackend):
    def generate(self, system_prompt, messages, tools=None):
        return "Confirmed path: README.md\nUnconfirmed branches: none\nNext step: none"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())


class FakeStructureDirectFileBackend(BaseBackend):
    def __init__(self):
        self.reader_turn_count = 0
        self.reader_first_messages = None

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_turn_count += 1
            if self.reader_turn_count == 1:
                self.reader_first_messages = list(messages)
                return AssistantTurn(
                    text="I need the outline first.",
                    tool_calls=[ToolCall(id="outline-1", name="get_outline", arguments={"path": "crush_py/store/session_store.py"})],
                    raw_content=[
                        {"type": "text", "text": "I need the outline first."},
                        {
                            "type": "tool_use",
                            "id": "outline-1",
                            "name": "get_outline",
                            "input": {"path": "crush_py/store/session_store.py"},
                        },
                    ],
                )
            return AssistantTurn(
                text=(
                    "Confirmed path: crush_py/store/session_store.py\n"
                    "Summary: SessionStore and SessionMeta define persisted session behavior.\n"
                    "Evidence: class SessionMeta; class SessionStore.\n"
                    "Unresolved uncertainty: none"
                )
            )
        return AssistantTurn(text="The file defines SessionMeta and SessionStore methods.")

    def supports_tool_calls(self):
        return True


class FakePartialDirectSummaryBackend(BaseBackend):
    def __init__(self):
        self.reader_messages = None

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_messages = list(messages)
            return AssistantTurn(
                text=(
                    "Candidate responsibilities for human review:\n"
                    "1. Manage session metadata.\n"
                    "   Evidence: SessionMeta; create_session.\n"
                    "   Tag: likely_core\n"
                    "2. Write message history.\n"
                    "   Evidence: append_message; messages.jsonl.\n"
                    "   Tag: likely_core\n"
                    "3. Normalize metadata before storing.\n"
                    "   Evidence: _sanitize_metadata.\n"
                    "   Tag: likely_helper\n"
                    "4. Organize the session storage layout.\n"
                    "   Evidence: _session_dir.\n"
                    "   Tag: likely_supporting\n"
                    "Review note: Item 3 may be too low-level for a top-level responsibility.\n"
                    "Suggested keep:\n"
                    "- Manage session metadata.\n"
                    "- Write message history.\n"
                    "- Organize the session storage layout.\n"
                    "Suggested review/remove:\n"
                    "- Normalize metadata before storing."
                )
            )
        return AssistantTurn(
            text=(
                "Candidate responsibilities for human review:\n"
                "1. Manage session metadata.\n"
                "   Evidence: SessionMeta; create_session.\n"
                "   Tag: likely_core\n"
                "2. Write message history.\n"
                "   Evidence: append_message; messages.jsonl.\n"
                "   Tag: likely_core\n"
                "3. Normalize metadata before storing.\n"
                "   Evidence: _sanitize_metadata.\n"
                "   Tag: likely_helper\n"
                "4. Organize the session storage layout.\n"
                "   Evidence: _session_dir.\n"
                "   Tag: likely_supporting\n"
                "Review note: Item 3 may be too low-level for a top-level responsibility.\n"
                "Suggested keep:\n"
                "- Manage session metadata.\n"
                "- Write message history.\n"
                "- Organize the session storage layout.\n"
                "Suggested review/remove:\n"
                "- Normalize metadata before storing."
            )
        )


class FakeBriefDirectSummaryBackend(BaseBackend):
    def __init__(self):
        self.reader_messages = None

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_messages = list(messages)
            return AssistantTurn(
                text=(
                    "1. Store each session's metadata and lifecycle timestamps.\n"
                    "   Evidence: SessionMeta; create_session; _write_meta.\n"
                    "2. Append and reload conversation history from disk.\n"
                    "   Evidence: append_message; load_messages; messages.jsonl.\n"
                    "3. Keep session folders and trace metadata organized.\n"
                    "   Evidence: list_sessions; _session_dir; _sanitize_metadata.\n"
                    "Review note: Helper details should stay folded into the main storage responsibilities.\n"
                    "Suggested keep:\n"
                    "- Store each session's metadata and lifecycle timestamps.\n"
                    "- Append and reload conversation history from disk.\n"
                    "Suggested review/remove:\n"
                    "- Keep session folders and trace metadata organized."
                )
            )
        return AssistantTurn(text="unused")

    def supports_tool_calls(self):
        return True


class FakeInsufficientReaderSummaryBackend(BaseBackend):
    def __init__(self):
        self.reader_turn_count = 0
        self.planner_turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_turn_count += 1
            return AssistantTurn(
                text=(
                    "Candidate responsibilities for human review:\n"
                    "1. Manage session metadata.\n"
                    "   Evidence: SessionMeta; create_session.\n"
                    "   Tag: likely_core\n"
                    "2. Write message history.\n"
                    "   Evidence: append_message; messages.jsonl.\n"
                    "   Tag: likely_core\n"
                    "3. Normalize metadata before storing.\n"
                    "   Evidence: _sanitize_metadata.\n"
                    "   Tag: likely_helper\n"
                    "4. Organize the session storage layout.\n"
                    "   Evidence: _session_dir.\n"
                    "   Tag: likely_supporting\n"
                    "Review note: Item 3 may be too low-level for a top-level responsibility.\n"
                    "Suggested keep:\n"
                    "- Manage session metadata.\n"
                    "- Write message history.\n"
                    "- Organize the session storage layout.\n"
                    "Suggested review/remove:\n"
                    "- Normalize metadata before storing."
                )
            )
        self.planner_turn_count += 1
        return AssistantTurn(
            text=(
                "Candidate responsibilities for human review:\n"
                "1. Manage each session's stored metadata and lifecycle.\n"
                "   Evidence: SessionMeta; create_session; _write_meta.\n"
                "   Tag: likely_core\n"
                "2. Persist and reload conversation history across runs.\n"
                "   Evidence: append_message; load_messages; messages.jsonl.\n"
                "   Tag: likely_core\n"
                "3. Keep session storage organized through internal helper paths.\n"
                "   Evidence: _session_dir; list_sessions.\n"
                "   Tag: likely_supporting\n"
                "4. Normalize metadata before trace persistence.\n"
                "   Evidence: _sanitize_metadata.\n"
                "   Tag: likely_helper\n"
                "Review note: Item 4 may be too low-level for a top-level responsibility.\n"
                "Suggested keep:\n"
                "- Manage each session's stored metadata and lifecycle.\n"
                "- Persist and reload conversation history across runs.\n"
                "- Keep session storage organized through internal helper paths.\n"
                "Suggested review/remove:\n"
                "- Normalize metadata before trace persistence."
            )
        )

    def supports_tool_calls(self):
        return True


class AgentRuntimeTests(unittest.TestCase):
    def _make_config(self, workspace):
        backend = BackendConfig(
            name="lm_studio",
            type="openai_compat",
            model="demo-3b",
            base_url="http://example.test/v1",
            api_key="not-needed",
            api_key_env=None,
            timeout=30,
            max_tokens=256,
        )
        return AppConfig(
            workspace_root=workspace,
            sessions_dir=workspace / ".crush_py" / "sessions",
            default_backend="lm_studio",
            trace_mode="lean",
            backends={"lm_studio": backend},
        )

    def test_ask_with_tool_loop_persists_tool_trace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            runtime._create_backend = lambda backend_cfg: FakeCatLoopBackend()

            result = runtime.ask("Trace notes.txt")
            messages = store.load_messages(runtime.active_session.id)

            self.assertIn("Confirmed path: notes.txt", result)
            self.assertEqual([message.kind for message in messages], ["message", "tool_use", "tool_use", "tool_result", "tool_result", "message"])
            self.assertEqual(messages[1].metadata["tool"], "reader")
            self.assertEqual(messages[2].metadata["agent"], "reader")
            self.assertEqual(messages[2].metadata["tool"], "cat")
            self.assertEqual(messages[2].content, "")
            self.assertNotIn("text", messages[2].metadata)
            self.assertEqual(messages[3].metadata["tool"], "cat")
            self.assertEqual(messages[3].content, "")
            self.assertIn("Read `notes.txt` lines 1-3.", messages[3].metadata["summary"])
            self.assertEqual(messages[4].metadata["tool"], "reader")

    def test_format_trace_reads_recent_tool_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            runtime._create_backend = lambda backend_cfg: FakeCatLoopBackend()

            runtime.ask("Trace notes.txt")
            text = _format_trace(runtime, limit=10)

            self.assertIn("[tool_use]", text)
            self.assertIn("tool: cat", text)
            self.assertIn("[tool_result]", text)
            self.assertIn("arguments: {'path': 'notes.txt'}", text)
            self.assertIn("stage: assistant_final", text)

    def test_find_single_candidate_forces_cat_before_answering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakePlannerReaderBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Find notes.txt and trace it.")

            self.assertIn("Confirmed path: notes.txt", result)
            self.assertIsNotNone(backend.reader_first_messages)
            self.assertIn("Target file: notes.txt", backend.reader_first_messages[0]["content"])
            self.assertIsNotNone(backend.planner_second_messages)
            self.assertTrue(
                any(
                    message.get("role") == "user"
                    and isinstance(message.get("content"), str)
                    and "Reader agent summary for `notes.txt`" in message.get("content")
                    for message in backend.planner_second_messages
                )
            )

    def test_broad_grep_keeps_answer_uncertain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            for index in range(20):
                (workspace / "f{0}.py".format(index)).write_text("needle = 1\nneedle = 2\nneedle = 3\nneedle = 4\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            runtime._create_backend = lambda backend_cfg: FakeBroadGrepBackend()

            result = runtime.ask("Trace where needle is used")

            self.assertIn("Unconfirmed branches", result)
            self.assertIn("narrow", result.lower())

    def test_prompt_named_file_forces_cat_before_any_other_tool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakePromptDirectCatBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。")

            self.assertIn("1.", result)
            self.assertEqual(backend.planner_tools_seen, [])

    def test_direct_file_summary_uses_cat_first_without_outline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakePromptDirectCatBackend()
            runtime._create_backend = lambda backend_cfg: backend

            runtime.ask("請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。")
            messages = store.load_messages(runtime.active_session.id)

            tool_use_names = [message.metadata.get("tool") for message in messages if message.kind == "tool_use"]
            self.assertEqual(tool_use_names, ["reader", "cat"])
            self.assertNotIn("get_outline", tool_use_names)
            cat_message = next(message for message in messages if message.kind == "tool_use" and message.metadata.get("tool") == "cat")
            self.assertTrue(cat_message.metadata["args"].get("full"))

    def test_direct_file_summary_reads_full_file_before_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("line1\nline2\nline3\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakePromptDirectCatBackend()
            runtime._create_backend = lambda backend_cfg: backend

            runtime.ask("請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。")
            messages = store.load_messages(runtime.active_session.id)

            cat_result = next(message for message in messages if message.kind == "tool_result" and message.metadata.get("tool") == "cat")
            self.assertIn("Read full file `crush_py/store/session_store.py`", cat_result.metadata["summary"])

    def test_direct_file_summary_prompt_requests_review_candidates_with_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakePromptDirectCatBackend()
            runtime._create_backend = lambda backend_cfg: backend

            runtime.ask("請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。")

            self.assertIsNotNone(backend.reader_first_messages)
            prompt_text = backend.reader_first_messages[0]["content"]
            self.assertIn("human-review draft", prompt_text)
            self.assertIn("Return 4 to 6 candidate responsibilities", prompt_text)
            self.assertIn("Evidence:", prompt_text)
            self.assertIn("Tag:", prompt_text)
            self.assertIn("Suggested keep:", prompt_text)

    def test_assistant_reuses_reader_review_draft_without_rewriting(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakePromptDirectCatBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。")

            self.assertEqual(backend.planner_turn_count, 0)
            self.assertIn("Candidate responsibilities for human review:", result)
            self.assertIn("Tag: likely_helper", result)
            self.assertIn("Suggested review/remove:", result)

    def test_structure_prompt_can_still_use_outline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeStructureDirectFileBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("請說明 crush_py/store/session_store.py 裡有哪些 class 與 method。")
            messages = store.load_messages(runtime.active_session.id)

            self.assertIn("SessionMeta", result)
            tool_use_names = [message.metadata.get("tool") for message in messages if message.kind == "tool_use"]
            self.assertIn("get_outline", tool_use_names)
            self.assertIn("Use `get_outline` first if it helps", backend.reader_first_messages[0]["content"])

    def test_partial_direct_file_summary_is_marked_preliminary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakePartialDirectSummaryBackend()
            runtime._collect_summary_file_reads = lambda session_id, rel_path: (
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": "reader-cat-page:{0}:0".format(rel_path),
                        "tool_name": "cat",
                        "content": '<file path="{0}" offset="0" limit="400">\n     1|partial\n</file>\nFile has more lines. Use offset >= 400 to continue.'.format(rel_path),
                    }
                ],
                "partial",
            )
            session = runtime.new_session()

            result = runtime._run_direct_file_summary_reader(
                session.id,
                backend,
                "請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。",
                "crush_py/store/session_store.py",
            )
            messages = store.load_messages(session.id)

            self.assertIn("Preliminary summary (partial file coverage).", result)
            reader_result = next(message for message in messages if message.kind == "tool_result" and message.metadata.get("tool") == "reader")
            self.assertEqual(reader_result.metadata["args"]["coverage"], "partial")

    def test_postprocess_direct_file_summary_output_prefixes_partial_label(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            runtime._session_has_partial_reader_summary = lambda session_id: True

            text = runtime._postprocess_direct_file_summary_output(
                "session-1",
                "請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。",
                "1. Main responsibility\n   Evidence: SessionStore",
            )

            self.assertIn("Preliminary summary (partial file coverage).", text)

    def test_direct_file_summary_defaults_to_review_draft_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakePromptDirectCatBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Give a summary for crush_py/store/session_store.py.")

            self.assertIn("Candidate responsibilities for human review:", result)
            self.assertIn("Evidence:", result)
            self.assertIn("Suggested keep:", result)
            self.assertFalse(runtime._is_brief_summary_prompt("Give a summary for crush_py/store/session_store.py."))

    def test_brief_direct_file_summary_omits_evidence_and_review_scaffolding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeBriefDirectSummaryBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Give a short summary for crush_py/store/session_store.py.")

            self.assertIn("1. Store each session's metadata and lifecycle timestamps.", result)
            self.assertNotIn("Evidence:", result)
            self.assertNotIn("Review note:", result)
            self.assertNotIn("Suggested keep:", result)
            self.assertNotIn("Suggested review/remove:", result)

    def test_quickly_summarize_direct_file_uses_brief_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("# crush_py\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeBriefDirectSummaryBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("quickly summarize README.md")

            self.assertTrue(runtime._is_brief_summary_prompt("quickly summarize README.md"))
            self.assertIn("1. Store each session's metadata and lifecycle timestamps.", result)
            self.assertNotIn("Evidence:", result)

    def test_brief_direct_file_summary_uses_spaced_bullets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeBriefDirectSummaryBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Give a short summary for crush_py/store/session_store.py.")

            self.assertIn(
                "1. Store each session's metadata and lifecycle timestamps.\n\n"
                "2. Append and reload conversation history from disk.\n\n"
                "3. Keep session folders and trace metadata organized.",
                result,
            )

    def test_review_draft_mode_keeps_evidence_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakePromptDirectCatBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Explain what crush_py/store/session_store.py is responsible for.")

            self.assertIn("Evidence:", result)
            self.assertIn("Tag: likely_helper", result)
            self.assertIn("Review note:", result)

    def test_compact_reader_cat_content_trims_large_payloads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            large_content = "<file path=\"README.md\" offset=\"0\" limit=\"200\">\n" + "\n".join(
                "{0:>6}|{1}".format(index, "x" * 80)
                for index in range(1, 80)
            ) + "\n</file>"

            compacted = runtime._compact_reader_cat_content(large_content)

            self.assertLess(len(compacted), len(large_content))
            self.assertIn("<file path=\"README.md\"", compacted)
            self.assertIn("lines omitted for summary context", compacted)
            self.assertIn("</file>", compacted)

    def test_partial_brief_direct_file_summary_preserves_preliminary_label(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeBriefDirectSummaryBackend()
            runtime._create_backend = lambda backend_cfg: backend
            runtime._collect_summary_file_reads = lambda session_id, rel_path: (
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": "reader-cat-page:{0}:0".format(rel_path),
                        "tool_name": "cat",
                        "content": '<file path="{0}" offset="0" limit="400">\n     1|partial\n</file>\nFile has more lines. Use offset >= 400 to continue.'.format(rel_path),
                    }
                ],
                "partial",
            )

            result = runtime.ask("Give a short summary for crush_py/store/session_store.py.")

            self.assertTrue(result.startswith("Preliminary summary (partial file coverage).\n1. "))
            self.assertNotIn("Evidence:", result)

    def test_trace_prompt_does_not_use_direct_summary_fast_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))

            self.assertFalse(runtime._is_direct_file_summary_prompt("Trace how crush_py/store/session_store.py appends messages."))

    def test_trace_shows_planner_and_reader_agents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            runtime._create_backend = lambda backend_cfg: FakePlannerReaderBackend()

            runtime.ask("Find notes.txt and trace it.")
            text = _format_trace(runtime, limit=20)

            self.assertIn("agent: planner", text)
            self.assertIn("agent: reader", text)
            self.assertIn("tool: reader", text)

    def test_small_cat_result_is_forwarded_to_backend_in_full(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeInlineCatResultBackend()
            runtime._create_backend = lambda backend_cfg: backend

            runtime.ask("Trace notes.txt")

            self.assertIsNotNone(backend.second_turn_messages)
            tool_results = backend.second_turn_messages[-1]["content"]
            self.assertIn('<file path="notes.txt"', tool_results[0]["content"])

    def test_reader_raw_tool_payload_is_excluded_from_planner_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            runtime._create_backend = lambda backend_cfg: FakeCatLoopBackend()

            runtime.ask("Trace notes.txt")
            history = runtime._messages_for_backend(runtime.active_session.id)
            rendered = json.dumps(history, ensure_ascii=False)

            self.assertIn("Reader summary for `notes.txt`", rendered)
            self.assertNotIn('<file path="notes.txt"', rendered)
            self.assertNotIn("I will read the file first.", rendered)

    def test_reader_can_use_up_to_three_tool_calls_before_forced_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeReaderThreeCallBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Trace notes.txt")

            self.assertIn("Confirmed path: notes.txt", result)
            self.assertEqual(
                backend.reader_tools_seen[:3],
                [["get_outline", "cat"], ["get_outline", "cat"], ["get_outline", "cat"]],
            )
            self.assertIsNone(backend.final_reader_tools)

    def test_plain_backend_persists_final_assistant_raw_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            runtime._create_backend = lambda backend_cfg: FakePlainBackend()

            result = runtime.ask("Summarize README.md")
            messages = store.load_messages(runtime.active_session.id)

            self.assertIn("Confirmed path: README.md", result)
            self.assertEqual([message.kind for message in messages], ["message", "message"])

    def test_format_history_reads_recent_conversation_messages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            runtime._create_backend = lambda backend_cfg: FakePlainBackend()

            runtime.ask("Summarize README.md")
            text = _format_history(runtime, limit=10)

            self.assertIn("[user]", text)
            self.assertIn("Summarize README.md", text)
            self.assertIn("[assistant]", text)
            self.assertIn("Confirmed path: README.md", text)

    def test_summary_cache_reuses_cat_summary_for_same_slice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            session = runtime.new_session()

            summary_a = runtime._summarize_tool_result(
                session.id,
                "cat",
                {"path": "notes.txt", "offset": 0, "limit": 80},
                '<file path="notes.txt" offset="0" limit="80">\n     1|one\n     2|two\n     3|three\n</file>',
            )
            summary_b = runtime._summarize_tool_result(
                session.id,
                "cat",
                {"path": "notes.txt", "offset": 0, "limit": 80},
                '<file path="notes.txt" offset="0" limit="80">\n     1|one\n     2|two\n     3|three\n</file>',
            )

            self.assertEqual(summary_a, summary_b)
            self.assertIn("notes.txt", runtime._state_for_session(session.id).file_summaries)


if __name__ == "__main__":
    unittest.main()
