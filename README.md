# 🧭 crush_py

以 **Python 3.9** 為優先、專門給 **3B 級本機小模型** 使用的 repo 閱讀助手。

## ✨ 這個版本的定位

- 📚 以 **read-only repo exploration** 為核心
- 🧵 主要任務是：
  - instruction Q&A
  - Python code tracing
  - C++ code tracing
- 🧠 設計重點不是功能很多，而是：
  - tool 選擇要少
  - context 要穩
  - 小模型不要被大型輸出撐爆

## 🛠️ 核心工具

- `ls`
- `tree`
- `find`
- `grep`
- `get_outline`
- `cat`

這些工具的預期流程是：

1. `tree` / `ls` 看區域
2. `find` 找檔
3. `grep` 找符號或字串
4. `get_outline` 確認 symbol 範圍
5. `cat` 讀局部區塊
6. 再回答

## 🔍 適合的問法

- 💬「這個 repo 的主要結構是什麼？」
- 💬「幫我 trace Python 裡 `main()` 之後呼叫了哪些函式。」
- 💬「幫我找 C++ 裡 `trace_me` 的定義與呼叫點。」
- 💬「這個設定值在哪裡被讀取？」
- 💬「請根據實際檔案內容回答，不確定的地方要明說。」

## 🚀 快速開始

1. 準備 `config.json`
2. 確認 LM Studio / OpenAI-compatible API 可連線
3. 在此目錄執行：

```bash
python -m crush_py
```

### CLI summary 用法

```bash
python -m crush_py --summarize README.md
python -m crush_py --summarize-brief README.md
```

- `--summarize`：direct-file summary 會走 `review draft mode`
- `--summarize-brief`：direct-file summary 會走 `brief summary mode`
- `--prompt "quickly summarize README.md"` 也會走 `brief summary mode`

### CLI trace 用法

```bash
python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"
python -m crush_py --trace "how prompt flows inside crush_py/agent/runtime.py"
```

- `variable trace` 目前會優先輸出：
  - `Variable`
  - `Confirmed file`
  - `Coverage`
  - evidence-backed sections
- `flow trace` 目前會優先輸出：
  - `Target`
  - `Confirmed file`
  - `Coverage`
  - `Reviewed symbol`
  - `Reviewed lines`
  - evidence-backed flow sections
- `summary`、`variable trace`、`flow trace` 都共用 backend timeout，預設是 `600s`

## 🧾 Trace 報告風格

- ✅ 盡量使用 qualname，例如 `SessionStore.create_session`、`AgentRuntime.ask`
- ✅ `Coverage` 會明確標示是不是只看了 local reviewed block
- ✅ `flow trace` 會區分：
  - entry point
  - local transformation
  - storage or persistence
  - downstream handoff
- ✅ 最後會保留 `Unresolved uncertainty`
- ❌ 不做跨函式 dataflow 幻覺推論
- ❌ 不把自然語言摘要寫得比證據更大聲

### 範例：variable trace

```text
Variable trace for human review:

Variable: session_id
Confirmed file: crush_py/store/session_store.py
Coverage: local (reviewed `SessionStore.create_session` block only)

1. Defined or first assigned at line 32 inside `SessionStore.create_session`
   Evidence: `session_id = str(uuid.uuid4())`

2. Reassignment
   No confirmed reassignment in the reviewed block.
```

### 範例：flow trace

```text
Flow trace for human review:

Target: prompt
Confirmed file: crush_py/agent/runtime.py
Coverage: local (reviewed `AgentRuntime.ask` block only)
Reviewed symbol: AgentRuntime.ask
Reviewed lines: 66-109

1. Entry point at line 66 inside `AgentRuntime.ask`
   Evidence: `def ask(self, prompt: str, stream: bool = False) -> str:`

2. Confirmed local transformation at line 76 inside `AgentRuntime.ask`
   Evidence: `state.entry_point = prompt.strip()`

3. Confirmed storage or persistence at line 78 inside `AgentRuntime.ask`
   Evidence: `self.session_store.append_message(session.id, "user", prompt)`

4. Confirmed downstream handoff at line 80 inside `AgentRuntime.ask`
   Evidence: `system_prompt = self._system_prompt_for_prompt(prompt)`
```

## 💬 REPL 指令

- `/help`
- `/new`
- `/sessions`
- `/use <session_id>`
- `/backend`
- `/tools`
- `/ls`
- `/tree`
- `/find`
- `/grep`
- `/cat`
- `/history`
- `/trace`

## 📘 常用指令格式

### 📂 `/tree [PATH] [DEPTH]`

```bash
/tree crush_py 2
```

### 🗂️ `/ls [PATH] [DEPTH]`

```bash
/ls crush_py/tools 1
```

### 🔎 `/find PATTERN [PATH]`

```bash
/find "*.py" crush_py
```

### 🧵 `/grep PATTERN [PATH] [INCLUDE]`

```bash
/grep "SessionStore" crush_py "*.py"
```

### 🧭 `/get_outline PATH`

`get_outline` 是 internal read tool，通常由 runtime 自動使用，不一定需要手動叫。

### 📄 `/cat PATH [OFFSET] [LIMIT]`

```bash
/cat crush_py/store/session_store.py 0 80
```

## ⚙️ 範例設定

- backend timeout 建議值：`600s / 10min`

```json
{
  "workspace_root": ".",
  "sessions_dir": ".crush_py/sessions",
  "default_backend": "lm_studio",
  "backends": {
    "lm_studio": {
      "type": "openai_compat",
      "model": "google/gemma-4-26b-a4b",
      "base_url": "http://192.168.40.1:1234/v1",
      "api_key": "not-needed",
      "timeout": 600,
      "max_tokens": 2048
    }
  }
}
```

## 🧪 執行測試

```bash
python -m unittest discover -s tests
python -m unittest tests.test_tools -q
python -m unittest tests.test_runtime -q
```

### 建議 smoke tests

```bash
python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"
python -m crush_py --trace "the variable default_backend in crush_py/config.py"
python -m crush_py --trace "how prompt flows inside crush_py/agent/runtime.py"
```

## 📎 補充文件

- [`session_2026-04-04_learnings.md`](session_2026-04-04_learnings.md)
- [`task8.md`](task8.md)
- [`task9.md`](task9.md)
- [`archieve/summary.txt`](archieve/summary.txt)
- [`archieve/plan.md`](archieve/plan.md)
- [`archieve/NEXT.md`](archieve/NEXT.md)

## Guide mode

```bash
python -m crush_py --guide "summarize README.md for a beginner"
python -m crush_py --guide "turn README.md into a checklist"
python -m crush_py --guide "which parts of README.md should a beginner read first?"
python -m crush_py --guide "I am stuck at step 3 in README.md"
```

- `--guide` adds a beginner-friendly docs mode for workspace-local instruction files.
- It prefers repo-local docs, plain-language explanations, and action-oriented output.
- When the prompt names one concrete doc, the runtime reads that file first and includes source hints with file and line clues.
- v1 stays intentionally lightweight: local read tools only, no embeddings, no external web knowledge, and no cross-repo retrieval.

### Multi-turn guide Q&A demo

```text
Q1: summarize README.md for a beginner

A1:
- Goal: explain what the project is for.
- You will accomplish: understand the tool's scope before using it.
- Prepare first: check the required Python version and backend setup.
- Main steps: read the overview, setup notes, and CLI examples first.
- Common beginner confusion: the project is read-only and does not act like a general web assistant.
- Sources: README.md:1-31

Q2: turn README.md into a checklist

A2:
1. Read the project overview and scope.
2. Confirm the required runtime and backend settings.
3. Try the documented CLI commands in the suggested order.
Success check: you can run one example command and understand what each read tool is for.
Sources: README.md:1-31

Q3: I am stuck during setup in README.md

A3:
- Likely current step: the initial environment or backend configuration stage.
- Relevant source section: the setup section and example commands.
- Possible causes: missing Python version, backend not configured, or running from the wrong workspace.
- What to check first: compare your local setup with the documented config example.
- What to do next: fix the missing prerequisite, then rerun the first example command.
- Sources: README.md:19-31, README.md:97-113
```

### CLI `--session` smoke demo

```bash
python -m crush_py --guide "summarize README.md for a beginner"
python -m crush_py --session <session_id> --guide "turn README.md into a checklist"
python -m crush_py --session <session_id> --guide "I am stuck during setup in README.md"
```

- The first command creates the session and writes a new session folder under `.crush_py/sessions/`.
- Use that generated session id in the later commands so the follow-up guide questions stay in the same conversation.
- This is the simplest smoke test for multi-turn guide behavior on one local doc.
