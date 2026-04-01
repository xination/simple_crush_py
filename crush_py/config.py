import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


DEFAULT_OPENAI_COMPAT_BASE_URL = "http://192.168.40.1:1234/v1"
DEFAULT_OPENAI_COMPAT_MODEL = "google/gemma-3-4b"


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
    trace_mode: str
    backends: Dict[str, BackendConfig]


def _default_config_dict() -> dict:
    return {
        "workspace_root": ".",
        "sessions_dir": ".crush_py/sessions",
        "default_backend": "lm_studio",
        "trace_mode": "lean",
        "backends": {
            "lm_studio": {
                "type": "openai_compat",
                "model": DEFAULT_OPENAI_COMPAT_MODEL,
                "base_url": DEFAULT_OPENAI_COMPAT_BASE_URL,
                "api_key": "not-needed",
                "timeout": 60,
                "max_tokens": 2048,
            }
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

    default_backend = raw.get("default_backend", "lm_studio")
    if default_backend not in backends:
        raise ConfigError("Default backend `{0}` is not configured.".format(default_backend))
    if default_backend != "lm_studio":
        raise ConfigError("Only `lm_studio` is supported in this version.")
    for backend in backends.values():
        if backend.type != "openai_compat":
            raise ConfigError(
                "Unsupported backend type `{0}`. Only `openai_compat` is supported.".format(
                    backend.type
                )
            )

    return AppConfig(
        workspace_root=workspace_root,
        sessions_dir=sessions_dir,
        default_backend=default_backend,
        trace_mode=str(raw.get("trace_mode", "lean")),
        backends=backends,
    )


def _merge_dicts(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged
