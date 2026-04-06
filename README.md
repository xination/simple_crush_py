# crush_py

🔎 一個專門給小型本地模型使用的 **read-focused repository helper**。

它不是通用聊天助理，而是把重點放在：

- 📚 穩定地讀 repo
- 🧭 追 code flow
- 📝 摘要文件與說明
- ⚠️ 清楚標示不確定性

## ✨ 這個工具適合做什麼

- 📝 檔案摘要：`--summarize`
- 🔬 流程追蹤：`--trace`
- 📖 文件導向教學：`--guide`
- ⚡ 單檔快速問答：`--file ... --prompt ...` 或 REPL 的 `/quick`

## 🧰 核心工具

- `ls`
- `tree`
- `find`
- `grep`
- `get_outline`
- `cat`

工具使用原則：

1. 🗺️ 先用 discovery tool 縮小範圍。
2. 📌 找到具體檔案後，再用 `cat` 確認內容。
3. 🧱 `get_outline` 比較適合 code 結構，不適合拿來讀 docs。
4. ⚠️ `grep` 命中是線索，不是證明。

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
      "model": "google/gemma-3-4b",
      "base_url": "http://192.168.40.1:1234/v1",
      "api_key": "not-needed",
      "timeout": 600,
      "max_tokens": 2048
    }
  }
}
```

### 2. 啟動方式

#### 方式 A：直接用 Python

```bash
python -m crush_py
```

#### 方式 B：用 `run.sh`

適合 Bash / WSL / Linux / macOS：

```bash
/path/to/crush_py/run.sh
```

如果你在別的資料夾啟動它，`crush_py` 會保留你原本的啟動位置作為 `workspace_root` 解析基準。

#### 方式 C：用 `run.tcsh`

適合 `tcsh` 環境：

```tcsh
/path/to/crush_py/run.tcsh
```

和 `run.sh` 一樣，就算你是在外部資料夾啟動，也會盡量以你原本所在資料夾作為工作根目錄。

### 3. 常用單次指令

```bash
python -m crush_py --summarize README.md
python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"
python -m crush_py --guide "turn README.md into a checklist"
python -m crush_py --file README.md --prompt "show me how to start" --stream
```

## 🧠 模式說明

### 📝 `--summarize`

```bash
python -m crush_py --summarize README.md
```

- 回傳短版重點摘要
- 適合問「這個檔案主要在做什麼」
- 不適合拿來做精細 trace

### 🔬 `--trace`

```bash
python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"
python -m crush_py --trace "how prompt flows inside crush_py/agent/runtime.py"
```

trace 原則：

- ✅ 區分 `confirmed` / `likely` / `unknown`
- ✅ 寧可誠實給局部答案，也不要硬補完整故事
- ✅ `grep` 只算 lead，不算 proof

### 📖 `--guide`

```bash
python -m crush_py --guide "summarize README.md for a beginner"
python -m crush_py --guide "turn README.md into a checklist"
python -m crush_py --guide "I am stuck during setup in README.md"
```

適合：

- 👶 beginner-friendly 說明
- ✅ checklist
- 🧰 troubleshooting
- 🪜 onboarding / learning path

## 💻 REPL 指令

- `/help`
- `/new`
- `/sessions`
- `/use <session_id>`
- `/info`
- `/tools`
- `/ls`
- `/tree`
- `/find`
- `/grep`
- `/cat`
- `/history`
- `/trace`
- `/quick`

### ℹ️ `/info`

`/info` 會顯示目前狀態：

- `Session`
- `Backend`
- `Model`
- `Workspace Root`
- `Sessions Dir`
- `Trace Mode`

### ⚡ `/quick`

格式：

```text
/quick @PATH, PROMPT
```

規則：

- 🧷 第一個逗號前是檔案路徑
- 📝 第一個逗號後全部都算 prompt
- 🚿 永遠用 stream 輸出
- 🧠 不會把先前對話當成 model context

範例：

```text
/quick @README.md, show me how to start
/quick @README.md, show me the key facts in Traditional Chinese
```

## 🗂️ Session 與寫入安全性

預設 `sessions_dir` 來自 `config.json`。

如果設定的位置不可寫，`crush_py` 會自動 fallback：

1. `config.json` 指定的 `sessions_dir`
2. `~/.crush_py/sessions`
3. 系統 temp 目錄下的 `.crush_py/sessions`

如果有發生 fallback，啟動時會印出 warning。

## 🧪 `trace_mode` 選項

目前實際可用的選項有 2 個：

- `lean`
- `debug`

差異：

- 🪶 `lean`
  - 平常使用建議
  - session metadata 比較精簡
- 🛠️ `debug`
  - 保留更多除錯資訊
  - quick-file cache 的 `miss` / `hit` metadata 也會保留

## 🧪 測試

```bash
python -m unittest discover -s tests
python -m unittest tests.test_tools -q
python -m unittest tests.test_runtime -q
```

推薦 smoke tests：

```bash
python -m crush_py --summarize README.md
python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"
python -m crush_py --trace "how prompt flows inside crush_py/agent/runtime.py"
python -m crush_py --guide "turn README.md into a checklist"
```

## 📎 相關文件

- [smoke_tests/README.md](smoke_tests/README.md)
