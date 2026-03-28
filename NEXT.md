# 🧭 Next

## 🎯 這份 NEXT 的定位

- 🥇 只放 **近期最值得做的事**
- ⚠️ 只放 **目前仍存在的限制 / 風險**
- 🚫 不重複完整架構說明
  - 架構與 phase 請看 [`plan.md`](plan.md)

## 🚀 近期優先順序

1. 🔌 **在真實 LM Studio 環境驗證 `openai_compat` automatic tool-calling**
   - 依照 [`LM_STUDIO_CHECKLIST.md`](LM_STUDIO_CHECKLIST.md) 執行
   - 這是目前最重要的「真機缺口」

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
- 🧪 `openai_compat` automatic tool-calling 已完成實作
  - 但尚未在 live LM Studio 環境完成驗證
- 📝 `write` 目前尚未納入 automatic tool-calling
- 🗃️ 此目錄目前不是獨立 git repository

## ✅ 最近已完成

- `bash`
- `/history`
- `/trace`
- final assistant raw response trace persistence
- automatic `edit` with per-call confirmation
- automatic `bash` with per-call confirmation
- `openai_compat` automatic tool-calling implementation
- `LM_STUDIO_CHECKLIST.md`
