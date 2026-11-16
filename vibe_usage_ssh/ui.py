#!/usr/bin/env python3
"""One responsive dashboard instance per SSH session."""

import asyncio
from datetime import date, datetime

from rich.text import Text
from textual import events, on
from textual.app import App, SystemCommand
from textual.binding import Binding
from textual.color import Color
from textual.command import CommandPalette
from textual.containers import Grid, Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Select, Static

from .api import fetch_exchange_rate, fetch_usage
from .charts import ChartTooltip, ThinScrollBarRender, Trend
from .diagnostics import configure_logging, log_failure
from .preferences import load_preferences, preferences_path, save_preferences
from .usage import (
    DISTRIBUTIONS,
    QUICK_FILTERS,
    RANGES,
    chinese_tokens,
    compact,
    display,
    format_cost,
    format_duration,
)
from .view import build_view
from .widgets import (
    AllThemesProvider,
    DateScreen,
    DistributionRows,
    DistributionTab,
    FilterSelect,
    MetricTab,
    RangeTab,
    UnitCard,
)


class UsageApp(App):
    TITLE = "Vibe Usage SSH"
    SUMMARY_TITLES = {
        "cost": "预估费用",
        "hero": "总Token",
        "cache-rate": "缓存命中率",
        "active": "活跃时长",
    }
    CSS_PATH = "dashboard.tcss"
    ENABLE_COMMAND_PALETTE = True
    TOOLTIP_DELAY = 0
    BINDINGS = [
        Binding("c", "currency", "币种"),
        Binding("t", "token_unit", "单位"),
        Binding("s", "sticky_top", "吸顶"),
        Binding("p", "cycle_theme", "主题"),
        Binding("x", "reset_filters", "重置"),
        Binding("r", "refresh_data", "刷新"),
        Binding("e", "exact", "数值", show=False),
        Binding("q", "quit", "退出"),
        Binding("1", "change_range('today')", "今天", show=False),
        Binding("2", "change_range('1')", "24H", show=False),
        Binding("3", "change_range('7')", "7D", show=False),
        Binding("4", "change_range('30')", "30D", show=False),
        Binding("5", "change_range('90')", "90D", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.theme = "textual-ansi"
        self._colors_theme = None
        self._theme_colors = {}
        self.range_key = "1"
        self.dates = None
        self.filters = {key: set() for key, _ in QUICK_FILTERS}
        self.data = None
        self.view = None
        self._view_data = None
        self._view_key = None
        self._view_request = None
        self._view_loading = False
        self.generation = 0
        self.status = "正在获取用量…"
        self.metric = "tokens"
        self.exact = False
        self.currency = "USD"
        self.token_unit = "international"
        self.exchange_rate = None
        self.exchange_date = None
        self.exchange_loading = False
        self.distribution_modes = {dim: "tokens" for dim, _, _, _ in DISTRIBUTIONS}
        self.visible_rows = []
        self.closing_filter_tap = False
        self.sticky_top = False
        self._preferences_path = preferences_path()
        preferences = load_preferences(self._preferences_path, self.available_themes)
        self.theme = preferences.get("theme", self.theme)
        self.range_key = preferences.get("range_key", self.range_key)
        self.dates = preferences.get("dates")
        self.filters.update(preferences.get("filters", {}))
        self._saved_preferences = self.preference_values()

    def compose(self):
        with VerticalScroll(id="body"):
            with Vertical(id="topbar"):
                with Horizontal(id="toolbar"):
                    for key, label in RANGES:
                        yield RangeTab(key, label)
                        yield Button(
                            label,
                            id="button-range-" + key,
                            classes="range-button",
                            variant="primary" if self.range_key == key else "default",
                        )
                with Grid(id="quick-filters"):
                    for dim, title in QUICK_FILTERS:
                        selected = next(iter(self.filters[dim]), None)
                        options = [(title + " 全部", ("all",))]
                        if selected is not None:
                            options.append((selected, ("value", selected)))
                        yield FilterSelect(
                            options,
                            value=("value", selected) if selected is not None else ("all",),
                            allow_blank=False,
                            id="quick-" + dim,
                        )
            with Grid(id="summary"):
                for key, title in self.SUMMARY_TITLES.items():
                    widget = UnitCard if key in ("cost", "hero") else Static
                    yield widget(
                        self.summary_placeholder(title, "加载中…"),
                        id=key,
                        classes="" if key == "hero" else "metric",
                    )
            with Vertical(id="chart-card"):
                with Horizontal(id="mode-row"):
                    yield Static("", id="updated")
                    yield MetricTab("tokens", "Token")
                    yield MetricTab("cost", "费用")
                    yield MetricTab("active", "活跃")
                with HorizontalScroll(id="trend-scroll"):
                    yield Trend(id="trend")
            with Vertical(id="distributions"):
                for dim, key, icon, title in DISTRIBUTIONS:
                    with Vertical(id=key, classes="distribution"):
                        with Horizontal(classes="distribution-header"):
                            yield Static(icon + " " + title, classes="distribution-title")
                            yield DistributionTab(dim, "tokens", "Token")
                            yield DistributionTab(dim, "cost", "费用")
                        yield Static("", classes="distribution-spacer")
                        yield DistributionRows(id=key + "-data", classes="distribution-data")
        yield Footer()
        yield ChartTooltip(id="chart-tooltip")

    async def on_event(self, event):
        if self.screen_stack and isinstance(
            event, (events.MouseEvent, events.AppBlur, events.Resize, events.Key)
        ):
            for chart in self.screen_stack[0].query(Trend):
                dismiss = isinstance(event, (events.AppBlur, events.Resize, events.Key))
                if isinstance(event, events.MouseEvent) and not event.is_forwarded:
                    dismiss = not chart.contains_pointer(
                        event.x, event.y, hover=isinstance(event, events.MouseMove)
                    )
                if dismiss and chart.details is not None:
                    chart.hide_details()
            if self.handle_filter_tap(event):
                return
        await super().on_event(event)

    def handle_filter_tap(self, event):
        if not isinstance(event, events.MouseEvent) or event.is_forwarded:
            return False
        if isinstance(event, events.MouseUp) and self.closing_filter_tap:
            self.closing_filter_tap = False
            return True
        if not isinstance(event, events.MouseDown) or event.button != 1:
            return False
        self.closing_filter_tap = False
        for select in self.screen.query(Select):
            if not select.expanded:
                continue
            current = select.query_one("SelectCurrent")
            overlay = select.query_one("SelectOverlay")
            if current.region.contains(event.x, event.y):
                # Close before focus changes can dismiss and then reopen the
                # native Select on a delayed touch release.
                select.expanded = False
                select.focus()
                self.closing_filter_tap = True
                return True
            if not overlay.region.contains(event.x, event.y):
                select.expanded = False
        return False

    def on_resize(self, event):
        self.apply_layout(event.size.width, event.size.height)

    def on_mount(self):
        self.apply_layout(self.size.width, self.size.height)
        self.query_one("#trend-scroll").horizontal_scrollbar.renderer = ThinScrollBarRender
        self.theme_changed_signal.subscribe(self.screen, self.repaint_theme)
        self.refresh_data()
        self.refresh_exchange_rate()
        self.set_interval(60, self.refresh_data)
        self.set_interval(3600, self.refresh_exchange_rate)

    def refresh_exchange_rate(self):
        if not self.exchange_loading:
            self.exchange_loading = True
            self.run_worker(self.load_exchange_rate())

    async def load_exchange_rate(self):
        try:
            self.exchange_rate, self.exchange_date = await asyncio.to_thread(fetch_exchange_rate)
        except Exception as exc:
            log_failure("exchange rate", exc)
            if self.currency == "CNY":
                message = (
                    "汇率更新失败，沿用上次汇率"
                    if self.exchange_rate
                    else "汇率获取失败，按 c 切换后可重试"
                )
                self.notify(message, severity="warning", timeout=3)
        finally:
            self.exchange_loading = False
        self.render_data()

    def format_cost(self, value):
        return format_cost(value, self.currency, self.exchange_rate)

    def format_tokens(self, value):
        if self.exact:
            return f"{value:,.0f}"
        return chinese_tokens(value) if self.token_unit == "chinese" else compact(value)

    def action_currency(self):
        self.currency = "CNY" if self.currency == "USD" else "USD"
        if self.currency == "CNY" and self.exchange_rate is None:
            self.refresh_exchange_rate()
        self.render_data()

    def action_token_unit(self):
        self.token_unit = "chinese" if self.token_unit == "international" else "international"
        self.exact = False
        self.render_data()

    def action_sticky_top(self):
        self.sticky_top = not self.sticky_top
        self.screen_stack[0].set_class(self.sticky_top, "sticky")
        self.bind("s", "sticky_top", description="随滚" if self.sticky_top else "吸顶")
        self.screen_stack[0].refresh_bindings()
        self.screen_stack[0].query_one(Trend).hide_details()

    def apply_layout(self, width, height):
        screen = self.screen_stack[0]
        screen.set_class(width >= 72, "wide")
        screen.set_class(width >= 144, "extra-wide")
        screen.set_class(width < 38, "narrow")
        screen.set_class(height >= (50 if width < 72 else 40), "roomy")
        for footer in screen.query(Footer):
            footer.compact = width < 60
        if screen.query("#updated"):
            for key, label in RANGES:
                if key == "custom" and width < 50:
                    label = "日期"
                elif key in ("7", "30", "90") and width < 38:
                    label = key + "D"
                screen.query_one("#button-range-" + key, Button).label = label
            plot_height = max(8, min(28, (height - 10) // 2))
            screen.query_one("#trend").styles.height = plot_height
            screen.query_one("#trend-scroll").styles.height = plot_height + 1
            screen.query_one("#chart-card").styles.height = plot_height + 2
            self.update_hint()
            self.call_after_refresh(self.fit_distributions)

    def fit_distributions(self):
        screen = self.screen_stack[0]
        columns = 4 if screen.has_class("extra-wide") else 2 if screen.has_class("wide") else 1
        cards = [
            screen.query_one("#" + key + "-data", DistributionRows)
            for _, key, _, _ in DISTRIBUTIONS
        ]
        heights = [max(1, len(card.entries) * 2) + 4 for card in cards]
        rows = [max(heights[i : i + columns]) for i in range(0, len(heights), columns)]
        grid = screen.query_one("#distributions")
        grid.styles.grid_rows = " ".join(map(str, rows))
        grid.styles.height = sum(rows) + (len(rows) - 1)

    def repaint_theme(self, theme):
        self.persist_preferences()
        self._colors_theme = None
        self.render_data()
        for tab in self.screen_stack[0].query(MetricTab):
            tab.refresh()
        for tab in self.screen_stack[0].query(RangeTab):
            tab.refresh()
        for tab in self.screen_stack[0].query(DistributionTab):
            tab.refresh()
        self.screen_stack[0].query_one("#trend", Trend).refresh()

    def theme_color(self, token):
        if self._colors_theme != self.theme:
            variables = self.get_css_variables()
            self._theme_colors = {
                name: Color.parse(variables[name]).rich_color.name
                for name in (
                    "foreground",
                    "primary",
                    "secondary",
                    "accent",
                    "success",
                    "warning",
                    "error",
                )
            }
            self._colors_theme = self.theme
        return self._theme_colors[token]

    def metric_color(self, metric):
        return self.theme_color(
            {"tokens": "foreground", "cost": "success", "active": "primary"}[metric]
        )

    def theme_names(self):
        return ["textual-ansi", *(name for name in self.available_themes if name != "textual-ansi")]

    def action_cycle_theme(self):
        themes = self.theme_names()
        self.theme = themes[(themes.index(self.theme) + 1) % len(themes)]
        self.notify("终端原生配色" if self.theme == "textual-ansi" else self.theme, timeout=2)

    def search_themes(self):
        self.push_screen(
            CommandPalette(
                providers=[AllThemesProvider], placeholder="选择主题（textual-ansi 为终端原生配色）"
            )
        )

    def set_metric(self, metric):
        if self.metric == metric:
            return
        self.metric = metric
        for tab in self.screen_stack[0].query(MetricTab):
            tab.refresh()
        self.render_trend()

    def action_refresh_data(self):
        self.refresh_data()

    def preference_values(self):
        return dict(
            theme=self.theme,
            range_key=self.range_key,
            dates=self.dates,
            filters={dim: sorted(values) for dim, values in self.filters.items()},
        )

    def persist_preferences(self):
        if self._preferences_path is None:
            return
        preferences = self.preference_values()
        if preferences == self._saved_preferences:
            return
        try:
            save_preferences(self._preferences_path, preferences)
        except OSError as exc:
            log_failure("save preferences", exc)
            self.notify("偏好保存失败", severity="warning", timeout=3)
        else:
            self._saved_preferences = preferences

    def action_reset_filters(self):
        reload_range = self.range_key != "1" or self.dates is not None
        self.filters = {dim: set() for dim, _ in QUICK_FILTERS}
        self.range_key, self.dates = "1", None
        self.sync_quick_filters()
        self.update_controls()
        if reload_range:
            self.clear_range()
            self.refresh_data()
        else:
            self.render_data()
        self.persist_preferences()

    def action_change_range(self, key):
        if key == "custom":
            self.push_screen(DateScreen(self.dates), self.date_chosen)
            return
        self.range_key = key
        self.dates = None
        self.clear_range()
        self.update_controls()
        self.refresh_data()
        self.persist_preferences()

    def date_chosen(self, result):
        if result:
            self.dates = result
            self.range_key = "custom"
            self.clear_range()
            self.refresh_data()
            self.persist_preferences()
        self.update_controls()

    @staticmethod
    def summary_placeholder(title, message):
        return Text.assemble((title + "\n", "dim"), (message, "dim"))

    def set_summary_state(self, message):
        for key, title in self.SUMMARY_TITLES.items():
            self.update_text(key, self.summary_placeholder(title, message))

    def clear_range(self):
        self.data = self.view = self._view_data = self._view_request = None
        self.set_summary_state("加载中…")
        self.screen_stack[0].query_one(Trend).set_data([], [])

    def refresh_data(self):
        self.generation += 1
        if self.data is None:
            self.set_summary_state("加载中…")
        self.status = "正在更新…"
        self.update_hint()
        self.run_worker(self.load(self.generation, self.range_key, self.dates))

    async def load(self, generation, range_key, dates):
        try:
            result = await asyncio.to_thread(fetch_usage, range_key, dates)
            if generation != self.generation:
                return
            self.data = result
            self._view_data = None
            self.status = datetime.now().strftime("%H:%M:%S")
            self.sync_quick_filters()
            self.render_data()
        except Exception as exc:
            log_failure("usage request", exc)
            if generation == self.generation:
                self.status = "更新失败"
                if not self.data:
                    self.set_summary_state(self.status)
        self.update_hint()

    def update_hint(self):
        if any(chart.details is not None for chart in self.screen_stack[0].query(Trend)):
            return
        status = self.status
        if len(status) == 8 and status.count(":") == 2:
            status = "更新于 " + (status[:5] if self.size.width < 45 else status)
        self.update_text("updated", Text(status))

    def update_controls(self):
        for tab in self.screen_stack[0].query(RangeTab):
            tab.refresh()
        for button in self.screen_stack[0].query(".range-button"):
            button.variant = (
                "primary" if button.id == "button-range-" + self.range_key else "default"
            )

    @on(Button.Pressed, ".range-button")
    def range_button_pressed(self, event):
        self.action_change_range(event.button.id.removeprefix("button-range-"))

    def sync_quick_filters(self):
        for dim, title in QUICK_FILTERS:
            select = self.screen_stack[0].query_one("#quick-" + dim, Select)
            selected = self.filters[dim]
            opts = [(title + " 全部", ("all",))]
            choices = dict(self.options(dim))
            for raw in selected:
                choices.setdefault(raw, display(self.data or {}, dim, raw))
            opts.extend((label, ("value", raw)) for raw, label in choices.items())
            desired = ("value", next(iter(selected))) if selected else ("all",)
            with select.prevent(Select.Changed):
                select.set_options(opts)
                select.value = desired

    @on(Select.Changed)
    def quick_selected(self, event):
        ident = event.select.id or ""
        if not ident.startswith("quick-") or not isinstance(event.value, tuple):
            return
        dim = ident[6:]
        values = {event.value[1]} if event.value[0] == "value" else set()
        if self.filters[dim] == values:
            return
        self.filters[dim] = values
        self.render_data()
        self.persist_preferences()

    def get_system_commands(self, screen):
        yield SystemCommand("更换主题", "选择终端原生配色或其他内置主题", self.search_themes)
        for command in super().get_system_commands(screen):
            if command.title not in ("Save screenshot", "Change theme"):
                yield command

    def action_screenshot(self, filename=None, path=None):
        pass

    def deliver_screenshot(self):
        pass

    def options(self, dim):
        found = {}
        raw_values = {str(row.get(dim) or "") for row in (self.data or {}).get("buckets", [])}
        for raw in raw_values:
            label = display(self.data, dim, raw)
            found[label if dim == "model" else raw] = label
        return sorted(found.items(), key=lambda item: item[1].casefold())

    async def load_view(self):
        try:
            while self._view_request is not None:
                data, key = request = self._view_request
                filters = {dim: set(values) for dim, values in key[3]}
                view = await asyncio.to_thread(build_view, data, filters, key[0], key[1])
                if request is not self._view_request:
                    continue
                self._view_request = None
                if data is self.data:
                    self.view = view
                    self._view_data, self._view_key = data, key
                    self.visible_rows = view.rows
                    self.render_data()
        except Exception as exc:
            log_failure("usage aggregation", exc)
            self._view_request = None
            self.status = "统计失败"
            if self.view is None:
                self.set_summary_state(self.status)
            self.update_hint()
        finally:
            self._view_loading = False

    def render_data(self):
        if self.data is None:
            return
        key = (
            self.range_key,
            self.dates,
            date.today(),
            tuple((dim, frozenset(values)) for dim, values in self.filters.items()),
        )
        if self._view_data is not self.data or self._view_key != key:
            pending = self._view_request
            if pending is None or pending[0] is not self.data or pending[1] != key:
                self._view_request = (self.data, key)
            if not self._view_loading:
                self._view_loading = True
                self.run_worker(self.load_view())
            return
        total, cost, active = (self.view.totals[key] for key in ("tokens", "cost", "active"))
        hero = Text()
        hero.append("总Token\n", style="dim")
        hero.append(self.format_tokens(total), style="bold")
        self.update_text("hero", hero)
        for key, title, value in [
            ("cost", "预估费用", self.format_cost(cost)),
            ("active", "活跃时长", format_duration(active)),
        ]:
            metric = Text()
            metric.append(title + "\n", style="dim")
            metric.append(value, style=self.metric_color(key) + " bold")
            self.update_text(key, metric)
        rate = self.view.cache_rate
        cache = Text()
        cache.append("缓存命中率\n", style="dim")
        cache.append(f"{rate:.1f}%" if rate is not None else "—", style="bold")
        self.update_text("cache-rate", cache)
        self.render_trend()
        self.render_distributions()
        self.update_hint()

    def update_text(self, key, content):
        widget = self.screen_stack[0].query_one("#" + key, Static)
        if widget.renderable != content:
            widget.update(content)

    def render_trend(self):
        if self.view is not None and self.data is not None:
            self.screen_stack[0].query_one(Trend).set_data(*self.view.series[self.metric])

    def distribution_palette(self):
        colors = []
        for token in ("primary", "secondary", "accent", "success", "warning", "error"):
            color = self.theme_color(token)
            if color not in colors:
                colors.append(color)
        return colors

    def render_distributions(self, dimension=None):
        if self.view is None:
            return
        resized = False
        for dim, key, _, _title in DISTRIBUTIONS:
            if dimension is not None and dim != dimension:
                continue
            mode = self.distribution_modes[dim]
            card = self.screen_stack[0].query_one("#" + key + "-data", DistributionRows)
            resized |= card.set_rows(self.view.distributions[dim][mode], mode)
        if resized:
            self.call_after_refresh(self.fit_distributions)

    def action_exact(self):
        self.exact = not self.exact
        self.render_data()


if __name__ == "__main__":
    configure_logging()
    UsageApp().run(mouse=True)
