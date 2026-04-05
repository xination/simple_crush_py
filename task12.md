# task12.md

## Goal
- Review and tighten the current prompt stack used by planner, reader, trace, summary, and guide flows.
- Make tool-selection behavior more explicit in prompts so runtime behavior and prompt guidance stay aligned.
- Preserve the current architecture while improving consistency, predictability, and output quality.

---

## Primary Problem

### Prompt behavior is workable but still partly implicit
- The current prompt stack already works, but several important behaviors are enforced mostly in code rather than clearly stated in prompt instructions.
- This can make model behavior feel inconsistent across:
  - planner mode
  - reader mode
  - trace mode
  - guide mode
  - direct-file summary mode

### Why this matters
- If the prompts and runtime rules drift apart, the model may still choose suboptimal tools even when code later constrains or corrects it.
- The best user experience comes from:
  - prompt guidance steering the model correctly early
  - runtime rules acting as guardrails, not as the main source of truth

---

## Current Prompt Stack To Review

### Base prompt
- `BASE_READ_HELPER_SYSTEM_PROMPT`

### Appendices
- `PLANNER_APPENDIX`
- `TRACE_APPENDIX`
- `DIRECT_FILE_APPENDIX`
- `GUIDE_APPENDIX`
- `READER_APPENDIX`

### Main source file
- `crush_py/agent/runtime_prompts.py`

### Prompt composition points
- `crush_py/agent/runtime.py`
- `crush_py/agent/reader_runtime.py`
- `crush_py/agent/summary_runtime.py`
- `crush_py/agent/trace_runtime.py`
- `crush_py/agent/guide_runtime.py`

---

## What Needs Improvement

### 1. Clarify tool roles
- Make the difference between discovery tools and evidence tools more explicit.
- Suggested distinction:
  - discovery:
    - `ls`
    - `find`
    - `grep`
    - `get_outline`
  - evidence:
    - `cat`

### 2. Make `get_outline` boundaries explicit
- The prompts should clearly say:
  - use `get_outline` only for supported code files
  - use `cat` for docs, config files, text files, and other non-code files

### 3. Align planner and reader responsibilities
- Planner should focus on:
  - locating likely files
  - narrowing candidates
  - delegating once one concrete path is confirmed
- Reader should focus on:
  - one concrete file only
  - evidence-backed reading
  - concise, planner-friendly output

### 4. Tighten evidence language
- Encourage answers grounded in:
  - headings
  - symbols
  - file fragments
  - local evidence
- Discourage vague summaries that do not point back to concrete evidence.

### 5. Tighten uncertainty language
- Especially in trace mode, make it harder for the model to overclaim.
- The prompt should reinforce:
  - proven vs likely vs unknown
  - grep hits are not the same as confirmed flow

### 6. Differentiate `--guide`, `--trace`, and `--summarize` more strongly
- `--guide`:
  - beginner-friendly docs help
  - setup, checklists, troubleshooting, onboarding
- `--trace`:
  - path/flow/usage tracing
  - where values move, get passed, get stored, or change
- `--summarize`:
  - short file responsibility summary
  - not a trace and not a docs guide

---

## Runtime Rules That Prompts Should Reflect

### Tool-result forwarding
- Small tool results are now forwarded in full for:
  - `cat`
  - `get_outline`
  - `ls`
  - `tree`
  - `find`
  - `grep`
- Prompts should assume the model may receive small raw tool results, not only summaries.

### Non-code files prefer `cat`
- Reader logic now explicitly prefers:
  - code file -> `get_outline` + `cat`
  - non-code file -> `cat` only
- Prompts should match this rule directly.

### Direct-file summaries prefer `cat`
- Direct-file summary paths should continue to bias toward:
  - `cat` first
  - `get_outline` only when the user explicitly asks about code structure

---

## Suggested Implementation Plan

### 1. Review the current prompt text line by line
- Identify:
  - duplicated guidance
  - ambiguous guidance
  - missing rules already enforced by code

### 2. Rewrite prompts with tighter role separation
- Keep the existing prompt structure.
- Improve wording instead of replacing the architecture.

### 3. Sync prompts with current runtime constraints
- Ensure prompts explicitly reflect:
  - non-code file -> `cat`
  - direct-file behavior
  - evidence and uncertainty expectations

### 4. Add or update tests where prompt wording matters
- Especially around:
  - reader strategy text
  - non-code file behavior
  - guide vs trace vs summary wording

### 5. Re-run smoke tests on common flows
- Examples:
  - summarize `README.md`
  - trace a variable in a code file
  - ask guide-style setup questions from docs

---

## Acceptance Checks
1. Prompt text clearly distinguishes planner, reader, trace, guide, and direct-file roles.
2. Prompt text explicitly reflects that non-code files should use `cat`, not `get_outline`.
3. Prompt text better distinguishes when to use `--trace`, `--summarize`, and `--guide`.
4. Trace-mode wording reduces overclaim risk and strengthens uncertainty handling.
5. Existing behavior and tests still pass after prompt tightening.

---

## Notes Learned So Far

### 1. REPL UX improvements mattered immediately
- Spinner visibility in REPL was missing because it was tied only to stream mode.
- We changed REPL so users can see thinking feedback without requiring stream mode.
- The spinner is now cleared before final printed output.

### 2. REPL results must be printed explicitly
- `/summarize`, `/guide`, `/trace`, and plain REPL prompts were executing correctly before, but some paths were not printing the returned text.
- This created a false impression that the command had done nothing.

### 3. Minimal REPL UI is easier to use
- We hid lower-priority commands from `/help` and root completion:
  - `/sessions`
  - `/use`
  - `/outline`
  - `/history`
  - `/tools`
  - `/tree`
- These features may still exist internally, but the default UI now stays simpler.

### 4. `/ls` is more useful than `/tree` for path selection
- We kept `/ls` as the main visible directory-view command.
- `/ls` now shows full relative paths for entries.
- This is easier for both users and the model when selecting exact files for later commands.

### 5. `/find` needed fuzzy behavior, but only with good limits
- We added fuzzy fallback for `/find`.
- We also tightened it to avoid noisy results such as `backends` when the user asked for `ben`.
- `/find` result highlighting now marks the matched fragment for readability.

### 6. Small tool results are often better than summaries
- Short results from `find` and `grep` now pass through in full instead of being collapsed into summaries.
- This makes downstream reasoning more accurate because real paths and match lines stay visible.

### 7. `get_outline` is intentionally narrow
- `get_outline` only supports Python/C/C++ source-like files.
- This is good, but the prompts should state that boundary more explicitly.

### 8. Non-code files should always prefer `cat`
- We turned this into explicit runtime behavior.
- Reader mode now limits itself to `cat` for non-code files like:
  - `README.md`
  - docs
  - config files
  - text files

### 9. CLI help benefits from stronger mode differentiation
- We improved `python -m crush_py --help` to better explain when to use:
  - `--trace`
  - `--summarize`
  - `--guide`
- This same clarity should now be reflected in the system prompts.

### 10. Prompt improvements should be incremental, not architectural
- The current prompt stack is already structured well enough to refine.
- The next step should be prompt tightening, not a large prompt-system redesign.

---

## One-line Summary
- `task12` should tighten the current prompt stack so that prompt wording matches the runtime rules we now trust, especially around tool roles, non-code files, trace uncertainty, and the distinction between guide, trace, and summarize modes.
