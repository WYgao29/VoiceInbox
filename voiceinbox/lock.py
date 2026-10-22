"""fcntl 文件锁。同一时间只允许一个转写进程，避免两份 Qwen 叠到约 19GB。"""

from __future__ import annotations

import fcntl
from pathlib import Path


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        self._handle = None
        self.acquired = False

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.acquired = False
            return False
        self.acquired = True
        return True

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._handle is not None:
            if self.acquired:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
        self.acquired = False
        return False
