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


if __name__ == "__main__":
    unittest.main()
