import pytest

from logtabs.config import ConfigError, find_config, from_args, load


def write(tmp_path, text):
    path = tmp_path / "logtabs.toml"
    path.write_text(text)
    return path


def test_full_config(tmp_path):
    config = load(write(tmp_path, '''
title = "demo"
hide_debug = true
drop = ['^\\s*$']

[patterns]
error = "boom"

[[tabs]]
name = "api"
path = "logs/api.log"
port = 8080
restart = "make restart-api"

[[tabs]]
name = "app"
sources = [
  { name = "metro", path = "/tmp/metro.log", port = 8081, color = "cyan" },
  { path = "~/ios.log" },
]

[[actions]]
key = "u"
description = "start all"
run = "make up"
'''))
    assert config.title == "demo" and config.hide_debug
    assert config.cwd == tmp_path.resolve()
    api, app = config.tabs
    assert api.sources[0].name == "api"
    assert api.sources[0].path == tmp_path.resolve() / "logs/api.log"
    assert api.sources[0].port == 8080 and api.restart == "make restart-api"
    assert [s.name for s in app.sources] == ["metro", "ios"]
    assert app.sources[1].path.is_absolute() and "~" not in str(app.sources[1].path)
    assert app.sources[0].color == "cyan" and app.sources[1].color  # the rest get a palette color
    assert config.error.search("it went boom")
    assert config.drop[0].search("   ")
    assert config.actions[0].key == "u"


@pytest.mark.parametrize("text, message", [
    ("", "no [[tabs]]"),
    ('[[tabs]]\npath = "a.log"', "needs a name"),
    ('[[tabs]]\nname = "a"', "needs a path or sources"),
    ('[[tabs]]\nname = "a"\npath = "a.log"\nport = "80"', "port must be an integer"),
    ('[[tabs]]\nname = "a"\npath = "a.log"\n[[actions]]\nkey = "q"\nrun = "x"', "taken by the viewer"),
    ('[[tabs]]\nname = "a"\npath = "a.log"\n[patterns]\nerror = "("', "valid regex"),
])
def test_bad_configs(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message.replace("[", r"\[")):
        load(write(tmp_path, text))


def test_find_config_walks_up(tmp_path):
    path = write(tmp_path, '[[tabs]]\nname = "a"\npath = "a.log"')
    nested = tmp_path / "x" / "y"
    nested.mkdir(parents=True)
    assert find_config(nested) == path


def test_from_args_groups_repeated_tab_names(tmp_path):
    config = from_args(["web=/tmp/web.log", "app=/tmp/metro.log", "app=/tmp/ios.log", "/var/log/system.log"])
    assert [t.name for t in config.tabs] == ["web", "app", "system"]
    assert [s.name for s in config.tabs[1].sources] == ["metro", "ios"]
