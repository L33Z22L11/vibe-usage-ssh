import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import asyncssh

from vibe_usage_ssh.config import api_credentials
from vibe_usage_ssh.server import ensure_host_key


class ConfigurationTests(unittest.TestCase):
    def test_environment_credentials_without_local_file(self):
        with patch.dict(
            os.environ,
            {"VIBE_API_KEY": "example-key", "VIBE_API_URL": "https://example.com/"},
            clear=True,
        ):
            self.assertEqual(api_credentials(), ("https://example.com", "example-key"))

    def test_generated_host_key_is_private_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ssh" / "key"
            with patch.dict(os.environ, {"VIBE_SSH_HOST_KEY": str(path)}):
                self.assertEqual(ensure_host_key(), str(path))
                original = path.read_bytes()
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                asyncssh.read_private_key(path)
                ensure_host_key()
                self.assertEqual(path.read_bytes(), original)
