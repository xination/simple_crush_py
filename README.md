# crush_py

一個專門給小型本地模型使用的 **read-focused repository helper**。

它的目標不是當通用聊天助理，而是用一組很小、很穩定的 read-only 工具，幫模型更可靠地閱讀 repo、追 code flow、整理文件內容。

## 🎯 專案定位

- 🔍 以 **read-only repo exploration** 為核心
- 🧠 針對小型模型優化，特別重視：
  - tool 選擇清楚
  - context 穩定
  - 回答要有本地證據
  - uncertainty 要誠實標示
- 🧭 主要支援 3 種任務：
  - 檔案摘要 `--summarize`
  - 程式流向追蹤 `--trace`
  - 文件導向教學 `--guide`

## 🧰 核心工具

- `ls`
- `tree`
- `find`
- `grep`
- `get_outline`
- `cat`

### 工具角色

- 🗺️ **discovery tools**
  - `ls` / `tree`：看結構
  - `find`：找檔名
  - `grep`：找符號、字串、候選位置
  - `get_outline`：看 code symbol 結構
- 📌 **evidence tool**
  - `cat`：確認實際內容，作為最終證據

### 使用原則

1. 先用最輕的 discovery tool 縮小範圍。
2. 找到具體檔案後，再用 `cat` 讀內容。
3. `get_outline` 只用在支援的 code 檔案。
4. `README.md`、docs、config、text 等非 code 檔案，優先直接用 `cat`。
5. `grep` 命中是線索，不是證明。

## 🧠 Runtime Flow 概念

### Planner / Reader 分工

- 🧭 **Planner**
  - 先找候選檔案
  - 盡量用 `ls` / `tree` / `find` / `grep`
  - 確認到單一具體路徑後，再交給 reader
- 📖 **Reader**
  - 一次只讀一個具體檔案
  - 只用 `get_outline` / `cat`
  - 回傳檔案摘要、局部證據、未解不確定性

### Intent 分流

目前 prompt intent 已集中處理，並同時支援英文與常見繁中表達。

- `--summarize`
  - 檔案職責摘要
  - 不是 trace
  - 不是 guide
- `--trace`
  - 追變數、值、flow、handoff、storage
  - 重視 confirmed / likely / unknown
- `--guide`
  - 面向 workspace docs 的 beginner-friendly 說明
  - 適合 setup、checklist、onboarding、troubleshooting

## 🚀 快速開始

### 1. 準備 `config.json`

```json
{
  "workspace_root": ".",
  "sessions_dir": ".crush_py/sessions",
  "default_backend": "lm_studio",
  "trace_mode": "lean",
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

### 2. 啟動 REPL

```bash
python -m crush_py
```

### 3. 單次指令模式

```bash
python -m crush_py --summarize README.md
python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"
python -m crush_py --guide "turn README.md into a checklist"
```

## Tips For Better Results

- Prefer concrete file or path requests over broad repo questions.
- Good examples:
  - `python -m crush_py --summarize README.md`
  - `python -m crush_py --prompt "summarize crush_py/agent/runtime.py"`
  - `python -m crush_py --prompt "according to README.md, what is this project for?"`
  - `python -m crush_py --trace "how prompt flows inside crush_py/agent/runtime.py"`
  - `python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"`
- Less reliable prompts:
  - `python -m crush_py --prompt "what is this repo for?"`
  - `python -m crush_py --prompt "explain the project"`
- Why this helps:
  - concrete paths make it easier to gather local evidence first
  - broad repo questions may need an extra discovery step before the answer is grounded
- If you want a repo-level answer, anchor it on evidence:
  - `python -m crush_py --prompt "according to README.md, what is this repo for?"`
  - `python -m crush_py --prompt "summarize README.md and tell me what this repo is for"`

## 📝 Summary Mode

### `--summarize`

```bash
python -m crush_py --summarize README.md
```

- 回傳短版 3 點摘要
- 目標是說明「這個檔案主要負責什麼」
- direct-file summary 會優先走 `cat`
- 只有使用者明確在問 structure / class / method / architecture 時，才會偏向 `get_outline`
- 目前摘要模式統一為短版 3 點摘要

### partial coverage 行為

- 若檔案過大、reader 只能讀到部分內容，輸出會標記：
  - `Preliminary summary (partial file coverage).`
- 這個 partial 標記現在是 **以當前檔案為準**
  - 不會再被同 session 裡其他檔案的 partial 結果污染

## 🔬 Trace Mode

```bash
python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"
python -m crush_py --trace "how prompt flows inside crush_py/agent/runtime.py"
```

### Trace 的核心原則

- ✅ 區分：
  - confirmed
  - likely
  - unknown
- ✅ `grep` 命中只算 lead，不算 proof
- ✅ 寧可給誠實的局部 trace，也不要過度宣稱整條 flow
- ✅ 對 Python / C++ 都會保留動態行為的不確定性

### variable trace

重點通常會包含：

- `Variable`
- `Confirmed file`
- `Coverage`
- assignment / reassignment / pass-through / usage role
- `Unresolved uncertainty`

### flow trace

重點通常會包含：

- `Target`
- `Confirmed file`
- `Coverage`
- `Reviewed symbol`
- `Reviewed lines`
- entry point
- transformation
- storage / persistence
- downstream handoff
- `Unresolved uncertainty`

## 📚 Guide Mode

```bash
python -m crush_py --guide "summarize README.md for a beginner"
python -m crush_py --guide "turn README.md into a checklist"
python -m crush_py --guide "which parts of README.md should a beginner read first?"
python -m crush_py --guide "I am stuck at step 3 in README.md"
```

### 適合的問題

- 👶 beginner-friendly 文件說明
- ✅ checklist
- 🧰 troubleshooting
- 🪜 onboarding / learning path

### guide 行為

- 優先回答 workspace 內的文件
- 盡量用 plain language
- 盡量給 action-oriented 結構
- 會補上 `Sources:`，附檔名與行號線索

### guide summary reuse

同一份文件的 multi-turn follow-up 會盡量重用上一輪 guide summary，避免每次都重讀整份 doc。

但以下情況 **不會 reuse**，而是強制 reread：

- 上一輪 guide coverage 不是 `complete` 或 `reused`
- 使用者要求精確行號、逐字內容、引用
- 使用者明確要求重讀或看全文

也就是說：

- ✅ 完整且夠新的 guide summary 可以重用
- ❌ partial / 過時 / 不足的 guide summary 不會被硬拿來回答

### Multi-turn guide 範例

```bash
python -m crush_py --guide "summarize README.md for a beginner"
python -m crush_py --session <session_id> --guide "turn README.md into a checklist"
python -m crush_py --session <session_id> --guide "I am stuck during setup in README.md"
```

- 第一次會建立 session
- 後續用同一個 `session_id` 才能保留前文
- 這也是最簡單的 guide smoke test

## 💻 REPL 指令

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

### 常用例子

```bash
/ls crush_py/tools 1
/find "*.py" crush_py
/grep "SessionStore" crush_py "*.py"
/cat crush_py/store/session_store.py 0 80
```

### `get_outline`

- `get_outline` 是 internal read tool
- 通常由 runtime 自動選擇
- 適合看 class / function / method / symbol 結構
- 不適合用來讀 docs 或 config

## 🧪 測試

```bash
python -m unittest discover -s tests
python -m unittest tests.test_tools -q
python -m unittest tests.test_runtime -q
```

### 推薦 smoke tests

```bash
python -m crush_py --summarize README.md
python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"
python -m crush_py --trace "how prompt flows inside crush_py/agent/runtime.py"
python -m crush_py --guide "turn README.md into a checklist"
```

- 更完整的手動 smoke suite 在 [smoke_tests/README.md](smoke_tests/README.md)

## 📎 相關文件

- [task12.md](task12.md)
- [wishList.md](wishList.md)
- [smoke_tests/README.md](smoke_tests/README.md)
- [session_2026-04-04_learnings.md](session_2026-04-04_learnings.md)
- [archieve/plan.md](archieve/plan.md)
- [archieve/NEXT.md](archieve/NEXT.md)
