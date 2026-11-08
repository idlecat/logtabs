import os

from logtabs.tail import Tail


def test_missing_file_then_created(tmp_path):
    path = tmp_path / "a.log"
    tail = Tail(path)
    assert tail.read() == (False, [])
    path.write_text("one\ntwo\n")
    assert tail.read() == (False, ["one", "two"])


def test_partial_line_waits_for_newline(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("")
    tail = Tail(path)
    tail.read()
    with path.open("a") as f:
        f.write("hel")
    assert tail.read() == (False, [])
    with path.open("a") as f:
        f.write("lo\n")
    assert tail.read() == (False, ["hello"])


def test_truncation_starts_over(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("old line one\nold line two\n")
    tail = Tail(path)
    tail.read()
    path.write_text("new\n")  # shorter: truncated and rewritten
    assert tail.read() == (True, ["new"])


def test_replaced_file_starts_over(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("old\n")
    tail = Tail(path)
    tail.read()
    other = tmp_path / "b.log"
    other.write_text("first of the new file\n")
    os.replace(other, path)
    assert tail.read() == (True, ["first of the new file"])


def test_first_read_of_big_file_skips_the_cut_line(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("".join(f"line {i}\n" for i in range(100)))
    reset, lines = Tail(path, initial_bytes=20).read()
    assert not reset
    assert lines == ["line 98", "line 99"]


def test_strips_ansi_and_carriage_returns(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("\x1b[31mred\x1b[0m\r\n")
    assert Tail(path).read() == (False, ["red"])


def test_rewritten_longer_in_place_starts_over(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("short\n")
    tail = Tail(path)
    tail.read()
    path.write_text("a longer first line after a restart\n")  # same inode, bigger than before
    assert tail.read() == (True, ["a longer first line after a restart"])


def test_appending_to_a_short_file_is_not_a_restart(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("ab")  # shorter than HEAD_BYTES, and no newline yet
    tail = Tail(path)
    tail.read()
    with path.open("a") as f:
        f.write("c\nmore\n")
    assert tail.read() == (False, ["abc", "more"])
