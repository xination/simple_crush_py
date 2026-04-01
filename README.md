# 🧭 crush_py

以 **Python 3.9** 為優先、專門給 **3B 級本機小模型** 使用的 repo 閱讀助手。

## ✨ 這個版本的定位

- 📚 以 **read-only repo exploration** 為核心
- 🧵 主要任務是：
  - instruction Q&A
  - Python code tracing
  - C++ code tracing
- 🧠 設計重點不是功能很多，而是：
  - tool 選擇要少
  - context 要穩
  - 小模型不要被大型輸出撐爆

## 🛠️ 核心工具

- `ls`
- `tree`
- `find`
- `grep`
- `cat`

這些工具的預期流程是：

1. `tree` / `ls` 看區域
2. `find` 找檔
3. `grep` 找符號或字串
4. `cat` 讀檔
5. 再回答

## 🔍 適合的問法

- 💬「這個 repo 的主要結構是什麼？」
- 💬「幫我 trace Python 裡 `main()` 之後呼叫了哪些函式。」
- 💬「幫我找 C++ 裡 `trace_me` 的定義與呼叫點。」
- 💬「這個設定值在哪裡被讀取？」
- 💬「請根據實際檔案內容回答，不確定的地方要明說。」

## 🚀 快速開始

1. 準備 `config.json`
2. 確認 LM Studio / OpenAI-compatible API 可連線
3. 在此目錄執行：

```bash
python -m crush_py
```

### CLI summary 用法

```bash
python -m crush_py --summarize README.md
python -m crush_py --summarize-brief README.md
```

- `--summarize`：direct-file summary 會走 `review draft mode`
- `--summarize-brief`：direct-file summary 會走 `brief summary mode`
- `--prompt "quickly summarize README.md"` 也會走 `brief summary mode`

## 💬 REPL 指令

- `/help`
- `/new`
- `/sessions`
- `/use <session_id>`
- `/backend`
- `/tools`
- `/ls`
- `/tree`
- `/find`
- `/grep`
- `/cat`
- `/history`
- `/trace`

## 📘 常用指令格式

### 📂 `/tree [PATH] [DEPTH]`

```bash
/tree crush_py 2
```

### 🗂️ `/ls [PATH] [DEPTH]`

```bash
/ls crush_py/tools 1
```

### 🔎 `/find PATTERN [PATH]`

```bash
/find "*.py" crush_py
```

### 🧵 `/grep PATTERN [PATH] [INCLUDE]`

```bash
/grep "SessionStore" crush_py "*.py"
```

### 📄 `/cat PATH [OFFSET] [LIMIT]`

```bash
/cat crush_py/store/session_store.py 0 80
```

## ⚙️ 範例設定

```json
{
  "workspace_root": ".",
  "sessions_dir": ".crush_py/sessions",
  "default_backend": "lm_studio",
  "backends": {
    "lm_studio": {
      "type": "openai_compat",
      "model": "google/gemma-3-4b",
      "base_url": "http://192.168.40.1:1234/v1",
      "api_key": "not-needed",
      "timeout": 60,
      "max_tokens": 2048
    }
  }
}
```

## 🧪 執行測試

```bash
python -m unittest discover -s tests
```

## 📎 補充文件

- [`summary.txt`](summary.txt)
- [`plan.md`](plan.md)
- [`NEXT.md`](NEXT.md)
