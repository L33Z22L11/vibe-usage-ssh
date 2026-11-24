"""Environment configuration with an optional Vibe Usage desktop config."""

import json
import os
from pathlib import Path

DEFAULT_API_URL = "https://vibefriends.com"


def api_credentials():
    key = os.environ.get("VIBE_API_KEY")
    url = os.environ.get("VIBE_API_URL")
    if not key:
        path = Path.home() / ".vibe-usage" / "config.json"
        if path.is_file():
            config = json.loads(path.read_text())
            key = config.get("apiKey")
            url = url or config.get("apiUrl")
    if not key:
        raise ValueError("请设置 VIBE_API_KEY")
    url = (url or DEFAULT_API_URL).rstrip("/")
    if url == "https://vibecafe.ai":
        url = DEFAULT_API_URL
    return url, key


def host_key_path():
    default = Path.home() / ".local" / "share" / "vibe-usage-ssh" / "host_key"
    return Path(os.environ.get("VIBE_SSH_HOST_KEY", str(default))).expanduser()
