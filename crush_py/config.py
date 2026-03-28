import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_OPENAI_COMPAT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_OPENAI_COMPAT_MODEL = "qwen2.5-coder-3b-instruct"


class ConfigError(Exception):
    pass


@dataclass
class BackendConfig:
    name: str
    type: str
    model: str
    base_url: str
    api_key: Optional[str]
    api_key_env: Optional[str]
    timeout: int
    max_tokens: int


@dataclass
class AppConfig:
    workspace_root: Path
    sessions_dir: Path
    default_backend: str
    backends: Dict[str, BackendConfig]
    ask_on_write: bool
    ask_on_shell: bool
    bash_timeout: int


def _default_config_dict() -> dict:
    return {
        "workspace_root": ".",
        "sessions_dir": ".crush_py/sessions",
        "default_backend": "anthropic",
        "backends": {
            "anthropic": {
                "type": "anthropic",
                "model": DEFAULT_ANTHROPIC_MODEL,
                "base_url": DEFAULT_ANTHROPIC_BASE_URL,
                "api_key_env": "ANTHROPIC_API_KEY",
                "timeout": 60,
                "max_tokens": 4096,
            },
            "lm_studio": {
                "type": "openai_compat",
                "model": DEFAULT_OPENAI_COMPAT_MODEL,
                "base_url": DEFAULT_OPENAI_COMPAT_BASE_URL,
                "api_key": "not-needed",
                "timeout": 60,
                "max_tokens": 4096,
            },
        },
        "permissions": {
            "ask_on_write": True,
            "ask_on_shell": True,
        },
        "tools": {
            "bash_timeout": 60,
        },
    }


def load_config(config_path: Optional[str] = None, base_dir: Optional[str] = None) -> AppConfig:
    root = Path(base_dir or os.getcwd()).resolve()
    if config_path:
        path = Path(config_path).resolve()
    else:
        candidates = [
            root / "config.json",
            root / "crush_py" / "config.json",
        ]
        path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    raw = _default_config_dict()

    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        raw = _merge_dicts(raw, loaded)
        config_root = path.parent
    else:
        config_root = root

    workspace_root = (config_root / raw["workspace_root"]).resolve()
    sessions_dir = (config_root / raw["sessions_dir"]).resolve()
    backends = {}
    for name, backend in raw.get("backends", {}).items():
        api_key = backend.get("api_key")
        api_key_env = backend.get("api_key_env")
        if not api_key and api_key_env:
            api_key = os.environ.get(api_key_env)
        backends[name] = BackendConfig(
            name=name,
            type=backend["type"],
            model=backend["model"],
            base_url=backend["base_url"],
            api_key=api_key,
            api_key_env=api_key_env,
            timeout=int(backend.get("timeout", 60)),
            max_tokens=int(backend.get("max_tokens", 4096)),
        )

    default_backend = raw.get("default_backend", "anthropic")
    if default_backend not in backends:
        raise ConfigError("Default backend `{0}` is not configured.".format(default_backend))

    permissions = raw.get("permissions", {})
    tools = raw.get("tools", {})
    return AppConfig(
        workspace_root=workspace_root,
        sessions_dir=sessions_dir,
        default_backend=default_backend,
        backends=backends,
        ask_on_write=bool(permissions.get("ask_on_write", True)),
        ask_on_shell=bool(permissions.get("ask_on_shell", True)),
        bash_timeout=int(tools.get("bash_timeout", 60)),
    )


def _merge_dicts(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged
