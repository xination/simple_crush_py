import json
from urllib import error, request

from .base import AssistantTurn, BackendError, BaseBackend, ToolCall


ANTHROPIC_VERSION = "2023-06-01"


class AnthropicBackend(BaseBackend):
    name = "anthropic"

    def __init__(self, model, api_key, base_url, timeout=60, max_tokens=4096):
        if not api_key:
            raise BackendError("Anthropic API key is missing.")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens

    def generate(self, system_prompt, messages, tools=None):
        response = self._request(system_prompt=system_prompt, messages=messages, stream=False, tools=tools)
        return self._parse_response(response)

    def generate_with_metadata(self, system_prompt, messages, tools=None):
        response = self._request(system_prompt=system_prompt, messages=messages, stream=False, tools=tools)
        return self._parse_turn_response(response)

    def generate_turn(self, system_prompt, messages, tools=None):
        response = self._request(system_prompt=system_prompt, messages=messages, stream=False, tools=tools)
        return self._parse_turn_response(response)

    def stream_generate(self, system_prompt, messages, tools=None):
        response = self._request(system_prompt=system_prompt, messages=messages, stream=True, tools=tools)
        for payload in self._iter_sse_payloads(response):
            if payload == "[DONE]":
                break
            try:
                body = json.loads(payload)
            except ValueError as exc:
                raise BackendError("Anthropic streaming payload was invalid: {0}".format(exc))
            if body.get("type") == "content_block_delta":
                text = body.get("delta", {}).get("text", "")
                if text:
                    yield text

    def _request(self, system_prompt, messages, stream, tools=None):
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system_prompt,
            "messages": messages,
        }
        if stream:
            payload["stream"] = True
        if tools:
            payload["tools"] = tools
        endpoint = "{0}/v1/messages".format(self.base_url)
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        req = request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            return request.urlopen(req, timeout=self.timeout)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise BackendError("Anthropic request failed with status {0}: {1}".format(exc.code, detail))
        except error.URLError as exc:
            raise BackendError("Unable to reach Anthropic API: {0}".format(exc.reason))

    def _parse_response(self, response):
        turn = self._parse_turn_response(response)
        if turn.tool_calls and not turn.text:
            return ""
        if not turn.text:
            raise BackendError("Anthropic API returned no text content.")
        return turn.text

    def _parse_turn_response(self, response):
        try:
            with response:
                raw = response.read().decode("utf-8")
            payload = json.loads(raw)
            content = payload.get("content", [])
            text_parts = []
            tool_calls = []
            raw_content = []
            for item in content:
                raw_content.append(item)
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif item.get("type") == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=item.get("id", ""),
                            name=item.get("name", ""),
                            arguments=item.get("input", {}) or {},
                        )
                    )
            text = "".join(text_parts).strip()
        except (ValueError, AttributeError) as exc:
            raise BackendError("Anthropic API returned invalid JSON: {0}".format(exc))
        return AssistantTurn(text=text, tool_calls=tool_calls, raw_content=raw_content)

    def supports_tool_calls(self) -> bool:
        return True

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
