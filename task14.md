# task14.md

## Goal
- Prototype **Option A** for the tiny intent router.
- Keep the current `intent` labels.
- Add a new structured field:
  - `needs_tools`
- Use this to let the router decide not only *what kind of request this is*, but also *whether tools should be used at all*.

---

## Option A Summary

### Design choice
- Keep existing intent labels such as:
  - `direct_file_summary`
  - `direct_file_doc_qa`
  - `direct_file_trace`
  - `guide`
  - `repo_search`
  - `general_qa`
- Add:
  - `needs_tools: true | false`

### Why we want this
- Some prompts are real information requests and should still use tools.
- Some prompts are conversation-only and should not enter planner/tool search at all.
- This is more flexible than encoding execution policy only inside the `intent` string.

### Main expected benefit
- `general_qa` can now split into:
  - questions that need tools
  - questions that do not need tools

---

## Problem We Want To Fix

### Current issue observed
- A simple conversational prompt like:
  - `hi`
- can still trigger planner behavior such as:
  - `tree`
  - `ls`
- which is not good UX and wastes tokens.

### Why this happens
- Current tiny router mainly helps choose *which file-oriented flow to use*.
- It does not yet make an explicit decision about *tool usage itself*.

---

## Proposed Router Output

### New output shape
- Strict JSON only.
- Example:

```json
{
  "intent": "general_qa",
  "confidence": "high",
  "target_path": null,
  "needs_full_cat": false,
  "needs_tools": false
}
```

### Notes
- `intent` stays as the semantic category.
- `needs_tools` becomes the execution-policy decision.
- `needs_full_cat` can remain as a file-flow hint where relevant.

---

## Behavioral Expectations

### 1. Conversation-only prompts
- Examples:
  - `hi`
  - `hello`
  - `thanks`
  - `ok`
  - `what can you do?`
- Expected:
  - `intent`: likely `general_qa`
  - `needs_tools`: `false`
- Runtime behavior:
  - should not enter planner tool loop
  - should not call `ls`, `tree`, `find`, `grep`, or `cat`

### 2. Repo-level questions that still need evidence
- Examples:
  - `what is this repo for?`
  - `what does this project do?`
- Expected:
  - `needs_tools`: usually `true`
- Runtime behavior:
  - should gather real evidence first
  - should not answer purely from directory names if no file evidence exists

### 3. Direct-file doc-QA
- Examples:
  - `according to README.md, what is crush_py built for?`
  - `from README.md, what kind of tool is crush_py?`
- Expected:
  - `intent`: `direct_file_doc_qa`
  - `needs_tools`: `true`

### 4. Direct-file summaries
- Examples:
  - `summarize README.md`
  - `read README.md and show me the key ideas`
- Expected:
  - `intent`: `direct_file_summary`
  - `needs_tools`: `true`

### 5. Trace requests
- Examples:
  - `trace how prompt flows inside crush_py/agent/runtime.py`
  - `trace the variable session_id in crush_py/store/session_store.py`
- Expected:
  - `intent`: `direct_file_trace` or trace-related route
  - `needs_tools`: `true`

---

## Suggested Test Cases

### Group A: no-tool conversation cases
- `hi`
- `hello`
- `thanks`
- `ok`
- `what can you do?`

### Group B: repo questions that should still use tools
- `what is this repo for?`
- `what does this project do?`
- `can you explain this repository?`

### Group C: direct-file doc-QA
- `according to README.md, what is crush_py built for?`
- `from README.md, what kind of tool is crush_py?`
- `read README.md and tell me what this project is for`

### Group D: direct-file summary
- `summarize README.md`
- `read README.md and show me the key ideas`
- `give a short summary for README.md`

### Group E: trace
- `trace how prompt flows inside crush_py/agent/runtime.py`
- `trace the variable session_id in crush_py/store/session_store.py`

---

## Acceptance Checks

### Router schema
- Router output includes:
  - `intent`
  - `confidence`
  - `target_path`
  - `needs_full_cat`
  - `needs_tools`

### Runtime behavior
- If `needs_tools = false`:
  - runtime should not enter planner tool loop
  - no tool-use records should be appended for that turn

### Conversation UX
- Greeting or lightweight conversational prompts should produce a direct assistant reply.
- They should not produce directory exploration first.

### Safety
- If router JSON parsing fails:
  - fall back safely to current heuristic behavior
- If router confidence is low:
  - prefer the safer route

### Evidence quality
- Repo-level factual answers should still use tools when needed.
- We should not regress into unsupported repo-summary guesses from filenames alone.

---

## Implementation Notes

### Minimal rollout idea
- First add `needs_tools` to the tiny router output and parser.
- Then add one runtime gate:
  - if `needs_tools == false`, skip planner/tool loop
- Keep heuristic fallback during rollout.

### Important caution
- `general_qa` should not automatically mean `needs_tools = false`.
- That is the main reason Option A is useful.

---

## Experimental Results

### Implementation status
- Added `needs_tools` to the tiny router decision schema.
- Extended router handling so it can classify both:
  - direct-file prompts
  - non-file prompts such as greetings and repo-level questions
- Added a runtime short-circuit:
  - if `needs_tools == false`, use a direct-answer path with no planner/tool loop
- Kept heuristic fallback so invalid router JSON still behaves safely.

### No-tool case results
- Prompt:
  - `hi`
- Result:
  - router path was evaluated
  - runtime answered directly
  - no planner tool loop was entered
  - no `tool_use` / `tool_result` records were appended for that turn
- This fixes the bad UX case where a simple greeting could trigger repo exploration first.

### Tool-needed case results
- Prompt:
  - `what is this repo for?`
- Result:
  - router marked the request as still needing tools
  - runtime entered the planner path
  - tool usage remained available for evidence gathering
- This preserves the intended behavior for factual repo-level questions.

### Regression checks
- Targeted runtime suite:
  - `pytest tests/test_runtime.py -q`
  - result: `60 passed`
- Full test suite:
  - `pytest -q`
  - result: `163 passed`

### Conclusion
- Option A is working better after this change.
- We now have a clean split inside `general_qa`:
  - lightweight conversation can skip tools
  - evidence-seeking questions can still use tools
- This improves UX without regressing repo-grounded answers.

---

## Recommended Immediate Next Step
- Compare a small before/after session set with real prompts such as:
  - `hello`
  - `thanks`
  - `what can you do?`
  - `what does this project do?`
- Measure:
  - tool usage count
  - response quality
  - unnecessary repo exploration
- If results stay good, expand the no-tool protected set beyond simple greetings.
