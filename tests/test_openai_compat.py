import io
import json
import unittest
from unittest.mock import patch

from crush_py.backends.base import BackendError
from crush_py.backends.openai_compat import OpenAICompatBackend


class FakeHTTPResponse:
    def __init__(self, payload):
        self._stream = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self):
        return self._stream.read()

    def readline(self):
        return self._stream.readline()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class OpenAICompatBackendTests(unittest.TestCase):
    def setUp(self):
        self.backend = OpenAICompatBackend(
            model="demo-3b",
            api_key="test-key",
            base_url="http://127.0.0.1:1234/v1",
        )

    def test_parse_turn_response_with_tool_calls(self):
        response = FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Let me inspect that.",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "cat",
                                        "arguments": "{\"path\": \"notes.txt\"}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )

        turn = self.backend._parse_turn_response(response)

        self.assertEqual(turn.text, "")
        self.assertEqual(turn.tool_calls[0].name, "cat")
        self.assertEqual(turn.tool_calls[0].arguments, {"path": "notes.txt"})
        self.assertEqual(turn.raw_content[0]["type"], "tool_use")

    def test_to_openai_messages_converts_internal_tool_loop_messages(self):
        messages = [
            {"role": "user", "content": "Trace notes.txt"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me inspect that."},
                    {"type": "tool_use", "id": "call-1", "name": "cat", "input": {"path": "notes.txt"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call-1", "content": '<file path="notes.txt">...</file>'},
                ],
            },
        ]

        converted = self.backend._to_openai_messages("system prompt", messages)

        self.assertEqual(converted[0], {"role": "system", "content": "system prompt"})
        self.assertEqual(converted[2]["role"], "assistant")
        self.assertEqual(converted[2]["tool_calls"][0]["function"]["name"], "cat")
        self.assertEqual(converted[3]["role"], "tool")
        self.assertEqual(converted[3]["tool_call_id"], "call-1")

    def test_to_openai_tools_converts_tool_schema(self):
        tools = [
            {
                "name": "cat",
                "description": "Read file",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ]

        converted = self.backend._to_openai_tools(tools)

        self.assertEqual(converted[0]["type"], "function")
        self.assertEqual(converted[0]["function"]["name"], "cat")
        self.assertEqual(converted[0]["function"]["parameters"]["required"], ["path"])

    def test_parse_turn_response_rejects_invalid_tool_arguments(self):
        response = FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "cat",
                                        "arguments": "{not-json",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        )

        with self.assertRaises(BackendError):
            self.backend._parse_turn_response(response)

    def test_to_openai_messages_truncates_long_tool_results(self):
        messages = [
            {"role": "user", "content": "Trace notes.txt"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "tool_name": "grep",
                        "content": "x" * (self.backend.DEFAULT_MAX_TOOL_RESULT_CHARS + 50),
                    },
                ],
            },
        ]

        converted = self.backend._to_openai_messages("system prompt", messages)

        self.assertEqual(converted[2]["role"], "tool")
        self.assertIn("...[truncated]", converted[2]["content"])

    def test_to_openai_messages_compacts_long_cat_results_before_truncating(self):
        view_body = "\n".join("{0:>6}|line {0}".format(index) for index in range(1, 500))
        messages = [
            {"role": "user", "content": "Trace notes.txt"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "tool_name": "cat",
                        "content": '<file path="notes.txt">\n{0}\n</file>\nFile has more lines. Use offset >= 500 to continue.'.format(view_body),
                    },
                ],
            },
        ]

        converted = self.backend._to_openai_messages("system prompt", messages)

        self.assertEqual(converted[2]["role"], "tool")
        self.assertIn('<file path="notes.txt">', converted[2]["content"])
        self.assertIn("more `cat` lines omitted", converted[2]["content"])
        self.assertIn("File has more lines.", converted[2]["content"])

    def test_tool_call_budget_is_fixed_for_small_model_mode(self):
        self.backend.max_tokens = 4096

        self.assertEqual(self.backend._effective_max_tokens(None), 4096)
        self.assertEqual(self.backend._effective_max_tokens([{"name": "cat"}]), 512)
        self.assertEqual(self.backend._tool_result_char_budget(), 1600)

    def test_stream_generate_turn_reconstructs_split_tool_call_arguments(self):
        sse_payload = "\n\n".join(
            [
                "data: " + json.dumps({"choices": [{"delta": {"content": "Let me inspect that. "}}]}),
                "data: "
                + json.dumps(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call-1",
                                            "function": {"name": "cat", "arguments": '{"path": "notes'},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ),
                "data: "
                + json.dumps(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {"arguments": '.txt"}'},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ),
                "data: [DONE]",
                "",
            ]
        )
        response = FakeHTTPResponse({})
        response._stream = io.BytesIO(sse_payload.encode("utf-8"))

        with patch.object(self.backend, "_request", return_value=response):
            turn = self.backend.stream_generate_turn("system prompt", [])

        self.assertEqual(turn.text, "")
        self.assertEqual(len(turn.tool_calls), 1)
        self.assertEqual(turn.tool_calls[0].name, "cat")
        self.assertEqual(turn.tool_calls[0].arguments, {"path": "notes.txt"})
        self.assertEqual(turn.raw_content[0]["type"], "text")
        self.assertEqual(turn.raw_content[1]["type"], "tool_use")

    def test_stream_generate_turn_supports_list_based_content_chunks(self):
        sse_payload = (
            'data: {"choices":[{"delta":{"content":[{"type":"output_text","text":"Hello "}]}}]}\n\n'
            'data: {"choices":[{"delta":{"content":[{"type":"text","text":"world"}]}}]}\n\n'
            "data: [DONE]\n\n"
        )
        response = FakeHTTPResponse({})
        response._stream = io.BytesIO(sse_payload.encode("utf-8"))

        with patch.object(self.backend, "_request", return_value=response):
            turn = self.backend.stream_generate_turn("system prompt", [])

        self.assertEqual(turn.text, "Hello world")
        self.assertEqual(turn.tool_calls, [])

    def test_stream_generate_yields_incremental_text_chunks(self):
        sse_payload = (
            'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"world"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        response = FakeHTTPResponse({})
        response._stream = io.BytesIO(sse_payload.encode("utf-8"))

        with patch.object(self.backend, "_request", return_value=response):
            chunks = list(self.backend.stream_generate("system prompt", []))

        self.assertEqual(chunks, ["Hello ", "world"])


if __name__ == "__main__":
    unittest.main()
