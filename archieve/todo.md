# 🧭 Token / Context 精簡開發計畫（更新版）

## 🎯 目前目標

- 針對目前實際使用模型 `gemma3 - 4B`，降低 `crush_py` 的 context / token 成本
- 保留「真的有讀檔」的可驗證性
- 避免小模型因為 context 爆炸、schema 過重、歷史過長而退化
- 為後續可能的 multi-agent 流程先打好基礎

## ✅ 目前已完成

- 已加入 `trace_mode`
  - `lean`：預設模式，落盤只保留必要 trace 欄位
  - `debug`：保留完整 trace
- 已精簡 `messages.jsonl` schema
  - `lean` 模式不再持久化重複的大欄位，例如 `raw_content` / `backend_content`
- 已讓 `/trace` 在 lean 模式仍可用
- 已優化 direct-file case
  - 當 prompt 直接指定檔案時，完成 `cat` 後，下一輪不再攜帶完整 tools schema
- 已改善長 `cat` 結果的壓縮方式
  - 不再只保留前段，改成保留前後段

## 🧪 真實 smoke test 結論

- 已測試：
  - `python -m crush_py --prompt "請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。"`
- 實際觀察：
  - 第一版會直接失敗，backend 回 `400 Context size has been exceeded`
  - 修正 direct-file flow 後，流程已可跑完
  - 但 `gemma3 - 4B` 對長檔仍偏脆弱，回答品質仍不夠穩
- 結論：
  - 現在的問題不只是落盤 schema
  - 更大的問題是單輪 prompt 裡仍有太多：
    - tool schema
    - 重複 user message
    - 過長 file payload

## 📌 最新判斷

- multi-agent 方向是合理的
- 但不應該立刻把目前流程原封不動拆成多 agent
- 先做 prompt / tool / history 瘦身，之後做 multi-agent 才不會把問題複製兩份

## 🔥 下一階段優先順序

### Phase 1: 繼續瘦身單 agent flow

- 精簡 tool schema
  - 濃縮 description
  - 移除每個 tool 重複出現的共同規則
  - 把共同規則收斂到 system prompt
- 精簡 system prompt
  - 改成更短的 bullet points
  - 依任務種類拆分 prompt
    - direct file explain
    - repo search
    - trace
- 合併重複訊息
  - 減少同一輪內多個重複 `user` message
  - direct-file case 優先改成單輪特化 prompt

### Phase 2: 智慧化讀檔

- 新增 `get_outline` 或 `symbol_outline` 工具
  - 先回傳 Python / C++ 檔案骨架
  - 例如 class / def / function signature / top-level symbol
- 讓模型先看 outline，再決定要不要 `cat`
- 研究是否加入簡單的 code minify / de-noise
  - 去掉空白行
  - 視情況去掉長註解
  - 但不能破壞 line number 對照能力

### Phase 3: 規劃 multi-agent

- 方向應該是 `planner + reader`，不是多個 agent 在同一 loop 裡互丟全文
- 建議最小原型：
  - `planner agent`
    - 負責決定查哪個檔、哪個 symbol、哪個工具
  - `reader agent`
    - 負責讀單一檔案或少數檔案
    - 回傳「摘要 + 證據片段 + 確認路徑」
  - `final answer step`
    - 只吃使用者問題 + reader 摘要，不直接吃全文

## 🧠 Multi-agent 設計原則

- agent 之間不要傳整份檔案
- agent 之間不要傳完整 tool schema
- agent 之間只傳：
  - confirmed path
  - summary
  - key evidence excerpts
  - unresolved branches

## 🧪 驗收標準（更新）

- `gemma3 - 4B` 下，direct-file prompt 不再容易 hit context exceeded
- `python -m crush_py --prompt "請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。"` 可穩定完成
- `messages.jsonl` 在 lean 模式維持精簡
- `/trace` 仍能回答：
  - 用了哪個 tool
  - 讀了哪個檔
  - 最後是否有實際 `cat`
- 相比現在版本：
  - backend payload 更小
  - direct-file case 回答品質更穩
  - 之後再評估 multi-agent prototype

## 🚀 建議下一個 session 直接接手的工作

1. 精簡 `tools/registry` 輸出的 tool descriptions
2. 把 system prompt 拆成較短的 task-specific prompt
3. 合併 direct-file case 的多個 `user` message
4. 新增 `get_outline` 工具
5. 再做 `planner + reader` 的 multi-agent prototype
