"""Drives the UI headlessly with Textual's pilot."""

import asyncio

from logtabs.app import OVERVIEW, LogTabs
from logtabs.config import from_args


def run(coro):
    asyncio.run(coro)


def make_logs(tmp_path):
    web, metro, ios = tmp_path / "web.log", tmp_path / "metro.log", tmp_path / "ios.log"
    web.write_text("".join(f"GET /{i}\n" for i in range(200)) + "DEBUG noisy\n")
    metro.write_text("bundled\n")
    ios.write_text("launched\n")
    return web, metro, ios, from_args([f"web={web}", f"app={metro}", f"app={ios}"])


def test_tabs_follow_filter_and_restart(tmp_path):
    web, metro, ios, config = make_logs(tmp_path)

    async def scenario():
        app = LogTabs(config)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.5)
            await pilot.press("1")
            await pilot.pause(0.2)
            view = app.active_view()
            assert app.active_tab() == "tab-0" and not view.compact
            assert view.feed.tab.name == "web" and len(view.feed.lines) == 201
            assert view.log_widget.follow

            await pilot.press("pageup")
            assert not view.log_widget.follow
            await pilot.press("end")
            assert view.log_widget.follow

            await pilot.press("slash", *"get /19\\b", "enter")
            await pilot.pause(0.2)
            assert app.filter_re is not None and len(view.log_widget.lines) == 1
            await pilot.press("escape")
            assert app.filter_re is None

            await pilot.press("2")
            await pilot.pause(0.2)
            app_view = app.active_view()
            assert app_view.feed.tab.name == "app"
            assert [(l.source, l.text) for l in app_view.feed.lines] == [("metro", "bundled"), ("ios", "launched")]

            ios.write_text("relaunched\n")  # rewritten: the source started over
            await pilot.pause(0.5)
            assert [l.text for l in app_view.feed.lines][-2:] == ["──── ios started over ────", "relaunched"]

            web.write_text("fresh\n")  # a one-source tab starts over completely
            await pilot.pause(0.5)
            assert [l.text for l in view.feed.lines] == ["fresh"]

    run(scenario())


def test_overview_shows_every_tab_and_follows_focus(tmp_path):
    web, metro, ios, config = make_logs(tmp_path)
    config.tabs[1].restart = "true"

    async def scenario():
        app = LogTabs(config)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.5)
            assert app.active_tab() == OVERVIEW  # the overview is where it starts
            cells = app.visible_views()
            assert [c.feed.tab.name for c in cells] == ["web", "app"] and all(c.compact for c in cells)
            assert all(len(c.log_widget.lines) > 0 for c in cells)

            # Both cells get new lines, from one read of each file.
            with metro.open("a") as f:
                f.write("reloaded\n")
            await pilot.pause(0.5)
            assert "reloaded" in str(cells[1].log_widget.lines[-1].text)
            full_app_view = next(v for v in app.views if not v.compact and v.feed is cells[1].feed)
            assert full_app_view.feed is cells[1].feed

            # Keys act on the focused cell: web has no restart command, app does.
            assert app.active_view() is cells[0]
            assert app.check_action("restart", ()) is None
            await pilot.click(cells[1].log_widget)
            await pilot.pause(0.1)
            assert app.active_view() is cells[1]
            assert app.check_action("restart", ()) is True

            # The layout cycles, and Enter opens the focused cell's own tab.
            assert app.query_one("#grid").styles.grid_size_columns == 1  # auto: rows for 2 tabs
            await pilot.press("s")
            assert app.split == "rows"
            await pilot.press("s")
            assert app.split == "columns" and app.query_one("#grid").styles.grid_size_columns == 2
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert app.active_tab() == "tab-1"
            assert app.check_action("cycle_split", ()) is None  # only in the overview

            await pilot.press("0")
            await pilot.pause(0.2)
            assert app.active_tab() == OVERVIEW

    run(scenario())


def test_no_overview_for_one_tab_or_when_disabled(tmp_path):
    log = tmp_path / "a.log"
    log.write_text("x\n")
    _, _, _, two_tabs = make_logs(tmp_path)
    two_tabs.overview = False

    async def scenario(config):
        app = LogTabs(config)
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            assert app.active_tab() == "tab-0"
            assert not any(v.compact for v in app.views)

    run(scenario(from_args([str(log)])))
    run(scenario(two_tabs))


def test_debug_lines_hidden_by_toggle(tmp_path):
    log = tmp_path / "a.log"
    log.write_text("INFO one\nDEBUG two\n")

    async def scenario():
        app = LogTabs(from_args([str(log)]))
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            view = app.active_view()
            assert len(view.log_widget.lines) == 2
            await pilot.press("d")
            await pilot.pause(0.2)
            assert len(view.log_widget.lines) == 1

    run(scenario())


def test_blank_lines_collapse(tmp_path):
    log = tmp_path / "a.log"
    log.write_text("one\n\nDEBUG x\n\n\nDEBUG y\n\ntwo\n")

    async def scenario():
        app = LogTabs(from_args([str(log)]))
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            view = app.active_view()
            assert [str(l.text) for l in view.log_widget.lines] == ["one", "", "DEBUG x", "", "DEBUG y", "", "two"]
            await pilot.press("d")  # hiding DEBUG would otherwise leave four blank lines in a row
            await pilot.pause(0.2)
            assert [str(l.text) for l in view.log_widget.lines] == ["one", "", "two"]

    run(scenario())
