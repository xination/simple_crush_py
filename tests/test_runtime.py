import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from crush_py.agent.runtime import AgentRuntime
from crush_py.agent.intent_router import IntentDecision
from crush_py.backends.base import AssistantTurn, BackendError, BaseBackend, ToolCall
from crush_py.config import AppConfig, BackendConfig
from crush_py.output_sanitize import sanitize_text
from crush_py.repl import _format_history, _format_trace
from crush_py.repl_commands import try_handle_command
from crush_py.store.session_store import SessionStore
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

    def test_streaming_tool_turn_keeps_tool_loop_instead_of_returning_early(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            runtime._create_backend = lambda backend_cfg: FakeStreamingToolCallBackend()

            result = runtime.ask("Trace notes.txt", stream=True)
            messages = store.load_messages(runtime.active_session.id)

            self.assertIn("Confirmed path: notes.txt", result)
            self.assertEqual([message.kind for message in messages], ["message", "tool_use", "tool_use", "tool_result", "tool_result", "message"])

    def test_streaming_only_prints_final_answer_not_tool_loop_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            runtime._create_backend = lambda backend_cfg: FakeStreamingToolCallBackend()

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = runtime.ask("Trace notes.txt", stream=True)

            rendered = stdout.getvalue()
            self.assertIn("Confirmed path: notes.txt", result)
            self.assertIn("Confirmed path: notes.txt", rendered)
            self.assertNotIn("I will inspect the file first.", rendered)
            self.assertNotIn("[thinking", rendered)

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

    def test_direct_file_summary_prompt_defaults_to_brief_instructions(self):
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
            self.assertIn("give a brief summary", prompt_text.lower())
            self.assertIn("Return exactly 3 numbered points.", prompt_text)
            self.assertIn("No Evidence, Tag, Review note, Suggested keep, or Suggested review/remove sections.", prompt_text)

    def test_assistant_reuses_reader_brief_summary_without_rewriting(self):
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
            self.assertIn("1. Manage each session's persisted metadata and lifecycle state.", result)
            self.assertNotIn("Evidence:", result)
            self.assertNotIn("Tag:", result)

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
            runtime._has_partial_reader_summary_for_path = lambda session_id, rel_path: True

            text = runtime._postprocess_direct_file_summary_output(
                "session-1",
                "請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。",
                "1. Main responsibility\n   Evidence: SessionStore",
            )

            self.assertIn("Preliminary summary (partial file coverage).", text)

    def test_postprocess_direct_file_summary_output_ignores_partial_from_other_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            session = runtime.new_session()

            store.append_message(
                session.id,
                "assistant",
                "Preliminary summary (partial file coverage).",
                kind="tool_result",
                metadata={
                    "agent": "reader",
                    "tool_name": "reader",
                    "tool_arguments": {"path": "README.md", "coverage": "partial", "mode": "summary"},
                    "tool_use_id": "reader:README.md",
                    "summary": "Preliminary summary (partial file coverage).",
                },
            )

            text = runtime._postprocess_direct_file_summary_output(
                session.id,
                "Give a summary for crush_py/store/session_store.py.",
                "1. Main responsibility\n   Evidence: SessionStore",
            )

            self.assertFalse(text.startswith("Preliminary summary"))

    def test_direct_file_summary_defaults_to_brief_mode(self):
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

            self.assertIn("1. Manage each session's persisted metadata and lifecycle state.", result)
            self.assertNotIn("Evidence:", result)
            self.assertTrue(runtime._is_brief_summary_prompt("Give a summary for crush_py/store/session_store.py."))

    def test_detects_direct_file_guide_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("# crush_py\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))

            prompt = (
                "Guide mode:\n"
                "User request: turn README.md into a checklist\n"
                "Guide expectations:\n"
                "- answer from workspace docs when possible"
            )
            self.assertTrue(runtime._is_guide_prompt(prompt))
            self.assertTrue(runtime._is_direct_file_guide_prompt(prompt))
            self.assertEqual(runtime._guide_output_mode(prompt), "checklist")

    def test_direct_file_guide_uses_reader_fast_path_and_appends_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("Intro\nStep one\nStep two\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeGuideDirectBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask(
                "Guide mode:\n"
                "User request: turn README.md into a checklist\n"
                "Guide expectations:\n"
                "- answer from workspace docs when possible\n"
                "- explain for a beginner in plain language"
            )

            self.assertEqual(backend.planner_turn_count, 0)
            self.assertIn("Checklist:", result)
            self.assertIn("Success check:", result)
            self.assertIn("Sources: README.md:1-3", result)
            self.assertIsNotNone(backend.reader_messages)
            self.assertIn("Always end with `Sources:`", backend.reader_messages[0]["content"])

    def test_guide_prompt_uses_guide_system_prompt_appendix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))

            prompt = (
                "Guide mode:\n"
                "User request: which docs should I read first to learn Program A?\n"
                "Guide expectations:\n"
                "- answer from workspace docs when possible"
            )
            system_prompt = runtime._system_prompt_for_prompt(prompt)

            self.assertIn("Guide mode:", system_prompt)
            self.assertIn("answer from workspace docs when possible", system_prompt)

    def test_guide_follow_up_on_same_file_reuses_previous_reader_summary_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text(
                "Overview\nSetup\nRun command\nTroubleshooting\n",
                encoding="utf-8",
            )
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeGuideFollowUpBackend()
            runtime._create_backend = lambda backend_cfg: backend

            first_result = runtime.ask(
                "Guide mode:\n"
                "User request: summarize README.md for a beginner\n"
                "Guide expectations:\n"
                "- answer from workspace docs when possible\n"
                "- explain for a beginner in plain language"
            )
            second_result = runtime.ask(
                "Guide mode:\n"
                "User request: I am stuck during setup in README.md\n"
                "Guide expectations:\n"
                "- answer from workspace docs when possible\n"
                "- explain for a beginner in plain language"
            )
            messages = store.load_messages(runtime.active_session.id)

            self.assertIn("Beginner summary:", first_result)
            self.assertIn("Troubleshooting:", second_result)
            self.assertEqual(len(backend.reader_messages), 2)
            self.assertNotIn("Previous guide summary", backend.reader_messages[0][0]["content"])
            self.assertIn("Previous guide summary for README.md", backend.reader_messages[1][0]["content"])
            self.assertIn("Reuse the previous guide summary first instead of rereading the full doc.", backend.reader_messages[1][0]["content"])
            self.assertIn("Beginner summary:", backend.reader_messages[1][0]["content"])
            cat_tool_uses = [
                message
                for message in messages
                if message.kind == "tool_use" and message.metadata.get("tool") == "cat"
            ]
            self.assertEqual(len(cat_tool_uses), 1)
            second_reader_result = next(
                message
                for message in reversed(messages)
                if message.kind == "tool_result" and message.metadata.get("tool") == "reader"
            )
            self.assertEqual(second_reader_result.metadata["args"]["coverage"], "reused")
            self.assertEqual(messages[-1].content, second_result)

    def test_guide_exact_line_follow_up_rereads_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text(
                "Overview\nSetup\nRun command\nTroubleshooting\n",
                encoding="utf-8",
            )
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeGuideFollowUpBackend()
            runtime._create_backend = lambda backend_cfg: backend

            runtime.ask(
                "Guide mode:\n"
                "User request: summarize README.md for a beginner\n"
                "Guide expectations:\n"
                "- answer from workspace docs when possible\n"
                "- explain for a beginner in plain language"
            )
            runtime.ask(
                "Guide mode:\n"
                "User request: which exact lines in README.md talk about setup?\n"
                "Guide expectations:\n"
                "- answer from workspace docs when possible\n"
                "- explain for a beginner in plain language"
            )
            messages = store.load_messages(runtime.active_session.id)

            self.assertEqual(len(backend.reader_messages), 2)
            self.assertNotIn("Reuse the previous guide summary first", backend.reader_messages[1][0]["content"])
            cat_tool_uses = [
                message
                for message in messages
                if message.kind == "tool_use" and message.metadata.get("tool") == "cat"
            ]
            self.assertEqual(len(cat_tool_uses), 2)

    def test_guide_partial_previous_summary_forces_reread(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text(
                "Overview\nSetup\nRun command\nTroubleshooting\n",
                encoding="utf-8",
            )
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeGuideFollowUpBackend()
            runtime._create_backend = lambda backend_cfg: backend
            session = runtime.new_session()

            store.append_message(
                session.id,
                "assistant",
                "Preliminary guide (partial file coverage).\nBeginner summary:\n- Goal: partial",
                kind="tool_result",
                metadata={
                    "agent": "reader",
                    "tool_name": "reader",
                    "tool_arguments": {"path": "README.md", "coverage": "partial", "mode": "guide"},
                    "tool_use_id": "reader:README.md",
                    "summary": "Preliminary guide (partial file coverage).\nBeginner summary:\n- Goal: partial",
                },
            )

            runtime.ask(
                "Guide mode:\n"
                "User request: I am stuck during setup in README.md\n"
                "Guide expectations:\n"
                "- answer from workspace docs when possible\n"
                "- explain for a beginner in plain language"
            )

            self.assertEqual(len(backend.reader_messages), 1)
            self.assertIn("Previous guide summary for README.md", backend.reader_messages[0][0]["content"])
            self.assertNotIn("Reuse the previous guide summary first", backend.reader_messages[0][0]["content"])

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

    def test_direct_file_summary_also_omits_review_draft_scaffolding_for_non_brief_wording(self):
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

            self.assertIn("1.", result)
            self.assertNotIn("Candidate responsibilities for human review:", result)
            self.assertNotIn("Evidence:", result)
            self.assertNotIn("Tag:", result)
            self.assertNotIn("Review note:", result)

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

    def test_key_ideas_prompt_uses_brief_summary_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("# crush_py\none\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakePromptDirectCatBackend()
            runtime._create_backend = lambda backend_cfg: backend
            runtime._intent_decision = lambda prompt, backend=None: IntentDecision(
                intent="direct_file_summary",
                confidence="high",
                target_path="README.md",
                needs_full_cat=True,
                needs_tools=True,
                source="test",
            )

            result = runtime.ask("read README.md and then show me the key ideas")

            self.assertIn("1.", result)
            self.assertNotIn("Candidate responsibilities for human review:", result)
            self.assertNotIn("Evidence:", result)
            self.assertNotIn("Tag:", result)

    def test_detects_direct_file_variable_trace_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("session_id = None\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))

            self.assertTrue(
                runtime._is_direct_file_variable_trace_prompt(
                    "Trace the variable session_id in crush_py/store/session_store.py"
                )
            )
            self.assertEqual(
                runtime._prompt_direct_trace_variable(
                    "Trace how session_id flows inside crush_py/store/session_store.py"
                ),
                "session_id",
            )
            self.assertTrue(
                runtime._is_direct_file_flow_trace_prompt(
                    "Trace how session_id flows inside crush_py/store/session_store.py"
                )
            )
            self.assertFalse(
                runtime._is_direct_file_variable_trace_prompt(
                    "Trace how session_id flows inside crush_py/store/session_store.py"
                )
            )

    def test_detects_direct_file_file_flow_trace_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py"
            target.mkdir(parents=True)
            (target / "repl_display.py").write_text("def format_trace(runtime):\n    return 'ok'\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))

            self.assertTrue(runtime._is_direct_file_file_flow_trace_prompt("Trace the flow for crush_py/repl_display.py"))
            self.assertFalse(runtime._is_direct_file_summary_prompt("Trace the flow for crush_py/repl_display.py"))

    def test_direct_file_variable_trace_uses_outline_grep_and_local_cat_reads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text(
                "\n".join(
                    [
                        "from dataclasses import dataclass",
                        "",
                        "@dataclass",
                        "class SessionMeta:",
                        "    session_id: str",
                        "",
                        "def create_session(payload):",
                        "    created_at = payload.get(\"created_at\")",
                        "    session_id = payload.get(\"session_id\") or \"generated\"",
                        "    meta = SessionMeta(session_id=session_id)",
                        "    if session_id:",
                        "        return _session_dir(session_id)",
                        "    return meta",
                        "",
                        "def _session_dir(session_id):",
                        "    return f\"sessions/{session_id}\"",
                    ]
                ),
                encoding="utf-8",
            )
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeVariableTraceDirectBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Trace the variable session_id in crush_py/store/session_store.py")

            self.assertEqual(backend.planner_turn_count, 0)
            self.assertIn("Variable trace for human review:", result)
            self.assertIn("Trace status: partial", result)
            self.assertIn("Confidence: local-only", result)
            self.assertIn("Coverage:", result)
            self.assertIn("- scope: local (reviewed `create_session` block only)", result)
            self.assertIn("- selection: grep-confirmed local windows", result)
            self.assertIsNotNone(backend.reader_messages)
            prompt_text = backend.reader_messages[0]["content"]
            self.assertIn("Variable: session_id", prompt_text)
            self.assertIn("outline first, grep the variable inside this file", prompt_text)
            payloads = backend.reader_messages[1]["content"]
            tool_names = [payload["tool_name"] for payload in payloads]
            self.assertIn("get_outline", tool_names)
            self.assertIn("grep", tool_names)
            self.assertIn("cat", tool_names)
            grep_payload = next(payload for payload in payloads if payload["tool_name"] == "grep")
            self.assertIn("crush_py/store/session_store.py:", grep_payload["content"])
            cat_payloads = [payload for payload in payloads if payload["tool_name"] == "cat"]
            self.assertTrue(cat_payloads)
            self.assertTrue(all(not payload["tool_use_id"].startswith("reader-cat-full:") for payload in cat_payloads))
            self.assertIn("3. Passed to line 12 inside `create_session`", result)
            self.assertIn("Evidence: `return _session_dir(session_id)`", result)
            self.assertIn("4. Used in", result)
            self.assertIn("Role: Stored into field at line 10 inside `create_session`", result)
            self.assertIn("No confirmed reassignment in the reviewed block.", result)
            self.assertIn("5. Unknown / not proven", result)
            self.assertNotIn("<|tool_response|>", result)
            self.assertEqual(result.count("Unknown / not proven"), 1)
            self.assertNotIn("No direct role site retained from reviewed windows", result)
            self.assertNotIn("- None", result)

    def test_direct_file_flow_trace_reads_containing_function_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "agent"
            target.mkdir(parents=True)
            (target / "runtime.py").write_text(
                "\n".join(
                    [
                        "class AgentRuntime:",
                        "    def ask(self, prompt: str, stream: bool = False) -> str:",
                        "        state = self._state()",
                        "        if not state.entry_point:",
                        "            state.entry_point = prompt.strip()",
                        "        return state.entry_point",
                        "",
                        "    def other(self):",
                        "        return 'x'",
                    ]
                ),
                encoding="utf-8",
            )
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeFlowTraceDirectBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Trace how prompt flows inside crush_py/agent/runtime.py")
            messages = store.load_messages(runtime.active_session.id)

            self.assertEqual(backend.planner_turn_count, 0)
            self.assertIn("Flow trace for human review:", result)
            prompt_text = backend.reader_messages[0]["content"]
            self.assertIn("Tracked name: prompt", prompt_text)
            self.assertIn("containing function or method blocks", prompt_text)
            payloads = backend.reader_messages[1]["content"]
            cat_payloads = [payload for payload in payloads if payload["tool_name"] == "cat"]
            self.assertTrue(cat_payloads)
            self.assertTrue(any("offset=\"1\"" in payload["content"] or "offset=\"0\"" in payload["content"] for payload in cat_payloads))
            reader_result = next(message for message in messages if message.kind == "tool_result" and message.metadata.get("tool") == "reader")
            self.assertEqual(reader_result.metadata["args"]["mode"], "flow_trace")
            self.assertEqual(reader_result.metadata["args"]["coverage"], "local")
            self.assertEqual(reader_result.metadata["args"]["trace_status"], "partial")
            self.assertEqual(reader_result.metadata["args"]["confidence"], "local-only")

    def test_flow_trace_uses_qualname_and_separates_persistence_from_handoff(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "agent"
            target.mkdir(parents=True)
            (target / "runtime.py").write_text(
                "\n".join(
                    [
                        "class AgentRuntime:",
                        "    def ask(self, prompt: str, stream: bool = False) -> str:",
                        "        state = self._state()",
                        "        normalized = prompt.strip()",
                        "        state.entry_point = normalized",
                        "        self.session_store.append_message(prompt)",
                        "        return self.backend.send(prompt)",
                    ]
                ),
                encoding="utf-8",
            )
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))

            payloads = [
                {
                    "tool_name": "get_outline",
                    "tool_use_id": "reader-outline:crush_py/agent/runtime.py",
                    "content": "unused",
                },
                {
                    "tool_name": "grep",
                    "tool_use_id": "reader-grep:crush_py/agent/runtime.py:prompt",
                    "content": (
                        "crush_py/agent/runtime.py:\n"
                        "  Line 2, Char 19: def ask(self, prompt: str, stream: bool = False) -> str:\n"
                        "  Line 4, Char 22: normalized = prompt.strip()\n"
                        "  Line 6, Char 43: self.session_store.append_message(prompt)\n"
                        "  Line 7, Char 34: return self.backend.send(prompt)\n"
                    ),
                },
                {
                    "tool_name": "cat",
                    "tool_use_id": "reader-cat:crush_py/agent/runtime.py:2:7",
                    "content": (
                        '<file path="crush_py/agent/runtime.py" offset="1" limit="6">\n'
                        "     2|    def ask(self, prompt: str, stream: bool = False) -> str:\n"
                        "     3|        state = self._state()\n"
                        "     4|        normalized = prompt.strip()\n"
                        "     5|        state.entry_point = normalized\n"
                        "     6|        self.session_store.append_message(prompt)\n"
                        "     7|        return self.backend.send(prompt)\n"
                        "</file>"
                    ),
                },
            ]

            result = runtime._append_flow_trace_postprocessing(
                "Flow trace for human review:\n\nTarget: prompt\nConfirmed file: crush_py/agent/runtime.py",
                "local",
                "prompt",
                payloads,
            )

            self.assertIn("Trace status: partial", result)
            self.assertIn("Confidence: local-only", result)
            self.assertIn("Coverage:", result)
            self.assertIn("- scope: local (reviewed `AgentRuntime.ask` block only)", result)
            self.assertIn("- selection: containing symbol blocks", result)
            self.assertIn("Reviewed symbol: AgentRuntime.ask", result)
            self.assertIn("2. Immediate transform at line 4 inside `AgentRuntime.ask`", result)
            self.assertIn("3. Stored or logged at line 6 inside `AgentRuntime.ask`", result)
            self.assertIn("4. Hand-off at line 7 inside `AgentRuntime.ask`", result)
            self.assertIn("5. Unknown / not proven", result)
            self.assertNotIn("Storage or state updates", result)

    def test_direct_file_file_flow_trace_uses_reader_and_fallback_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py"
            target.mkdir(parents=True)
            (target / "repl_display.py").write_text(
                "\n".join(
                    [
                        "def format_trace(runtime, limit=20):",
                        "    messages = []",
                        "    return '\\n'.join(format_trace_message(message) for message in messages)",
                        "",
                        "def format_trace_message(message):",
                        "    return message.kind",
                        "",
                        "def format_history(runtime, limit=20):",
                        "    return '\\n'.join(format_history_message(message) for message in [])",
                        "",
                        "def format_history_message(message):",
                        "    return message.role",
                        "",
                        "def _single_line(text):",
                        "    return text.strip()",
                    ]
                ),
                encoding="utf-8",
            )
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeFileFlowTraceDirectBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Trace the flow for crush_py/repl_display.py")

            self.assertEqual(backend.planner_turn_count, 0)
            self.assertIn("File flow for human review:", result)
            self.assertIn("`format_trace`", result)
            self.assertIn("`format_trace_message`", result)
            self.assertIn("`return '\\n'.join(format_trace_message(message) for message in messages)`", result)
            self.assertNotIn("The user wants to complete the flow trace", result)

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

    def test_small_find_result_is_forwarded_to_backend_in_full(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeInlineFindResultBackend()
            runtime._create_backend = lambda backend_cfg: backend

            runtime.ask("Locate the notes file")

            self.assertIsNotNone(backend.second_turn_messages)
            tool_results = next(
                message["content"]
                for message in backend.second_turn_messages
                if isinstance(message.get("content"), list)
                and message["content"]
                and message["content"][0].get("type") == "tool_result"
                and message["content"][0].get("tool_name") == "find"
            )
            self.assertIn("notes.txt", tool_results[0]["content"])
            self.assertNotIn("Find produced", tool_results[0]["content"])

    def test_small_grep_result_is_forwarded_to_backend_in_full(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "src").mkdir()
            (workspace / "src" / "demo.py").write_text("needle = 1\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeInlineGrepResultBackend()
            runtime._create_backend = lambda backend_cfg: backend

            runtime.ask("Find where needle appears")

            self.assertIsNotNone(backend.second_turn_messages)
            tool_results = next(
                message["content"]
                for message in backend.second_turn_messages
                if isinstance(message.get("content"), list)
                and message["content"]
                and message["content"][0].get("type") == "tool_result"
                and message["content"][0].get("tool_name") == "grep"
            )
            self.assertIn("src/demo.py:", tool_results[0]["content"])
            self.assertNotIn("matched 1 file", tool_results[0]["content"])

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

    def test_repl_ls_command_history_is_available_to_next_natural_language_turn(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
            (workspace / "run.tcsh").write_text("echo hi\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeHistoryAwareBackend()
            runtime._create_backend = lambda backend_cfg: backend
            runtime.new_session()

            tree_stdout = io.StringIO()
            with redirect_stdout(tree_stdout):
                handled, exit_code = try_handle_command(runtime, "/ls . 2")

            self.assertTrue(handled)
            self.assertIsNone(exit_code)
            self.assertIn("run.sh", tree_stdout.getvalue())

            result = runtime.ask("find the file with string 'sh' ")
            rendered = json.dumps(backend.messages_seen, ensure_ascii=False)

            self.assertIn("run.sh", result)
            self.assertIn("/ls . 2", rendered)
            self.assertIn('"tool_name": "ls"', rendered)
            self.assertIn("Directory overview gathered for `.`.", rendered)
            self.assertIn("find the file with string 'sh'", rendered)

    def test_repl_ls_then_prompt_runs_planner_search_and_reader_cat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "run.sh").write_text("#!/bin/sh\necho hello\n", encoding="utf-8")
            (workspace / "run.tcsh").write_text("echo hello\n", encoding="utf-8")
            (workspace / "notes.txt").write_text("plain text only\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakePlannerUsesRealToolsBackend()
            runtime._create_backend = lambda backend_cfg: backend
            runtime.new_session()

            with redirect_stdout(io.StringIO()):
                handled, exit_code = try_handle_command(runtime, "/ls . 2")

            self.assertTrue(handled)
            self.assertIsNone(exit_code)

            result = runtime.ask("find the file with string 'sh'")
            messages = store.load_messages(runtime.active_session.id)
            tool_use_names = [message.metadata.get("tool") for message in messages if message.kind == "tool_use"]
            tool_result_names = [message.metadata.get("tool") for message in messages if message.kind == "tool_result"]
            planner_rendered = json.dumps(backend.planner_messages[0], ensure_ascii=False)

            self.assertIn("run.sh", result)
            self.assertIn("grep", tool_use_names)
            self.assertIn("reader", tool_use_names)
            self.assertIn("cat", tool_use_names)
            self.assertIn("ls", tool_result_names)
            self.assertIn("grep", tool_result_names)
            self.assertIn("cat", tool_result_names)
            self.assertIn("reader", tool_result_names)
            self.assertIn("/ls . 2", planner_rendered)
            self.assertIn("Directory overview gathered for `.`.", planner_rendered)

    def test_duplicate_tool_result_is_deduped_in_session_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            session = store.create_session(backend="test", model="fake")

            store.append_message(
                session.id,
                "user",
                "first result",
                kind="tool_result",
                metadata={"tool": "cat", "args": {"path": "README.md"}, "summary": "Read README", "agent": "reader"},
            )
            store.append_message(
                session.id,
                "user",
                "first result",
                kind="tool_result",
                metadata={"tool": "cat", "args": {"path": "README.md"}, "summary": "Read README", "agent": "reader"},
            )

            messages = store.load_messages(session.id)

            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].metadata["tool"], "cat")

    def test_direct_file_question_reuses_reader_answer_without_planner_rewrite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("# crush_py\n說明\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeReaderSufficientDirectAnswerBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("according to README.md, what is crush_py built for?")

            self.assertEqual(backend.planner_turn_count, 0)
            self.assertIn("According to `README.md`", result)
            self.assertIn("read-only repo exploration", result)
            self.assertIsNotNone(backend.reader_messages)
            self.assertTrue(any(isinstance(message.get("content"), list) for message in backend.reader_messages))

    def test_cat_and_summary_preserve_utf8_chinese_readme_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text(
                "# crush_py\n\n一個專門給小型本地模型使用的 read-focused repository helper。\n",
                encoding="utf-8",
            )
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            session = runtime.new_session()

            result = CatTool(workspace).run({"path": "README.md"})
            summary = runtime._summarize_tool_result(session.id, "cat", {"path": "README.md"}, result)

            self.assertIn("一個專門給小型本地模型使用", result)
            self.assertIn("一個專門給小型本地模型使用", summary)
            self.assertNotIn("銝", summary)

    def test_reader_can_use_up_to_three_tool_calls_before_forced_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.py").write_text("def one():\n    return 1\n\ndef two():\n    return 2\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeReaderThreeCallBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Trace notes.py")

            self.assertIn("Confirmed path: notes.txt", result)
            self.assertEqual(
                backend.reader_tools_seen[:3],
                [["get_outline", "cat"], ["get_outline", "cat"], ["get_outline", "cat"]],
            )
            self.assertIsNone(backend.final_reader_tools)

    def test_reader_uses_cat_only_for_non_code_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeReaderToolSelectionBackend()
            runtime._create_backend = lambda backend_cfg: backend

            runtime.ask("What is in README.md?")

            self.assertEqual(backend.reader_tools_seen, [["cat"]])

    def test_base_system_prompt_distinguishes_discovery_from_evidence_tools(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))

            system_prompt = runtime._system_prompt_for_prompt("Find where session_id is used")

            self.assertIn("Discovery tools narrow the search", system_prompt)
            self.assertIn("Evidence tools confirm claims: `cat`.", system_prompt)
            self.assertIn("For docs, config, text, and other non-code files, use `cat` instead.", system_prompt)

    def test_trace_system_prompt_strengthens_uncertainty_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))

            system_prompt = runtime._system_prompt_for_prompt("Trace how prompt flows inside crush_py/agent/runtime.py")

            self.assertIn("Trace mode:", system_prompt)
            self.assertIn("treat grep hits as leads, not proof", system_prompt)
            self.assertIn("label claims as confirmed, likely, or unknown", system_prompt)

    def test_traditional_chinese_prompt_intent_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            target = workspace / "crush_py" / "store"
            target.mkdir(parents=True)
            (target / "session_store.py").write_text("class SessionStore:\n    pass\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))

            self.assertTrue(runtime._is_direct_file_summary_prompt("請摘要 crush_py/store/session_store.py"))
            self.assertTrue(runtime._is_brief_summary_prompt("請簡述 crush_py/store/session_store.py"))
            self.assertTrue(runtime._is_direct_file_flow_trace_prompt("追蹤 session_id 的流向，檔案在 crush_py/store/session_store.py"))

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

    def test_plain_backend_retries_and_sanitizes_final_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeRetryBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("Summarize README.md")
            messages = store.load_messages(runtime.active_session.id)

            self.assertEqual(backend.turn_count, 2)
            self.assertEqual(result, "Confirmed path: README.md\nSummary: ok")
            self.assertEqual(messages[-1].content, "Confirmed path: README.md\nSummary: ok")

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

    def test_tiny_intent_router_routes_direct_file_doc_qa(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("# crush_py\nread-focused repository helper\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeRouterDocQaBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("from README.md, can you show me what crush_py is?")

            self.assertEqual(backend.planner_turn_count, 0)
            self.assertEqual(backend.router_call_count, 1)
            self.assertIn("According to `README.md`", result)
            self.assertIsNotNone(backend.reader_messages)
            self.assertTrue(any(isinstance(message.get("content"), list) for message in backend.reader_messages))

    def test_tiny_intent_router_falls_back_when_json_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("# crush_py\nread-focused repository helper\n", encoding="utf-8")
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            backend = FakeRouterInvalidJsonBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("according to README.md, what is crush_py built for?")

            self.assertEqual(backend.router_call_count, 1)
            self.assertIn("fallback reader answer.", result)

    def test_no_tool_conversation_prompt_skips_planner_tool_loop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeNoToolConversationBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("hi")
            messages = store.load_messages(runtime.active_session.id)

            self.assertEqual(backend.router_call_count, 1)
            self.assertEqual(result, "Hello! I can help read this repository and answer questions about local files.")
            self.assertEqual([message.kind for message in messages], ["message", "message"])
            self.assertEqual(len(backend.generate_turn_calls), 1)
            self.assertIsNone(backend.generate_turn_calls[0]["tools"])
            self.assertIn("Direct-answer mode:", backend.generate_turn_calls[0]["system_prompt"])

    def test_repo_question_still_enters_tool_loop_when_router_requires_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("# crush_py\nread-focused repository helper\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeRepoQuestionNeedsToolsBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("what is this repo for?")
            messages = store.load_messages(runtime.active_session.id)
            tool_use_names = [message.metadata.get("tool") for message in messages if message.kind == "tool_use"]

            self.assertEqual(backend.router_call_count, 1)
            self.assertIn("read-focused repository helper", result)
            self.assertIn("ls", tool_use_names)

    def test_repo_question_requires_evidence_before_accepting_planner_answer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeRepoQuestionNeedsRetryBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("what is this repo for?")
            messages = store.load_messages(runtime.active_session.id)
            tool_use_names = [message.metadata.get("tool") for message in messages if message.kind == "tool_use"]
            retry_rendered = json.dumps(backend.messages_seen[1], ensure_ascii=False)

            self.assertEqual(backend.router_call_count, 1)
            self.assertIn("read-focused repository helper", result)
            self.assertIn("ls", tool_use_names)
            self.assertIn("Evidence is required before answering this request.", retry_rendered)

    def test_repo_question_falls_back_safely_if_planner_refuses_tools_twice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeRepoQuestionStillRefusesToolsBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("what is this repo for?")
            messages = store.load_messages(runtime.active_session.id)

            self.assertEqual(backend.router_call_count, 1)
            self.assertIn("inspect local repository files", result)
            self.assertEqual([message.kind for message in messages], ["message", "message"])

    def test_repo_question_can_anchor_to_readme_after_initial_discovery(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "README.md").write_text("# crush_py\nread-focused repository helper\n", encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeRepoQuestionReadmeAnchorBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("what is this repo for?")
            messages = store.load_messages(runtime.active_session.id)
            tool_use_names = [message.metadata.get("tool") for message in messages if message.kind == "tool_use"]

            self.assertEqual(backend.router_call_count, 1)
            self.assertIn("read-focused repository helper", result)
            self.assertIn("ls", tool_use_names)
            self.assertIn("reader", tool_use_names)
            self.assertIn("cat", tool_use_names)

    def test_sanitize_text_removes_ansi_escape_codes(self):
        cleaned = sanitize_text("\x1b[31mconfig.json\x1b[0m")

        self.assertEqual(cleaned, "config.json")

    def test_single_doc_workspace_fast_path_avoids_config_drift(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "INSTRUCTIONS.md").write_text("# Guide\nUse one variable at a time.\n", encoding="utf-8")
            (workspace / "config.json").write_text('{"workspace_root":"."}\n', encoding="utf-8")
            config = self._make_config(workspace)
            store = SessionStore(config.sessions_dir, trace_mode=config.trace_mode)
            runtime = AgentRuntime(config, store)
            backend = FakeImplicitSingleDocBackend()
            runtime._create_backend = lambda backend_cfg: backend

            result = runtime.ask("help me understand the instruction")
            messages = store.load_messages(runtime.active_session.id)
            tool_use_names = [message.metadata.get("tool") for message in messages if message.kind == "tool_use"]
            rendered = json.dumps([message.content for message in messages], ensure_ascii=False)

            self.assertEqual(backend.router_call_count, 1)
            self.assertEqual(backend.planner_turn_count, 0)
            self.assertIn("INSTRUCTIONS.md", result)
            self.assertIn("TensorFlow experiments", result)
            self.assertIn("reader", tool_use_names)
            self.assertNotIn("config.json", rendered)

    def test_session_model_override_is_used_for_backend_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            config = self._make_config(workspace)
            runtime = AgentRuntime(config, SessionStore(config.sessions_dir, trace_mode=config.trace_mode))
            runtime.new_session()
            runtime.set_session_model("google/gemma-3-4b")

            original_backend = runtime._create_backend
            try:
                runtime._create_backend = lambda backend_cfg: CaptureModelBackend(
                    model=backend_cfg.model,
                    api_key=backend_cfg.api_key,
                    base_url=backend_cfg.base_url,
                    timeout=backend_cfg.timeout,
                    max_tokens=backend_cfg.max_tokens,
                )
                runtime.ask("Summarize README.md")
            finally:
                runtime._create_backend = original_backend

            self.assertEqual(CaptureModelBackend.last_model, "google/gemma-3-4b")


if __name__ == "__main__":
    unittest.main()
