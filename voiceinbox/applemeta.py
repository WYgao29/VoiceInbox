"""只读 CloudRecordings.db，取标题、日期、时长。读不到就返回空，不挡转写。

表名和列名随 macOS 版本变化。2026-10-07 这台机器上还读不到真实库
（没有完全磁盘访问，Recordings 目录也不存在），所以按已知列名做防御式查询，
并用合成库做测试。真实表结构仍待授权后用 doctor / 一次真实录音确认。
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from voiceinbox.files import SKIP_DIR_NAMES, access_status
from voiceinbox.log import LOG
from voiceinbox.timeutil import interpret_apple_time

_TABLE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PATH_COLS = ("ZPATH", "ZFILENAME", "ZPATHSTRING", "ZFILENAMESTRING", "PATH", "FILENAME")
_TITLE_COLS = ("ZCUSTOMLABEL", "ZTITLE", "ZNAME", "ZDISPLAYNAME", "ZENCRYPTEDTITLE")
_DATE_COLS = ("ZDATE", "ZCREATIONDATE", "ZRECORDINGDATE")
_DUR_COLS = ("ZDURATION", "ZDURATIONSECONDS", "DURATION")


@dataclass
class AppleMeta:
    title: str | None = None
    recorded_at: datetime | None = None
    duration_s: float | None = None


def find_cloud_db(root: Path) -> Path | None:
    for candidate in (root / "CloudRecordings.db", root / "Recordings" / "CloudRecordings.db"):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    if access_status(root) != "ok":
        return None
    try:
        for path in root.rglob("CloudRecordings.db"):
            if any(part.startswith(".") or part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.is_file():
                return path
    except OSError as exc:
        LOG.warning("搜索 CloudRecordings.db 失败：%s", exc)
    return None


def lookup(root: Path, audio_path: Path) -> AppleMeta:
    try:
        db_path = find_cloud_db(root)
        if db_path is None:
            return AppleMeta()
        return _query_copy(db_path, audio_path.name)
    except Exception as exc:
        LOG.warning("读取 CloudRecordings.db 失败，改用文件名或修改时间：%s", exc)
        return AppleMeta()


def _sidecars(db_path: Path) -> list[Path]:
    return [db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")]


def _query_copy(db_path: Path, filename: str) -> AppleMeta:
    # 先复制再打开，避免和语音备忘录抢同一把 SQLite 锁。
    with tempfile.TemporaryDirectory(prefix="voiceinbox-db-") as tmp:
        folder = Path(tmp)
        for src in _sidecars(db_path):
            if src.is_file():
                shutil.copy2(src, folder / src.name)
        copied = folder / db_path.name
        uri = copied.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return _read(conn, filename)
        finally:
            conn.close()


def _read(conn: sqlite3.Connection, filename: str) -> AppleMeta:
    tables = [
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        if isinstance(row[0], str) and _TABLE_RE.fullmatch(row[0])
    ]
    if "ZCLOUDRECORDING" in tables:
        tables = ["ZCLOUDRECORDING"] + [name for name in tables if name != "ZCLOUDRECORDING"]
    for table in tables:
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        path_col = _first(columns, _PATH_COLS)
        if path_col is None:
            continue
        for row in conn.execute(f"SELECT * FROM {table}"):
            if _names_match(row[path_col], filename):
                return _meta_from_row(row, columns)
    return AppleMeta()


def _first(columns: list[str], preferred: tuple[str, ...]) -> str | None:
    have = set(columns)
    for name in preferred:
        if name in have:
            return name
    return None


def _names_match(value: object, filename: str) -> bool:
    text = _as_text(value)
    if not text:
        return False
    return Path(text).name == filename


def _meta_from_row(row: sqlite3.Row, columns: list[str]) -> AppleMeta:
    title = None
    for name in _TITLE_COLS:
        if name in columns:
            title = _as_text(row[name])
            if title:
                break
    recorded = None
    for name in _DATE_COLS:
        if name in columns:
            recorded = interpret_apple_time(row[name])
            if recorded:
                break
    duration = None
    for name in _DUR_COLS:
        if name in columns and row[name] is not None:
            try:
                duration = float(row[name])
            except (TypeError, ValueError):
                duration = None
            if duration is not None and duration >= 0:
                break
            duration = None
    return AppleMeta(title=title, recorded_at=recorded, duration_s=duration)


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.replace("\x00", "").strip()
        return text or None
    if isinstance(value, bytes):
        for encoding in ("utf-8", "utf-16"):
            try:
                text = value.decode(encoding)
            except UnicodeDecodeError:
                continue
            text = text.replace("\x00", "").strip()
            if text and _printable(text):
                return text
    return None


def _printable(text: str) -> bool:
    return all(char.isprintable() or char.isspace() for char in text)
