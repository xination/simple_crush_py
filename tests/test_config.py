import json
import tempfile
import unittest
from pathlib import Path

from crush_py.config import ConfigError, load_config


class LoadConfigTests(unittest.TestCase):
    def test_loads_default_read_helper_config_when_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config(base_dir=tmpdir)

            root = Path(tmpdir).resolve()
            self.assertEqual(config.workspace_root, root)
            self.assertEqual(config.sessions_dir, root / ".crush_py" / "sessions")
            self.assertEqual(config.default_backend, "lm_studio")
            self.assertEqual(config.trace_mode, "lean")
            self.assertEqual(sorted(config.backends.keys()), ["lm_studio"])
            self.assertEqual(config.backends["lm_studio"].type, "openai_compat")
            self.assertEqual(config.backends["lm_studio"].timeout, 600)

    def test_merges_custom_config_for_openai_compat_backend(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "workspace_root": "workspace",
                        "sessions_dir": "state/sessions",
                        "trace_mode": "debug",
                        "backends": {
                            "lm_studio": {
                                "type": "openai_compat",
                                "model": "demo-3b",
                                "base_url": "http://example.test/v1",
                                "api_key": "not-needed",
                                "timeout": 12,
                                "max_tokens": 345,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path=str(config_path), base_dir=tmpdir)

            self.assertEqual(config.workspace_root, (Path(tmpdir) / "workspace").resolve())
            self.assertEqual(config.sessions_dir, (Path(tmpdir) / "state" / "sessions").resolve())
            self.assertEqual(config.trace_mode, "debug")
            self.assertEqual(config.backends["lm_studio"].model, "demo-3b")
            self.assertEqual(config.backends["lm_studio"].timeout, 12)
            self.assertEqual(config.backends["lm_studio"].max_tokens, 345)

    def test_base_dir_controls_default_config_discovery_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            caller_root = Path(tmpdir) / "caller"
            caller_root.mkdir()
            nested_repo = caller_root / "crush_py"
            nested_repo.mkdir()
            (nested_repo / "config.json").write_text(
                json.dumps(
                    {
                        "workspace_root": "..",
                        "sessions_dir": "../.crush_py/sessions",
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(base_dir=str(caller_root))

            self.assertEqual(config.workspace_root, caller_root.resolve())
            self.assertEqual(config.sessions_dir, (caller_root / ".crush_py" / "sessions").resolve())

    def test_raises_when_default_backend_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(
                json.dumps({"default_backend": "missing"}),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                load_config(config_path=str(config_path), base_dir=tmpdir)

    def test_raises_for_non_openai_compat_backends(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "backends": {
                            "lm_studio": {
                                "type": "anthropic",
                                "model": "demo",
                                "base_url": "https://example.test",
                                "api_key": "test",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                load_config(config_path=str(config_path), base_dir=tmpdir)


if __name__ == "__main__":
    unittest.main()
