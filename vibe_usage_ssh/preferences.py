"""Per-client display preferences, separate from credentials and usage data."""

import hashlib
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from .usage import QUICK_FILTERS, RANGES


def client_id(peer, version, username):
    identity = json.dumps([peer[0] if peer else "", version, username], ensure_ascii=True)
    return hashlib.sha256(identity.encode()).hexdigest()


def preferences_path():
    identity = os.environ.get("VIBE_SSH_CLIENT_ID", "")
    if not re.fullmatch(r"[a-f0-9]{64}", identity):
        return None
    return Path.home() / ".local/share/vibe-usage-ssh/preferences" / (identity + ".json")


def load_preferences(path, themes):
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {}
        result = {}
        if data.get("theme") in themes:
            result["theme"] = data["theme"]
        if data.get("range_key") in {key for key, _ in RANGES}:
            result["range_key"] = data["range_key"]
        if result.get("range_key") == "custom":
            dates = data.get("dates")
            try:
                if not isinstance(dates, list) or len(dates) != 2:
                    raise ValueError
                start, end = (date.fromisoformat(value) for value in dates)
                if start > end:
                    raise ValueError
                result["dates"] = (start.isoformat(), end.isoformat())
            except (ValueError, TypeError):
                result.pop("range_key", None)
        filters = data.get("filters")
        if isinstance(filters, dict):
            result["filters"] = {
                dim: {value for value in filters.get(dim, []) if isinstance(value, str)}
                for dim, _ in QUICK_FILTERS
                if isinstance(filters.get(dim, []), list)
            }
        return result
    except (OSError, ValueError, TypeError):
        return {}


def save_preferences(path, data):
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
