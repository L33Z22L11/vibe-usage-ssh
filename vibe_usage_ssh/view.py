"""Aggregate a filtered usage snapshot once, independently of its presentation."""

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

from .usage import DISTRIBUTIONS, FIELDS, cache_hit_rate, day_key, display, trend_series


@dataclass
class UsageView:
    rows: list
    totals: dict
    cache_rate: float | None
    series: dict
    distributions: dict


def build_view(data, filters, range_key, dates):
    today = date.today().isoformat()

    def matches(row, dims):
        for key in dims:
            if filters[key]:
                raw = str(row.get(key) or "")
                value = display(data, key, raw) if key == "model" else raw
                if value not in filters[key]:
                    return False
        if range_key == "today":
            stamp = row.get("bucketStart") or row.get("firstMessageAt")
            if stamp and day_key(stamp) != today:
                return False
        return True

    rows = [row for row in data["buckets"] if matches(row, filters)]
    sessions = [
        row
        for row in (data.get("sessions") or [])
        if matches(row, ("source", "hostname", "project"))
    ]
    token_slots, _ = trend_series(rows, sessions, range_key, dates, "tokens")
    active_slots, _ = trend_series(rows, sessions, range_key, dates, "active")
    hourly = range_key in ("today", "1")
    points = {
        slot: dict(
            time=slot.strftime("%m/%d %H:%M" if hourly else "%Y/%m/%d"),
            tokens=0,
            input=0,
            output=0,
            cached=0,
            reasoning=0,
            cost=0,
            active=0,
        )
        for slot in set(token_slots) | set(active_slots)
    }

    def point_at(stamp):
        try:
            slot = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
            slot = slot.replace(minute=0, second=0, microsecond=0)
            if not hourly:
                slot = slot.replace(hour=0)
            return points.get(slot)
        except (ValueError, AttributeError):
            return None

    groups = {dim: {"tokens": Counter(), "cost": Counter()} for dim, *_ in DISTRIBUTIONS}
    names = {}
    totals = dict(tokens=0, cost=0, active=0)
    for row in rows:
        counts = [int(row.get(field) or 0) for field in FIELDS]
        total, cost = sum(counts), float(row.get("estimatedCost") or 0)
        totals["tokens"] += total
        totals["cost"] += cost
        point = point_at(row.get("bucketStart"))
        if point is not None:
            point["tokens"] += total
            point["cost"] += cost
            for key, value in zip(("input", "output", "cached", "reasoning"), counts, strict=True):
                point[key] += value
        for dim in groups:
            raw = row.get(dim, "")
            if (dim, raw) not in names:
                names[dim, raw] = display(data, dim, raw)
            label = names[dim, raw]
            groups[dim]["tokens"][label] += total
            groups[dim]["cost"][label] += cost
    for row in sessions:
        active = int(row.get("activeSeconds") or 0)
        totals["active"] += active
        point = point_at(row.get("firstMessageAt"))
        if point is not None:
            point["active"] += active
    series = {}
    for metric in ("tokens", "cost", "active"):
        slots = active_slots if metric == "active" else token_slots
        series[metric] = (
            [slot.strftime("%H:%M" if hourly else "%m/%d") for slot in slots],
            [points[slot][metric] for slot in slots],
            [points[slot] for slot in slots],
        )
    return UsageView(
        rows,
        totals,
        cache_hit_rate(rows),
        series,
        {
            dim: {metric: values.most_common() for metric, values in modes.items()}
            for dim, modes in groups.items()
        },
    )
