# 🧠 crush_py

一個以 **Python 3.9** 為優先、適合較舊環境與 air-gap 情境的 terminal coding assistant。

## 💡 這個工具可以幫你做什麼

- 🤝 **像在 terminal 裡多一個懂程式的助手**
  - 你可以直接問它專案裡有什麼、某段程式在做什麼、某個功能在哪裡。
- 🔎 **快速看懂陌生專案**
  - 如果你剛接手一個程式，或過一陣子回來看自己的舊 code，它可以幫你先找檔案、找關鍵字、整理脈絡。
- ✍️ **協助做小幅修改**
  - 例如改字串、調整函式、補一點註解、整理一小段程式，不用每次都自己慢慢翻。
- 🛡️ **每一步都比較安心**
  - 需要修改檔案或執行指令時，會先顯示 preview 並請你確認，不容易一口氣改太多。
- 🧰 **適合資源比較保守的環境**
  - 如果你的機器比較舊、環境比較簡單，或不方便直接用大型雲端工具，這種 terminal 方式會很實用。
- 🧪 **也適合拿來試本機模型**
  - 如果你想測試本機 LLM 或 API 相容服務，這個專案也能當成一個清楚的實驗入口。

## 🌱 第一次使用，可以先做這 3 件事

1. 📂 **先請它幫你認識專案**
   - 例如看看有哪些資料夾、哪些檔案最重要、主要功能大概放在哪裡。
2. 🔍 **再請它幫你找一個你在意的功能**
   - 例如某個指令怎麼運作、某個設定在哪裡、某段邏輯是從哪個檔案開始的。
3. ✍️ **最後再讓它幫你做一個小修改**
   - 例如改一句文字、補一個註解、整理一小段程式，先從低風險的小地方開始最舒服。

## 🪄 幾個很適合新手的問法

- 💬 「這個專案的主要結構是什麼？」
- 💬 「幫我找和某個功能有關的檔案或關鍵字。」
- 💬 「這段程式在做什麼？可以用白話解釋嗎？」
- 💬 「幫我整理這段程式，讓它比較好懂。」
- 💬 「幫我補上簡單註解，或改一小段文字。」

## 🎯 這份 README 的定位

- 👋 給第一次進來的人快速了解這是什麼
- 🚀 告訴你怎麼啟動、怎麼跑測試、怎麼操作 REPL
- 📎 需要更深入的規劃與待辦時，再去看：
  - [`NEXT.md`](NEXT.md)
  - [`plan.md`](plan.md)
  - [`LM_STUDIO_CHECKLIST.md`](LM_STUDIO_CHECKLIST.md)
  - [`benchmark/README.md`](benchmark/README.md)

## ✨ 目前可用功能

- 🤖 backend
  - `lm_studio`
  - `anthropic`
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
2. 預設會使用 LM Studio，確認 `http://192.168.40.1:1234/v1` 可連線
3. 若你要改用 Anthropic backend，再設定 `ANTHROPIC_API_KEY`
4. 在此目錄下執行：

```bash
python -m crush_py
```

## 🧪 執行測試

```bash
python -m unittest discover -s tests
```

## 📊 跑 small-model benchmark

```bash
python scripts/run_small_model_benchmark.py --config config.json
```

- benchmark prompt 集合在 [`benchmark/small_model_cases.json`](benchmark/small_model_cases.json)
- benchmark 說明在 [`benchmark/README.md`](benchmark/README.md)

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
  "default_backend": "lm_studio",
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
      "model": "google/gemma-3-4b",
      "base_url": "http://192.168.40.1:1234/v1",
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
