"""logtabs [-c CONFIG] [[TAB=]FILE ...]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import CONFIG_NAME, ConfigError, find_config, from_args, load


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="logtabs",
        description="Follow log files in a tabbed terminal UI.",
        epilog=f"With no files and no -c, reads {CONFIG_NAME} from this directory or the nearest one above it.",
    )
    parser.add_argument("files", nargs="*", metavar="[TAB=]FILE",
                        help="a file to follow; repeat a TAB name to put several files in one tab")
    parser.add_argument("-c", "--config", type=Path, help=f"the config file (default: the nearest {CONFIG_NAME})")
    parser.add_argument("--check", action="store_true", help="validate the config and print what it defines")
    parser.add_argument("--version", action="version", version=f"logtabs {__version__}")
    args = parser.parse_args(argv)

    try:
        if args.files and args.config:
            parser.error("pass either files or -c, not both")
        if args.files:
            config = from_args(args.files)
        else:
            path = args.config or find_config(Path.cwd())
            if path is None:
                parser.error(f"no {CONFIG_NAME} here or above, and no files given")
            config = load(path)
    except (ConfigError, OSError) as e:
        print(f"logtabs: {e}", file=sys.stderr)
        return 2

    if args.check:
        for i, tab in enumerate(config.tabs, 1):
            print(f"{i} {tab.name}" + (f"  (r: {tab.restart})" if tab.restart else ""))
            for s in tab.sources:
                print(f"    {s.name}: {s.path}" + (f"  port {s.port}" if s.port else ""))
        for a in config.actions:
            print(f"{a.key} {a.description}: {a.run}")
        return 0

    from .app import LogTabs  # Textual is slow to import; not needed for --check or errors

    LogTabs(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
