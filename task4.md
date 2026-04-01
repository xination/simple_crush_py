# 🧩 task4.md

## 🎯 這份文件的目的

- 承接 `task3.md` 完成後的實際觀察
- 累積目前對 `crush_py` 單檔摘要 flow 的已知經驗
- 明確指出下一輪真正的主戰場已從：
  - `routing / planner flow`
  - 轉到
  - `summarization quality / responsibility ranking`
- 讓新 session 可以直接接手，不用重新摸索上下文

---

## ✅ 目前已完成的進展

### 1. flow 已大致合理

目前 direct-file summary 的主流程已經從偏繞路的版本，收斂成：

```text
planner -> reader -> cat(full or paged) -> reader summary -> assistant
```

已經去掉前一版明顯不理想的行為，例如：

- reader 固定先 `get_outline`
- reader summary 後 planner 又去跑 `ls .`
- planner 在單檔摘要任務中繼續探索 repo

### 2. coverage 問題已顯著改善

這一輪已加入：

- `cat(full=true)` 讀完整檔
- 若不適合整檔讀，則改為分頁補讀
- reader summary 帶 `coverage`
  - `complete`
  - `partial`

因此，前一版那種：

- 只讀前 80 行
- 卻假裝是完整摘要

的問題，現在已不是主問題。

### 3. partial / complete 已可區分

目前系統已能表達：

- 檔案完整覆蓋後再做正式 summary
- 若只做部分覆蓋，則標記為：

```text
Preliminary summary (partial file coverage).
```

這對 planner/assistant 的後續行為很重要。

---

## 📌 現在真正的主要瓶頸

### 問題不是「沒讀完」

現在更大的問題是：

> 檔案即使讀完了，小模型仍可能把 helper / utility / implementation detail 誤當成主責任。

也就是說，新的瓶頸是：

- `summary drift`
- `responsibility ranking` 不夠穩
- 模型沒有把：
  - `core responsibility`
  - 和
  - `supporting detail`
  - 清楚分開

### 典型錯誤型態

小模型容易出現：

- 看到明確 helper function，就抬成 top-level bullet
- 看到 metadata accessor，就誤判成整個 file 的主責任
- 沒有把 main class 的整體工作放在最高權重

這種錯不在 coverage，而在 summarization policy。

---

## 🧠 關鍵洞察

### 1. `summary` 不是夠精確的任務描述

如果只跟 reader 說：

```text
Summarize the file.
```

小模型很容易：

- 平均抓點
- 對小函式過度加權
- 做出「看起來有內容，但主軸不對」的摘要

### 2. 應改成 `main responsibilities`

下一輪應將 reader 任務定義從一般摘要改成：

> Identify the 3 main responsibilities of the file, focusing on the primary class/module behavior, not minor helper utilities.

這樣模型比較有機會：

- 先排序
- 再摘要

### 3. helper suppression 應該是硬規則

不要只寫成模糊建議。

應明確規定：

> Do not use trivial helper functions, formatting helpers, or small metadata accessors as standalone responsibilities unless they are central to the file’s purpose.

這一條很重要。

### 4. evidence 必須是強制欄位

如果每一點 responsibility 都沒有綁到：

- class 名稱
- method 名稱
- 具體 evidence

小模型就更容易漂移。

---

## ✅ 下一輪建議的核心目標

### 目標 1：把 reader 從「摘要器」改成「責任摘要器」

reader 不應再被要求輸出 generic summary。

reader 應輸出：

- exactly 3 main responsibilities
- 每點都對應主體與 evidence
- 優先說明「這個檔案整體是做什麼」

### 目標 2：壓制 helper 漂移

要明確避免以下情況：

- `utc_now_iso()` 被抬成一整點主責任
- metadata accessor 被抬成一整點主責任
- 單一小 utility 被當成 file 的核心價值

### 目標 3：assistant 不要再自由重寫太多

目前風險之一是雙重漂移：

1. reader 漂一次
2. assistant 再漂一次

因此建議加入明確規則：

> If reader output already matches the user request, lightly reformat it instead of resummarizing.

---

## 🛠️ 建議實作方向

## 1. reader 任務規格改寫

建議改成類似下面的 prompt：

```text
Read the full file and produce exactly 3 main responsibilities.

Rules:
1. Focus on the file’s primary purpose, not minor helper functions.
2. Prioritize the main class or module-level behavior.
3. A helper/utility should not become a top-level responsibility unless the whole file mainly exists for it.
4. Each responsibility must be supported by concrete evidence such as class names, key methods, or repeated patterns in the file.
5. Prefer responsibilities that explain what the file is for from a user/developer perspective.
6. Before finalizing, check whether any chosen bullet is merely a helper detail. If so, replace it with a broader responsibility.

Output format:
1. <main responsibility>
   Evidence: <class/method names>
2. <main responsibility>
   Evidence: <class/method names>
3. <main responsibility>
   Evidence: <class/method names>
```

## 2. assistant handoff rule

若 reader 已輸出符合 user 任務的 3 點內容，assistant 應：

- 盡量保留 reader 的三點
- 只做最小格式整理
- 不重新自由摘要

## 3. trace / session store metadata 可保留 coverage

reader tool result metadata 可延續：

```json
{
  "coverage": "complete"
}
```

或：

```json
{
  "coverage": "partial"
}
```

但 user-facing wording 應更接近任務本身。

---

## 🧪 下一輪建議測試

### 必加測試

1. 單檔 responsibility summary 不會把 trivial helper 當成 top-3
2. 每一點 summary 都要附 evidence
3. assistant 在 reader 已符合 user task 時只做輕量 reformulation
4. partial coverage 時會保留 `Preliminary summary`
5. helper-heavy 檔案與 true utility file 不會被錯誤壓制

### 建議測試名稱

- `test_direct_file_summary_prefers_main_responsibilities_over_helpers`
- `test_direct_file_summary_requires_evidence_for_each_bullet`
- `test_assistant_reuses_reader_responsibility_summary_when_sufficient`
- `test_partial_direct_file_summary_preserves_preliminary_label`
- `test_utility_file_can_still_surface_helper_as_primary_responsibility`

---

## 🔍 驗收標準

### 對這種 prompt：

```bash
python -m crush_py --prompt "請讀 crush_py/store/session_store.py，用 3 點說明它負責什麼。"
```

理想結果應滿足：

- trace 仍然很短
- reader 已讀完整檔或清楚標示 partial
- summary 聚焦於：
  - session metadata/state 管理
  - message persistence / session storage flow
  - supporting metadata handling as subordinate responsibility
- 不會把單一 helper（如時間函式、accessor）抬成主責任

### 理想回答風格

比起這種不理想版本：

- 管理 session data
- 取得第一個 tool name
- 處理 UTC time

更應接近：

1. 管理 session 的基本資料與狀態  
   Evidence: `SessionMeta`, session id/title/backend/model/timestamps

2. 負責將 session 訊息持久化保存並重建  
   Evidence: `create_session`, `append_message`, `load_messages`, `session.json`, `messages.jsonl`

3. 提供 session 讀寫流程所需的 metadata 整理與輔助處理  
   Evidence: `_sanitize_metadata`, `_write_meta`, `_session_dir`, helper utilities

---

## 🧱 新 session 應優先記住的事

如果從新 session 開始，請先記住：

1. `task3.md` 已經把 flow 與 coverage 大致修到合理，不要輕易打回舊流程。
2. 現在最重要的不是再加工具，而是提高 responsibility summary 的穩定度。
3. 下一輪應該優先改 prompt / policy / handoff，而不是再動 planner routing。
4. 單檔摘要的真正風險是：
   - helper 被誤升級
   - assistant 二次漂移
5. 新增規則時，要同時保護：
   - 一般 class/module file
   - 真正以 utility/helper 為主的 file

---

## ✅ 一句話總結

`task3` 解掉了「流程太繞」與「只讀部分就收工」；`task4` 的任務是解掉「讀完之後，還是不知道什麼才是這個檔案真正的主責任」。
