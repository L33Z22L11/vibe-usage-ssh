"""Usage trend, compact hover details and horizontal scrollbar rendering."""

from math import floor

from rich.segment import Segment, Segments
from rich.style import Style
from rich.text import Text
from textual.geometry import Offset
from textual.scrollbar import ScrollBarRender
from textual.widgets import Static

from .usage import format_duration


class ThinScrollBarRender(ScrollBarRender):
    """Keep native scrollbar hit targets and dragging, with a thin horizontal track."""

    @classmethod
    def render_bar(cls, *args, **kwargs):
        bar = super().render_bar(*args, **kwargs)
        if kwargs.get("vertical", True):
            return bar
        segments = []
        for segment in bar.segments:
            if segment.text == "\n":
                segments.append(segment)
                continue
            meta = segment.style.meta if segment.style else {}
            thumb = meta.get("@mouse.down") == "grab"
            style = Style(
                color=kwargs["bar_color"], bgcolor=kwargs["back_color"], dim=not thumb, meta=meta
            )
            segments.append(Segment(("━" if thumb else "─") * len(segment.text), style))
        return Segments(segments, new_lines=False)


class ChartTooltip(Static):
    """A tightly sized chart readout kept entirely inside the terminal."""

    def show(self, details, anchor):
        viewport = self.app.size
        margin = min(3, max(0, (viewport.width - 1) // 2))
        width = min(viewport.width - margin * 2, max(line.cell_len for line in details.split("\n")))
        height = len(details.wrap(self.app.console, width, overflow="fold"))
        x = anchor.x + 2
        if x + width > viewport.width - margin:
            x = anchor.x - width - 2
        x = max(margin, min(x, viewport.width - width - margin))
        y = anchor.y + 1
        if y + height > viewport.height:
            y = anchor.y - height
        y = max(0, min(y, viewport.height - height))
        self.styles.width = width
        self.styles.height = height
        self.absolute_offset = Offset(x, y)
        self.update(details)
        self.display = True

    def on_click(self, event):
        event.stop()
        self.app.screen_stack[0].query_one(Trend).hide_details()


class Trend(Static):
    COMPONENT_CLASSES = {"trend--cached", "trend--uncached"}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.values, self.labels, self.points = [], [], []
        self.selected = None
        self.dragging = False
        self.details = None
        self._draw_style = None

    def set_data(self, labels, values, points=None):
        draw_style = self.app.metric, self.app.theme
        unchanged = (
            labels is self.labels
            and values is self.values
            and points is self.points
            and draw_style == self._draw_style
        )
        self._draw_style = draw_style
        keep_details = (
            self.details is not None
            and labels == self.labels
            and self.contains_pointer(*self.app.mouse_position)
        )
        self.labels, self.values, self.points = labels, values, points or []
        if unchanged:
            if keep_details:
                self.inspect_at(self.app.mouse_position.x - self.region.x, force=True)
            return
        self.styles.min_width = max(1, len(self.values) * 2)
        self.styles.width = "1fr"
        if keep_details:
            self.inspect_at(self.app.mouse_position.x - self.region.x, force=True)
        else:
            self.hide_details()
        self.refresh()

    def bins(self):
        width = max(1, self.content_size.width)
        values = self.values or [0]
        count = len(values)
        return [
            (
                i * len(values) // count,
                (i + 1) * len(values) // count,
                i * width // count,
                (i + 1) * width // count,
            )
            for i in range(count)
        ]

    def inspect_at(self, x, *, force=False):
        bins = self.bins()
        index = next((i for i, (_, _, left, right) in enumerate(bins) if left <= x < right), None)
        if index is None or not self.values:
            return
        if not force and index == self.selected and self.details is not None:
            return
        self.selected = index
        start, end, _, _ = bins[index]
        caption = self.points[start]["time"] if self.points else self.labels[start]
        points = self.points[start:end]

        def total(field):
            return sum(point.get(field, 0) for point in points)

        details = Text()
        details.append(caption + "\n", style="bold")
        if self.app.metric == "tokens":
            details.append(f"总Token {self.app.format_tokens(total('tokens'))}\n")
            input_text = self.app.format_tokens(total("input")).replace("K", "k")
            output_text = self.app.format_tokens(total("output") + total("reasoning")).replace(
                "K", "k"
            )
            details.append(f"输入 {input_text}  输出 {output_text}\n", style="dim")
            denominator = total("input") + total("cached")
            ratio = f"{100 * total('cached') / denominator:.1f}%" if denominator else "—"
            details.append(f"缓存命中率 {ratio}\n", style="dim")
            details.append(
                "费用 " + self.app.format_cost(total("cost")), style=self.app.metric_color("cost")
            )
        elif self.app.metric == "cost":
            details.append(self.app.format_cost(total("cost")), style=self.app.metric_color("cost"))
        else:
            details.append(format_duration(total("active")), style=self.app.metric_color("active"))
        self.details = details
        self.screen.query_one(ChartTooltip).show(details, self.app.mouse_position)
        self.app.screen_stack[0].query_one("#updated", Static).update(Text(caption))
        self.refresh()

    def on_mouse_move(self, event):
        if self.contains_pointer(event.screen_x, event.screen_y, hover=True):
            self.inspect_at(event.x)
        else:
            self.hide_details()

    def contains_pointer(self, x, y, *, hover=False):
        # Hide before the pointer leaves the terminal, which may emit no leave event.
        if hover and x < 4:
            return False
        if self.app.sticky_top and self.app.screen_stack[0].query_one("#topbar").region.contains(
            x, y
        ):
            return False
        visible = self.region.intersection(self.parent.content_region)
        visible = visible.intersection(self.app.screen_stack[0].query_one("#body").content_region)
        return visible.contains(x, y)

    def hide_details(self):
        if self.selected is None and self.details is None:
            return
        self.selected = None
        self.details = None
        for tooltip in self.app.screen_stack[0].query(ChartTooltip):
            tooltip.display = False
        self.app.update_hint()
        self.refresh()

    def on_mouse_down(self, event):
        if event.button == 1:
            self.dragging = True
            self.capture_mouse()
            self.inspect_at(event.x)
            event.stop()

    def on_mouse_up(self, event):
        if self.dragging:
            self.dragging = False
            self.release_mouse()
            if self.contains_pointer(event.screen_x, event.screen_y):
                self.inspect_at(event.x)
            else:
                self.hide_details()
            event.stop()

    def on_leave(self):
        if not self.dragging:
            self.hide_details()

    @staticmethod
    def token_bar_units(value, cached, scale):
        cached = max(0, min(value, cached))
        total = max(0, floor(value * scale * 8 + 0.5))
        base = min(total, floor(cached * scale * 8 + 0.5))
        return base, total - base

    def axis_labels(self, width, bins):
        labels = []
        for label, (_, _, left, right) in zip(self.labels, bins, strict=True):
            text = Text(label)
            text.truncate(width, overflow="ellipsis")
            start = max(0, min((left + right - 1 - text.cell_len) // 2, width - text.cell_len))
            if not labels or start >= labels[-1][0] + labels[-1][1].cell_len + 1:
                labels.append((start, text))
        if self.labels:
            last = Text(self.labels[-1])
            last.truncate(width, overflow="ellipsis")
            _, _, left, right = bins[-1]
            start = max(0, min((left + right - 1 - last.cell_len) // 2, width - last.cell_len))
            while labels and labels[-1][0] + labels[-1][1].cell_len + 1 > start:
                labels.pop()
            labels.append((start, last))
        axis = Text(style="dim")
        for start, label in labels:
            axis.append(" " * max(0, start - axis.cell_len))
            axis.append_text(label)
        return axis

    def render(self):
        width = max(1, self.content_size.width)
        bins = self.bins()
        bars = [sum(self.values[a:b]) for a, b, _, _ in bins]
        maximum = max(1, max(bars))
        height = max(1, self.content_size.height - 1)
        token_mode = self.app.metric == "tokens"
        color = self.app.metric_color(self.app.metric)
        cached_style = self.get_component_rich_style("trend--cached")
        uncached_style = self.get_component_rich_style("trend--uncached")
        mixed_style = Style(color=cached_style.color, bgcolor=uncached_style.color)
        stacks = []
        if token_mode:
            cached_values = [
                sum(p.get("cached", 0) for p in self.points[a:b]) for a, b, _, _ in bins
            ]
            stacks = [
                self.token_bar_units(value, cached, height / maximum)
                for value, cached in zip(bars, cached_values, strict=True)
            ]
        columns = []
        for i, (value, (_, _, left, right)) in enumerate(zip(bars, bins, strict=True)):
            units = round(value / maximum * height * 8)
            base = 0
            if token_mode:
                base, cap = stacks[i]
                units = base + cap
            selection = Style(bold=i == self.selected)
            cell = right - left
            gap = 1 if cell >= 2 else 0
            columns.append(
                (
                    units,
                    base,
                    cell - gap,
                    gap,
                    Style(color=color) + selection,
                    cached_style + selection,
                    uncached_style + selection,
                )
            )
        text = Text()
        for level in range(height, 0, -1):
            for units, base, bar_width, gap, style, muted, bright in columns:
                fill = max(0, min(8, units - (level - 1) * 8))
                glyph = " ▁▂▃▄▅▆▇█"[fill]
                if token_mode and fill:
                    cached_fill = max(0, min(fill, base - (level - 1) * 8))
                    if cached_fill == fill:
                        style = muted
                    elif cached_fill == 0:
                        style = bright
                    elif fill == 8 and cached_style.color != uncached_style.color:
                        glyph = " ▁▂▃▄▅▆▇█"[cached_fill]
                        style = mixed_style + Style(bold=bool(style.bold))
                    else:
                        # A character cell cannot carry two foreground intensities.
                        # Keep the dominant component without changing the bar's height.
                        style = muted if cached_fill * 2 >= fill else bright
                text.append(glyph * bar_width, style=style)
                text.append(" " * gap)
            text.append("\n")
        if self.labels:
            text.append_text(self.axis_labels(width, bins))
        return text
