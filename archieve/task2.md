# 🧩 task2.md

## 🎯 目標

- 針對目前 `planner + reader` prototype 的真實問題做第二輪收斂
- 直接解決目前觀察到的兩種失敗模式：
  - backend timeout
  - planner tool loop 不收斂
- 核心方向：**state isolation + history squashing**

## 📌 下一輪實作項目

### 1. tool call 出現時，自動剝除 filler text，不讓它進歷史

- 問題：
  - 小模型常在 tool call 前輸出無用的 polite/filler 文字
  - 例如「我先看看這個檔案」「先取得 outline」之類
  - 這些內容會污染後續 history，增加 token 壓力
- 建議做法：
  - 不只靠 system prompt 禁止
  - 在 runtime / backend parser 層做保險
  - 只要 assistant turn 含有 `tool_calls`，就把 surrounding filler text 從持久化 history 中移除或壓縮
- 驗收標準：
  - trace 仍能看出 tool flow
  - 但 history 不再累積「thinking out loud」內容

### 2. planner 只接 reader summary，不再接 reader 的 raw tool results

- 問題：
  - 現在雖然有 `planner + reader` 分工
  - 但如果 planner 還是間接背到 reader 的原始 `cat` / `get_outline` 結果，context 還是會膨脹
- 建議做法：
  - planner 端只保留：
    - target path
    - reader summary
    - evidence excerpt
    - unresolved uncertainty
  - reader 的 raw tool results 只存在 reader 的內部 loop 或 trace，不再回灌給 planner prompt
- 驗收標準：
  - planner prompt token 顯著下降
  - direct-file case 不再因 reader 的 raw payload 撐大 planner context

### 3. reader 改成「最多 3 次 tool calls」而不是「只准 1 次 cat」

- 問題：
  - 現在如果限制太死，可能讀到一半就被迫停止
  - 尤其當需要：
    - 先 `get_outline`
    - 再 `cat`
    - 或 `cat` 分頁續讀
- 建議做法：
  - reader session 設定一個很小的上限，例如最多 `3` 次 tool calls
  - 允許常見模式：
    - `get_outline -> cat`
    - `cat -> cat`
    - `get_outline -> cat -> cat`
  - 超過上限就強制收尾，回傳目前可確認摘要
- 驗收標準：
  - reader 不會無限 loop
  - 但仍保有一點 pagination / follow-up 空間

### 4. `cat` 預設 chunk 從 120 降到 80

- 問題：
  - `120` 行對小模型常常太大
  - 一次 `cat` 就可能帶來很重的 token payload
- 建議做法：
  - 將 [cat.py](C:/PL/Dropbox/3_my_program/experimenting/crush_py/crush_py/tools/cat.py) 的 `DEFAULT_LIMIT` 由 `120` 調成 `80`
  - 視情況同步更新測試與 continuation hint
- 驗收標準：
  - 預設 `cat` payload 變小
  - 常見 class / function 說明任務仍可順利完成

## 🔥 核心判斷

- Gemini 的回饋與目前真實 bug 完全對齊
- 最重要的不是再縮一點 description 文案
- **最重要的是把 planner 與 reader 的 state 真正隔離**
- 如果不做 history squashing，multi-agent 只是形式上分流，token 最後還是會堆回 planner

## 🧪 建議優先驗證 case

1. `python -m crush_py --prompt "請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。"`
2. `python -m crush_py --prompt "Trace how SessionStore appends messages."`
3. direct-file case + 長檔 case 各跑一次
4. 檢查 `/trace` 是否仍能看出 planner / reader flow，但 planner prompt 已不再背 raw file payload

## ✅ 完成條件

- direct-file case 可以穩定完成
- planner 不再 hit `Tool loop exceeded the maximum number of rounds`
- reader 不再因 filler / context 膨脹而容易 timeout
- 測試維持全綠
