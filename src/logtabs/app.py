"""The Textual app: one tab per configured tab, each following its sources' log files, plus an
"all" tab that shows every one of them at once in a split view."""

from __future__ import annotations

import math
import os
import re
import socket
import subprocess
from collections import deque
from dataclasses import dataclass

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical
from textual.widget import Widget
from textual.widgets import Footer, Input, RichLog, Static, TabbedContent, TabPane

from .config import SPLITS, Config, Tab
from .tail import Tail

KEEP_LINES = 20_000  # per tab, in memory
RENDER_LINES = 5_000  # re-rendering after a filter change is the slow part; older matches are rarely wanted
OVERVIEW = "overview"


@dataclass
class Line:
    source: str
    text: str
    debug: bool


class Feed:
    """A tab's lines, read from its sources. Shared by every view that shows the tab, so each
    file is read once however many places it's shown."""

    def __init__(self, tab: Tab, index: int, config: Config):
        self.tab = tab
        self.index = index
        self.config = config
        self.tails = [(s.name, Tail(s.path)) for s in tab.sources]
        self.lines: deque[Line] = deque(maxlen=KEEP_LINES)

    def make_line(self, source: str, text: str) -> Line:
        return Line(source, text, bool(self.config.debug.search(text)))

    def poll(self) -> tuple[bool, list[Line]]:
        """(whether the tab started over, so its views must redraw; the lines added)."""
        drop = self.config.drop
        added: list[Line] = []
        restarted = False
        for name, tail in self.tails:
            reset, texts = tail.read()
            if reset and len(self.tails) == 1:
                restarted = True
                added.clear()
            elif reset:
                added.append(self.make_line(name, f"──── {name} started over ────"))
            added.extend(self.make_line(name, t) for t in texts if not any(p.search(t) for p in drop))
        if restarted:
            self.lines.clear()
        self.lines.extend(added)
        return restarted, added

    def clear(self) -> None:
        self.lines.clear()


class FollowLog(RichLog):
    """A RichLog that stays on the newest line until you scroll up, and again once you scroll
    back to the bottom or press End."""

    follow = True

    def keep_at_end(self) -> None:
        if self.follow:
            self.call_after_refresh(self.scroll_end, animate=False)

    def check_end(self) -> None:
        if self.scroll_y >= self.max_scroll_y - 1:
            self.follow = True

    def on_mouse_scroll_up(self) -> None:
        self.follow = False

    def on_mouse_scroll_down(self) -> None:
        self.call_after_refresh(self.check_end)

    def action_scroll_up(self) -> None:
        self.follow = False
        super().action_scroll_up()

    def action_page_up(self) -> None:
        self.follow = False
        super().action_page_up()

    def action_scroll_home(self) -> None:
        self.follow = False
        super().action_scroll_home()

    def action_scroll_down(self) -> None:
        super().action_scroll_down()
        self.call_after_refresh(self.check_end)

    def action_page_down(self) -> None:
        super().action_page_down()
        self.call_after_refresh(self.check_end)

    def action_scroll_end(self) -> None:
        self.follow = True
        super().action_scroll_end()


class LogView(Vertical):
    """A status line over a feed's log. Compact views are the overview's cells: the tab's name
    is the border title and the status line keeps to the essentials."""

    def __init__(self, feed: Feed, compact: bool = False):
        super().__init__(classes="cell" if compact else "")
        self.feed = feed
        self.compact = compact
        self.source_colors = {s.name: s.color for s in feed.tab.sources}
        self.label_width = max(len(s.name) for s in feed.tab.sources)
        self.prev_blank = False
        if compact:
            self.border_title = f"{feed.index + 1} {feed.tab.name}"

    @property
    def viewer(self) -> LogTabs:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static(classes="status")
        # min_width: RichLog never renders narrower than 78 columns by default, which gives
        # narrow overview cells a horizontal scrollbar instead of wrapped lines.
        yield FollowLog(max_lines=KEEP_LINES, wrap=True, auto_scroll=False, min_width=20)

    @property
    def log_widget(self) -> FollowLog:
        return self.query_one(FollowLog)

    def write(self, line: Line) -> None:
        if not self.viewer.shows(line):
            return
        # Runs of blank lines collapse to one. Hiding DEBUG lines otherwise piles up the blank
        # lines logged between them until they fill the view.
        blank = not line.text.strip()
        if blank and self.prev_blank:
            return
        self.prev_blank = blank
        self.log_widget.write(self.format_line(line), expand=True, scroll_end=False)

    def append(self, lines: list[Line]) -> None:
        for line in lines:
            self.write(line)
        self.log_widget.keep_at_end()

    def format_line(self, line: Line) -> Text:
        config = self.viewer.config
        text = Text()
        if len(self.feed.tails) > 1:
            text.append(f"{line.source:<{self.label_width}}", style=f"bold {self.source_colors[line.source]}")
            text.append(" ")
        style = ""
        if config.error.search(line.text):
            style = "red"
        elif config.warn.search(line.text):
            style = "yellow"
        elif line.debug:
            style = "dim"
        body = Text(line.text, style=style)
        if self.viewer.filter_re is not None:
            body.highlight_regex(self.viewer.filter_re, style="black on yellow")
        text.append_text(body)
        return text

    def rerender(self) -> None:
        log = self.log_widget
        log.clear()
        self.prev_blank = False
        shown = [line for line in self.feed.lines if self.viewer.shows(line)]
        for line in shown[-RENDER_LINES:]:
            self.write(line)
        log.keep_at_end()
        self.update_status()

    def update_status(self) -> None:
        viewer = self.viewer
        parts = []
        for source in self.feed.tab.sources:
            if source.port is None:
                state = Text("log", style="dim") if len(self.feed.tails) > 1 else Text("")
            else:
                up = viewer.health.get(source.port)
                if up is None:
                    state = Text("…", style="dim")
                else:
                    state = Text("up" if up else "down", style="bold green" if up else "bold red")
            parts.append(Text.assemble((source.name, f"bold {source.color}"), " ", state))
        status = Text("  ·  ").join(parts)
        follow = self.log_widget.follow
        if self.compact:
            if not follow:
                status.append("    paused — End to follow", style="yellow")
        else:
            status.append("    ")
            status.append("following" if follow else "paused — End to follow",
                          style="green" if follow else "yellow")
            status.append("  ·  DEBUG hidden" if viewer.hide_debug else "  ·  DEBUG shown", style="dim")
            if viewer.filter_re is not None:
                status.append("  ·  filter: ", style="dim")
                status.append(viewer.filter_re.pattern, style="black on yellow")
        self.query_one(".status", Static).update(status)


BUILTIN_BINDINGS = [
    Binding("slash", "filter", "filter"),
    Binding("escape", "clear_filter", "clear filter", show=False),
    Binding("d", "toggle_debug", "debug"),
    Binding("w", "toggle_wrap", "wrap"),
    Binding("end", "follow", "follow", show=False),
    Binding("c", "clear", "clear"),
    Binding("r", "restart", "restart"),
    Binding("enter", "zoom", "open tab"),
    Binding("s", "cycle_split", "split"),
]


class LogTabs(App):
    CSS = """
    .status { height: 1; padding: 0 1; background: $boost; }
    FollowLog { height: 1fr; }
    #filter { dock: bottom; display: none; }
    #filter.visible { display: block; }
    TabPane { padding: 0; }
    #grid { height: 1fr; grid-gutter: 0; }
    LogView.cell { height: 1fr; border: round $panel-lighten-2; border-title-color: $text-muted; }
    LogView.cell:focus-within { border: round $accent; border-title-color: $text; border-title-style: bold; }
    LogView.cell .status { background: transparent; }
    """

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.title = config.title
        self.filter_re: re.Pattern | None = None
        self.hide_debug = config.hide_debug
        self.split = config.split
        self.health: dict[int, bool] = {}
        self.feeds = [Feed(tab, i, config) for i, tab in enumerate(config.tabs)]
        self.views: list[LogView] = []
        # Bound here rather than in BINDINGS so the footer reads: tabs, viewer keys, the
        # config's actions, quit.
        if config.has_overview:
            self.bind("0", "tab('overview')", description="all")
        for i, tab in enumerate(config.tabs):
            self.bind(str(i + 1), f"tab('tab-{i}')", description=tab.name)
        for b in BUILTIN_BINDINGS:
            self.bind(b.key, b.action, description=b.description, show=b.show)
        for i, action in enumerate(config.actions):
            self.bind(action.key, f"run_action({i})", description=action.description)
        self.bind("q", "quit", description="quit")

    def compose(self) -> ComposeResult:
        with TabbedContent(initial=OVERVIEW if self.config.has_overview else "tab-0"):
            if self.config.has_overview:
                with TabPane("all", id=OVERVIEW):
                    with Grid(id="grid"):
                        for feed in self.feeds:
                            yield LogView(feed, compact=True)
            for feed in self.feeds:
                with TabPane(feed.tab.name, id=f"tab-{feed.index}"):
                    yield LogView(feed)
        # Disabled while hidden, or it takes focus at startup and swallows the tab keys.
        yield Input(placeholder="regex, Enter to apply, Esc to clear", id="filter", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.views = list(self.query(LogView))
        self.apply_split()
        self.poll()
        for view in self.views:
            view.update_status()
        self.focus_active()
        self.set_interval(0.3, self.poll)
        if any(s.port is not None for s in self.config.sources()):
            self.set_interval(2, self.check_health)
            self.check_health()

    # Which view the keys act on: the tab's own, or in the overview the focused cell.

    def active_tab(self) -> str:
        return self.query_one(TabbedContent).active

    def visible_views(self) -> list[LogView]:
        if self.active_tab() == OVERVIEW:
            return [v for v in self.views if v.compact]
        return [v for v in self.views if not v.compact and f"tab-{v.feed.index}" == self.active_tab()]

    def active_view(self) -> LogView:
        visible = self.visible_views()
        node: Widget | None = self.focused
        while node is not None:
            if isinstance(node, LogView) and node in visible:
                return node
            node = node.parent  # type: ignore[assignment]
        return visible[0]

    def focus_active(self) -> None:
        self.active_view().log_widget.focus()

    def shows(self, line: Line) -> bool:
        if self.hide_debug and line.debug:
            return False
        return self.filter_re is None or bool(self.filter_re.search(line.text))

    def poll(self) -> None:
        for feed in self.feeds:
            restarted, added = feed.poll()
            if not (restarted or added):
                continue
            for view in self.views:
                if view.feed is feed:
                    view.rerender() if restarted else view.append(added)
        for view in self.visible_views():
            view.update_status()  # following/paused changes as you scroll

    @work(thread=True, exclusive=True, group="health")
    def check_health(self) -> None:
        # A bare TCP connect: an HTTP request every few seconds would show up in the logs.
        results = {}
        for port in {s.port for s in self.config.sources() if s.port is not None}:
            try:
                socket.create_connection(("localhost", port), timeout=1).close()
                results[port] = True
            except OSError:
                results[port] = False
        self.call_from_thread(self.apply_health, results)

    def apply_health(self, results: dict[int, bool]) -> None:
        self.health.update(results)
        for view in self.views:
            view.update_status()

    def rerender_all(self) -> None:
        for view in self.views:
            view.rerender()

    def on_resize(self) -> None:
        # Lines are wrapped when written, so a new width means writing them again.
        self.set_timer(0.2, self.rerender_all, name="rewrap")

    def apply_split(self) -> None:
        if not self.config.has_overview:
            return
        n = len(self.feeds)
        split = self.split
        if split == "auto":
            split = "rows" if n <= 3 else "grid"
        columns = {"rows": 1, "columns": n, "grid": 2}[split]
        grid = self.query_one("#grid", Grid)
        grid.styles.grid_size_columns = columns
        grid.styles.grid_size_rows = math.ceil(n / columns)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if not self.views:  # before mount
            return True
        if action == "restart":
            return True if self.active_view().feed.tab.restart else None
        if action in ("zoom", "cycle_split"):
            return True if self.active_tab() == OVERVIEW else None
        return True

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self.refresh_bindings()  # restart follows the focused cell in the overview

    def action_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id

    @on(TabbedContent.TabActivated)
    def tab_shown(self) -> None:
        # A hidden log can't scroll, so one that was following catches up when it's shown.
        for view in self.visible_views():
            view.log_widget.keep_at_end()
            view.update_status()
        self.focus_active()
        self.refresh_bindings()

    def action_zoom(self) -> None:
        self.action_tab(f"tab-{self.active_view().feed.index}")

    def action_cycle_split(self) -> None:
        current = SPLITS.index(self.split)
        self.split = SPLITS[(current + 1) % len(SPLITS)]
        self.apply_split()
        self.notify(f"split: {self.split}", timeout=2)
        # The cells' widths changed, so their lines need wrapping again once laid out.
        self.call_after_refresh(lambda: [v.rerender() for v in self.views if v.compact])

    def hide_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.remove_class("visible")
        box.disabled = True
        self.focus_active()

    def action_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.disabled = False
        box.add_class("visible")
        box.focus()

    @on(Input.Submitted, "#filter")
    def apply_filter(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value:
            try:
                self.filter_re = re.compile(value, re.IGNORECASE)
            except re.error:
                self.filter_re = re.compile(re.escape(value), re.IGNORECASE)
        else:
            self.filter_re = None
        self.hide_filter()
        self.rerender_all()

    def action_clear_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.value = ""
        self.hide_filter()
        if self.filter_re is not None:
            self.filter_re = None
            self.rerender_all()

    def action_toggle_debug(self) -> None:
        self.hide_debug = not self.hide_debug
        self.rerender_all()

    def action_toggle_wrap(self) -> None:
        for view in self.views:
            view.log_widget.wrap = not view.log_widget.wrap
            view.rerender()

    def action_follow(self) -> None:
        self.active_view().log_widget.action_scroll_end()

    def action_clear(self) -> None:
        feed = self.active_view().feed
        feed.clear()
        for view in self.views:
            if view.feed is feed:
                view.rerender()

    def action_restart(self) -> None:
        tab = self.active_view().feed.tab
        if tab.restart:
            self.notify(f"Restarting {tab.name}…")
            self.run_command(tab.restart)

    def action_run_action(self, index: int) -> None:
        action = self.config.actions[index]
        self.notify(f"{action.description}…")
        self.run_command(action.run)

    @work(thread=True)
    def run_command(self, command: str) -> None:
        done = subprocess.run(command, shell=True, cwd=self.config.cwd, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env={**os.environ, "TERM": "dumb"})
        if done.returncode == 0:
            self.call_from_thread(self.notify, f"Done: {command}")
        else:
            last = (done.stderr or done.stdout).strip().splitlines()[-1:] or [f"exit {done.returncode}"]
            self.call_from_thread(self.notify, f"{command}: {last[0]}", severity="error", timeout=10)
