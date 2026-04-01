# Demo Workspace

This folder is a safe playground for `crush_py`.

## What is special here

- `workspace_root` is this `demo/` directory only
- automatic file writes inside `demo/` do not ask for per-call confirmation
- shell commands still require confirmation

## Recommended way to run

From the project root:

```bash
python -m crush_py --config demo/config.json
```

## Good demo prompt

```text
Write a small Python script named `print_now.py` that prints the current date and time.
```

## Safety note

Even with write confirmation disabled, `crush_py` can only write inside this `demo/` workspace when you use this config file.
