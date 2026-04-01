# 🧩 task5.md

## 🎯 這份文件的目的

- 承接 `task4.md` 後的新策略調整
- 把單檔摘要從「一定要一次答對」改成：
  - 有時走 `human review draft`
  - 有時走 `brief summary`
- 記錄目前已學到的實測經驗，避免新 session 重踩同樣的坑

---

## ✅ 到目前為止已經做到的事

### 1. direct-file flow 已經夠短

目前單檔摘要的主要流程已經是：

```text
planner -> reader -> cat(full or paged) -> reader output -> assistant
```

重點是：

- 不再讓 planner 到處亂探索 repo
- 單檔 prompt 會直接進 reader
- reader 會先讀檔，再產出內容

### 2. coverage 問題已經不是主戰場

目前已經有：

- `cat(full=true)` 優先完整讀檔
- 檔案太大時分段補讀
- `coverage = complete / partial`
- partial 時保留：

```text
Preliminary summary (partial file coverage).
```

所以現在最大的問題不再是「沒讀完」。

### 3. `task4` 已把 UX 往 human review 方向推進

目前 direct-file summary 已改成偏 review draft 的格式，而不是假裝 final truth。

reader 現在會輸出類似：

- candidate responsibilities
- review note
- suggested keep
- suggested review/remove

assistant 也不再對 direct-file summary 做太多二次改寫，而是盡量沿用 reader 草稿。

---

## 📌 目前真實觀察到的問題

### 1. live 3B 模型還是會抓歪主責任

即使 flow 與 prompt 都比以前合理，
實測仍然看到這種情況：

- 把 `tool arg parsing`
- 把 `utc_now_iso()`
- 把 metadata helper

抬成候選責任的一部分。

這表示：

- 問題不只是 coverage
- 也不只是 assistant 二次漂移
- 而是小模型本身在「主責任 vs helper 細節」的區分上還是不穩

### 2. human review 比硬做 ranking 更適合現階段

目前看起來，比起強迫模型一次選對 top-3，
更實際的路線是：

- 先讓模型列出可審核候選
- 保留 review hints
- 讓人類快速保留 / 刪除 / 修 wording

這樣更符合真實模型品質，也比較省工程成本。

### 3. `Evidence` 很有價值，但不是每次都該保留

目前我們學到：

- 如果是 review draft，`Evidence` 很重要
- 如果 user 明確要求：
  - `briefly`
  - `short summary`
  - `just give me a short summary`
  - `簡短`
  - `簡要`

那保留 `Evidence` 反而可能違背 user 的 UX 期待

所以後續不應該只做一種固定格式，
而應該根據 prompt 切成不同輸出模式。

---

## 🧠 task5 的核心方向

## 1. 新增兩種輸出模式

### A. `review draft mode`

適用情境：

- user 要求說明檔案負責什麼
- 但沒有要求「brief / short」
- user 看起來需要可審核、可修正的候選稿

輸出目標：

- 產生 4~6 個 candidate responsibilities
- 每點附 `Evidence`
- 不一定要強迫剛好 3 點
- 明說這是 for human review，不是假 final answer

建議格式：

```text
Candidate responsibilities for human review:

1. <candidate>
   Evidence: <class/method/pattern>

2. <candidate>
   Evidence: <class/method/pattern>

3. <candidate>
   Evidence: <class/method/pattern>

4. <candidate>
   Evidence: <class/method/pattern>

Review note:
<one short sentence>

Suggested keep:
- <candidate>
- <candidate>

Suggested review/remove:
- <candidate>
- <candidate>
```

### B. `brief summary mode`

適用情境：

- user 明確要求：
  - `briefly`
  - `brief summary`
  - `short summary`
  - `just give me a short summary`
  - `簡短`
  - `簡要`

輸出目標：

- 短
- 可直接讀
- 不保留 review scaffolding
- 不保留 `Evidence`

建議格式：

```text
1. <short summary point>

2. <short summary point>

3. <short summary point>
```

重點：

- 每個 bullet 中間保留一個空白行
- 不要輸出 `Evidence:`
- 不要輸出 `Tag:`
- 不要輸出 `Review note:`
- 不要輸出 `Suggested keep/remove`

---

## 🛠️ 具體實作建議

## 1. 先做 prompt mode detection

新增一個明確判斷：

- `is_direct_file_summary_prompt`
- `is_brief_summary_prompt`

判斷 brief summary 的 signal 可以先從關鍵詞開始，例如：

- `briefly`
- `brief summary`
- `short summary`
- `just give me`
- `簡短`
- `簡要`

只要 signal 足夠強，就走 brief mode。

## 2. reader prompt 要依模式切換

### review draft mode

reader 應被要求：

- 產生 4~6 candidates
- 每點附 evidence
- 補 review note
- 補 suggested keep/remove

### brief summary mode

reader 應被要求：

- 只輸出 3 個最短、最核心的點
- 不要 evidence
- 不要 tags
- 不要 review note
- 不要 suggested keep/remove

## 3. assistant 端要盡量直接沿用 reader

對 direct-file summary：

- review mode：直接沿用 reader draft
- brief mode：直接沿用 reader short summary

不要再讓 assistant 自由重摘要。

## 4. formatter 應支援空行風格

brief mode 的輸出應明確使用：

- bullet item
- 空一行
- 下一個 bullet item

這對 CLI 可讀性很重要。

## 5. `Evidence` 不應該是全域移除

這次的重要結論是：

- `Evidence` 對 review 很有用
- 但 brief mode 應移除

所以不應做成：

- 永遠顯示 evidence
- 或永遠拿掉 evidence

而應做成：

- review mode 顯示 evidence
- brief mode 移除 evidence

---

## 🧪 task5 建議新增測試

### 必加測試

1. 一般 direct-file summary 走 `review draft mode`
2. brief 類 prompt 走 `brief summary mode`
3. brief mode 不包含 `Evidence:`
4. brief mode 不包含 `Review note:`
5. brief mode 不包含 `Suggested keep:`
6. brief mode 的 bullet 間保留空行
7. review mode 仍保留 `Evidence:`
8. partial coverage 時 brief mode 仍保留 `Preliminary summary`

### 建議測試名稱

- `test_direct_file_summary_defaults_to_review_draft_mode`
- `test_brief_direct_file_summary_omits_evidence_and_review_scaffolding`
- `test_brief_direct_file_summary_uses_spaced_bullets`
- `test_review_draft_mode_keeps_evidence_lines`
- `test_partial_brief_direct_file_summary_preserves_preliminary_label`

---

## 🔍 驗收標準

### 對這種 prompt：

```bash
python -m crush_py --prompt "請讀 crush_py/store/session_store.py，用 3 點簡短說明它負責什麼。"
```

理想結果應該接近：

```text
1. 管理 session metadata 與基本狀態

2. 儲存與讀回 session 訊息紀錄

3. 提供 session 檔案與 metadata 的讀寫流程
```

且不應包含：

- `Evidence:`
- `Review note:`
- `Suggested keep:`
- `Suggested review/remove:`

### 對這種 prompt：

```bash
python -m crush_py --prompt "請讀 crush_py/store/session_store.py，說明它負責什麼。"
```

理想結果應該是 review draft：

- 有 4~6 個候選
- 有 `Evidence`
- 有 `Review note`
- 有 `Suggested keep/remove`

---

## 🧱 新 session 應先記住的事

1. `task3` 解掉的是 flow 與 coverage。
2. `task4` 解掉的是「不要假裝 final answer」，改成 review draft。
3. `task5` 要解的是：
   - 同樣是 direct-file summary
   - 依 user 語氣與需求切換輸出模式
4. `Evidence` 不是錯，只是不應該永遠出現。
5. 目前最好的方向不是更強 ranking，而是：
   - review mode 給可審核草稿
   - brief mode 給乾淨短摘要

---

## ✅ 一句話總結

`task5` 的目標不是讓模型更會選 top-3，而是讓 direct-file summary 根據 user 的要求，在 `review draft` 與 `brief summary` 之間切換，既保留可審核性，也保留簡潔度。
