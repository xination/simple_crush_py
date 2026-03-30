import io
import json
import unittest

from crush_py.backends.base import BackendError
from crush_py.backends.openai_compat import OpenAICompatBackend


class FakeHTTPResponse:
    def __init__(self, payload):
        self._stream = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self):
        return self._stream.read()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class OpenAICompatBackendTests(unittest.TestCase):
    def setUp(self):
        self.backend = OpenAICompatBackend(
            model="demo-model",
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
                                        "name": "view",
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

        self.assertEqual(turn.text, "Let me inspect that.")
        self.assertEqual(turn.tool_calls[0].name, "view")
        self.assertEqual(turn.tool_calls[0].arguments, {"path": "notes.txt"})
        self.assertEqual(turn.raw_content[1]["type"], "tool_use")

    def test_to_openai_messages_converts_internal_tool_loop_messages(self):
        messages = [
            {"role": "user", "content": "Summarize notes.txt"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me inspect that."},
                    {"type": "tool_use", "id": "call-1", "name": "view", "input": {"path": "notes.txt"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call-1", "content": "<file path=\"notes.txt\">...</file>"},
                ],
            },
        ]

        converted = self.backend._to_openai_messages("system prompt", messages)

        self.assertEqual(converted[0], {"role": "system", "content": "system prompt"})
        self.assertEqual(converted[2]["role"], "assistant")
        self.assertEqual(converted[2]["tool_calls"][0]["function"]["name"], "view")
        self.assertEqual(converted[3]["role"], "tool")
        self.assertEqual(converted[3]["tool_call_id"], "call-1")

    def test_to_openai_tools_converts_tool_schema(self):
        tools = [
            {
                "name": "view",
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
        self.assertEqual(converted[0]["function"]["name"], "view")
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
                                        "name": "view",
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
            {"role": "user", "content": "Summarize notes.txt"},
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

    def test_to_openai_messages_compacts_long_view_results_before_truncating(self):
        view_body = "\n".join("{0:>6}|line {0}".format(index) for index in range(1, 500))
        messages = [
            {"role": "user", "content": "Summarize notes.txt"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "tool_name": "view",
                        "content": "<file path=\"notes.txt\">\n{0}\n</file>\nFile has more lines. Use offset >= 500 to continue.".format(view_body),
                    },
                ],
            },
        ]

        converted = self.backend._to_openai_messages("system prompt", messages)

        self.assertEqual(converted[2]["role"], "tool")
        self.assertIn("<file path=\"notes.txt\">", converted[2]["content"])
        self.assertIn("more `view` lines omitted", converted[2]["content"])
        self.assertIn("File has more lines.", converted[2]["content"])

    def test_effective_max_tokens_is_reduced_for_tool_calls(self):
        self.backend.max_tokens = 4096

        self.assertEqual(self.backend._effective_max_tokens(None), 4096)
        self.assertEqual(self.backend._effective_max_tokens([{"name": "view"}]), 1024)

    def test_small_model_budget_is_lower_for_4b_models(self):
        backend = OpenAICompatBackend(
            model="google/gemma-3-4b",
            api_key="test-key",
            base_url="http://127.0.0.1:1234/v1",
        )

        self.assertEqual(backend._small_model_profile_name(), "small_model")
        self.assertEqual(backend._tool_call_token_budget(), 768)
        self.assertEqual(backend._tool_result_char_budget(), 2500)

    def test_small_model_strict_budget_is_lower_for_3b_models(self):
        backend = OpenAICompatBackend(
            model="qwen2.5-coder-3b",
            api_key="test-key",
            base_url="http://127.0.0.1:1234/v1",
        )

        self.assertEqual(backend._small_model_profile_name(), "small_model_strict")
        self.assertEqual(backend._tool_call_token_budget(), 512)
        self.assertEqual(backend._tool_result_char_budget(), 1600)


if __name__ == "__main__":
    unittest.main()
