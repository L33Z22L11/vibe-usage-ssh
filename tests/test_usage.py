import unittest
from datetime import datetime, timedelta

from vibe_usage_ssh.usage import (
    QUICK_FILTERS,
    cache_hit_rate,
    chinese_tokens,
    format_cost,
    format_duration,
    tokens,
    trend_series,
)
from vibe_usage_ssh.view import build_view


class UsageTests(unittest.TestCase):
    def test_aggregated_view_preserves_metrics_filters_and_distributions(self):
        now = datetime.now().astimezone()
        row = dict(
            bucketStart=now.isoformat(),
            source="codex",
            hostname="mac",
            project="app",
            model="model-a",
            inputTokens=100,
            outputTokens=20,
            cachedInputTokens=300,
            reasoningOutputTokens=5,
            estimatedCost=1.2,
        )
        rows = [row, dict(row, model="model-b", estimatedCost=2.4)]
        sessions = [
            dict(
                firstMessageAt=(now - timedelta(days=2)).isoformat(),
                source="codex",
                hostname="mac",
                project="app",
                activeSeconds=120,
            )
        ]
        data = dict(buckets=rows, sessions=sessions, names={"models": {"model-a": "A"}})
        filters = {dim: set() for dim, _ in QUICK_FILTERS}
        for range_key in ("1", "7", "30", "90", "custom"):
            dates = (
                (now.date().isoformat(), now.date().isoformat()) if range_key == "custom" else None
            )
            view = build_view(data, filters, range_key, dates)
            for metric in ("tokens", "cost", "active"):
                _, expected = trend_series(rows, sessions, range_key, dates, metric)
                self.assertEqual(view.series[metric][1], expected)
        filters["model"] = {"A"}
        view = build_view(data, filters, "7", None)
        self.assertEqual(view.rows, [row])
        self.assertEqual(view.totals, dict(tokens=425, cost=1.2, active=120))
        self.assertEqual(view.cache_rate, 75)
        self.assertEqual(view.distributions["model"]["tokens"], [("A", 425)])
        self.assertEqual(view.distributions["project"]["cost"], [("app", 1.2)])

    def test_units_and_currency(self):
        self.assertEqual(chinese_tokens(12345), "1.23万")
        self.assertEqual(chinese_tokens(100_000_000), "1亿")
        self.assertEqual(chinese_tokens(1_000_000_000_000), "1万亿")
        self.assertEqual(format_cost(1.25, "CNY", 6.8), "¥8.50")
        self.assertEqual(format_cost(1.25), "$1.25")
        self.assertEqual(format_cost(1.25, "CNY"), "¥—")
        self.assertEqual(format_cost(0.0012), "$0.0012")

    def test_windows_preserve_totals_and_empty_hours(self):
        now = datetime.now().astimezone().replace(hour=9, minute=30)
        row = dict(
            bucketStart=now.isoformat(),
            inputTokens=100,
            outputTokens=20,
            cachedInputTokens=300,
            reasoningOutputTokens=5,
            estimatedCost=1.2,
        )
        slots, values = trend_series([row], [], "today", None, "tokens", now)
        self.assertEqual((len(slots), slots[0].hour, slots[-1].hour), (24, 0, 23))
        self.assertEqual(sum(values), tokens(row))
        self.assertEqual(values[10:], [0] * 14)
        for days in (7, 30, 90):
            old = dict(row, bucketStart=(now - timedelta(days=days - 1)).isoformat())
            slots, values = trend_series([row, old], [], str(days), None, "cost", now)
            self.assertEqual(len(slots), days)
            self.assertAlmostEqual(sum(values), 2.4)

    def test_cache_and_duration(self):
        self.assertEqual(cache_hit_rate([dict(inputTokens=100, cachedInputTokens=300)]), 75)
        self.assertIsNone(cache_hit_rate([]))
        self.assertIsNone(cache_hit_rate([dict(inputTokens=1)]))
        self.assertIsNone(cache_hit_rate([dict(inputTokens=0, cachedInputTokens=0)]))
        self.assertEqual(format_duration(90060), "1d 1h 1m")
        self.assertEqual(format_duration(3600), "1h 0m")
