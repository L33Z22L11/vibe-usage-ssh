import asyncio
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from rich.color import Color
from rich.text import Text
from textual import events
from textual.geometry import Offset
from textual.screen import ModalScreen
from textual.widgets import Footer, Select

from vibe_usage_ssh.charts import ChartTooltip, Trend
from vibe_usage_ssh.ui import UsageApp
from vibe_usage_ssh.usage import DISTRIBUTIONS, QUICK_FILTERS, tokens
from vibe_usage_ssh.view import build_view
from vibe_usage_ssh.widgets import AllThemesProvider, DistributionRows


def fixture():
    stamp = datetime.now().astimezone().isoformat()
    return {
        "buckets": [
            dict(
                bucketStart=stamp,
                source="codex" if i < 2 else "claude",
                hostname="mac" if i < 3 else "other",
                model="model-" + str(i),
                project="很长的项目名称用于检查截断和右侧数字" + str(i),
                inputTokens=100 * (i + 1),
                outputTokens=20,
                cachedInputTokens=300,
                reasoningOutputTokens=5,
                estimatedCost=6 - i,
            )
            for i in range(5)
        ],
        "sessions": [
            dict(firstMessageAt=stamp, source="codex", hostname="mac", activeSeconds=90060)
        ],
    }


class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Rendering assertions do not need asyncio's expensive per-task debug tracing.
        asyncio.get_running_loop().set_debug(False)
        exchange = patch("vibe_usage_ssh.ui.fetch_exchange_rate", return_value=(6.8, "2026-11-13"))
        self.exchange = exchange.start()
        self.addCleanup(exchange.stop)

    async def test_resize_alignment_and_scroll_bounds(self):
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()):
            app = UsageApp()
            async with app.run_test(size=(38, 30)) as pilot:
                for width, height, columns in [
                    (38, 30, 1),
                    (100, 42, 2),
                    (160, 55, 4),
                    (200, 60, 4),
                    (38, 30, 1),
                ]:
                    with self.subTest(size=(width, height)):
                        await pilot.resize_terminal(width, height)
                        await pilot.pause()
                        cards = [app.query_one("#" + key) for _, key, _, _ in DISTRIBUTIONS]
                        self.assertEqual(len({c.region.x for c in cards}), columns)
                        self.assertEqual(
                            cards[-1].region.right,
                            app.query_one("#distributions").content_region.right,
                        )
                        for start in range(0, 4, columns):
                            group = cards[start : start + columns]
                            self.assertEqual(len({c.region.bottom for c in group}), 1)
                            self.assertLessEqual(
                                max(c.size.width for c in group) - min(c.size.width for c in group),
                                1,
                            )
                        body = app.query_one("#body")
                        self.assertEqual(
                            body.max_scroll_y,
                            max(0, cards[-1].region.bottom - body.content_region.bottom),
                        )
                        self.assertEqual(app.query_one(Footer).size.height, 1)
                        roomy = height >= (50 if width < 72 else 40)
                        expected_controls = 3 if roomy else 1
                        self.assertEqual(app.query_one("#toolbar").size.height, expected_controls)
                        selects = [app.query_one("#quick-" + key) for key, _ in QUICK_FILTERS]
                        filter_columns = 4 if width >= 72 else 2
                        self.assertEqual(len({s.region.x for s in selects}), filter_columns)
                        self.assertEqual(
                            selects[-1].region.right, app.query_one("#topbar").content_region.right
                        )
                        for start in range(0, 4, filter_columns):
                            group = selects[start : start + filter_columns]
                            self.assertEqual(len({s.region.y for s in group}), 1)
                            self.assertTrue(
                                all(
                                    a.region.right + 1 == b.region.x
                                    for a, b in zip(group, group[1:], strict=False)
                                )
                            )
                        self.assertEqual(
                            app.query_one(
                                "#button-range-custom" if roomy else "#range-custom"
                            ).region.right,
                            app.query_one("#topbar").content_region.right,
                        )
                        self.assertEqual(
                            app.query_one("#chart-card").region.y,
                            app.query_one("#summary").region.bottom,
                        )
                        self.assertEqual(
                            app.query_one("#trend").size.height, max(8, min(28, (height - 10) // 2))
                        )
                        for _dim, key, _, _ in DISTRIBUTIONS:
                            data = app.query_one("#" + key + "-data", DistributionRows)
                            lines = data.render().split("\n")
                            self.assertTrue(
                                all(line.cell_len == data.content_size.width for line in lines[::2])
                            )
                            self.assertTrue(all(set(line.plain) <= {"▀"} for line in lines[1::2]))
                            self.assertTrue(
                                all(line.cell_len <= data.content_size.width for line in lines)
                            )
                            header = data.parent.query_one(".distribution-header")
                            self.assertEqual(data.region.y - header.region.bottom, 1)

    async def test_controls_tooltips_and_theme(self):
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()) as fetch:
            app = UsageApp()
            async with app.run_test(size=(120, 45), tooltips=True) as pilot:
                await pilot.pause()
                self.assertIsNotNone(app.data)
                self.assertNotEqual(app.status, "更新失败")
                for metric in ("tokens", "cost", "active"):
                    await pilot.click("#mode-" + metric)
                    for other in ("tokens", "cost", "active"):
                        style = (
                            app.query_one("#mode-" + other)
                            .render()
                            .get_style_at_offset(app.console, 0)
                        )
                        self.assertEqual(bool(style.dim), other != metric)
                    chart = app.query_one("#trend", Trend)
                    await pilot.hover("#trend", offset=(chart.size.width - 3, 1))
                    tooltip = app.query_one(ChartTooltip)
                    self.assertTrue(tooltip.display)
                    self.assertEqual(tuple(tooltip.styles.padding), (0, 0, 0, 0))
                    self.assertEqual(tuple(tooltip.styles.margin), (0, 0, 0, 0))
                    self.assertEqual(tooltip.region, tooltip.content_region)
                    self.assertEqual(tooltip.size.height, 5 if metric == "tokens" else 2)
                    self.assertEqual(
                        len(chart.details.plain.splitlines()), 5 if metric == "tokens" else 2
                    )
                    self.assertTrue(chart.details.get_style_at_offset(app.console, 0).bold)
                    for x in (3, 9, 20):
                        await pilot.hover("#trend", offset=(x, 1))
                        self.assertTrue(tooltip.display, "tooltip must remain visible across bars")
                    await pilot.mouse_down("#trend", offset=(3, 1))
                    await pilot.hover("#trend", offset=(20, 1))
                    self.assertTrue(chart.dragging)
                    await pilot.mouse_up("#trend", offset=(20, 1))
                    self.assertFalse(chart.dragging)
                    await pilot.hover("#toolbar")
                    self.assertIsNone(chart.details)
                    self.assertIn("更新于", str(app.query_one("#updated").renderable))
                await pilot.click("#dist-hostname-cost")
                self.assertEqual(app.distribution_modes["hostname"], "cost")
                self.assertEqual(app.distribution_modes["source"], "tokens")
                self.assertEqual(app.metric, "active")
                for theme in app.theme_names():
                    calls = fetch.call_count
                    app.theme = theme
                    await pilot.pause()
                    self.assertEqual(fetch.call_count, calls)
                    self.assertEqual(app.metric_color("cost"), app.theme_color("success"))
                    self.assertEqual(app.metric_color("active"), app.distribution_palette()[0])
                    for key in ("cost", "active"):
                        text = app.query_one("#" + key).renderable
                        style = text.get_style_at_offset(app.console, text.plain.index("\n") + 1)
                        self.assertEqual(style.color, Color.parse(app.metric_color(key)))
                    data = app.query_one("#models-data", DistributionRows).render()
                    self.assertTrue(
                        {span.style for span in data.spans}.issubset(
                            set(app.distribution_palette())
                        )
                    )
                before = app.query_one("#body").region
                await pilot.click("#quick-source", offset=(2, 1))
                self.assertTrue(app.query_one("#quick-source", Select).expanded)
                self.assertEqual(app.query_one("#body").region, before)
                await pilot.press("down", "enter")
                self.assertTrue(app.filters["source"])
                await pilot.click("#button-range-custom")
                self.assertIsInstance(app.screen, ModalScreen)
                await pilot.press("escape")
                await pilot.click("#button-range-custom")
                await pilot.click(offset=(0, 0))
                self.assertNotIsInstance(app.screen, ModalScreen)
                self.assertEqual(app.query_one("#body").region, before)
                await pilot.click("#button-range-today")
                await pilot.pause()
                self.assertEqual(
                    (chart.labels[0], chart.labels[-1], len(chart.values)), ("00:00", "23:00", 24)
                )
                self.assertNotIn(
                    "Save screenshot", [c.title for c in app.get_system_commands(app.screen)]
                )
                await pilot.press("ctrl+p")
                self.assertIn("Command", type(app.screen).__name__)
                await pilot.press("escape")

    async def test_project_filter_and_global_display_units(self):
        data = fixture()
        first = data["buckets"][0]
        first["inputTokens"] = 12345
        data["sessions"][0]["project"] = first["project"]
        data["sessions"].append(dict(data["sessions"][0], project="other", activeSeconds=3600))
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=data) as fetch:
            app = UsageApp()
            async with app.run_test(size=(160, 55), tooltips=True) as pilot:
                await pilot.pause()
                calls = fetch.call_count
                await pilot.click("#quick-project", offset=(2, 1))
                await pilot.press("down", "enter")
                self.assertEqual(app.filters["project"], {first["project"]})
                self.assertEqual(app.visible_rows, [first])
                self.assertIn("1d 1h 1m", app.query_one("#active").renderable.plain)
                await pilot.click("#cost")
                self.assertEqual(app.currency, "CNY")
                self.assertIn("¥40.80", app.query_one("#cost").renderable.plain)
                await pilot.click("#hero")
                self.assertEqual(app.token_unit, "chinese")
                self.assertIn("1.27万", app.query_one("#hero").renderable.plain)
                for dim, key, _, _ in DISTRIBUTIONS:
                    card = app.query_one("#" + key + "-data", DistributionRows)
                    self.assertIn("1.27万", card.render().plain)
                    await pilot.click("#dist-" + dim + "-cost")
                    self.assertIn("¥40.80", card.render().plain)
                await pilot.hover("#trend", offset=(app.query_one("#trend").size.width - 3, 1))
                chart = app.query_one("#trend", Trend)
                self.assertIn("1.27万", chart.details.plain)
                self.assertIn("¥40.80", chart.details.plain)
                self.assertIn("输入 1.23万", chart.details.plain)
                await pilot.click("#mode-cost")
                await pilot.hover("#trend", offset=(chart.size.width - 3, 1))
                self.assertIn("¥40.80", chart.details.plain)
                await pilot.press("c", "t")
                self.assertEqual((app.currency, app.token_unit), ("USD", "international"))
                self.assertIn("$6.00", app.query_one("#cost").renderable.plain)
                self.assertEqual(app.format_tokens(tokens(first)), "12.67K")
                self.assertEqual(fetch.call_count, calls)
                self.assertNotIn("筛选", [binding.description for binding in app.BINDINGS])
                app.query_one("#hero").focus()
                await pilot.press("enter")
                self.assertEqual(app.token_unit, "chinese")
                app.query_one("#cost").focus()
                await pilot.press("space")
                self.assertEqual(app.currency, "CNY")

    async def test_exchange_failure_does_not_invent_a_rate(self):
        self.exchange.side_effect = OSError("offline")
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()):
            app = UsageApp()
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await pilot.press("c")
                await pilot.pause()
                self.assertIsNone(app.exchange_rate)
                self.assertIn("¥—", app.query_one("#cost").renderable.plain)
                self.assertNotEqual(app.status, "更新失败")
                app.exchange_rate = 6.8
                app.refresh_exchange_rate()
                await pilot.pause()
                self.assertEqual(app.exchange_rate, 6.8)

    async def test_touch_dismissal_and_tooltip_edges(self):
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()):
            app = UsageApp()
            async with app.run_test(size=(56, 43)) as pilot:
                await pilot.pause()

                async def tap(x, y):
                    app.post_message(events.MouseDown(None, x, y, 0, 0, 1, False, False, False))
                    await pilot.pause(0.15)
                    app.post_message(events.MouseUp(None, x, y, 0, 0, 1, False, False, False))
                    await pilot.pause()

                select = app.query_one("#quick-source", Select)
                for expected in (True, False, True):
                    await tap(
                        *(
                            int(value)
                            for value in select.query_one("SelectCurrent").content_region.center
                        )
                    )
                    self.assertEqual(select.expanded, expected)
                await tap(0, 0)
                self.assertFalse(select.expanded)

                chart = app.query_one(Trend)
                await tap(chart.region.x + 2, chart.region.y + 1)
                tooltip = app.query_one(ChartTooltip)
                self.assertTrue(tooltip.display)
                await tap(0, 0)
                self.assertFalse(tooltip.display)
                await tap(chart.region.x + 2, chart.region.y + 1)
                app.post_message(events.AppBlur())
                await pilot.pause()
                self.assertFalse(tooltip.display)
                for x, visible in ((5, True), (3, False), (1, False), (0, False), (5, True)):
                    app.post_message(
                        events.MouseMove(None, x, chart.region.y + 1, 0, 0, 0, False, False, False)
                    )
                    await pilot.pause()
                    self.assertEqual(tooltip.display, visible)
                    if not visible:
                        self.assertIsNone(chart.details)
                        self.assertIsNone(chart.selected)
                        self.assertIn("更新于", str(app.query_one("#updated").renderable))
                details = Text(
                    "2026/11/16\n总Token 1.23亿\n输入 12.3万 输出 456k\n缓存命中率 80%\n费用 ¥123.45"
                )
                for point in (Offset(0, 0), Offset(55, 0), Offset(0, 42), Offset(55, 42)):
                    tooltip.show(details, point)
                    await pilot.pause()
                    self.assertTrue(app.screen.region.contains_region(tooltip.region))
                    self.assertGreaterEqual(tooltip.region.x, 3)
                    self.assertEqual(tooltip.region, tooltip.content_region)
                    self.assertEqual(tooltip.size.height, 5)
                for x in (0, 4, 15, 30, 55):
                    tooltip.show(details, Offset(x, chart.region.y + 1))
                    await pilot.pause()
                    # Verify final compositing, not just the tooltip's own renderable.
                    # Textual's cuts at the underlying chart edge used to erase 总/输/缓/费.
                    strips = app.screen._compositor.render_strips()
                    actual = [
                        strips[y].crop(tooltip.region.x, tooltip.region.right).text.rstrip()
                        for y in tooltip.region.line_range
                    ]
                    self.assertEqual(actual, details.plain.splitlines())

    async def test_client_preferences_restore_and_reset_without_changing_theme(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()),
        ):
            path = Path(directory) / "client.json"
            with patch("vibe_usage_ssh.ui.preferences_path", return_value=path):
                app = UsageApp()
                async with app.run_test(size=(100, 40)) as pilot:
                    await pilot.pause()
                    app.query_one("#quick-source", Select).value = ("value", "codex")
                    app.action_change_range("7")
                    app.theme = "nord"
                    await pilot.pause()
                restored = UsageApp()
                self.assertEqual(restored.theme, "nord")
                self.assertEqual(restored.range_key, "7")
                self.assertEqual(restored.filters["source"], {"codex"})
                async with restored.run_test(size=(100, 40)) as pilot:
                    await pilot.pause()
                    self.assertEqual(
                        restored.query_one("#quick-source", Select).value, ("value", "codex")
                    )
                    await pilot.press("x")
                    self.assertEqual(restored.theme, "nord")
                    self.assertEqual(restored.range_key, "1")
                    self.assertTrue(all(not values for values in restored.filters.values()))
                    self.assertEqual(restored.query_one("#quick-source", Select).value, ("all",))
                reset = UsageApp()
                self.assertEqual(reset.theme, "nord")
                self.assertEqual(reset.range_key, "1")
                self.assertTrue(all(not values for values in reset.filters.values()))
            with patch(
                "vibe_usage_ssh.ui.preferences_path", return_value=Path(directory) / "other.json"
            ):
                other = UsageApp()
                self.assertEqual(other.theme, "textual-ansi")
                self.assertEqual(other.range_key, "1")

    async def test_daily_bars_scroll_without_merging(self):
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()):
            app = UsageApp()
            async with app.run_test(size=(38, 40)) as pilot:
                await pilot.pause()
                chart = app.query_one(Trend)
                dates = [f"day-{i + 1:02d}" for i in range(90)]
                chart.set_data(dates, list(range(90)), [dict(time=day) for day in dates])
                await pilot.pause()
                self.assertEqual(len(chart.bins()), 90)
                self.assertTrue(all(end - start == 1 for start, end, _, _ in chart.bins()))
                viewport = app.query_one("#trend-scroll")
                self.assertGreater(viewport.max_scroll_x, 0)
                scrollbar = viewport.horizontal_scrollbar
                segments = list(
                    app.console.render(
                        scrollbar.render(),
                        app.console.options.update(width=scrollbar.size.width, height=1),
                    )
                )
                track = "".join(segment.text for segment in segments)
                self.assertIn("━", track)
                self.assertIn("─", track)
                self.assertNotIn("█", track)
                await pilot.mouse_down(scrollbar, offset=(1, 0))
                await pilot.hover(scrollbar, offset=(scrollbar.size.width // 2, 0))
                await pilot.mouse_up(scrollbar, offset=(scrollbar.size.width // 2, 0))
                self.assertGreater(viewport.scroll_x, 0)
                viewport.scroll_to(x=viewport.max_scroll_x, animate=False)
                await pilot.pause()
                await pilot.hover(offset=(viewport.content_region.right - 1, viewport.region.y + 1))
                self.assertEqual(chart.selected, 89)
                self.assertEqual(chart.details.plain.splitlines()[0], "day-90")

    async def test_long_filter_label_wraps_and_uses_full_width(self):
        data = fixture()
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=data):
            app = UsageApp()
            async with app.run_test(size=(38, 40)) as pilot:
                await pilot.pause()
                project = app.query_one("#quick-project", Select)
                project.value = ("value", data["buckets"][0]["project"])
                await pilot.pause()
                label = project.query_one("#label")
                self.assertEqual(str(label.renderable), data["buckets"][0]["project"])
                self.assertGreater(label.size.height, 1)
                self.assertGreaterEqual(project.size.width, 17)
                self.assertEqual(app.query_one("#quick-filters").max_scroll_x, 0)

    async def test_sticky_header_menu_translation_and_date_dialog(self):
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()):
            app = UsageApp()
            async with app.run_test(size=(56, 43)) as pilot:
                await pilot.pause()
                body, top = app.query_one("#body"), app.query_one("#topbar")
                self.assertFalse(app.sticky_top)
                original = top.region.y
                body.scroll_to(y=10, animate=False)
                await pilot.pause()
                self.assertLess(top.region.y, original)
                await pilot.press("s")
                self.assertTrue(app.sticky_top)
                self.assertEqual(top.region.y, original)
                body.scroll_to(y=20, animate=False)
                await pilot.pause()
                self.assertEqual(top.region.y, original)
                await pilot.click("#quick-source", offset=(2, 0))
                self.assertEqual(app.screen.active_bindings["escape"].binding.description, "收起")
                await pilot.press("escape")
                await pilot.click("#range-custom")
                dialog = app.screen.query_one("#date-dialog")
                shadow = app.screen.query_one("#date-shadow")
                start, end = app.screen.query_one("#date-from"), app.screen.query_one("#date-to")
                actions = app.screen.query_one(".dialog-actions")
                self.assertEqual(start.region.y, end.region.y)
                self.assertEqual(actions.region.y - start.region.bottom, 1)
                self.assertEqual(dialog.styles.border.top[0], "solid")
                self.assertGreater(shadow.region.right, dialog.region.right)
                self.assertEqual(shadow.region.bottom - dialog.region.bottom, 1)
                self.assertEqual(actions.region.bottom, dialog.content_region.bottom)
                await pilot.press("escape")
                await pilot.press("s")
                self.assertFalse(app.sticky_top)

    async def test_monochrome_tokens_and_metric_colors(self):
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()):
            app = UsageApp()
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                chart = app.query_one(Trend)
                self.assertEqual(app.theme, "textual-ansi")
                self.assertTrue(app.ansi_color)
                self.assertEqual(app.metric_color("tokens"), "default")
                for cached, component in ((100, "trend--cached"), (0, "trend--uncached")):
                    chart.set_data(["00:00"], [100], [dict(time="00:00", cached=cached)])
                    await pilot.pause()
                    style = chart.render().get_style_at_offset(app.console, 0)
                    self.assertEqual(style.color, chart.get_component_rich_style(component).color)
                    self.assertEqual(bool(style.dim), cached > 0)
                # Output and reasoning belong to the bright cap, even without input tokens.
                row = dict(
                    inputTokens=0, outputTokens=70, reasoningOutputTokens=30, cachedInputTokens=100
                )
                chart.set_data(["00:00"], [tokens(row)], [dict(time="00:00", cached=100)])
                await pilot.pause()
                rendered = chart.render()
                lines = rendered.plain.splitlines(keepends=True)
                height = chart.content_size.height - 1
                for line, component in (
                    (height // 4, "trend--uncached"),
                    (height * 3 // 4, "trend--cached"),
                ):
                    offset = sum(len(text) for text in lines[:line])
                    self.assertEqual(
                        rendered.get_style_at_offset(app.console, offset).color,
                        chart.get_component_rich_style(component).color,
                    )
                    self.assertEqual(
                        bool(rendered.get_style_at_offset(app.console, offset).dim),
                        component == "trend--cached",
                    )
                self.assertEqual(app.metric_color("cost"), app.distribution_palette()[3])
                self.assertEqual(app.metric_color("active"), app.distribution_palette()[0])

    async def test_theme_menu_and_cycle_include_native_colors(self):
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()) as fetch:
            app = UsageApp()
            async with app.run_test(size=(80, 35)) as pilot:
                await pilot.pause()
                calls = fetch.call_count
                names = app.theme_names()
                self.assertEqual(set(names), set(app.available_themes))
                provider = AllThemesProvider(app.screen)
                self.assertEqual([name for name, _ in provider.commands], names)
                self.assertIn("更换主题", [c.title for c in app.get_system_commands(app.screen)])
                for expected in names[1:] + names[:1]:
                    await pilot.press("p")
                    await pilot.pause()
                    self.assertEqual(app.theme, expected)
                    self.assertEqual(app.ansi_color, expected == "textual-ansi")
                self.assertEqual(fetch.call_count, calls)
                app.search_themes()
                await pilot.pause()
                await pilot.press("escape")
                self.assertEqual(app.theme, "textual-ansi")

    def test_token_segments_use_proportional_eighth_rows_without_minimums(self):
        for cached, units in ((0, 0), (0.3, 2), (0.5, 4), (1.3, 10), (1.5, 12), (2.5, 20)):
            base, cap = Trend.token_bar_units(cached + 0.2, cached, 1)
            self.assertEqual(base, units)
            self.assertEqual(cap, 2)
        self.assertEqual(Trend.token_bar_units(0, 0, 1), (0, 0))
        self.assertEqual(Trend.token_bar_units(0.3, 0.3, 1), (2, 0))
        self.assertEqual(Trend.token_bar_units(0.3001, 0.3, 1), (2, 0))
        self.assertEqual(Trend.token_bar_units(0.001, 0.001, 1), (0, 0))

    async def test_short_bars_stay_proportional_and_single_column(self):
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()):
            app = UsageApp()
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                chart = app.query_one(Trend)
                for theme in ("textual-ansi", "catppuccin-mocha"):
                    app.theme = theme
                    await pilot.pause()
                    height = chart.content_size.height - 1
                    for fill in (1, 4):
                        short = 1000 * fill / (height * 8)
                        chart.set_data(
                            ["old", "today"],
                            [1000, short],
                            [dict(time="old", cached=1000), dict(time="today", cached=short * 0.8)],
                        )
                        await pilot.pause()
                        rendered = chart.render()
                        lines = rendered.plain.splitlines(keepends=True)
                        _, _, left, right = chart.bins()[1]
                        width = right - left - 1
                        bottom = sum(len(line) for line in lines[: height - 1]) + left
                        above = sum(len(line) for line in lines[: height - 2]) + left
                        self.assertEqual(
                            rendered.plain[bottom : bottom + width], " ▁▂▃▄▅▆▇█"[fill] * width
                        )
                        self.assertEqual(rendered.plain[above : above + width], " " * width)
                        cached = chart.get_component_rich_style("trend--cached")
                        for offset in range(bottom, bottom + width):
                            style = rendered.get_style_at_offset(app.console, offset)
                            self.assertEqual(style.color, cached.color)
                            self.assertEqual(bool(style.dim), bool(cached.dim))
                    chart.set_data(
                        [str(i) for i in range(90)],
                        [1000] + [short] * 89,
                        [
                            dict(time=str(i), cached=1000 if i == 0 else short * 0.8)
                            for i in range(90)
                        ],
                    )
                    await pilot.pause()
                    self.assertEqual(chart.content_size.width, 180)
                    rendered = chart.render()
                    lines = rendered.plain.splitlines(keepends=True)
                    offset = sum(len(line) for line in lines[: height - 1]) + chart.bins()[1][2]
                    self.assertEqual(rendered.plain[offset : offset + 2], "▄ ")

    async def test_roomy_threshold_and_dense_axis_labels(self):
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()):
            app = UsageApp()
            async with app.run_test(size=(60, 45)) as pilot:
                await pilot.pause()
                self.assertFalse(app.screen.has_class("roomy"))
                self.assertEqual(app.query_one(Trend).size.height, 17)
                await pilot.resize_terminal(100, 45)
                self.assertTrue(app.screen.has_class("roomy"))
                await pilot.resize_terminal(60, 50)
                self.assertTrue(app.screen.has_class("roomy"))
                await pilot.resize_terminal(100, 45)
                chart = app.query_one(Trend)
                labels = [f"{i:02}:00" for i in range(24)]
                chart.set_data(labels, [1] * 24)
                await pilot.pause()
                axis = chart.render().plain.splitlines()[-1]
                shown = [label for label in labels if label in axis]
                self.assertGreater(len(shown), 8)
                self.assertIn(labels[0], shown)
                self.assertIn(labels[-1], shown)
                self.assertLessEqual(len(axis), chart.content_size.width)
                self.assertTrue(
                    all(
                        axis.index(b) - axis.index(a) >= 6
                        for a, b in zip(shown, shown[1:], strict=False)
                    )
                )

    async def test_chart_render_and_hover_avoid_repeated_work(self):
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()):
            app = UsageApp()
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                chart = app.query_one(Trend)
                tooltip = app.query_one(ChartTooltip)
                chart.set_data(
                    [str(i) for i in range(90)],
                    [100] * 90,
                    [dict(time=str(i), tokens=100, cached=70) for i in range(90)],
                )
                await pilot.pause()
                with patch.object(app, "get_css_variables", wraps=app.get_css_variables) as colors:
                    for _ in range(3):
                        chart.render()
                    self.assertEqual(colors.call_count, 0)
                with patch.object(tooltip, "show", wraps=tooltip.show) as show:
                    left = chart.bins()[2][2]
                    chart.inspect_at(left)
                    with patch.object(chart, "refresh", wraps=chart.refresh) as refresh:
                        for _ in range(20):
                            chart.inspect_at(left + 1)
                        self.assertEqual(refresh.call_count, 0)
                    self.assertEqual(show.call_count, 1)
                    chart.inspect_at(chart.bins()[3][2])
                    self.assertEqual(show.call_count, 2)
                    self.assertEqual(chart.selected, 3)
                chart.hide_details()
                with patch.object(chart, "refresh", wraps=chart.refresh) as refresh:
                    chart.hide_details()
                    self.assertEqual(refresh.call_count, 0)

    async def test_summary_loading_and_failure_states_are_consistent(self):
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()) as fetch:
            app = UsageApp()
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()

                def check_state(message):
                    for key, title in app.SUMMARY_TITLES.items():
                        content = app.query_one("#" + key).renderable
                        self.assertEqual(content.plain, title + "\n" + message)
                        self.assertTrue(
                            content.get_style_at_offset(app.console, len(title) + 1).dim
                        )

                fetch.side_effect = OSError("offline")
                app.action_change_range("7")
                check_state("加载中…")
                await pilot.pause()
                check_state("更新失败")
                fetch.side_effect = None
                app.refresh_data()
                check_state("加载中…")
                await pilot.pause()
                await app.workers.wait_for_complete()
                for key, title in app.SUMMARY_TITLES.items():
                    content = app.query_one("#" + key).renderable.plain
                    self.assertTrue(content.startswith(title + "\n"))
                    self.assertNotIn("加载中", content)
                before = {key: app.query_one("#" + key).renderable for key in app.SUMMARY_TITLES}
                app.refresh_data()
                self.assertEqual(
                    before, {key: app.query_one("#" + key).renderable for key in before}
                )
                await pilot.pause()
                today = datetime.now().date().isoformat()
                app.date_chosen((today, today))
                check_state("加载中…")
                await pilot.pause()

    async def test_display_changes_reuse_statistics_and_refresh_preserves_hover(self):
        data = fixture()
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=data):
            app = UsageApp()
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                with patch("vibe_usage_ssh.ui.build_view", wraps=build_view) as aggregate:
                    chart = app.query_one(Trend)
                    rows = app.query_one("#hosts-data", DistributionRows)
                    with (
                        patch.object(chart, "refresh", wraps=chart.refresh) as chart_refresh,
                        patch.object(rows, "refresh", wraps=rows.refresh) as rows_refresh,
                    ):
                        app.action_currency()
                        self.assertEqual(chart_refresh.call_count, 0)
                        self.assertEqual(rows_refresh.call_count, 0)
                    await pilot.press("c", "t", "p")
                    app.set_metric("cost")
                    app.distribution_modes["hostname"] = "cost"
                    app.render_distributions("hostname")
                    await pilot.pause()
                    self.assertEqual(aggregate.call_count, 0)
                chart = app.query_one(Trend)
                await pilot.hover(chart, offset=(chart.size.width - 3, 1))
                self.assertTrue(app.query_one(ChartTooltip).display)
                selected = chart.selected
                data["buckets"][0]["estimatedCost"] += 1
                app.refresh_data()
                await pilot.pause()
                self.assertTrue(app.query_one(ChartTooltip).display)
                self.assertEqual(chart.selected, selected)
                self.assertEqual(
                    app.query_one("#updated").renderable.plain, chart.details.plain.splitlines()[0]
                )

    async def test_filter_aggregation_keeps_ui_responsive_and_uses_latest_selection(self):
        with patch("vibe_usage_ssh.ui.fetch_usage", return_value=fixture()):
            app = UsageApp()
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                started, release = threading.Event(), threading.Event()

                def slow_build(*args):
                    started.set()
                    if not release.wait(5):
                        raise TimeoutError("test did not release aggregation")
                    return build_view(*args)

                with patch("vibe_usage_ssh.ui.build_view", side_effect=slow_build):
                    try:
                        app.filters["source"] = {"codex"}
                        app.render_data()
                        self.assertTrue(await asyncio.to_thread(started.wait, 2))
                        await pilot.press("t")
                        self.assertEqual(app.token_unit, "chinese")
                        app.filters["source"] = {"claude"}
                        app.render_data()
                    finally:
                        release.set()
                    await pilot.pause()
                    await app.workers.wait_for_complete()
                self.assertEqual({row["source"] for row in app.visible_rows}, {"claude"})
