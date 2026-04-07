import argparse
import json
import sys
from pathlib import Path
from urllib import error, request

DEFAULT_BASE_URL = "http://192.168.40.1:1234/v1"
DEFAULT_MODEL = "google/gemma-3-4b"
DEFAULT_TIMEOUT = 600
DEFAULT_API_KEY = "not-needed"


def read_file_text(path_text):
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.resolve(), path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.resolve(), path.read_text(encoding="utf-8", errors="replace")


def build_user_content(prompt, file_path=None):
    if not file_path:
        return prompt

    resolved_path, file_text = read_file_text(file_path)
    return (
        "User prompt:\n"
        "{0}\n\n"
        "File path:\n"
        "{1}\n\n"
        "File content:\n"
        "```text\n"
        "{2}\n"
        "```"
    ).format(prompt, resolved_path, file_text)


def build_messages(prompt, system_prompt=None, file_path=None):
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append(
        {
            "role": "user",
            "content": build_user_content(prompt=prompt, file_path=file_path),
        }
    )
    return messages


def open_stream(base_url, model, messages):
    endpoint = "{0}/chat/completions".format(base_url.rstrip("/"))
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    req = request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "authorization": "Bearer {0}".format(DEFAULT_API_KEY),
        },
        method="POST",
    )
    try:
        return request.urlopen(req, timeout=DEFAULT_TIMEOUT)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            "OpenAI-compatible request failed with status {0}: {1}".format(exc.code, detail)
        )
    except error.URLError as exc:
        raise RuntimeError("Unable to reach OpenAI-compatible backend: {0}".format(exc.reason))


def iter_sse_payloads(response):
    data_lines = []
    while True:
        line = response.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if text.startswith("data:"):
            data_lines.append(text[5:].strip())


def iter_delta_text(delta):
    content = delta.get("content", "")
    if isinstance(content, str):
        if content:
            yield content
        return
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in ("text", "output_text") and item.get("text"):
                yield item["text"]


def stream_chat(base_url, model, messages):
    emitted_any = False
    with open_stream(base_url=base_url, model=model, messages=messages) as response:
        for payload in iter_sse_payloads(response):
            if payload == "[DONE]":
                break
            try:
                body = json.loads(payload)
                choices = body.get("choices", [])
                delta = choices[0].get("delta", {})
            except (ValueError, AttributeError, IndexError, KeyError) as exc:
                raise RuntimeError("Invalid streaming payload: {0}".format(exc))
            for chunk in iter_delta_text(delta):
                if not chunk:
                    continue
                emitted_any = True
                print(chunk, end="", flush=True)
    if emitted_any:
        print()


def build_parser():
    parser = argparse.ArgumentParser(
        description="Super simplified one-shot streaming client for LM Studio.",
    )
    parser.add_argument("--file", help="Optional file to read and include in the prompt.")
    parser.add_argument("--base_url", default=DEFAULT_BASE_URL, help="OpenAI-compatible base URL.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name.")
    parser.add_argument("--prompt", required=True, help="User prompt.")
    system_prompt_group = parser.add_mutually_exclusive_group()
    system_prompt_group.add_argument("--system_prompt", help="Optional system prompt.")
    system_prompt_group.add_argument(
        "--system_prompt_file",
        help="Optional file whose contents will be used as the system prompt.",
    )
    return parser


def resolve_system_prompt(args):
    if args.system_prompt is not None:
        return args.system_prompt
    if args.system_prompt_file:
        _path, text = read_file_text(args.system_prompt_file)
        return text
    return None


def main():
    args = build_parser().parse_args()
    prompt = args.prompt
    if not prompt.strip():
        raise SystemExit("`--prompt` must not be empty.")

    messages = build_messages(
        prompt=prompt.strip(),
        system_prompt=resolve_system_prompt(args),
        file_path=args.file,
    )
    try:
        stream_chat(
            base_url=args.base_url,
            model=args.model,
            messages=messages,
        )
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
