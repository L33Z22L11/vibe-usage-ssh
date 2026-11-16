import tempfile
import unittest
from pathlib import Path

from vibe_usage_ssh.preferences import client_id, load_preferences, save_preferences


class PreferenceTests(unittest.TestCase):
    def test_client_identity_ignores_ephemeral_port_and_separates_clients(self):
        first = client_id(("192.168.1.2", 12345), "SSH-2.0-OpenSSH", "cafe")
        self.assertEqual(first, client_id(("192.168.1.2", 54321), "SSH-2.0-OpenSSH", "cafe"))
        self.assertNotEqual(first, client_id(("192.168.1.3", 12345), "SSH-2.0-OpenSSH", "cafe"))
        self.assertNotEqual(first, client_id(("192.168.1.2", 12345), "SSH-2.0-Termius", "cafe"))
        self.assertNotEqual(first, client_id(("192.168.1.2", 12345), "SSH-2.0-OpenSSH", "phone"))

    def test_preferences_are_private_validated_and_replaceable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences/client.json"
            data = dict(
                theme="nord",
                range_key="custom",
                dates=["2026-11-01", "2026-11-16"],
                filters={"project": ["项目一"], "source": ["codex"]},
            )
            save_preferences(path, data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            loaded = load_preferences(path, {"nord"})
            self.assertEqual(loaded["dates"], ("2026-11-01", "2026-11-16"))
            self.assertEqual(loaded["filters"]["project"], {"项目一"})
            save_preferences(path, dict(theme="unknown", range_key="custom", dates=["bad", "bad"]))
            self.assertEqual(load_preferences(path, {"nord"}), {})
            self.assertEqual(list(path.parent.iterdir()), [path])
            path.write_text("invalid json")
            self.assertEqual(load_preferences(path, {"nord"}), {})
