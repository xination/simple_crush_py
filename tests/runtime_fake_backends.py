import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from crush_py.agent.runtime import AgentRuntime
from crush_py.agent.intent_router import IntentDecision
from crush_py.backends.base import AssistantTurn, BackendError, BaseBackend, ToolCall
from crush_py.config import AppConfig, BackendConfig
from crush_py.output_sanitize import sanitize_text
from crush_py.repl import _format_history, _format_trace
from crush_py.repl_commands import try_handle_command
from crush_py.store.session_store import SessionStore
from crush_py.tools.base import ToolError
from crush_py.tools.cat import CatTool


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


class FakeStreamingToolCallBackend(BaseBackend):
    def __init__(self):
        self.turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def stream_generate_turn(self, system_prompt, messages, tools=None):
        self.turn_count += 1
        if self.turn_count == 1:
            return AssistantTurn(
                text="I will inspect the file first.",
                tool_calls=[ToolCall(id="tool-1", name="cat", arguments={"path": "notes.txt"})],
                raw_content=[
                    {"type": "text", "text": "I will inspect the file first."},
                    {"type": "tool_use", "id": "tool-1", "name": "cat", "input": {"path": "notes.txt"}},
                ],
            )
        return AssistantTurn(text="Confirmed path: notes.txt\nUnconfirmed branches: none\nNext step: none")

    def generate_turn(self, system_prompt, messages, tools=None):
        raise AssertionError("stream_generate_turn should be used when stream=True")

    def supports_tool_calls(self):
        return True


class FakeHistoryAwareBackend(BaseBackend):
    def __init__(self):
        self.messages_seen = None

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.messages_seen = list(messages)
        return AssistantTurn(text="Likely matches from history-aware search: run.sh, run.tcsh")


class FakePlannerUsesRealToolsBackend(BaseBackend):
    def __init__(self):
        self.planner_turn_count = 0
        self.reader_turn_count = 0
        self.planner_messages = []
        self.reader_messages = []

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_turn_count += 1
            self.reader_messages.append(list(messages))
            if self.reader_turn_count == 1:
                return AssistantTurn(
                    text="I found one likely match and will read it.",
                    tool_calls=[ToolCall(id="reader-cat-1", name="cat", arguments={"path": "run.sh"})],
                    raw_content=[
                        {"type": "text", "text": "I found one likely match and will read it."},
                        {"type": "tool_use", "id": "reader-cat-1", "name": "cat", "input": {"path": "run.sh"}},
                    ],
                )
            return AssistantTurn(
                text=(
                    "Confirmed path: run.sh\n"
                    "Summary: the file is a shell script that contains `#!/bin/sh` and prints hello.\n"
                    "Evidence: 1|#!/bin/sh ; 2|echo hello\n"
                    "Unresolved uncertainty: none"
                )
            )

        self.planner_turn_count += 1
        self.planner_messages.append(list(messages))
        if self.planner_turn_count == 1:
            return AssistantTurn(
                text="I will search file contents for the requested string.",
                tool_calls=[
                    ToolCall(
                        id="grep-1",
                        name="grep",
                        arguments={"pattern": "sh", "path": ".", "include": "*", "literal_text": True},
                    )
                ],
                raw_content=[
                    {"type": "text", "text": "I will search file contents for the requested string."},
                    {
                        "type": "tool_use",
                        "id": "grep-1",
                        "name": "grep",
                        "input": {"pattern": "sh", "path": ".", "include": "*", "literal_text": True},
                    },
                ],
            )
        return AssistantTurn(
            text="The file containing 'sh' is run.sh. It contains a shell shebang and an echo command."
        )

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


class FakeInlineFindResultBackend(BaseBackend):
    def __init__(self):
        self.second_turn_messages = None
        self.turn_count = 0

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
        self.second_turn_messages = list(messages)
        return AssistantTurn(text="Confirmed path: notes.txt\nUnconfirmed branches: none\nNext step: none")

    def supports_tool_calls(self):
        return True


class FakeInlineGrepResultBackend(BaseBackend):
    def __init__(self):
        self.second_turn_messages = None
        self.turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.turn_count += 1
        if self.turn_count == 1:
            return AssistantTurn(
                text="I will grep for the target string.",
                tool_calls=[ToolCall(id="grep-1", name="grep", arguments={"pattern": "needle", "path": ".", "include": "*.py"})],
                raw_content=[
                    {"type": "text", "text": "I will grep for the target string."},
                    {
                        "type": "tool_use",
                        "id": "grep-1",
                        "name": "grep",
                        "input": {"pattern": "needle", "path": ".", "include": "*.py"},
                    },
                ],
            )
        self.second_turn_messages = list(messages)
        return AssistantTurn(text="Confirmed path: src/demo.py\nUnconfirmed branches: none\nNext step: none")

    def supports_tool_calls(self):
        return True


class FakeRouterDocQaBackend(BaseBackend):
    def __init__(self):
        self.reader_messages = None
        self.planner_turn_count = 0
        self.router_call_count = 0

    def generate(self, system_prompt, messages, tools=None):
        if "Intent router:" in system_prompt:
            self.router_call_count += 1
            return json.dumps(
                {
                    "intent": "direct_file_doc_qa",
                    "confidence": "high",
                    "target_path": "README.md",
                    "needs_full_cat": True,
                }
            )
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_messages = list(messages)
            return AssistantTurn(text="According to `README.md`, crush_py is built for read-focused repository exploration.")
        self.planner_turn_count += 1
        return AssistantTurn(text="planner should not be used")

    def supports_tool_calls(self):
        return True


class FakeRouterInvalidJsonBackend(BaseBackend):
    def __init__(self):
        self.reader_messages = None
        self.router_call_count = 0

    def generate(self, system_prompt, messages, tools=None):
        if "Intent router:" in system_prompt:
            self.router_call_count += 1
            return "not-json"
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_messages = list(messages)
            return AssistantTurn(text="According to `README.md`, fallback reader answer.")
        return AssistantTurn(text="Fallback planner answer.")

    def supports_tool_calls(self):
        return True


class FakeNoToolConversationBackend(BaseBackend):
    def __init__(self):
        self.router_call_count = 0
        self.generate_turn_calls = []

    def generate(self, system_prompt, messages, tools=None):
        if "Intent router:" in system_prompt:
            self.router_call_count += 1
            return "not-json"
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.generate_turn_calls.append({"system_prompt": system_prompt, "tools": tools, "messages": list(messages)})
        if tools:
            return AssistantTurn(
                text="I should explore first.",
                tool_calls=[ToolCall(id="tool-1", name="ls", arguments={"path": "."})],
                raw_content=[
                    {"type": "text", "text": "I should explore first."},
                    {"type": "tool_use", "id": "tool-1", "name": "ls", "input": {"path": "."}},
                ],
            )
        return AssistantTurn(text="Hello! I can help read this repository and answer questions about local files.")

    def supports_tool_calls(self):
        return True


class FakeQuickFileBackend(BaseBackend):
    def __init__(self):
        self.system_prompt = None
        self.messages_seen = None
        self.tools_seen = None

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.system_prompt = system_prompt
        self.messages_seen = list(messages)
        self.tools_seen = tools
        return AssistantTurn(text="Start with `python -m crush_py --help`.")


class FakeQuickFileStreamingBackend(BaseBackend):
    def __init__(self):
        self.stream_messages = None
        self.stream_system_prompt = None
        self.generate_turn_called = False

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        self.stream_system_prompt = system_prompt
        self.stream_messages = list(messages)
        yield "1. Start with"
        yield " `python -m crush_py --help`."

    def generate_turn(self, system_prompt, messages, tools=None):
        self.generate_turn_called = True
        raise AssertionError("generate_turn should not be used for no-tool streaming")


class FakeRepoQuestionNeedsToolsBackend(BaseBackend):
    def __init__(self):
        self.router_call_count = 0
        self.turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        if "Intent router:" in system_prompt:
            self.router_call_count += 1
            return json.dumps(
                {
                    "intent": "general_qa",
                    "confidence": "high",
                    "target_path": None,
                    "needs_full_cat": False,
                    "needs_tools": True,
                }
            )
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.turn_count += 1
        if self.turn_count == 1:
            return AssistantTurn(
                text="I will inspect the repo first.",
                tool_calls=[ToolCall(id="tool-1", name="ls", arguments={"path": "."})],
                raw_content=[
                    {"type": "text", "text": "I will inspect the repo first."},
                    {"type": "tool_use", "id": "tool-1", "name": "ls", "input": {"path": "."}},
                ],
            )
        return AssistantTurn(text="This repo is a read-focused repository helper for small local models.")

    def supports_tool_calls(self):
        return True


class FakeRepoQuestionNeedsRetryBackend(BaseBackend):
    def __init__(self):
        self.router_call_count = 0
        self.turn_count = 0
        self.messages_seen = []

    def generate(self, system_prompt, messages, tools=None):
        if "Intent router:" in system_prompt:
            self.router_call_count += 1
            return json.dumps(
                {
                    "intent": "repo_search",
                    "confidence": "high",
                    "target_path": None,
                    "needs_full_cat": False,
                    "needs_tools": True,
                }
            )
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.turn_count += 1
        self.messages_seen.append(list(messages))
        if self.turn_count == 1:
            return AssistantTurn(text="This repo is a Flask web app.")
        if self.turn_count == 2:
            return AssistantTurn(
                text="I will inspect the repo first.",
                tool_calls=[ToolCall(id="tool-1", name="ls", arguments={"path": "."})],
                raw_content=[
                    {"type": "text", "text": "I will inspect the repo first."},
                    {"type": "tool_use", "id": "tool-1", "name": "ls", "input": {"path": "."}},
                ],
            )
        return AssistantTurn(text="This repo is a read-focused repository helper for small local models.")

    def supports_tool_calls(self):
        return True


class FakeRepoQuestionStillRefusesToolsBackend(BaseBackend):
    def __init__(self):
        self.router_call_count = 0
        self.turn_count = 0
        self.messages_seen = []

    def generate(self, system_prompt, messages, tools=None):
        if "Intent router:" in system_prompt:
            self.router_call_count += 1
            return json.dumps(
                {
                    "intent": "repo_search",
                    "confidence": "high",
                    "target_path": None,
                    "needs_full_cat": False,
                    "needs_tools": True,
                }
            )
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.turn_count += 1
        self.messages_seen.append(list(messages))
        return AssistantTurn(text="This repo is definitely a Flask web app.")

    def supports_tool_calls(self):
        return True


class FakeRepoQuestionReadmeAnchorBackend(BaseBackend):
    def __init__(self):
        self.router_call_count = 0
        self.planner_turn_count = 0
        self.reader_turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        if "Intent router:" in system_prompt:
            self.router_call_count += 1
            return json.dumps(
                {
                    "intent": "repo_search",
                    "confidence": "high",
                    "target_path": None,
                    "needs_full_cat": False,
                    "needs_tools": True,
                }
            )
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_turn_count += 1
            if self.reader_turn_count == 1:
                return AssistantTurn(
                    text="I will inspect README first.",
                    tool_calls=[ToolCall(id="cat-1", name="cat", arguments={"path": "README.md"})],
                    raw_content=[
                        {"type": "text", "text": "I will inspect README first."},
                        {"type": "tool_use", "id": "cat-1", "name": "cat", "input": {"path": "README.md"}},
                    ],
                )
            return AssistantTurn(
                text=(
                    "Confirmed path: README.md\n"
                    "Summary: crush_py is a read-focused repository helper for small local models.\n"
                    "Evidence: 1|# crush_py\n"
                    "Unresolved uncertainty: none"
                )
            )

        self.planner_turn_count += 1
        if self.planner_turn_count == 1:
            return AssistantTurn(
                text="I will inspect the repo root first.",
                tool_calls=[ToolCall(id="tool-1", name="ls", arguments={"path": "."})],
                raw_content=[
                    {"type": "text", "text": "I will inspect the repo root first."},
                    {"type": "tool_use", "id": "tool-1", "name": "ls", "input": {"path": "."}},
                ],
            )
        return AssistantTurn(text="According to README.md, this repo is a read-focused repository helper for small local models.")

    def supports_tool_calls(self):
        return True


class FakeImplicitSingleDocBackend(BaseBackend):
    def __init__(self):
        self.router_call_count = 0
        self.planner_turn_count = 0
        self.reader_messages = None

    def generate(self, system_prompt, messages, tools=None):
        if "Intent router:" in system_prompt:
            self.router_call_count += 1
            return "not-json"
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_messages = list(messages)
            return AssistantTurn(
                text=(
                    "Confirmed path: INSTRUCTIONS.md\n"
                    "Summary: this document explains how to run small, repeatable TensorFlow experiments.\n"
                    "Evidence: define one clear question ; change one variable at a time ; record metrics and conclusions.\n"
                    "Unresolved uncertainty: none"
                )
            )
        self.planner_turn_count += 1
        return AssistantTurn(text="planner should not be used")

    def supports_tool_calls(self):
        return True


class CaptureModelBackend(BaseBackend):
    last_model = None

    def __init__(self, model, api_key, base_url, timeout=60, max_tokens=4096):
        CaptureModelBackend.last_model = model

    def generate(self, system_prompt, messages, tools=None):
        return "ok"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        return AssistantTurn(text="ok")


class FakeVariableTraceDirectBackend(BaseBackend):
    def __init__(self):
        self.reader_messages = None
        self.reader_system_prompt = None
        self.planner_turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_system_prompt = system_prompt
            self.reader_messages = list(messages)
            return AssistantTurn(
                text=(
                    "Variable trace for human review:\n\n"
                    "Variable: session_id\n"
                    "Confirmed file: crush_py/store/session_store.py\n\n"
                    "1. Defined or first assigned at line 10 inside `create_session`\n"
                    "   Evidence: `session_id = payload.get(\"session_id\") or str(uuid4())`\n\n"
                    "2. Reassigned at `No confirmed reassignment in reviewed windows`\n"
                    "   Evidence: `title = title.strip()`\n\n"
                    "3. Passed as an argument at line 18 inside `create_session`\n"
                    "   Evidence: `SessionMeta(session_id=session_id, created_at=created_at)`\n\n"
                    "4. Used in condition, return, or storage at line 31 inside `create_session`\n"
                    "   Evidence: `return self._session_dir(session_id)`\n\n"
                    "Unresolved uncertainty:\n"
                    "- None\n\n"
                    "Unresolved uncertainty:\n"
                    "- The trace is limited to the reviewed grep windows in this file."
                )
            )
        self.planner_turn_count += 1
        return AssistantTurn(text="planner should not be used")

    def supports_tool_calls(self):
        return True


class FakeFlowTraceDirectBackend(BaseBackend):
    def __init__(self):
        self.reader_messages = None
        self.reader_system_prompt = None
        self.planner_turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_system_prompt = system_prompt
            self.reader_messages = list(messages)
            return AssistantTurn(
                text=(
                    "<|tool_response|>Flow trace for human review:\n\n"
                    "Target: prompt\n"
                    "Confirmed file: crush_py/agent/runtime.py\n"
                    "Coverage: local\n\n"
                    "1. Entry point\n"
                    "   Evidence: `def ask(self, prompt: str, stream: bool = False) -> str:`\n\n"
                    "2. Confirmed local transformation\n"
                    "   Evidence: `prompt.strip()` inside `state.entry_point = prompt.strip()`\n\n"
                    "3. Confirmed storage or persistence\n"
                    "   Evidence: `state.entry_point = prompt.strip()`\n\n"
                    "4. Confirmed downstream handoff\n"
                    "   Evidence: `No confirmed downstream handoff in reviewed blocks`\n\n"
                    "5. Confirmed local flow\n"
                    "   Evidence: `ask(prompt)` -> `prompt.strip()` -> `state.entry_point`\n\n"
                    "Unresolved uncertainty:\n"
                    "- The trace is limited to the reviewed local blocks."
                )
            )
        self.planner_turn_count += 1
        return AssistantTurn(text="planner should not be used")

    def supports_tool_calls(self):
        return True


class FakeFileFlowTraceDirectBackend(BaseBackend):
    def __init__(self):
        self.reader_messages = None
        self.reader_system_prompt = None
        self.planner_turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_system_prompt = system_prompt
            self.reader_messages = list(messages)
            return AssistantTurn(
                text=(
                    "The user wants to complete the flow trace for `crush_py/repl_display.py`. "
                    "The summary ends abruptly and I should inspect more context."
                )
            )
        self.planner_turn_count += 1
        return AssistantTurn(text="planner should not be used")

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


class FakeRetryBackend(BaseBackend):
    def __init__(self):
        self.turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        self.turn_count += 1
        if self.turn_count == 1:
            raise BackendError("temporary timeout")
        return AssistantTurn(text='<|tool_response|>Confirmed path: README.md\nSummary: ok')

    def supports_tool_calls(self):
        return False


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
                    "Confirmed path: notes.py\n"
                    "Summary: the file spans four lines gathered across two cat pages.\n"
                    "Evidence: 1|one ; 2|two ; 3|three ; 4|four\n"
                    "Unresolved uncertainty: none"
                )
            )
        return AssistantTurn(text="Confirmed path: notes.txt\nUnconfirmed branches: none\nNext step: none")

    def supports_tool_calls(self):
        return True


class FakeReaderToolSelectionBackend(BaseBackend):
    def __init__(self):
        self.reader_tools_seen = []

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_tools_seen.append([tool["name"] for tool in (tools or [])])
            return AssistantTurn(text="Confirmed path: README.md\nSummary: doc file\nEvidence: README\nUnresolved uncertainty: none")
        return AssistantTurn(text="Confirmed path: README.md\nUnconfirmed branches: none\nNext step: none")

    def supports_tool_calls(self):
        return True


class FakeReaderSufficientDirectAnswerBackend(BaseBackend):
    def __init__(self):
        self.planner_turn_count = 0
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
                    "According to `README.md`, `crush_py` is a read-focused repository helper for small local models.\n\n"
                    "Confirmed path: `README.md`\n"
                    "Summary:\n"
                    "- It is built for read-only repo exploration.\n"
                    "- It helps trace code flow and summarize docs."
                )
            )
        self.planner_turn_count += 1
        return AssistantTurn(text="planner should not be used")

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


class FakeGuideDirectBackend(BaseBackend):
    def __init__(self):
        self.reader_messages = None
        self.reader_system_prompt = None
        self.planner_turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_system_prompt = system_prompt
            self.reader_messages = list(messages)
            return AssistantTurn(
                text=(
                    "Checklist:\n"
                    "1. Read the project overview so you know what this tool is for.\n"
                    "2. Run the documented CLI command in the order shown.\n"
                    "3. Compare the output with the listed success cues.\n"
                    "Success check: the command finishes and the described result appears.\n"
                )
            )
        self.planner_turn_count += 1
        return AssistantTurn(text="planner should not be used")

    def supports_tool_calls(self):
        return True


class FakeGuideFollowUpBackend(BaseBackend):
    def __init__(self):
        self.reader_messages = []
        self.reader_turn_count = 0

    def generate(self, system_prompt, messages, tools=None):
        return "unused"

    def stream_generate(self, system_prompt, messages, tools=None):
        return iter(())

    def generate_turn(self, system_prompt, messages, tools=None):
        if "Reader mode:" in system_prompt:
            self.reader_turn_count += 1
            self.reader_messages.append(list(messages))
            if self.reader_turn_count == 1:
                return AssistantTurn(
                    text=(
                        "Beginner summary:\n"
                        "- Goal: explain what the project does.\n"
                        "- You will accomplish: understand the basic purpose.\n"
                        "- Prepare first: have Python ready.\n"
                        "- Main steps: read the overview, setup, and command examples.\n"
                        "- Common beginner confusion: this project is read-only.\n"
                        "Sources: README.md:1-6"
                    )
                )
            return AssistantTurn(
                text=(
                    "Troubleshooting:\n"
                    "- Likely current step: the first run or setup stage.\n"
                    "- Relevant source section: setup and command examples.\n"
                    "- Possible causes: the user skipped an environment prerequisite.\n"
                    "- What to check first: compare the setup steps with the previous beginner summary.\n"
                    "- What to do next: verify the documented setup before retrying.\n"
                    "Sources: README.md:1-12"
                )
            )
        return AssistantTurn(text="planner should not be used")

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

