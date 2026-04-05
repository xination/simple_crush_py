# task13.md

## Goal
- Capture the current plan and status after the recent REPL/runtime quality fixes.
- Record what we learned from smoke tests and session-record reviews.
- Define the next step: a tiny LLM intent router that can make the flow smarter without relying on fragile prompt-pattern engineering.

---

## Current Status

### Completed in this round
- Added `tool_result` dedupe in session storage so repeated identical tool results do not bloat session history.
- Added a UTF-8 Chinese `README.md` regression test to ensure `cat` output and summary snippets preserve readable Chinese text.
- Added a direct-adoption path so when the reader already gives a sufficiently complete direct-file answer, the planner does not rewrite it again.
- Added a direct-file file-flow path for prompts like `Trace the flow for crush_py/repl_display.py`.
- Added a doc-QA fast path for direct-file documentation questions so some single-file questions can skip extra planner-style reasoning.

### Verified
- `python -m pytest tests/test_runtime.py`
- Result: all tests passed after the latest changes.

### Non-goals for this round
- We did not redesign the whole planner/reader architecture.
- We did not remove all heuristic routing yet.
- We did not fully eliminate duplicate reader/final assistant records from session history.

---

## Smoke Tests Run

### Smoke test 1
- Prompt:
  - `from README.md, can you show me what crush_py is?`
- Outcome:
  - Successful answer.
  - Correctly grounded in `README.md`.
- Session reviewed:
  - `c272c36e-6586-4369-8cf3-6e54eea74b77`

### Smoke test 2
- Prompt:
  - `what does README.md say crush_py is built for?`
- Outcome:
  - Successful answer.
  - Correctly grounded in `README.md`.
- Session reviewed:
  - `8ba2dfe0-1be5-4132-bef4-3c48308bc49a`

### Earlier comparison smoke test
- Prompt:
  - `according to README.md, what is crush_py built for?`
- Outcome:
  - Successful answer after doc-QA fast-path tightening.
- Session reviewed:
  - `303a1b42-6b92-4f11-88c3-99db4d7cab81`

---

## What We Learned So Far

### 1. Phrase-specific routing is still too brittle
- Similar questions with different wording still take different internal paths.
- Example:
  - `what does README.md say ...` used a better full-file read path.
  - `from README.md, can you show me ...` still used a less consistent path.
- This confirms that prompt-pattern engineering alone is not a great long-term strategy.

### 2. Session history quality improved, but is still not ideal
- The new dedupe logic removed repeated identical `tool_result` records.
- However, direct-file success cases still often store:
  - reader summary
  - final assistant message
- These are often nearly identical.

### 3. The real file/tool path is decoding Chinese correctly
- `CatTool` reads `README.md` correctly as UTF-8.
- Session JSONL records also preserve the Chinese correctly.
- This suggests that earlier mojibake observations were likely caused by display/shell rendering on some paths, not by core file decoding logic.

### 4. Direct-file doc questions are a very common and valuable fast path
- Questions like:
  - `from README.md, ...`
  - `what does README.md say ...`
  - `read README.md and tell me ...`
- should ideally route to a smart, compact, evidence-backed doc-QA flow.

### 5. We want semantic routing, not phrase memorization
- The system should recognize intent from meaning, not from a narrow list of trigger strings.
- This is the main motivation for the tiny LLM intent router.

---

## Main Problem Now

### Heuristic routing is becoming costly to maintain
- We already have many useful runtime behaviors.
- But the decision of which flow to use is still partly driven by handcrafted string-pattern rules.
- This makes the system:
  - harder to extend
  - less consistent across wording variations
  - vulnerable to overfitting on test phrases

### Why this matters
- A read-helper should feel stable when the user rephrases the same question.
- If two semantically equivalent questions take different routes and produce noticeably different quality, the UX feels unreliable.

---

## Proposed Next Step

## Tiny LLM Intent Router

### Objective
- Use a small LLM with a short prompt as an intent decision agent.
- The router should decide which runtime flow to use.
- The router should not answer the user's question itself.

### Why this is promising
- It should generalize better across wording variations.
- It reduces the need for expanding fragile pattern lists such as:
  - `according to`
  - `based on`
  - `from README.md`
  - `what does ... say`
- It gives us a cleaner separation between:
  - routing
  - reading
  - answering

### Recommended shape
- Keep deterministic facts first:
  - direct file path detected or not
  - path exists or not
  - code file vs non-code file
- Then ask the tiny router to classify the request.

### Suggested output format
- Strict JSON only.
- Example:

```json
{
  "intent": "direct_file_doc_qa",
  "confidence": "high",
  "target_path": "README.md",
  "needs_full_cat": true
}
```

### Candidate intent labels
- `direct_file_summary`
- `direct_file_doc_qa`
- `direct_file_trace`
- `direct_file_flow_trace`
- `direct_file_variable_trace`
- `guide`
- `repo_search`
- `general_qa`

### Guardrails
- If JSON parsing fails:
  - fall back to existing heuristic logic
- If confidence is low:
  - choose the safer, less-assumptive flow
- The router should never produce the actual user-facing answer

---

## Implementation Plan

### 1. Introduce a minimal intent-router module
- Create a small routing helper that takes:
  - user prompt
  - detected direct-file path, if any
  - file-type facts
- Return a tiny structured decision object.

### 2. Keep deterministic facts outside the LLM
- File existence and file type should still be determined in code.
- The LLM should classify intent, not inspect the filesystem itself.

### 3. Use router decisions only for routing
- The router should choose the runtime flow.
- The reader/planner/guide logic should remain the execution layer.

### 4. Preserve heuristic fallback
- Existing prompt-intent heuristics should remain available as fallback during rollout.
- This lowers migration risk.

### 5. Add smoke tests with paraphrases
- We should keep testing semantically similar prompts with different surface forms.
- Examples:
  - `from README.md, can you show me what crush_py is?`
  - `what does README.md say crush_py is built for?`
  - `read README.md and tell me what this project is for`

---

## Acceptance Checks
1. Semantically similar direct-file doc questions choose the same or similar high-quality flow.
2. The router output is structured and parseable.
3. Router failures fall back safely to existing heuristics.
4. Smoke-test session records become more consistent across prompt wording.
5. We reduce prompt-pattern engineering rather than simply adding more trigger phrases.

---

## Experimental Results

### A/B evaluation setup
- We ran a small paraphrase smoke-test comparison for direct-file doc-QA prompts grounded in `README.md`.
- We compared:
  - `before`: heuristic-only routing
  - `after`: tiny intent router enabled
- We checked:
  - route consistency
  - answer quality
  - session-record consistency

### Prompt set used
- `from README.md, can you show me what crush_py is?`
- `what does README.md say crush_py is built for?`
- `according to README.md, what is crush_py built for?`
- `read README.md and tell me what this project is for`
- `using README.md, explain what crush_py is`
- `based on README.md, what is crush_py for?`
- `please read README.md and say what crush_py does`
- `from README.md, what kind of tool is crush_py?`

### Aggregate results

#### Before: heuristic-only routing
- Case count:
  - `8`
- Planner used cases:
  - `8`
- Reader `doc_qa` cases:
  - `2`
- Average quality score:
  - `4.25 / 5`
- Average message count:
  - `6.25`
- Distinct route variants observed:
  - `4`

#### After: tiny intent router enabled
- Case count:
  - `8`
- Planner used cases:
  - `8`
- Reader `doc_qa` cases:
  - `8`
- Average quality score:
  - `4.75 / 5`
- Average message count:
  - `6.0`
- Distinct route variants observed:
  - `1`

### What changed

#### 1. Route consistency improved clearly
- Before, semantically similar README doc-QA prompts split across multiple flows:
  - `doc_qa`
  - `summary`
  - generic direct-file reader paths without stable mode tagging
- After, all 8 prompts routed through the same `direct_file_doc_qa`-style path.
- This is the strongest signal that the tiny router is reducing wording sensitivity.

#### 2. Answer quality improved modestly but meaningfully
- Average quality score increased from:
  - `4.25`
  - to `4.75`
- The most important qualitative improvement:
  - one heuristic-only case failed badly for
    - `from README.md, what kind of tool is crush_py?`
  - the answer asked the user to provide `README.md` content instead of answering from the file
- With the tiny router enabled, that same style of question produced a grounded answer describing `crush_py` correctly as a read-focused repository helper.

#### 3. Session records became more uniform
- Before, session traces varied more:
  - different reader modes
  - one extra reader `cat` retry in a failing case
  - more variability in message patterns
- After, session traces were much more regular:
  - same reader mode
  - same basic message shape
  - same tool-use pattern across all 8 prompts
- This should make later debugging and smoke-test comparison easier.

### Interpretation
- The tiny intent router appears to be a net improvement.
- The clearest improvement is:
  - routing robustness across paraphrases
- There is also:
  - a smaller but real improvement in answer quality
  - noticeably better session-record consistency
- Output style is still not perfectly uniform.
- Some answers are still more paragraph-like while others are more list-like.
- That looks like a response-formatting issue more than a routing issue.

### Current conclusion
- The tiny intent router is worth keeping.
- It already appears to outperform phrase-specific heuristic routing for direct-file doc-QA paraphrases.
- The next useful expansion would be:
  - more paraphrase cases
  - side-by-side session-record comparisons
  - possible follow-up cleanup for answer-style consistency

---

## Open Questions
- Should direct-file doc-QA always use `cat(full=True)` for small doc files like `README.md`?
- Should direct-file successful answers still store both:
  - reader summary
  - assistant final message
- Should we expose router confidence in debug trace mode for easier inspection?

---

## Recommended Immediate Next Task
- Prototype the tiny LLM intent router behind a fallback path.
- Start with routing only for:
  - direct-file doc-QA
  - direct-file summary
  - direct-file trace
- Then compare smoke-test session records before and after.
