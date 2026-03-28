import json
import os
import tempfile
import unittest
from pathlib import Path

from crush_py.config import ConfigError, load_config


class LoadConfigTests(unittest.TestCase):
    def test_loads_default_config_when_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config(base_dir=tmpdir)

            root = Path(tmpdir).resolve()
            self.assertEqual(config.workspace_root, root)
            self.assertEqual(config.sessions_dir, root / ".crush_py" / "sessions")
            self.assertEqual(config.default_backend, "anthropic")
            self.assertIn("anthropic", config.backends)
            self.assertIn("lm_studio", config.backends)
            self.assertTrue(config.ask_on_write)
            self.assertTrue(config.ask_on_shell)
            self.assertEqual(config.bash_timeout, 60)

    def test_merges_custom_config_and_resolves_api_key_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "workspace_root": "workspace",
                        "sessions_dir": "state/sessions",
                        "default_backend": "anthropic",
                        "permissions": {
                            "ask_on_write": False,
                            "ask_on_shell": False,
                        },
                        "tools": {
                            "bash_timeout": 90
                        },
                        "backends": {
                            "anthropic": {
                                "type": "anthropic",
                                "model": "fake-model",
                                "base_url": "https://example.test",
                                "api_key_env": "TEST_ANTHROPIC_API_KEY",
                                "timeout": 12,
                                "max_tokens": 345,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            original = os.environ.get("TEST_ANTHROPIC_API_KEY")
            os.environ["TEST_ANTHROPIC_API_KEY"] = "secret-key"
            try:
                config = load_config(config_path=str(config_path), base_dir=tmpdir)
            finally:
                if original is None:
                    del os.environ["TEST_ANTHROPIC_API_KEY"]
                else:
                    os.environ["TEST_ANTHROPIC_API_KEY"] = original

            self.assertEqual(config.workspace_root, (Path(tmpdir) / "workspace").resolve())
            self.assertEqual(config.sessions_dir, (Path(tmpdir) / "state" / "sessions").resolve())
            self.assertFalse(config.ask_on_write)
            self.assertFalse(config.ask_on_shell)
            self.assertEqual(config.bash_timeout, 90)
            self.assertEqual(config.backends["anthropic"].api_key, "secret-key")
            self.assertEqual(config.backends["anthropic"].timeout, 12)
            self.assertEqual(config.backends["anthropic"].max_tokens, 345)

    def test_raises_when_default_backend_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "default_backend": "missing",
                        "backends": {
                            "anthropic": {
                                "type": "anthropic",
                                "model": "fake-model",
                                "base_url": "https://example.test",
                                "api_key": "test-key",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                load_config(config_path=str(config_path), base_dir=tmpdir)


if __name__ == "__main__":
    unittest.main()
