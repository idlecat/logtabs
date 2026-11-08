"""The config: which tabs there are, the log files each one follows, and the commands bound to
keys. Read from a TOML file (see README.md) or built from command-line arguments."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "logtabs.toml"

DEFAULT_ERROR = r"\b(ERROR|FATAL|SEVERE|CRITICAL|Exception|Traceback|panic)\b|\b[Ee]rror:|^\s+at \S"
DEFAULT_WARN = r"\b(WARN|WARNING)\b|\b[Ww]arning:"
DEFAULT_DEBUG = r"\b(DEBUG|TRACE)\b"

# Keys the viewer itself uses; actions can't take them. Digits switch tabs.
RESERVED_KEYS = {"q", "slash", "/", "escape", "d", "w", "c", "r", "s", "end", "enter", "tab", "shift+tab",
                 "ctrl+p"} | {str(i) for i in range(10)}

SPLITS = ("auto", "rows", "columns", "grid")

PALETTE = ["green", "cyan", "magenta", "blue", "yellow", "bright_green", "bright_cyan", "bright_magenta"]


class ConfigError(Exception):
    pass


@dataclass
class Source:
    name: str
    path: Path
    port: int | None = None  # shown as up/down by whether it accepts a TCP connection
    color: str = ""


@dataclass
class Tab:
    name: str
    sources: list[Source]
    restart: str | None = None  # a shell command, run from the config's directory


@dataclass
class Action:
    key: str
    description: str
    run: str


@dataclass
class Config:
    title: str = "logs"
    tabs: list[Tab] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    hide_debug: bool = False
    overview: bool = True  # an "all" tab with every tab's log side by side (only with 2+ tabs)
    split: str = "auto"  # the overview's layout: one of SPLITS
    drop: list[re.Pattern] = field(default_factory=list)
    error: re.Pattern = field(default_factory=lambda: re.compile(DEFAULT_ERROR))
    warn: re.Pattern = field(default_factory=lambda: re.compile(DEFAULT_WARN))
    debug: re.Pattern = field(default_factory=lambda: re.compile(DEFAULT_DEBUG))
    cwd: Path = field(default_factory=Path.cwd)

    def sources(self) -> list[Source]:
        return [s for tab in self.tabs for s in tab.sources]

    @property
    def has_overview(self) -> bool:
        return self.overview and len(self.tabs) > 1


def find_config(start: Path) -> Path | None:
    """logtabs.toml in [start] or the nearest directory above it."""
    for directory in [start, *start.parents]:
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def _path(value: str, base: Path) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    return path if path.is_absolute() else base / path


def _regex(value: object, where: str) -> re.Pattern:
    if not isinstance(value, str):
        raise ConfigError(f"{where} must be a string")
    try:
        return re.compile(value)
    except re.error as e:
        raise ConfigError(f"{where} isn't a valid regex: {e}") from e


def _assign_colors(config: Config) -> Config:
    for i, source in enumerate(config.sources()):
        if not source.color:
            source.color = PALETTE[i % len(PALETTE)]
    return config


def load(path: Path) -> Config:
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    base = path.resolve().parent
    config = Config(title=data.get("title", base.name), hide_debug=bool(data.get("hide_debug", False)),
                    overview=bool(data.get("overview", True)), split=data.get("split", "auto"), cwd=base)
    if config.split not in SPLITS:
        raise ConfigError(f"split must be one of {', '.join(SPLITS)}")

    patterns = data.get("patterns", {})
    for name in ("error", "warn", "debug"):
        if name in patterns:
            setattr(config, name, _regex(patterns[name], f"patterns.{name}"))
    config.drop = [_regex(p, "drop") for p in data.get("drop", [])]

    for i, raw in enumerate(data.get("tabs", [])):
        where = f"tabs[{i}]"
        name = raw.get("name")
        if not name:
            raise ConfigError(f"{where} needs a name")
        if "path" in raw:  # shorthand for a tab with one source
            raw_sources = [{"name": name, **{k: raw[k] for k in ("path", "port", "color") if k in raw}}]
        else:
            raw_sources = raw.get("sources", [])
        if not raw_sources:
            raise ConfigError(f"tab {name!r} needs a path or sources")
        sources = []
        for src in raw_sources:
            if "path" not in src:
                raise ConfigError(f"a source in tab {name!r} has no path")
            port = src.get("port")
            if port is not None and not isinstance(port, int):
                raise ConfigError(f"source {src.get('name')!r}: port must be an integer")
            sources.append(Source(src.get("name") or Path(src["path"]).stem, _path(src["path"], base),
                                  port, src.get("color", "")))
        config.tabs.append(Tab(name, sources, raw.get("restart")))
    if not config.tabs:
        raise ConfigError(f"{path} defines no [[tabs]]")
    if len(config.tabs) > 9:
        raise ConfigError("at most 9 tabs (they're switched with the keys 1-9)")

    for raw in data.get("actions", []):
        key, run = raw.get("key"), raw.get("run")
        if not key or not run:
            raise ConfigError("every [[actions]] needs a key and a run command")
        if key in RESERVED_KEYS:
            raise ConfigError(f"action key {key!r} is taken by the viewer")
        config.actions.append(Action(key, raw.get("description", run), run))
    return _assign_colors(config)


def from_args(specs: list[str]) -> Config:
    """A config from `[tab=]path` arguments. Repeating a tab name puts several files in one tab."""
    config = Config(title="logtabs")
    tabs: dict[str, Tab] = {}
    for spec in specs:
        name, sep, value = spec.partition("=")
        if not sep:
            name, value = Path(spec).stem, spec
        path = _path(value, Path.cwd())
        source = Source(path.stem, path)
        if name in tabs:
            tabs[name].sources.append(source)
        else:
            tabs[name] = Tab(name, [source])
    config.tabs = list(tabs.values())
    if len(config.tabs) > 9:
        raise ConfigError("at most 9 tabs (they're switched with the keys 1-9)")
    return _assign_colors(config)
