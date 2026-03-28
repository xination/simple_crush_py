# 🧭 `crush_py` Implementation Plan

## 🎯 這份 plan 的定位

- 🧱 說明專案的 **架構、範圍、設計原則、phase roadmap**
- 🧭 不是日常待辦清單
- 📌 近期待辦請看 [`NEXT.md`](NEXT.md)

## 🎯 目標

- 建立一個 **Python 3.9** 可用、偏向 **pure-python**、適合較舊 air-gap 環境的 terminal AI coding assistant
- 新專案不是完整重寫 Go 版 Crush，而是做一個 **保留核心工作流、明確縮小範圍** 的 Python 版
- 主要參考來源：
  - `ref/crush/`：功能切分、runtime / agent 概念參考
  - `ref/ask_ai/`：backend 與標準函式庫 HTTP 寫法參考

## 🧱 產品方向

- v1 採用 **REPL-first** CLI
- 預設 backend 為 **Anthropic Messages API**
- 同時保留後續擴充：
  - LM Studio / OpenAI-compatible backend
  - Hugging Face local backend
- session 與歷史記錄使用 **JSON / JSONL**
- 不使用 SQLite

## 🔐 設計原則

- 👀 讀取型工具可以自動使用
- ✍️ 修改型工具預設採保守策略
- 🖥️ shell 執行要確認
- 🛡️ automatic `edit` 可以存在
  - 但必須逐次人工確認
- 🛡️ automatic `bash` 也可以存在
  - 但必須逐次人工確認
- 🚫 `write` 目前不自動開放給模型

## ✂️ v1 Scope

### ✅ Include

- REPL 互動模式
- 單一 agent runtime
- session 建立、續接、列出
- JSON / JSONL persistence
- read-only tools：
  - `view`
  - `ls`
  - `glob`
  - `grep`
- mutating tools：
  - `write`
  - `edit`
- `bash`
- tool permission gate
- backend adapters：
  - `anthropic`
  - `openai_compat`
- Python 3.9 相容

### ❌ Exclude

- Bubble Tea 類 TUI
- LSP
- MCP
- SQLite / migration / sqlc
- telemetry / update checker
- OAuth / provider catalog auto-update
- 多 agent 協調
- 複雜 UI 元件與 diff view

## 🏗️ 核心架構

### 🔁 Runtime

- `agent runtime` 負責：
  - 載入 session 與歷史訊息
  - 建立 system prompt
  - 告知模型可用工具與 schema
  - 呼叫 backend
  - 解析模型輸出的 tool call
  - 分派給預先定義好的 tools
  - 將 tool result 回填給模型
  - 保存 user / assistant / tool messages
- runtime **不是 rule-based classifier**
- action 選擇主要由模型決定；runtime 是 orchestration layer

### 🔌 Backends

- `anthropic`
  - 預設 backend
  - 已支援多輪 messages 與 tool loop
- `openai_compat`
  - 主要對應 LM Studio
  - 已完成 payload / parser / tool-calling 實作
  - 尚待 live runtime 驗證
- `hf_local`
  - 目前僅 stub

### 🛠️ Tools

- read tools：
  - `view`
  - `ls`
  - `glob`
  - `grep`
- write / exec tools：
  - `write`
  - `edit`
  - `bash`

### Current automation boundary

- 自動 tool-calling 目前開放：
  - `view`
  - `ls`
  - `glob`
  - `grep`
  - `edit`
  - `bash`
- `edit` 採：
  - preview
  - user confirmation
  - 通過後才真正執行
- `bash` 採：
  - preview
  - user confirmation
  - 通過後才真正執行
- `write` 目前不自動暴露給模型

## 🧾 Config 設計

- 採用新的 `config.json`
- 不追求相容 `ref/crush/crush.json`
- 重點欄位：
  - `workspace_root`
  - `sessions_dir`
  - `default_backend`
  - `backends`
  - `permissions.ask_on_write`
  - `permissions.ask_on_shell`
  - `tools.bash_timeout`

## 🗂️ Persistence 設計

- 每個 session 一個資料夾
- 建議結構：

```text
.crush_py/
  sessions/
    <session_id>/
      session.json
      messages.jsonl
      artifacts/
```

- `messages.jsonl` 目前已保存：
  - 一般 user / assistant 訊息
  - `tool_use`
  - `tool_result`
  - final assistant raw response metadata

## 🚀 Phase Roadmap

### Phase 1. Bootstrap

- 專案骨架、CLI、基本入口
- 狀態：✅ 已完成

### Phase 2. Config + Session Store

- `config.py`
- `session_store.py`
- 狀態：✅ 已完成

### Phase 3. Anthropic Runtime + Read Tools

- `anthropic` backend
- `view` / `ls` / `glob` / `grep`
- 最小 tool loop
- 狀態：✅ 已完成

### Phase 4. Mutating Tools + Permission Gate

- `write`
- `edit`
- `bash`
- permission flow
- 狀態：✅ 已完成

### Phase 5. REPL Inspection

- `/history`
- `/trace`
- final assistant raw response trace persistence
- 狀態：✅ 已完成

### Phase 6. OpenAI-compatible Backend

- `openai_compat`
- automatic tool-calling
- parser / fake tests
- 狀態：🟡 程式完成，待 live LM Studio 驗證

### Phase 7. Hardening

- tests
- air-gap / setup 文件
- LM Studio checklist
- 狀態：🟡 已完成大部分，仍可持續強化

### Phase 8. HF Local Evaluation

- 先保留 stub
- 再依實際環境決定是否正式支援
- 狀態：⏸️ 尚未開始

## 🧪 測試方向

- config 載入
- session append / resume
- backend request payload mapping
- backend response parsing
- tool schema 與 dispatch
- permission gate
- REPL helper
- `edit` 成功 / 失敗 / 拒絕案例
- `bash` 執行與 timeout
- automatic tool loop
- OpenAI-compatible tool-call parsing

## 📝 已鎖定的重要決策

- 預設 backend 是 **Anthropic Messages API**
- `ref/ask_ai` 是 backend 實作參考
- `ref/crush` 是功能切分與 agent/runtime 參考
- v1 以 **REPL + JSON session + 單一 agent runtime** 為核心
- 不追求 Go 版 Crush 功能對等
- `write` 目前不自動開放給模型
- `edit` 可自動提出，但必須逐次人工確認
- `bash` 可自動提出，但必須逐次人工確認
