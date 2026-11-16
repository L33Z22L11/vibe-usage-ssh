"""Read-only Vibe Usage HTTPS client. Credentials stay in the local config."""

import json
import math
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import api_credentials

EXCHANGE_URL = "https://api.frankfurter.dev/v2/rate/USD/CNY"


def fetch_exchange_rate():
    request = Request(EXCHANGE_URL, headers={"Accept": "application/json"})
    with build_opener(NoRedirect).open(request, timeout=10) as response:
        data = json.load(response)
    rate = float(data["rate"])
    if data["base"] != "USD" or data["quote"] != "CNY" or not math.isfinite(rate) or rate <= 0:
        raise ValueError("汇率返回格式异常")
    return rate, data["date"]


def local_timezone():
    configured = os.environ.get("TZ")
    if configured:
        return configured
    try:
        return str(Path("/etc/localtime").resolve()).split("/zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return "Asia/Shanghai"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_usage(range_key, dates):
    base, key = api_credentials()
    if urlsplit(base).scheme != "https":
        raise ValueError("API 必须使用 HTTPS")
    params = {"tz": local_timezone()}
    if range_key == "today":
        params["from"] = (
            datetime.now()
            .astimezone()
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
        )
    elif range_key == "custom":
        params.update({"from": dates[0], "to": dates[1]})
    else:
        params["days"] = range_key
    request = Request(
        base + "/api/usage?" + urlencode(params),
        headers={
            "Authorization": "Bearer " + key,
            "Accept": "application/json",
        },
    )
    with build_opener(NoRedirect).open(request, timeout=20) as response:
        data = json.load(response)
    if not isinstance(data.get("buckets"), list):
        raise ValueError("API 返回格式异常")
    return data
