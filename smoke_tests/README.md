# Smoke Tests

這個資料夾放的是手動 smoke tests。

目標不是做完整測試覆蓋，而是用少量高訊號案例，快速確認目前最重要的 user flows 還活著。

## 範圍

1. Summary
2. Trace
3. Guide
4. Multi-turn guide
5. REPL
6. Single-file workspace

## 1. Summary

### Case Q1: quick file mode

```bash
python -m unittest smoke_tests.test_quick_file_mode -q
```

Format:

- `/quick @PATH, PROMPT`
- use the first comma to separate the file path and the prompt
- everything after the first comma is treated as the prompt
- example: `/quick @README.md, show me how to start in Traditional Chinese`

Expected:

- exits with code 0
- exercises `--file` + `--prompt` through the CLI entry point
- prints a single-file answer without planner or tool-loop behavior
- repeated quick-mode reads reuse cached file text
- repeated quick-mode turns stay stateless at the model-context level
- in `trace_mode=debug`, session metadata shows quick-file cache `miss` then `hit`

### Case S1: brief direct-file summary

```bash
python -m crush_py --summarize README.md
```

Expected:

- 回傳短版 3 點摘要
- 聚焦在檔案主要內容
- 不要變成 trace

## 2. Trace

### Case T1: variable trace

```bash
python -m crush_py --trace "the variable session_id in crush_py/store/session_store.py"
```

Expected:

- 有 `Variable trace for human review:`
- 有 `Coverage`
- 有 evidence-backed sections
- 有 uncertainty / partial wording

### Case T2: flow trace

```bash
python -m crush_py --trace "how prompt flows inside crush_py/agent/runtime.py"
```

Expected:

- 有 `Flow trace for human review:`
- 有 `Target: prompt`
- 有 flow sections，例如 entry point / transformation / downstream handoff

### Case T3: Traditional Chinese trace intent

```bash
python -m crush_py --trace "追蹤 session_id 的流向，檔案在 crush_py/store/session_store.py"
```

Expected:

- 仍然走 trace，不是 summary
- flow / variable trace intent 判定正常

## 3. Guide

### Case G1: checklist

```bash
python -m crush_py --guide "turn README.md into a checklist"
```

Expected:

- 有 `Checklist:`
- 有 `Success check:`
- 有 `Sources:`

### Case G2: beginner summary

```bash
python -m crush_py --guide "summarize README.md for a beginner"
```

Expected:

- 內容偏 beginner-friendly
- 有 `Sources:`
- 不要變成 code trace

### Case G3: exact-line guide question

```bash
python -m crush_py --guide "which exact lines in README.md talk about setup?"
```

Expected:

- 會重新讀文件
- 有 line clue 或 source clue

## 4. Multi-turn Guide Session

### Case M1: same-session follow-up

```bash
python -m crush_py --guide "summarize README.md for a beginner"
python -m crush_py --session <session_id> --guide "turn README.md into a checklist"
python -m crush_py --session <session_id> --guide "I am stuck during setup in README.md"
```

Expected:

- follow-up 留在同一個 session
- checklist / troubleshooting 分流正常
- guide summary 能在合理情況下被重用

## 5. REPL

### Case R1: `/ls` 後接自然語言問題

```bash
python -m crush_py
```

進入 REPL 後輸入：

```text
/ls . 2
find the file with string 'sh'
```

Expected:

- `/ls` 結果可正常顯示
- 下一輪自然語言問題可以利用剛才的 context
- 最後能合理收斂到 `run.sh`

## 6. Single-file Workspace

### Case W1: implicit instruction doc in a tiny workspace

```powershell
cd C:\PL\Dropbox\3_my_program\experimenting\crush_py\tf_experiment_test
$env:PYTHONPATH = (Resolve-Path '..').Path
python -m crush_py --config config.json --prompt "help me understand the instruction"
```

Expected:

- runtime 會錨定 `INSTRUCTIONS.md`
- 不會 drift 到 `config.json`
- 回答會用 plain language 解釋 TensorFlow experiment guide
- 回答會帶出來自 instruction file 的 evidence

## 維護原則

- 新增 mode 或改變主路徑時，要同步更新這份 smoke suite
- 如果某個 smoke case 不再代表真實主流程，就刪掉或重寫
- 如果某個 case 已經太長，就拆成新的案例
