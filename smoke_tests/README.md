# Smoke Tests

這個資料夾放的是 **手動 smoke tests**。

目標不是做完整測試覆蓋，而是用最少的案例，快速確認目前最重要的 user flows 還活著。

## 🎯 原則

- ✅ 每個案例都應該很短、很好跑
- ✅ 每個案例都要對應一個明確 flow
- ✅ 優先驗證 CLI / REPL / guide / trace / summarize 的主路徑
- ❌ 不在這裡重複單元測試細節

## 📦 建議執行順序

1. Summary
2. Trace
3. Guide
4. Multi-turn guide
5. REPL

## 1. Summary

### Case S1: brief direct-file summary

```bash
python -m crush_py --summarize README.md
```

預期：

- 有正常輸出
- 是短版摘要
- 不應該看起來像 trace

## 2. Trace

### Case T1: variable trace

```bash
python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"
```

預期：

- 有 `Variable trace for human review:`
- 有 `Coverage`
- 有 evidence-backed sections
- 有 uncertainty / partial wording

### Case T2: flow trace

```bash
python -m crush_py --trace "how prompt flows inside crush_py/agent/runtime.py"
```

預期：

- 有 `Flow trace for human review:`
- 有 `Target: prompt`
- 有 flow sections，例如 entry point / transformation / downstream handoff

### Case T3: 繁中 trace intent

```bash
python -m crush_py --trace "追蹤 session_id 的流向，檔案在 crush_py/store/session_store.py"
```

預期：

- 應走 trace，不是 summary
- 有 flow / variable trace 類型輸出

## 3. Guide

### Case G1: checklist

```bash
python -m crush_py --guide "turn README.md into a checklist"
```

預期：

- 有 `Checklist:`
- 有 `Success check:`
- 有 `Sources:`

### Case G2: beginner summary

```bash
python -m crush_py --guide "summarize README.md for a beginner"
```

預期：

- 語氣偏 beginner-friendly
- 有 `Sources:`
- 不應該像 code trace

### Case G3: exact-line guide question

```bash
python -m crush_py --guide "which exact lines in README.md talk about setup?"
```

預期：

- 應該偏向重新讀文件
- 回答要帶明確 line clue 或 source clue

## 4. Multi-turn Guide Session

### Case M1: same-session follow-up

```bash
python -m crush_py --guide "summarize README.md for a beginner"
python -m crush_py --session <session_id> --guide "turn README.md into a checklist"
python -m crush_py --session <session_id> --guide "I am stuck during setup in README.md"
```

預期：

- follow-up 留在同一個 session
- checklist / troubleshooting 分流正常
- 若前一輪 guide summary 足夠完整，可重用上下文

## 5. REPL

### Case R1: `/ls` 後接自然語言問題

```bash
python -m crush_py
```

然後在 REPL 中輸入：

```text
/ls . 2
find the file with string 'sh'
```

預期：

- `/ls` 先正常列出內容
- 下一句自然語言問題可以承接前一輪 context
- 最後應能找到像 `run.sh` 這樣的檔案

## 🔁 維護規則

- 新增 mode 或改變主路徑時，要同步更新這裡
- 如果某個 smoke case 不再代表真實主流程，就刪掉或重寫
- 如果某個 case 已經太長，就拆成新的案例
