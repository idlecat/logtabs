"""Drives the UI headlessly with Textual's pilot."""

import asyncio

from logtabs.app import LogTabs
from logtabs.config import from_args


def run(coro):
    asyncio.run(coro)


def test_tabs_follow_filter_and_restart(tmp_path):
    web, metro, ios = tmp_path / "web.log", tmp_path / "metro.log", tmp_path / "ios.log"
    web.write_text("".join(f"GET /{i}\n" for i in range(200)) + "DEBUG noisy\n")
    metro.write_text("bundled\n")
    ios.write_text("launched\n")
    config = from_args([f"web={web}", f"app={metro}", f"app={ios}"])

    async def scenario():
        app = LogTabs(config)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.5)
            pane = app.active_pane()
            assert pane.tab.name == "web" and len(pane.lines) == 201
            assert pane.log_widget.follow

            await pilot.press("pageup")
            assert not pane.log_widget.follow
            await pilot.press("end")
            assert pane.log_widget.follow

            await pilot.press("slash", *"get /19\\b", "enter")
            await pilot.pause(0.2)
            assert app.filter_re is not None and len(pane.log_widget.lines) == 1
            await pilot.press("escape")
            assert app.filter_re is None

            await pilot.press("2")
            await pilot.pause(0.2)
            app_pane = app.active_pane()
            assert app_pane.tab.name == "app"
            assert [(l.source, l.text) for l in app_pane.lines] == [("metro", "bundled"), ("ios", "launched")]

            ios.write_text("relaunched\n")  # shorter: the source started over
            await pilot.pause(0.5)
            assert [l.text for l in app_pane.lines][-2:] == ["──── ios started over ────", "relaunched"]

            web.write_text("fresh\n")  # a one-source tab starts over completely
            await pilot.pause(0.5)
            assert [l.text for l in pane.lines] == ["fresh"]

    run(scenario())


def test_debug_lines_hidden_by_toggle(tmp_path):
    log = tmp_path / "a.log"
    log.write_text("INFO one\nDEBUG two\n")

    async def scenario():
        app = LogTabs(from_args([str(log)]))
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            pane = app.active_pane()
            assert len(pane.log_widget.lines) == 2
            await pilot.press("d")
            await pilot.pause(0.2)
            assert len(pane.log_widget.lines) == 1

    run(scenario())
