"""The Textual app: one tab per configured tab, each following its sources' log files."""

from __future__ import annotations

import os
import re
import socket
import subprocess
from collections import deque
from dataclasses import dataclass

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Input, RichLog, Static, TabbedContent, TabPane

from .config import Config, Tab
from .tail import Tail

KEEP_LINES = 20_000  # per tab, in memory
RENDER_LINES = 5_000  # re-rendering after a filter change is the slow part; older matches are rarely wanted


@dataclass
class Line:
    source: str
    text: str
    debug: bool


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


class LogPane(Vertical):
    """One tab: a status line over the log."""

    def __init__(self, tab: Tab, index: int):
        super().__init__(id=f"pane-{index}")
        self.tab = tab
        self.tails = [(s.name, Tail(s.path)) for s in tab.sources]
        self.source_colors = {s.name: s.color for s in tab.sources}
        self.label_width = max(len(s.name) for s in tab.sources)
        self.lines: deque[Line] = deque(maxlen=KEEP_LINES)

    @property
    def viewer(self) -> LogTabs:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static(classes="status")
        yield FollowLog(max_lines=KEEP_LINES, wrap=True, auto_scroll=False)

    @property
    def log_widget(self) -> FollowLog:
        return self.query_one(FollowLog)

    def make_line(self, source: str, text: str) -> Line:
        return Line(source, text, bool(self.viewer.config.debug.search(text)))

    def poll(self) -> None:
        drop = self.viewer.config.drop
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
            self.rerender()
        elif added:
            self.lines.extend(added)
            log = self.log_widget
            for line in added:
                if self.viewer.shows(line):
                    log.write(self.format_line(line), expand=True, scroll_end=False)
            log.keep_at_end()

    def format_line(self, line: Line) -> Text:
        config = self.viewer.config
        text = Text()
        if len(self.tails) > 1:
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
        shown = [line for line in self.lines if self.viewer.shows(line)]
        for line in shown[-RENDER_LINES:]:
            log.write(self.format_line(line), expand=True, scroll_end=False)
        log.keep_at_end()
        self.update_status()

    def update_status(self) -> None:
        viewer = self.viewer
        parts = []
        for source in self.tab.sources:
            if source.port is None:
                state = Text("log", style="dim") if len(self.tails) > 1 else Text("")
            else:
                up = viewer.health.get(source.port)
                if up is None:
                    state = Text("…", style="dim")
                else:
                    state = Text("up" if up else "down", style="bold green" if up else "bold red")
            parts.append(Text.assemble((source.name, f"bold {source.color}"), " ", state))
        status = Text("  ·  ").join(parts)
        status.append("    ")
        follow = self.log_widget.follow
        status.append("following" if follow else "paused — End to follow", style="green" if follow else "yellow")
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
    Binding("r", "restart", "restart tab"),
]


class LogTabs(App):
    CSS = """
    .status { height: 1; padding: 0 1; background: $boost; }
    FollowLog { height: 1fr; }
    #filter { dock: bottom; display: none; }
    #filter.visible { display: block; }
    TabPane { padding: 0; }
    """

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.title = config.title
        self.filter_re: re.Pattern | None = None
        self.hide_debug = config.hide_debug
        self.health: dict[int, bool] = {}
        # Bound here rather than in BINDINGS so the footer reads: tabs, viewer keys, the
        # config's actions, quit.
        for i, tab in enumerate(config.tabs):
            self.bind(str(i + 1), f"tab({i})", description=tab.name)
        for b in BUILTIN_BINDINGS:
            self.bind(b.key, b.action, description=b.description, show=b.show)
        for i, action in enumerate(config.actions):
            self.bind(action.key, f"run_action({i})", description=action.description)
        self.bind("q", "quit", description="quit")

    def compose(self) -> ComposeResult:
        with TabbedContent(initial="tab-0"):
            for i, tab in enumerate(self.config.tabs):
                with TabPane(tab.name, id=f"tab-{i}"):
                    yield LogPane(tab, i)
        # Disabled while hidden, or it takes focus at startup and swallows the tab keys.
        yield Input(placeholder="regex, Enter to apply, Esc to clear", id="filter", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        for pane in self.panes():
            pane.poll()
            pane.update_status()
        self.active_pane().log_widget.focus()
        self.set_interval(0.3, self.poll)
        if any(s.port is not None for s in self.config.sources()):
            self.set_interval(2, self.check_health)
            self.check_health()

    def panes(self) -> list[LogPane]:
        return list(self.query(LogPane))

    def active_pane(self) -> LogPane:
        index = self.query_one(TabbedContent).active.removeprefix("tab-")
        return self.query_one(f"#pane-{index}", LogPane)

    def shows(self, line: Line) -> bool:
        if self.hide_debug and line.debug:
            return False
        return self.filter_re is None or bool(self.filter_re.search(line.text))

    def poll(self) -> None:
        for pane in self.panes():
            pane.poll()
        self.active_pane().update_status()  # following/paused changes as you scroll

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
        for pane in self.panes():
            pane.update_status()

    def rerender_all(self) -> None:
        for pane in self.panes():
            pane.rerender()

    def on_resize(self) -> None:
        # Lines are wrapped when written, so a new width means writing them again.
        self.set_timer(0.2, self.rerender_all, name="rewrap")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "restart":
            try:
                return True if self.active_pane().tab.restart else None
            except Exception:  # before the panes exist
                return None
        return True

    def action_tab(self, index: int) -> None:
        self.query_one(TabbedContent).active = f"tab-{index}"

    @on(TabbedContent.TabActivated)
    def tab_shown(self) -> None:
        # A hidden log can't scroll, so one that was following catches up when it's shown.
        pane = self.active_pane()
        pane.log_widget.focus()
        pane.log_widget.keep_at_end()
        pane.update_status()
        self.refresh_bindings()  # whether this tab has a restart key

    def hide_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.remove_class("visible")
        box.disabled = True
        self.active_pane().log_widget.focus()

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
        for pane in self.panes():
            pane.log_widget.wrap = not pane.log_widget.wrap
            pane.rerender()

    def action_follow(self) -> None:
        self.active_pane().log_widget.action_scroll_end()

    def action_clear(self) -> None:
        pane = self.active_pane()
        pane.lines.clear()
        pane.rerender()

    def action_restart(self) -> None:
        tab = self.active_pane().tab
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
