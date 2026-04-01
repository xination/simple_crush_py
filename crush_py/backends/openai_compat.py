import json
from urllib import error, request

from .base import AssistantTurn, BackendError, BaseBackend, ToolCall


class OpenAICompatBackend(BaseBackend):
    name = "openai_compat"
    DEFAULT_MAX_TOOL_CALL_TOKENS = 512
    DEFAULT_MAX_TOOL_RESULT_CHARS = 1600

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
            "max_tokens": self._effective_max_tokens(tools),
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
            if tool_calls:
                text = self._squash_tool_call_text(text)
            elif text:
                raw_content.append({"type": "text", "text": text})
            for tool_call in message.get("tool_calls", []) or []:
                function = tool_call.get("function", {}) or {}
                arguments = function.get("arguments", "") or "{}"
                parsed_arguments = json.loads(arguments)
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
                    "content": self._truncate_tool_result(item),
                }
            )
        return messages

    def _truncate_tool_result(self, item):
        content = item.get("content", "")
        if item.get("tool_name") == "cat":
            content = self._compact_cat_result(content)
        max_tool_result_chars = self._tool_result_char_budget()
        if len(content) <= max_tool_result_chars:
            return content
        return content[: max_tool_result_chars] + "\n...[truncated]"

    def _compact_cat_result(self, content):
        max_tool_result_chars = self._tool_result_char_budget()
        if len(content) <= max_tool_result_chars:
            return content

        lines = content.splitlines()
        if not lines:
            return content

        open_tag = lines[0] if lines and lines[0].startswith("<file ") else ""
        close_tag = "</file>" if "</file>" in lines else ""
        continuation = ""
        if lines and lines[-1].startswith("File has more lines."):
            continuation = lines[-1]

        body = []
        for line in lines:
            if not line or line == open_tag or line == close_tag or line == continuation:
                continue
            body.append(line)

        reserved = 200
        budget = max(200, max_tool_result_chars - reserved)
        parts = []
        used = 0

        for item in (open_tag,):
            if item:
                parts.append(item)
                used += len(item) + 1

        front = []
        back = []
        front_used = 0
        back_used = 0
        front_index = 0
        back_index = len(body) - 1

        while front_index <= back_index:
            take_front = front_used <= back_used
            candidate = body[front_index] if take_front else body[back_index]
            line_cost = len(candidate) + 1
            if used + front_used + back_used + line_cost > budget:
                break
            if take_front:
                front.append(candidate)
                front_used += line_cost
                front_index += 1
            else:
                back.append(candidate)
                back_used += line_cost
                back_index -= 1

        kept_body_lines = len(front) + len(back)
        omitted = len(body) - kept_body_lines
        parts.extend(front)
        if omitted > 0:
            parts.append("...[{0} more `cat` lines omitted]".format(omitted))
        parts.extend(reversed(back))
        if close_tag:
            parts.append(close_tag)
        if continuation:
            parts.append(continuation)
        return "\n".join(parts)

    def _effective_max_tokens(self, tools):
        if tools:
            return min(self.max_tokens, self._tool_call_token_budget())
        return self.max_tokens

    def _tool_call_token_budget(self):
        return self.DEFAULT_MAX_TOOL_CALL_TOKENS

    def _tool_result_char_budget(self):
        return self.DEFAULT_MAX_TOOL_RESULT_CHARS

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

    def _squash_tool_call_text(self, text):
        return ""
