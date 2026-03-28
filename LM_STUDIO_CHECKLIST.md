# LM Studio Validation Checklist

This checklist is for validating `crush_py` against a live LM Studio
OpenAI-compatible runtime after the local implementation work is done.

## Goal

- Confirm that plain chat works with the `openai_compat` backend.
- Confirm that automatic read-only tool-calling works end-to-end.
- Confirm that automatic `edit` requests work with per-call confirmation.
- Confirm that `/trace` shows the expected execution flow.

## Prerequisites

- LM Studio server is running.
- The OpenAI-compatible API endpoint is reachable.
- You know the model identifier exposed by LM Studio.
- You have this project available on the target machine.

## 1. Verify the LM Studio API is reachable

- Confirm the endpoint is available, for example:
  - `http://127.0.0.1:1234/v1`
- If you want, first verify that LM Studio can answer a minimal
  `/chat/completions` request before testing `crush_py`.

## 2. Prepare config

Make sure your config points to the LM Studio backend.

Example:

```json
{
  "workspace_root": ".",
  "sessions_dir": ".crush_py/sessions",
  "default_backend": "lm_studio",
  "backends": {
    "lm_studio": {
      "type": "openai_compat",
      "model": "your-model-name",
      "base_url": "http://127.0.0.1:1234/v1",
      "api_key": "not-needed",
      "timeout": 60,
      "max_tokens": 4096
    }
  }
}
```

## 3. Start the REPL

From the project directory:

```bash
python -m crush_py
```

Verify:

- `/backend` shows `lm_studio`
- The active session is using the expected backend

## 4. Validate plain text chat first

Start with a simple prompt:

- `say hello in one sentence`

Success criteria:

- The model replies normally
- No request/parsing errors occur
- No tool-calling is required for this step

## 5. Validate automatic read-only tool-calling

Use prompts that should trigger repo inspection:

- `Read README.md and summarize the quick start.`
- `Find where SessionStore is implemented and summarize it.`
- `Look for the edit tool and explain how it works.`

Success criteria:

- The model automatically uses `view`, `ls`, `glob`, or `grep`
- The final answer is reasonable
- `/trace` shows:
  - `tool_use`
  - `tool_result`
  - final assistant stage

## 6. Validate automatic edit with confirmation

Create a small safe test file, for example:

- `tmp/lmstudio_edit_test.txt`

Content:

```text
alpha
beta
gamma
```

Prompt:

- `Change beta to BETA in tmp/lmstudio_edit_test.txt`

Success criteria:

- The model requests `edit`
- REPL shows a confirmation preview
- The preview clearly shows:
  - path
  - `replace_all`
  - `old_text`
  - `new_text`
- After approving with `y`, the file is modified correctly
- `/trace` records the request and result

## 7. Validate rejection flow for automatic edit

Repeat a similar edit prompt, but reject the confirmation with `n`.

Success criteria:

- The file remains unchanged
- The assistant does not falsely claim success
- `/trace` shows the resulting tool error / refusal path

## 8. Validate edge cases

Test at least these:

- `old_text` not found
- `old_text` appears multiple times without `replace_all`
- multi-step flow where the model first reads, then edits
- UTF-8 / non-English file content
- nested relative paths inside subdirectories

## 9. Watch for OpenAI-compatible response format differences

Pay close attention to whether LM Studio returns data in the shape expected by
the current implementation:

- `choices[0].message.content`
- `choices[0].message.tool_calls`
- `tool_calls[*].function.name`
- `tool_calls[*].function.arguments`

Things to verify:

- `content` is either a string or empty when tool calls are present
- `function.arguments` is valid JSON text
- tool result messages are accepted by the model in follow-up turns

## 10. Record the outcome

After validation, note:

- prompts that worked well
- prompts that failed
- any LM Studio format differences
- any model-specific quirks
- whether code changes are needed

## Recommended first 3 prompts

- `Read README.md and summarize the quick start.`
- `Find where the edit tool is implemented and explain it briefly.`
- `Change beta to BETA in tmp/lmstudio_edit_test.txt`

## Final success criteria

- Plain chat works
- Automatic read-only tools work
- Automatic `edit` works with confirmation
- Rejecting automatic `edit` is safe
- `/trace` clearly shows the flow
- No OpenAI-compatible parsing failures occur
