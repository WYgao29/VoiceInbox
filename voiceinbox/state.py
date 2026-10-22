"""SQLite 状态库。主键是音频内容的 sha256，和文件名无关。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from voiceinbox.timeutil import now_iso

_MUTABLE = {
    "source",
    "original_path",
    "staged_path",
    "recorded_at",
    "duration_s",
    "apple_title",
    "attempts",
    "last_error",
    "next_retry_at",
    "text_path",
    "archive_path",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
  id            TEXT PRIMARY KEY,
  sha256        TEXT UNIQUE NOT NULL,
  source        TEXT NOT NULL,
  original_path TEXT NOT NULL,
  staged_path   TEXT,
  recorded_at   TEXT,
  duration_s    REAL,
  apple_title   TEXT,
  status        TEXT NOT NULL,
  attempts      INTEGER NOT NULL DEFAULT 0,
  last_error    TEXT,
  next_retry_at TEXT,
  text_path     TEXT,
  archive_path  TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_files (
  path       TEXT PRIMARY KEY,
  size       INTEGER NOT NULL,
  mtime      REAL NOT NULL,
  sha256     TEXT,
  since_skip TEXT
);
"""


@dataclass
class Record:
    id: str
    sha256: str
    source: str
    original_path: str
    staged_path: str | None
    recorded_at: str | None
    duration_s: float | None
    apple_title: str | None
    status: str
    attempts: int
    last_error: str | None
    next_retry_at: str | None
    text_path: str | None
    archive_path: str | None
    created_at: str
    updated_at: str


def _record(row: sqlite3.Row) -> Record:
    return Record(*(row[name] for name in Record.__dataclass_fields__))


class State:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(seen_files)")}
        if "since_skip" not in columns:
            conn.execute("ALTER TABLE seen_files ADD COLUMN since_skip TEXT")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL").fetchall()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, rec_id: str) -> Record | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM recordings WHERE id = ?", (rec_id,)).fetchone()
        return _record(row) if row else None

    def get_by_sha(self, sha256: str) -> Record | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM recordings WHERE sha256 = ?", (sha256,)).fetchone()
        return _record(row) if row else None

    def upsert_discovered(
        self,
        *,
        sha256: str,
        source: str,
        original_path: str,
        staged_path: str | None = None,
        recorded_at: str | None = None,
        duration_s: float | None = None,
        apple_title: str | None = None,
    ) -> Record:
        existing = self.get_by_sha(sha256)
        if existing:
            return existing
        rec_id = sha256[:12]
        taken = self.get(rec_id)
        if taken is not None and taken.sha256 != sha256:
            rec_id = sha256[:20]
        stamp = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO recordings (
                  id, sha256, source, original_path, staged_path, recorded_at,
                  duration_s, apple_title, status, attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovered', 0, ?, ?)
                """,
                (
                    rec_id,
                    sha256,
                    source,
                    original_path,
                    staged_path,
                    recorded_at,
                    duration_s,
                    apple_title,
                    stamp,
                    stamp,
                ),
            )
        found = self.get(rec_id)
        if found is None:
            raise RuntimeError(f"写入状态库失败：{rec_id}")
        return found

    def advance(self, rec_id: str, status: str, **fields: object) -> None:
        unknown = set(fields) - _MUTABLE
        if unknown:
            raise ValueError(f"不能写入字段：{sorted(unknown)}")
        assignments = {"status": status, "updated_at": now_iso(), **fields}
        columns = ", ".join(f"{name} = ?" for name in assignments)
        values = list(assignments.values()) + [rec_id]
        with self._connect() as conn:
            cursor = conn.execute(f"UPDATE recordings SET {columns} WHERE id = ?", values)
            if cursor.rowcount != 1:
                raise KeyError(rec_id)

    def begin_attempt(self, rec_id: str, *, max_attempts: int) -> str:
        """开工前占一次尝试并立刻落盘。进程在本次中途被杀掉时，次数已经记下。

        次数已经达到上限则标为 dead，调用方不要再处理。成功后由调用方把 attempts 清零。
        """
        rec = self.get(rec_id)
        if rec is None:
            raise KeyError(rec_id)
        if rec.status in {"transcribed", "dead", "skipped"}:
            return rec.status
        if rec.attempts >= max_attempts:
            reason = rec.last_error or "处理过程中被中断，已达重试上限"
            self.advance(rec_id, "dead", last_error=reason, next_retry_at=None)
            return "dead"
        self.advance(
            rec_id,
            "processing",
            attempts=rec.attempts + 1,
            next_retry_at=None,
        )
        return "processing"

    def mark_failed(
        self,
        rec_id: str,
        err: str,
        *,
        max_attempts: int,
        retry_base_s: int,
        now: datetime,
    ) -> str:
        """失败一次。未到上限则指数退避（5 分钟、10 分钟…）；到上限变为 dead。

        status 为 processing 时，attempts 已由 begin_attempt 加上，这里不再加。
        """
        rec = self.get(rec_id)
        if rec is None:
            raise KeyError(rec_id)
        if rec.status == "processing" and rec.attempts >= 1:
            attempts = rec.attempts
        else:
            attempts = rec.attempts + 1
        if attempts >= max_attempts:
            self.advance(
                rec_id,
                "dead",
                attempts=attempts,
                last_error=err,
                next_retry_at=None,
            )
            return "dead"
        wait = retry_base_s * (2 ** (attempts - 1))
        nxt = (now + timedelta(seconds=wait)).isoformat(timespec="seconds")
        self.advance(
            rec_id,
            "failed",
            attempts=attempts,
            last_error=err,
            next_retry_at=nxt,
        )
        return "failed"

    def forget_since_skips(self, *, sha256: str | None = None, original_path: str | None = None) -> None:
        """删掉「早于 since」的旧 skipped 行。这种跳过不再落库，改过 since 后应能重新看到。"""
        clauses: list[str] = []
        args: list[str] = []
        if sha256:
            clauses.append("sha256 = ?")
            args.append(sha256)
        if original_path:
            clauses.append("original_path = ?")
            args.append(original_path)
        if not clauses:
            return
        where = " OR ".join(clauses)
        with self._connect() as conn:
            conn.execute(
                f"""
                DELETE FROM recordings
                WHERE status = 'skipped'
                  AND last_error LIKE '早于 since%'
                  AND ({where})
                """,
                args,
            )

    def reset_for_retry(self, rec_id: str) -> None:
        if self.get(rec_id) is None:
            raise KeyError(rec_id)
        self.advance(rec_id, "stable", attempts=0, last_error=None, next_retry_at=None)

    def due_for_work(self, now: datetime) -> list[Record]:
        stamp = now.isoformat(timespec="seconds")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM recordings
                WHERE status NOT IN ('transcribed', 'dead', 'skipped')
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY COALESCE(recorded_at, '9999') ASC, created_at ASC
                """,
                (stamp,),
            ).fetchall()
        return [_record(row) for row in rows]

    def seen_sha_if_unchanged(self, path: str, size: int, mtime: float) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT size, mtime, sha256 FROM seen_files WHERE path = ?", (path,)).fetchone()
        if row is None or row["sha256"] is None:
            return None
        if row["size"] == size and abs(row["mtime"] - mtime) < 1e-4:
            return row["sha256"]
        return None

    def remember_seen(
        self,
        path: str,
        size: int,
        mtime: float,
        sha256: str,
        *,
        since_skip: str | None = None,
    ) -> None:
        """记住这个路径的大小、修改时间和哈希。

        since_skip 只用于「早于 since、不写 recordings」的语音备忘录。
        值是当时的 since（YYYY-MM-DD）。下次 since 对不上就当没记过。
        普通调用不传这个参数，会把旧的 since_skip 清掉。
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO seen_files (path, size, mtime, sha256, since_skip)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                  size = excluded.size,
                  mtime = excluded.mtime,
                  sha256 = excluded.sha256,
                  since_skip = excluded.since_skip
                """,
                (path, size, mtime, sha256, since_skip),
            )

    def matches_since_skip(self, path: str, size: int, mtime: float, since_skip: str) -> bool:
        """大小和修改时间没变，且这条记录就是为当前 since 记下的跳过。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT size, mtime, since_skip FROM seen_files WHERE path = ?",
                (path,),
            ).fetchone()
        if row is None or row["since_skip"] != since_skip:
            return False
        return row["size"] == size and abs(row["mtime"] - mtime) < 1e-4

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM recordings GROUP BY status").fetchall()
        return {row["status"]: row["n"] for row in rows}

    def recent(self, limit: int = 10) -> list[Record]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recordings ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_record(row) for row in rows]

    def list_by_status(self, status: str) -> list[Record]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recordings WHERE status = ? ORDER BY COALESCE(recorded_at, created_at)",
                (status,),
            ).fetchall()
        return [_record(row) for row in rows]

    def all_records(self) -> list[Record]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM recordings
                ORDER BY COALESCE(recorded_at, created_at) DESC, id ASC
                """
            ).fetchall()
        return [_record(row) for row in rows]
