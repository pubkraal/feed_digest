"""Tests for config.py — configuration loading."""

import os
import tempfile
import unittest

from config import load_config


class TestLoadConfig(unittest.TestCase):

    def _write_config(self, content):
        f = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            delete=False,
        )
        f.write(content)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_loads_yaml_file(self):
        path = self._write_config("anthropic:\n  api_key: test-key\n")
        cfg = load_config(path)

        self.assertEqual(cfg["anthropic"]["api_key"], "test-key")

    def test_file_not_found_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_config("/nonexistent/config.yaml")

    def test_anthropic_api_key_env_override(self):
        path = self._write_config("anthropic:\n  api_key: from-file\n")
        env = {"ANTHROPIC_API_KEY": "from-env"}

        with unittest.mock.patch.dict(os.environ, env, clear=False):
            cfg = load_config(path)

        self.assertEqual(cfg["anthropic"]["api_key"], "from-env")

    def test_mailgun_env_overrides(self):
        path = self._write_config(
            "mailgun:\n  api_key: file-key\n  domain: file-domain\n"
        )
        env = {
            "MAILGUN_API_KEY": "env-key",
            "MAILGUN_DOMAIN": "env-domain",
        }

        with unittest.mock.patch.dict(os.environ, env, clear=False):
            cfg = load_config(path)

        self.assertEqual(cfg["mailgun"]["api_key"], "env-key")
        self.assertEqual(cfg["mailgun"]["domain"], "env-domain")

    def test_no_feedly_env_overrides(self):
        """Feedly env overrides should no longer exist."""
        path = self._write_config("feeds:\n  categories: {}\n")
        env = {
            "FEEDLY_ACCESS_TOKEN": "should-be-ignored",
            "FEEDLY_USER_ID": "should-be-ignored",
        }

        with unittest.mock.patch.dict(os.environ, env, clear=False):
            cfg = load_config(path)

        self.assertNotIn("feedly", cfg)


# Need this import for patch.dict
import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
