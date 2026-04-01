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
- 預設 backend 為 **LM Studio (`lm_studio`)**
- 同時保留後續擴充：
  - Anthropic Messages API
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
  - `lm_studio`
  - `anthropic`
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
- 目前已加入較瘦的 prompt 與較強的 routing 控制：
  - read-only / mutating 動態工具子集
  - sliding window 式歷史裁切
  - 單一候選檔時的 `glob/grep -> view` routing guard
  - 重複失敗的 `bash/edit` retry guard
  - 目標是降低 repo 導覽時的路徑猜測、幻覺與無效重試

### 🔌 Backends

- `lm_studio`
  - 預設 backend
  - 底層型別為 `openai_compat`
  - 已完成 payload / parser / tool-calling 實作
  - 已完成 live LM Studio 驗證
  - tool-calling request 目前會使用較小的 `max_tokens`
  - tool result 目前會先做截斷，避免 payload 過胖
  - 目前主要強化方向是 read-only tool selection 穩定性
- `anthropic`
  - 已支援多輪 messages 與 tool loop
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
  - read-only 問題：
    - `view`
    - `ls`
    - `glob`
    - `grep`
  - 明確修改 / 執行問題：
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
- live LM Studio validation
- 狀態：✅ 已完成

### Phase 7. Hardening

- tests
- air-gap / setup 文件
- LM Studio checklist
- read-only tool selection stability
- context compaction / payload slimming
- multi-run benchmark runner 與 aggregate comparator
- 狀態：🟡 進行中

### Phase 8. HF Local Evaluation

- 先保留 stub
- 再依實際環境決定是否正式支援
- 狀態：⏸️ 尚未開始

## 🔮 候選後續方向

- Unified diff 形式的 `edit` preview
- `/trace` 類型過濾與更細的檢視模式

## 🧪 Benchmark Hardening Plan

### 為什麼要加 multi-run

- small local model 的 routing 很容易受微小隨機性影響
- 單次 benchmark 容易被 lucky run / unlucky run 誤導
- 我們真正想優化的是：
  - prompt 與 context engineering 是否讓簡單任務更穩
  - tool routing 是否更一致
  - 是否更常在回答前真的去讀對檔案

### Multi-run benchmark runner 目標

- 同一組 benchmark case 可連續跑多輪
- 每輪仍維持：
  - 每個 case 使用 fresh session
  - 保留單輪細節結果，方便事後抽查
- 額外輸出 aggregate 區塊，讓我們看：
  - `first_tool` 是否集中
  - `used_view` 比率是否上升
  - `tool_call_count` 是否下降或更穩
  - `locator_tool_count` 是否下降或更穩
  - error rate 是否下降
  - final answer 是否出現過多變體

### Aggregate payload 規劃

- benchmark result JSON 保留原本單輪可讀性
- 當 runner 啟用 multi-run 時，輸出：
  - `requested_runs`
  - `completed_runs`
  - `runs`
    - 每輪個別 `results`
    - 每輪摘要
  - `aggregate`
    - `overall`
    - `cases`
- `aggregate.cases[]` 每個 case 至少包含：
  - `id`
  - `run_count`
  - `success_count`
  - `error_count`
  - `error_rate`
  - `used_view_count`
  - `used_view_rate`
  - `first_tool_counts`
  - `first_tool_mode`
  - `avg_tool_call_count`
  - `avg_locator_tool_count`
  - `answer_variant_count`
  - `tool_sequence_variant_count`

### Aggregate comparator 目標

- 比較兩份 multi-run aggregate 結果，而不是只比單次 run
- 主要想回答：
  - 候選 prompt/runtime 是否讓 `view` 使用率更高？
  - `first_tool` 是否更集中到合理路徑？
  - 平均 tool call 是否下降？
  - error rate 是否下降？
  - case 的跨輪波動是否變小？
- comparator 應輸出：
  - `error_rate_deltas`
  - `used_view_rate_deltas`
  - `avg_tool_call_deltas`
  - `first_tool_mode_changes`
  - `stability_changes`
  - `needs_manual_review`

### 實作原則

- 盡量維持 backward compatibility
- 單輪 runner 與既有 comparator 繼續可用
- multi-run 與 aggregate 以新增欄位 / 新流程擴充，不破壞既有 JSON consumer
- metric 保持簡單、可人工驗證，避免過早引入複雜評分公式
- `/history` 顯示模式擴充
- `write` 是否納入 automatic tool-calling
- 更精細的 context compaction / canonicalization
- `view` 結果的結構化摘要，而不只是截斷
- 大檔案分段閱讀策略
- session checkpoint / undo
- token-aware output truncation

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

- 預設 backend 是 **LM Studio (`lm_studio`)**
- `ref/ask_ai` 是 backend 實作參考
- `ref/crush` 是功能切分與 agent/runtime 參考
- v1 以 **REPL + JSON session + 單一 agent runtime** 為核心
- 不追求 Go 版 Crush 功能對等
- `write` 目前不自動開放給模型
- `edit` 可自動提出，但必須逐次人工確認
- `bash` 可自動提出，但必須逐次人工確認
