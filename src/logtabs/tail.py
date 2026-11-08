"""Following a file that may not exist yet, be truncated or be replaced."""

from __future__ import annotations

import re
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

INITIAL_BYTES = 1_000_000  # how far back into an existing file the first read starts
HEAD_BYTES = 64  # compared on every read, to notice a file rewritten in place


class Tail:
    """Reads the lines appended to a file since the last read.

    A file that shrinks, is replaced (a new inode) or whose first bytes change counts as started
    over, which is how most dev scripts restart a service's log. The last check catches a file
    truncated and then written past its old length between two reads. Colors and other ANSI
    escapes are stripped.
    """

    def __init__(self, path: Path, initial_bytes: int = INITIAL_BYTES):
        self.path = path
        self.initial_bytes = initial_bytes
        self.pos: int | None = None  # None until the first read
        self.ino: int | None = None
        self.partial = b""
        self.head = b""  # the file's first bytes, as of the last read

    def read(self) -> tuple[bool, list[str]]:
        """(whether the file started over, the complete lines added since the last read)."""
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return False, []
        reset = mid_line = False
        with self.path.open("rb") as f:
            head = f.read(HEAD_BYTES)
            if self.pos is None:
                self.pos = max(0, st.st_size - self.initial_bytes)
                mid_line = self.pos > 0
            elif st.st_ino != self.ino or st.st_size < self.pos or not head.startswith(self.head):
                self.pos, self.partial, reset = 0, b"", True
            self.ino, self.head = st.st_ino, head
            if st.st_size == self.pos:
                return reset, []
            f.seek(self.pos)
            data = f.read(st.st_size - self.pos)
        self.pos += len(data)
        chunks = (self.partial + data).split(b"\n")
        self.partial = chunks.pop()
        if mid_line and chunks:
            chunks = chunks[1:]  # the first read starts partway through a line
        return reset, [ANSI.sub("", c.decode("utf-8", "replace")).rstrip("\r") for c in chunks]
