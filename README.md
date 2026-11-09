# logtabs

Follow a project's log files in a tabbed terminal UI. Each tab follows one or more files, which
are interleaved and labeled when there are several, and an "all" tab shows every tab's log at
once, split into panes. You get a regex filter across all tabs, a
DEBUG toggle, up/down markers for services with a port, and keys that run your project's own
restart and start commands.

It reads files, so it works with anything that logs to one: a dev script that tees each
service to `/tmp/<name>.log`, `tmux pipe-pane`, `docker logs -f x > x.log`, and so on.

## Install

```bash
uv tool install git+https://github.com/idlecat/logtabs   # or, from a clone: uv tool install --editable .
```

Or run it without installing: `uvx --from git+https://github.com/idlecat/logtabs logtabs`.

## Use

```bash
logtabs                                  # reads logtabs.toml here or in the nearest parent directory
logtabs -c path/to/logtabs.toml
logtabs api.log web.log                  # ad hoc: one tab per file, named after it
logtabs api=/tmp/srv.log app=/tmp/metro.log app=/tmp/ios.log   # a repeated name shares a tab
logtabs --check                          # validate the config and print what it defines
```

| Key | |
| --- | --- |
| `0` | the "all" tab: every tab's log in its own pane (there with two or more tabs) |
| `1`–`9` | switch tabs (or click) |
| `Tab`, click | in "all", pick the pane that `r`, `c` and scrolling act on (it gets a highlighted border) |
| `Enter` | in "all", open the picked pane's own tab |
| `s` | in "all", cycle the split: auto, rows, columns, grid |
| `/` | filter every tab by a case-insensitive regex, with matches highlighted; `Esc` clears it |
| `d` | show or hide DEBUG/TRACE lines |
| `w` | wrap long lines or not |
| scroll up, `PgUp`, `Home` | pause on what you're reading; `End` or scrolling back down follows again |
| `c` | clear the tab |
| `r` | run the tab's `restart` command, if it has one |
| your `[[actions]]` keys | run that command |
| `q` | quit |

Runs of blank lines show as one. A file that is truncated or replaced counts as started over. A tab with one file then shows
only the new content; a tab with several marks the point with a `──── name started over ────`
line. Color escapes are stripped. Each tab keeps its last 20,000 lines in memory, and the first
read of an existing file starts from its last 1 MB.

## Config

`logtabs.toml`; relative paths and commands are resolved from its directory, `~` and `$VARS`
are expanded in paths.

```toml
title = "myproject logs"   # default: the directory's name
hide_debug = true          # start with DEBUG lines hidden (d toggles)
overview = true            # the "all" tab, which it opens on (default true; needs two or more tabs)
split = "auto"             # its layout: auto (rows for up to 3 tabs, else a 2-column grid),
                           # rows, columns or grid
drop = ['^\s*$']           # regexes for lines never to show at all

[patterns]                 # override how lines are colored (Python regexes)
error = '\b(ERROR|FATAL)\b|^\s+at \S'
warn = '\bWARN\b'
debug = '\bDEBUG\b'

[[tabs]]                   # one file: name, path, and optionally port and restart
name = "api"
path = "/tmp/myproject-api.log"
port = 8080                # shown as up/down by whether localhost:8080 accepts a connection
restart = "scripts/dev.sh restart api"

[[tabs]]                   # several files, interleaved and labeled
name = "app"
restart = "scripts/dev.sh restart metro && scripts/dev.sh restart ios"
sources = [
  { name = "metro", path = "/tmp/myproject-metro.log", port = 8081, color = "cyan" },
  { name = "ios", path = "/tmp/myproject-ios.log" },
]

[[actions]]                # extra keys; the viewer's own keys can't be reused
key = "u"
description = "start all"
run = "scripts/dev.sh up -d"
```

Commands run through the shell with the config's directory as the working directory, in the
background; a toast shows when they finish or the last line of output if they fail.

## Develop

```bash
uv run pytest
uv run logtabs tests/…     # or any log files
```

## License

MIT — see [LICENSE](LICENSE).
