# 🧩 task3.md

## 🎯 目標

- 針對目前 `direct-file summary` flow 再做一輪瘦身
- 讓明確單檔摘要任務更適合 `Gemma3 4B` 這類小模型
- 優先解決兩個已觀察到的多餘步驟：
  - reader 在單檔摘要時不必要地先跑 `get_outline`
  - planner 在 reader 已經提供足夠摘要後，仍額外跑 `ls .`

## 📌 問題摘要

目前 direct-file case 的 trace 大致是：

1. `planner` delegate 給 `reader`
2. `reader` 跑 `get_outline`
3. `reader` 跑 `cat`
4. `reader` 產出 summary
5. `planner` 又跑一次 `ls .`
6. assistant 才回答

對這種 prompt：

```bash
python -m crush_py --prompt "請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。"
```

上面第 `2` 與第 `5` 步都很可疑，尤其對小模型來說會增加：

- tool call 次數
- token 消耗
- planner 失焦機率
- 不必要的探索行為

## ✅ 這一輪的核心方向

- **單檔摘要任務走 fast path**
- **reader 採用 cat-first，而不是固定 outline-first**
- **planner 收到足夠 reader summary 後立即停止**
- **讓 planner 更像 router，而不是 explorer**

## 1. 加入 direct-file summary fast path

### 目標

當 prompt 同時滿足以下條件時，走最短路徑：

- 使用者明確指定單一檔案
- 任務是摘要 / 說明 / explain / summarize 類型
- 不要求 cross-file trace / import chain / call flow / repo context

### 建議做法

在 `AgentRuntime` 增加一個明確判斷，例如：

- `_is_direct_file_summary_prompt(prompt) -> bool`

判斷信號可包含：

- prompt 中有 concrete file path
- 含有：
  - `summarize`
  - `summary`
  - `explain`
  - `what does`
  - `負責什麼`
  - `說明`
  - `幾點`
  - `3 點`
- 不含：
  - `trace`
  - `call path`
  - `used`
  - `where`
  - `flow`
  - `import`
  - `how X calls Y`

### 驗收標準

- direct-file summary case 不再走一般 planner 探索流程
- planner 在這類任務中不使用 `ls/tree/find/grep`

## 2. reader 改成 conditional outline，而不是固定 outline-first

### 目標

對單檔摘要任務：

- 小檔案直接 `cat`
- 只有在檔案較大或任務明確需要結構資訊時，才先 `get_outline`

### 建議做法

reader decision rule 可改成：

```text
if direct-file summary and file likely small:
    cat
elif task is structure-oriented:
    get_outline
else:
    get_outline -> optional cat
```

### 可用條件

- prompt 含：
  - `class`
  - `function`
  - `method`
  - `架構`
  - `結構`
  - `outline`
  - `symbol`
  - `哪些類別`
  - `哪些函式`
  - `彼此怎麼合作`
  - 才偏向先 `get_outline`

- 否則：
  - direct-file summary 預設先 `cat`

### 可接受的簡化版本

如果暫時不想加檔案大小判斷，也可以先做：

- `direct-file summary => cat first`
- 其餘 reader case 維持現狀

### 驗收標準

- 對 `請讀 X，用 3 點說明它負責什麼` 這類 prompt，reader trace 只剩：
  - `cat`
  - `reader summary`

## 3. planner 收到足夠 reader summary 後立即停止

### 目標

避免這種無意義尾端探索：

```json
{"kind":"tool_use","tool":"ls","args":{"path":"."},"agent":"planner"}
```

### 建議做法

加入明確 stopping rule：

> For explicit single-file summarization tasks, if the reader has returned a sufficient summary, the planner must stop and produce the final answer immediately.

### 實作方向

可以在 `_ask_with_tool_loop()` 中加一個短路條件，例如：

- 如果 `forced_cat_path` 存在
- 且 `reader_summary` 已存在
- 且任務屬於 direct-file summary
- 則 planner 不再進入 locator tool loop
- 直接產出 final answer

### 兩種可選設計

#### 設計 A：完全跳過 planner 第二階段

- planner delegate 完 reader 後
- 直接把 reader summary 當作最後答案輸出

優點：

- 最省 token
- 最穩

風險：

- 最後回答格式可能比較受 reader prompt 影響

#### 設計 B：保留 planner finalization，但禁止再用 tool

- planner 收到 reader summary 後
- 可以再生成一次 final answer
- 但 `tools=None`

優點：

- planner 仍可統一 final answer 風格

風險：

- 還是有一次額外生成成本

### 建議

先做 **設計 B**，因為比較不破壞既有架構。

### 驗收標準

- reader summary 之後，不再出現 `ls/find/tree/grep`
- direct-file summary case 的 planner tool count 降到 `0` 或 `1`（只有 reader delegate）

## 4. 讓 reader summary 帶明確完成訊號

### 目標

讓 planner 能更可靠地判定：

- 是否已足夠回答
- 是否還需要補查

### 建議做法

先用輕量版，不一定真的改成 JSON。

可新增一個 helper，例如：

- `_reader_summary_is_sufficient(summary_text, prompt) -> bool`

檢查 reader summary 是否至少包含：

- confirmed path
- concise summary
- evidence
- unresolved uncertainty

如果都有，就視為 `enough_for_user_request = true`

### 後續可升級版

若未來要更強，可考慮讓 reader 回傳結構化欄位：

```json
{
  "status": "done",
  "enough_for_user_request": true,
  "confirmed_path": "...",
  "summary": "...",
  "evidence": "...",
  "uncertainty": "..."
}
```

### 驗收標準

- planner 不再因為 reader summary 已足夠，卻誤判成還要探索

## 5. 測試計畫

### 必加測試

1. direct-file summary case 只做 reader delegate + cat
2. direct-file summary case 不會在 reader 後跑 `ls .`
3. direct-file summary case 的 final trace 明顯短於目前版本
4. structure-oriented single-file case 仍可使用 `get_outline`
5. trace/call-flow 任務不會誤走 fast path

### 建議新增的測試名稱

- `test_direct_file_summary_uses_cat_first_without_outline`
- `test_direct_file_summary_stops_after_reader_summary`
- `test_structure_prompt_can_still_use_outline`
- `test_trace_prompt_does_not_use_direct_summary_fast_path`

## 🧪 建議驗證 case

1. `python -m crush_py --prompt "請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。"`
2. `python -m crush_py --prompt "Explain what crush_py/store/session_store.py is responsible for in 3 bullets."`
3. `python -m crush_py --prompt "請說明 crush_py/store/session_store.py 裡有哪些 class 與 method。"`
4. `python -m crush_py --prompt "Trace how SessionStore appends messages."`

## ✅ 完成條件

- direct-file summary trace 變成：

```json
{"kind":"tool_use","tool":"reader","args":{"path":"crush_py/store/session_store.py"},"agent":"planner"}
{"kind":"tool_use","tool":"cat","args":{"path":"crush_py/store/session_store.py"},"agent":"reader"}
{"kind":"tool_result","tool":"cat","summary":"Read crush_py/store/session_store.py lines 1-80 ...","agent":"reader"}
{"kind":"tool_result","tool":"reader","summary":"1. ... 2. ... 3. ...","agent":"reader"}
```

- 不再固定出現 `get_outline`
- 不再出現 reader 後的 `ls .`
- direct-file summary case 更快、trace 更短、答案仍正確
