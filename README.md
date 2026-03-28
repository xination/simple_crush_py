# 🧠 crush_py

一個以 **Python 3.9** 為優先、適合較舊環境與 air-gap 情境的 terminal coding assistant。

## 🎯 這份 README 的定位

- 👋 給第一次進來的人快速了解這是什麼
- 🚀 告訴你怎麼啟動、怎麼跑測試、怎麼操作 REPL
- 📎 需要更深入的規劃與待辦時，再去看：
  - [`NEXT.md`](NEXT.md)
  - [`plan.md`](plan.md)
  - [`LM_STUDIO_CHECKLIST.md`](LM_STUDIO_CHECKLIST.md)

## ✨ 目前可用功能

- 🤖 backend
  - `anthropic`
  - `openai_compat`
- 👀 read-only tools
  - `view`
  - `ls`
  - `glob`
  - `grep`
- ✍️ mutating tools
  - `write`
  - `edit`
- 🖥️ shell tool
  - `bash`
- 📜 session inspection
  - `/history`
  - `/trace`

## 🔐 目前安全邊界

- ✅ `view` / `ls` / `glob` / `grep` 可自動使用
- ✅ `edit` 可由模型自動提出
  - 但每次都會先顯示 preview
  - 並要求使用者逐次確認
- ✅ `bash` 也可由模型自動提出
  - 但每次都會先顯示 preview
  - 並要求使用者逐次確認
- 🚫 `write` 目前仍只開放手動 REPL 使用
- ⚠️ `openai_compat` automatic tool-calling 已完成實作與 fake tests
  - 尚待 live LM Studio 真機驗證

## 🚀 快速開始

1. 準備 `config.json`
2. 若使用預設 Anthropic backend，設定 `ANTHROPIC_API_KEY`
3. 在此目錄下執行：

```bash
python -m crush_py
```

## 🧪 執行測試

```bash
python -m unittest discover -s tests
```

## 💬 REPL 指令

- `/help`
- `/new`
- `/sessions`
- `/use <session_id>`
- `/backend`
- `/tools`
- `/ls`
- `/glob`
- `/grep`
- `/bash`
- `/history`
- `/trace`
- `/write`
- `/edit`
- `/view path/to/file.py`

## 📘 常用指令格式

### 👀 `/view PATH [OFFSET] [LIMIT]`

```bash
/view ref/ask_ai/tests/test_text_file_ai.py 200 50
```

### 🗂️ `/ls [PATH] [DEPTH]`

```bash
/ls ref/ask_ai 2
```

### 🔎 `/glob PATTERN [PATH]`

```bash
/glob "**/*.py" ref/ask_ai
```

### 🧵 `/grep PATTERN [PATH] [INCLUDE]`

```bash
/grep "AnthropicBackend" ref/ask_ai "*.py"
```

### 🖥️ `/bash COMMAND`

- 執行前會要求確認
- 會回傳 stdout / stderr / exit code

### 📜 `/history [LIMIT]`

```bash
/history 10
```

### 🧾 `/trace [LIMIT]`

```bash
/trace 10
```

### ✍️ `/write PATH`

- 在 REPL 中輸入完整內容覆寫檔案
- 結束輸入用一行：

```text
.end
```

### 🛠️ `/edit PATH`

- 依序輸入：
  - 舊文字
  - 新文字
- 每段都用 `.end` 收尾

## ⚙️ 範例設定

```json
{
  "workspace_root": "..",
  "sessions_dir": ".crush_py/sessions",
  "default_backend": "anthropic",
  "backends": {
    "anthropic": {
      "type": "anthropic",
      "model": "claude-haiku-4-5-20251001",
      "base_url": "https://api.anthropic.com",
      "api_key_env": "ANTHROPIC_API_KEY",
      "timeout": 60,
      "max_tokens": 4096
    },
    "lm_studio": {
      "type": "openai_compat",
      "model": "qwen2.5-coder-3b-instruct",
      "base_url": "http://127.0.0.1:1234/v1",
      "api_key": "not-needed",
      "timeout": 60,
      "max_tokens": 4096
    }
  },
  "permissions": {
    "ask_on_write": true,
    "ask_on_shell": true
  },
  "tools": {
    "bash_timeout": 60
  }
}
```

## 📎 相關文件

- 🥇 近期待辦與風險：
  - [`NEXT.md`](NEXT.md)
- 🥈 架構、範圍與 phase 規劃：
  - [`plan.md`](plan.md)
- 🥉 LM Studio 實機驗證：
  - [`LM_STUDIO_CHECKLIST.md`](LM_STUDIO_CHECKLIST.md)
