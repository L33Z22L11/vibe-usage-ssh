"""Usage totals, local-time windows, display names and numeric formatting."""

from collections import Counter
from datetime import datetime, timedelta

QUICK_FILTERS = [("hostname", "终端"), ("source", "工具"), ("model", "模型"), ("project", "项目")]
RANGES = [
    ("today", "今天"),
    ("1", "24H"),
    ("7", "7天"),
    ("30", "30天"),
    ("90", "90天"),
    ("custom", "自定义"),
]
DISTRIBUTIONS = [
    ("hostname", "hosts", "\uf108", "终端分布"),
    ("source", "tools", "\uf120", "工具分布"),
    ("model", "models", "\uf2db", "模型分布"),
    ("project", "projects", "\uf07b", "项目分布"),
]
FIELDS = ("inputTokens", "outputTokens", "cachedInputTokens", "reasoningOutputTokens")


def safe(value):
    return "".join(
        c
        for c in str(value or "未知")
        if c.isprintable() and not 0x202A <= ord(c) <= 0x202E and not 0x2066 <= ord(c) <= 0x2069
    )[:100]


def tokens(row):
    return sum(int(row.get(field) or 0) for field in FIELDS)


def cache_hit_rate(rows):
    if any(row.get("inputTokens") is None or row.get("cachedInputTokens") is None for row in rows):
        return None
    missed = sum(row.get("inputTokens", 0) for row in rows)
    hits = sum(row.get("cachedInputTokens", 0) for row in rows)
    return 100 * hits / (missed + hits) if missed + hits > 0 else None


def display(data, dim, raw):
    names = data.get("names") or {}
    registry = {"source": "sources", "model": "models"}.get(dim)
    return safe((names.get(registry) or {}).get(raw, raw) if registry else raw)


def day_key(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().date().isoformat()
    except (ValueError, AttributeError):
        return "未知日期"


def compact(value):
    for threshold, suffix in [(1e9, "B"), (1e6, "M"), (1e3, "K")]:
        if abs(value) >= threshold:
            return f"{value / threshold:,.2f}{suffix}"
    return f"{value:,.0f}"


def chinese_tokens(value):
    for threshold, suffix in [(1e12, "万亿"), (1e8, "亿"), (1e4, "万")]:
        if abs(value) >= threshold:
            return f"{value / threshold:,.2f}".rstrip("0").rstrip(".") + suffix
    return f"{value:,.0f}"


def format_cost(usd, currency="USD", rate=None):
    if currency == "CNY" and rate is None:
        return "¥—"
    amount = usd * rate if currency == "CNY" else usd
    symbol = "¥" if currency == "CNY" else "$"
    precision = 4 if 0 < abs(amount) < 0.01 else 2
    return f"{symbol}{amount:,.{precision}f}"


def trend_series(rows, sessions, range_key, dates, metric, now=None):
    now = now or datetime.now().astimezone()
    hourly = range_key in ("today", "1")
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    end = (
        now.replace(minute=0, second=0, microsecond=0)
        if hourly
        else now.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    if range_key == "today":
        start = end.replace(hour=0)
        end = start.replace(hour=23)
    elif range_key == "1":
        start = end - timedelta(hours=23)
    elif range_key == "custom":
        start = datetime.fromisoformat(dates[0]).replace(tzinfo=now.tzinfo)
        end = datetime.fromisoformat(dates[1]).replace(tzinfo=now.tzinfo)
    else:
        start = end - timedelta(days=int(range_key) - 1)
    grouped = Counter()
    for row in sessions if metric == "active" else rows:
        try:
            instant = datetime.fromisoformat(
                row.get("firstMessageAt" if metric == "active" else "bucketStart", "").replace(
                    "Z", "+00:00"
                )
            ).astimezone()
        except ValueError:
            continue
        slot = instant.replace(minute=0, second=0, microsecond=0)
        if not hourly:
            slot = slot.replace(hour=0)
        value = (
            row.get("activeSeconds", 0)
            if metric == "active"
            else row.get("estimatedCost", 0)
            if metric == "cost"
            else tokens(row)
        )
        grouped[slot] += value or 0
    # API rolling windows can include a partial first hour/day, as in the lockscreen app.
    if grouped and range_key not in ("today", "custom"):
        start = min(start, min(grouped))
    count = max(1, (end - start) // step + 1)
    slots = [start + i * step for i in range(count)]
    return slots, [grouped[t] for t in slots]


def format_duration(seconds):
    minutes = max(0, int(seconds)) // 60
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"
