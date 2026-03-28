import json
from urllib import error, request

from .base import AssistantTurn, BackendError, BaseBackend, ToolCall


class OpenAICompatBackend(BaseBackend):
    name = "openai_compat"

    def __init__(self, model, api_key, base_url, timeout=60, max_tokens=4096):
        self.model = model
        self.api_key = api_key or "not-needed"
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens

    def generate(self, system_prompt, messages, tools=None):
        response = self._request(system_prompt=system_prompt, messages=messages, stream=False, tools=tools)
        return self._parse_response(response)

    def generate_turn(self, system_prompt, messages, tools=None):
        response = self._request(system_prompt=system_prompt, messages=messages, stream=False, tools=tools)
        return self._parse_turn_response(response)

    def generate_with_metadata(self, system_prompt, messages, tools=None):
        return self.generate_turn(system_prompt, messages, tools=tools)

    def stream_generate(self, system_prompt, messages, tools=None):
        response = self._request(system_prompt=system_prompt, messages=messages, stream=True, tools=tools)
        for payload in self._iter_sse_payloads(response):
            if payload == "[DONE]":
                break
            try:
                body = json.loads(payload)
                choices = body.get("choices", [])
                delta = choices[0].get("delta", {})
                text = delta.get("content", "")
            except (ValueError, AttributeError, IndexError, KeyError) as exc:
                raise BackendError("OpenAI-compatible streaming payload was invalid: {0}".format(exc))
            if text:
                yield text

    def _request(self, system_prompt, messages, stream, tools=None):
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._to_openai_messages(system_prompt, messages),
        }
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = self._to_openai_tools(tools)
        endpoint = "{0}/chat/completions".format(self.base_url)
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "authorization": "Bearer {0}".format(self.api_key),
        }
        req = request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            return request.urlopen(req, timeout=self.timeout)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise BackendError("OpenAI-compatible request failed with status {0}: {1}".format(exc.code, detail))
        except error.URLError as exc:
            raise BackendError("Unable to reach OpenAI-compatible backend: {0}".format(exc.reason))

    def _parse_response(self, response):
        turn = self._parse_turn_response(response)
        if turn.tool_calls and not turn.text:
            return ""
        if not turn.text:
            raise BackendError("OpenAI-compatible backend returned no text content.")
        return turn.text

    def _parse_turn_response(self, response):
        try:
            with response:
                raw = response.read().decode("utf-8")
            payload = json.loads(raw)
            choices = payload.get("choices", [])
            message = choices[0]["message"]
            content = message.get("content")
            text = content.strip() if isinstance(content, str) else ""
            tool_calls = []
            raw_content = []
            if text:
                raw_content.append({"type": "text", "text": text})
            for tool_call in message.get("tool_calls", []) or []:
                function = tool_call.get("function", {}) or {}
                arguments = function.get("arguments", "") or "{}"
                try:
                    parsed_arguments = json.loads(arguments)
                except ValueError as exc:
                    raise BackendError("OpenAI-compatible tool call arguments were invalid JSON: {0}".format(exc))
                tool_calls.append(
                    ToolCall(
                        id=tool_call.get("id", ""),
                        name=function.get("name", ""),
                        arguments=parsed_arguments,
                    )
                )
                raw_content.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.get("id", ""),
                        "name": function.get("name", ""),
                        "input": parsed_arguments,
                    }
                )
        except (ValueError, AttributeError, IndexError, KeyError, TypeError) as exc:
            raise BackendError("OpenAI-compatible backend returned invalid JSON: {0}".format(exc))
        return AssistantTurn(text=text, tool_calls=tool_calls, raw_content=raw_content)

    def supports_tool_calls(self) -> bool:
        return True

    def _to_openai_messages(self, system_prompt, messages):
        openai_messages = [{"role": "system", "content": system_prompt}]
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if isinstance(content, list):
                if role == "assistant":
                    openai_messages.append(self._assistant_blocks_to_message(content))
                    continue
                if role == "user":
                    openai_messages.extend(self._tool_result_blocks_to_messages(content))
                    continue
            openai_messages.append({"role": role, "content": content})
        return openai_messages

    def _assistant_blocks_to_message(self, blocks):
        text_parts = []
        tool_calls = []
        for item in blocks:
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif item.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": item.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": item.get("name", ""),
                            "arguments": json.dumps(item.get("input", {}) or {}, ensure_ascii=False),
                        },
                    }
                )
        message = {
            "role": "assistant",
            "content": "".join(text_parts) if text_parts else "",
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message

    def _tool_result_blocks_to_messages(self, blocks):
        messages = []
        for item in blocks:
            if item.get("type") != "tool_result":
                continue
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("tool_use_id", ""),
                    "content": item.get("content", ""),
                }
            )
        return messages

    def _to_openai_tools(self, tools):
        converted = []
        for tool in tools:
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
            )
        return converted

    def _iter_sse_payloads(self, response):
        with response:
            data_lines = []
            while True:
                line = response.readline()
                if not line:
                    break
                text = line.decode("utf-8").strip()
                if not text:
                    if data_lines:
                        yield "\n".join(data_lines)
                        data_lines = []
                    continue
                if text.startswith("data:"):
                    data_lines.append(text[5:].strip())
