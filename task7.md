# task7.md

## Goal
- Continue cleanup after `task6`
- Make `crush_py/agent/runtime.py` smaller without changing behavior
- Keep the recent tracing improvements intact:
  - `--trace` CLI entry
  - `variable_trace` and `flow_trace`
  - more honest coverage labels
  - function-block reads for flow tracing

---

## Session recap

### Main changes
- Added explicit trace mode:
  - `python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"`
  - `python -m crush_py --trace "how prompt flows inside crush_py/agent/runtime.py"`
- Split tracing into:
  - `variable_trace` for definition / reassignment / passing / storage style questions
  - `flow_trace` for "how X flows" questions
- Tightened coverage reporting:
  - prefer `local` or `partial`
  - avoid calling small read windows `complete`
- Improved `flow_trace` read strategy:
  - get outline
  - find matched lines
  - read containing function or method block
- Split runtime code into:
  - `crush_py/agent/runtime_prompts.py`
  - `crush_py/agent/trace_runtime.py`
  - `crush_py/agent/summary_runtime.py`
- `AgentRuntime` now uses:
  - `SummaryRuntimeMixin`
  - `TraceRuntimeMixin`
- `runtime.py` shrank from about `1492` lines to about `876`

---

## Key lessons

### What worked
- `session_id` is still the clearest smoke-test case
- block-level reads work better than tiny local windows for flow questions

### What broke down
- `prompt` exposed that flow tracing cannot reuse the old variable-trace output shape
- claim/evidence mismatch is a real quality problem
- tiny read scopes make small-model tracing overfit nearby lines
- `config.runtime_timeout_180.json` having `timeout: 60` showed config naming needs to stay truthful

### Important follow-up
- tracing still needs basic claim validation
- good first checks:
  - reassignment claims must point to a real rebinding of the tracked name
  - argument-passing claims must point to a real call or parameter site
  - storage claims must point to a real store/assignment operation

---

## Current status

### Structure
- `crush_py/agent/runtime.py`
  - still the main orchestration file
  - now much smaller after moving summary, trace, and reader logic out
- `crush_py/agent/trace_runtime.py`
  - tracing logic
- `crush_py/agent/summary_runtime.py`
  - direct-file summary logic
- `crush_py/agent/reader_runtime.py`
  - reader agent execution
  - reader tool logging
  - reader summary history handling
- `crush_py/agent/runtime_prompts.py`
  - prompt constants and appendices
- `crush_py/repl.py`
  - main REPL loop and completion
- `crush_py/repl_display.py`
  - `/history` and `/trace` formatting
- `crush_py/repl_commands.py`
  - REPL command parsing and dispatch

### Tests
- Passing:
  - `python -m unittest tests.test_cli -q`
  - `python -m unittest tests.test_runtime -q`
  - `python -m unittest tests.test_repl -q`
  - `python -m unittest tests.test_repl_commands -q`
  - `python -m unittest tests.test_reader_runtime -q`

### Saved smoke traces
- `.crush_py/sessions/...`
- successful examples:
  - `session_id`
  - `prompt`
  - `offset`
  - `default_backend`
  - `summary`

---

## Recommended direction

### Split order
1. Clean out the `_legacy_*` summary methods still living in `runtime.py` ✅
2. Split `runtime.py` again ✅
3. Split `repl.py` ✅
4. Only split `trace_runtime.py` later if tracing keeps growing

### Likely next extraction
- `runtime_history.py` or `reader_history.py`
- maybe a smaller completion/helper split from `repl.py` if the REPL keeps growing

### Why this order
- `runtime.py` is still the biggest cleanup target
- legacy summary code should be removed before more structural splits
- `repl.py` is a better next split target than tracing right now
- `trace_runtime.py` already improved, so it can wait unless scope grows again

---

## Completed in this round

- Removed the `_legacy_*` summary helpers from `runtime.py`
- Split reader-agent responsibilities into `crush_py/agent/reader_runtime.py`
- Split REPL display formatting into `crush_py/repl_display.py`
- Split REPL command parsing/dispatch into `crush_py/repl_commands.py`
- Added more direct tests for:
  - `reader_runtime.py`
  - `repl_display.py`
  - `repl_commands.py`

---

## Acceptance checks

1. `_legacy_*` summary helpers are removed from `runtime.py` ✅
2. `runtime.py` becomes materially smaller again ✅
3. direct-file summary behavior has one clear implementation path ✅
4. CLI/runtime tests still pass ✅
5. `repl.py` is the next split candidate after `runtime.py` ✅
6. `repl.py` command parsing is split out ✅

---

## Handy commands

```bash
python -m unittest tests.test_cli -q
python -m unittest tests.test_runtime -q
python -m unittest tests.test_repl -q
python -m unittest tests.test_repl_commands -q
python -m unittest tests.test_reader_runtime -q
python -m crush_py --trace "how prompt flows inside crush_py/agent/runtime.py"
python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"
```

---

## One-line summary
- `task7` has now finished the legacy-summary cleanup plus another `runtime.py` and `repl.py` split; the next structural question is whether history/completion code or tracing growth justifies another extraction.
