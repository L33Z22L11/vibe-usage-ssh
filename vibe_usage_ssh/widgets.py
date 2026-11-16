"""Dashboard selectors, summary cards, date dialog and distribution rows."""

from datetime import date
from functools import partial

from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.theme import ThemeProvider
from textual.widgets import Button, Input, Label, Select, Static
from textual.widgets._select import SelectCurrent, SelectOverlay


class AllThemesProvider(ThemeProvider):
    @property
    def commands(self):
        return [
            (name, partial(setattr, self.app, "theme", name)) for name in self.app.theme_names()
        ]


class FilterMenu(SelectOverlay):
    BINDINGS = [Binding("escape", "dismiss", "收起")]


class FilterSelect(Select):
    def compose(self):
        yield SelectCurrent(self.prompt)
        yield FilterMenu()


class UnitCard(Static):
    can_focus = True
    BINDINGS = [
        Binding("enter", "toggle", "", show=False),
        Binding("space", "toggle", "", show=False),
    ]

    def on_click(self):
        self.action_toggle()

    def action_toggle(self):
        if self.id == "cost":
            self.app.action_currency()
        else:
            self.app.action_token_unit()


class DismissibleModal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "关闭", show=False, priority=True)]

    def action_cancel(self):
        self.dismiss(None)

    def on_click(self, event):
        panel = self.query_one(".modal-panel")
        if not panel.region.contains(event.screen_x, event.screen_y):
            event.stop()
            self.dismiss(None)


class DateScreen(DismissibleModal):
    def __init__(self, dates=None):
        super().__init__()
        self.dates = dates or (date.today().isoformat(), date.today().isoformat())

    def compose(self):
        with Vertical(id="date-shadow"):
            with Vertical(id="date-dialog", classes="modal-panel"):
                yield Label("自定义日期", id="dialog-title")
                with Horizontal(id="date-inputs"):
                    start = Input(value=self.dates[0], placeholder="YYYY-MM-DD", id="date-from")
                    start.border_title = "开始"
                    yield start
                    end = Input(value=self.dates[1], placeholder="YYYY-MM-DD", id="date-to")
                    end.border_title = "结束"
                    yield end
                yield Label("", id="date-error")
                with Horizontal(classes="dialog-actions"):
                    yield Button("取消", id="date-cancel")
                    yield Button("应用", id="date-apply", variant="primary")

    @on(Button.Pressed)
    def button(self, event):
        event.stop()
        if event.button.id == "date-cancel":
            self.dismiss(None)
        elif event.button.id == "date-apply":
            start = self.query_one("#date-from", Input).value.strip()
            end = self.query_one("#date-to", Input).value.strip()
            try:
                a, b = date.fromisoformat(start), date.fromisoformat(end)
                if a > b or a.isoformat() != start or b.isoformat() != end:
                    raise ValueError()
            except ValueError:
                error = self.query_one("#date-error", Label)
                error.update("日期无效")
                error.display = True
                return
            self.dismiss((start, end))


class RangeTab(Static):
    can_focus = True
    BINDINGS = [
        Binding("enter", "select", "", show=False),
        Binding("space", "select", "", show=False),
    ]

    def __init__(self, key, label):
        super().__init__(id="range-" + key)
        self.key, self.label = key, label

    def render(self):
        label = "日期" if self.key == "custom" and self.app.size.width < 38 else self.label
        style = (
            self.app.theme_color("primary") + " reverse bold"
            if self.app.range_key == self.key
            else ""
        )
        return Text(label, style=style, justify="center")

    def on_click(self):
        self.action_select()

    def action_select(self):
        self.app.action_change_range(self.key)


class DistributionRows(Static):
    """Aligned name/value row followed by a half-height colored bar."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.entries = []
        self.mode = "tokens"
        self._presentation = None

    def set_rows(self, entries, mode):
        presentation = (
            mode,
            self.app.theme,
            (self.app.currency, self.app.exchange_rate)
            if mode == "cost"
            else (self.app.token_unit, self.app.exact),
        )
        if entries is self.entries and presentation == self._presentation:
            return False
        self._presentation = presentation
        resized = len(entries) != len(self.entries)
        self.entries = entries
        self.mode = mode
        self.refresh(layout=resized)
        return resized

    def get_content_height(self, container, viewport, width):
        return max(1, len(self.entries) * 2)

    def render(self):
        if not self.entries:
            return Text("暂无数据", style="dim")
        width = max(1, self.content_size.width)
        total = sum(value for _, value in self.entries)
        palette = self.app.distribution_palette()
        text = Text()
        for index, (name, value) in enumerate(self.entries):
            fraction = value / total if total else 0
            amount = (
                self.app.format_cost(value)
                if self.mode == "cost"
                else self.app.format_tokens(value)
            )
            number = Text(amount + f"  {fraction * 100:3.0f}%")
            label = Text(name)
            label.truncate(max(0, width - number.cell_len - 1), overflow="ellipsis")
            text.append_text(label)
            text.append(" " * max(1, width - label.cell_len - number.cell_len))
            text.append_text(number)
            text.append("\n")
            bar = "▀" * (max(1, round(fraction * width)) if value > 0 else 0)
            text.append(bar, style=palette[index % len(palette)])
            if index < len(self.entries) - 1:
                text.append("\n")
        return text


class DistributionTab(Static):
    can_focus = True
    BINDINGS = [
        Binding("enter", "select", "", show=False),
        Binding("space", "select", "", show=False),
    ]

    def __init__(self, dim, metric, label):
        super().__init__(id=f"dist-{dim}-{metric}")
        self.dim, self.metric, self.label = dim, metric, label

    def render(self):
        selected = self.app.distribution_modes[self.dim] == self.metric
        style = self.app.metric_color(self.metric) + " bold underline" if selected else "dim"
        return Text(self.label, style=style)

    def on_click(self):
        self.action_select()

    def action_select(self):
        if self.app.distribution_modes[self.dim] == self.metric:
            return
        self.app.distribution_modes[self.dim] = self.metric
        self.app.render_distributions(self.dim)
        for tab in self.parent.query(DistributionTab):
            tab.refresh()


class MetricTab(Static):
    can_focus = True
    BINDINGS = [
        Binding("enter", "select", "", show=False),
        Binding("space", "select", "", show=False),
    ]

    def __init__(self, metric, label):
        super().__init__(label, id="mode-" + metric)
        self.metric, self.label = metric, label

    def render(self):
        selected = self.app.metric == self.metric
        style = self.app.metric_color(self.metric) + " bold underline" if selected else "dim"
        return Text(self.label, style=style)

    def on_click(self):
        self.action_select()

    def action_select(self):
        self.app.set_metric(self.metric)
