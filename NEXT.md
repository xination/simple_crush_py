# 🧭 Next

## 🎯 這份 NEXT 的定位

- 🥇 只放 **近期最值得做的事**
- ⚠️ 只放 **目前仍存在的限制 / 風險**
- 🚫 不重複完整架構說明
  - 架構與 phase 請看 [`plan.md`](plan.md)

## 🚀 近期優先順序

1. 🛠️ **提升 LM Studio read-only tool selection 穩定性**
   - 目前已完成：
     - core prompt 瘦身
     - small-model prompt profiles
     - read-only / mutating 動態工具子集
     - sliding window 式歷史裁切
     - tool result 截斷
     - `view` result 結構化 compaction
     - runtime-level routing guard
   - 真機 trace 已能穩定走成 `glob -> view -> answer`
   - 下一步重點是：
     - 用多個 3B / 4B 模型做固定 prompt matrix 實測
     - 繼續觀察真機穩定性
     - 視需要把 context compaction 做得更精細
   - 目標是降低：
     - 路徑猜錯
     - 找到檔案後直接憑印象摘要
     - 偶發 hallucination

2. 🧾 **定稿 `/trace` 的顯示格式**
   - assistant text
   - assistant raw content
   - `tool_use`
   - `tool_result`
   - summary / full 模式

3. ✍️ **決定 `write` 是否進入 automatic tool-calling**
   - 若要做，建議採：
     - proposal mode
     - 明確 preview
     - 每次確認

4. 📜 **強化 `/history`**
   - full multiline output
   - assistant-only
   - user-only

5. 🧼 **強化 `edit` 確認 preview**
   - 更清楚的 old/new 區塊
   - 更接近 diff 的視覺格式

## ⚠️ 當前風險 / 限制

- ⏳ Anthropic tool loop 目前仍是 **non-streaming**
- ⚠️ `openai_compat` 已完成 live LM Studio 驗證
  - plain chat 正常
  - read-only tools 可用，且已避開 `.crush_py` / `.codex` / cache / `tests` 噪音
  - 已新增 shared selection policy、動態工具子集、tool result 截斷、sliding window
  - 3B / 4B 小模型現在會自動套用較明示的 prompt profile
  - runtime routing guard 已可把單一候選檔流程收斂成 `glob/grep -> view -> answer`
  - `view` tool result 現在會先做較結構化的 compaction 再截斷
  - 但仍可能出現摘要品質偏保守，或 context compaction 不夠精細
  - automatic `edit` 同意流程正常
  - automatic `edit` 拒絕後，現在會正確停在未修改狀態
- 📝 `write` 目前尚未納入 automatic tool-calling
- 🗃️ 此目錄目前不是獨立 git repository

## ✅ 最近已完成

- live LM Studio validation
- clearer tool schema and path guidance for LM Studio
- shared read-only tool selection policy
- slimmer core prompt and dynamic tool subsets
- sliding window history trimming
- runtime routing guard for `glob/grep -> view`
- tool result truncation and lower tool-call `max_tokens`
- small-model prompt profiles
- structured `view` tool-result compaction
- repeated `bash/edit` failure guard
- automatic `edit` rejection flow hard-stop
- `bash`
- `/history`
- `/trace`
- final assistant raw response trace persistence
- automatic `edit` with per-call confirmation
- automatic `bash` with per-call confirmation
- `openai_compat` automatic tool-calling implementation
- `LM_STUDIO_CHECKLIST.md`
