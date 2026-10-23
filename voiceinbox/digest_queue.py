"""删除录音后，先作废旧摘要，再持久重试受影响日期的摘要。

所有带 locked 后缀的函数由调用方持有 cfg.lock_path；模型调用和文件写入
都在 SQLite 事务外进行，队列只在摘要文件和状态库都保存成功后完成。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from voiceinbox.config import Config
from voiceinbox.log import LOG
from voiceinbox.state import State

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_digests (
  day          TEXT PRIMARY KEY,
  state        TEXT NOT NULL DEFAULT 'pending',
  error        TEXT NOT NULL DEFAULT '',
  paths_json   TEXT NOT NULL DEFAULT '[]',
  updated_at   TEXT NOT NULL
)
"""


def _ensure_schema(conn) -> None:
    conn.execute(_SCHEMA)


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _valid_day(day: str) -> None:
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError("日期必须是 YYYY-MM-DD")


def _row(cfg: Config, day: str) -> dict | None:
    from voiceinbox.enrich import _db

    with _db(cfg) as conn:
        _ensure_schema(conn)
        row = conn.execute("SELECT * FROM pending_digests WHERE day = ?", (day,)).fetchone()
    return dict(row) if row else None


def pending_days(cfg: Config) -> list[str]:
    """所有尚未完成的日期，包含上次处理失败的日期。"""
    from voiceinbox.enrich import _db

    with _db(cfg) as conn:
        _ensure_schema(conn)
        return [row[0] for row in conn.execute("SELECT day FROM pending_digests ORDER BY day")]


def _failed(cfg: Config, day: str, error: Exception | str) -> None:
    from voiceinbox.enrich import _db

    with _db(cfg) as conn:
        _ensure_schema(conn)
        conn.execute("UPDATE pending_digests SET state = 'failed', error = ?, updated_at = ? WHERE day = ?", (str(error)[:2000], _stamp(), day))


def _complete(cfg: Config, day: str) -> None:
    from voiceinbox.enrich import _db

    with _db(cfg) as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM pending_digests WHERE day = ?", (day,))


def _deletions_for_day(state: State, day: str) -> list[dict]:
    pending = getattr(state, "pending_deletions", None)
    if pending is None:
        return []
    return [task for task in pending() if (task.get("manifest") or {}).get("day") == day and task.get("state") != "completed"]


def _find_digest_paths(cfg: Config, day: str) -> set[str]:
    """按受管身份找回改名、移动或复制过的摘要；历史备份不属于清理范围。"""
    from voiceinbox import obsidian
    from voiceinbox.deletion import _absolute, _preserved

    if cfg.obsidian_root is None:
        return set()
    root = _absolute(cfg.obsidian_root)
    # 即使默认文件已不存在，也检查管理目录及其祖先，拒绝符号链接。
    obsidian._deletion_text(cfg, root / obsidian.DAILY_DIR / f"{day} 当日摘要.md")
    found = set()
    if root.exists():
        for path in root.rglob("*.md"):
            if _preserved(cfg, path) or path.is_symlink():
                continue
            text = obsidian._deletion_text(cfg, path)
            if text is None:
                continue
            entries, _body = obsidian.split_frontmatter(text)
            identity = {name: obsidian._field(entries, name) for name in ("date", "type", "source")}
            if identity == {"date": day, "type": "当日摘要", "source": "voiceinbox"}:
                found.add(str(_absolute(path)))
    return found


def invalidate_day_locked(cfg: Config, state: State, day: str) -> None:
    """先持久标记过期，再清旧文件；清理失败保留错误并抛给删除任务。"""
    from voiceinbox import enrich, obsidian

    _valid_day(day)
    with enrich._db(cfg) as conn:
        _ensure_schema(conn)
        previous = conn.execute("SELECT paths_json FROM pending_digests WHERE day = ?", (day,)).fetchone()
        paths = set(json.loads(previous[0])) if previous else set()
        old = conn.execute("SELECT path FROM digests WHERE day = ?", (day,)).fetchone()
        if old and old[0]:
            paths.add(str(old[0]))
        if cfg.obsidian_root is not None:
            paths.add(str(cfg.obsidian_root / obsidian.DAILY_DIR / f"{day} 当日摘要.md"))
        conn.execute(
            "INSERT INTO pending_digests (day, state, error, paths_json, updated_at) VALUES (?, 'pending', '', ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET state = 'pending', error = '', paths_json = excluded.paths_json, updated_at = excluded.updated_at",
            (day, json.dumps(sorted(paths), ensure_ascii=False), _stamp()),
        )
        conn.execute("DELETE FROM digests WHERE day = ?", (day,))
    try:
        paths.update(_find_digest_paths(cfg, day))
        # 完整路径先持久保存。随后任一文件失败或进程中断都能继续清理。
        with enrich._db(cfg) as conn:
            conn.execute("UPDATE pending_digests SET paths_json = ?, updated_at = ? WHERE day = ?", (json.dumps(sorted(paths), ensure_ascii=False), _stamp(), day))
        for path in sorted(paths):
            obsidian.remove_digest_content(cfg, Path(path), day)
        if not any(enrich._day_of(rec) == day for rec in state.list_by_status("transcribed")):
            _complete(cfg, day)
    except Exception as exc:
        _failed(cfg, day, exc)
        raise


def digest_status(cfg: Config, state: State, day: str) -> dict[str, str]:
    """未完成的录音删除和摘要队列始终优先于旧摘要的 ready 状态。"""
    from voiceinbox.enrich import _db

    tasks = _deletions_for_day(state, day)
    failed = next((task for task in tasks if task.get("state") == "delete_failed"), None)
    if failed is not None:
        return {"state": "failed", "error": str(failed.get("error") or failed.get("last_error") or "录音删除尚未完成")}
    if tasks:
        return {"state": "pending", "error": ""}
    queued = _row(cfg, day)
    if queued:
        return {"state": "failed" if queued["state"] == "failed" else "pending", "error": queued["error"] or ""}
    with _db(cfg) as conn:
        row = conn.execute("SELECT path FROM digests WHERE day = ?", (day,)).fetchone()
    if row and row[0]:
        path = Path(row[0])
        if path.is_file() and not path.is_symlink():
            return {"state": "ready", "error": ""}
    return {"state": "none", "error": ""}


def process_pending_locked(cfg: Config, state: State, box=None) -> int:
    """独立于每日计划重试历史日期；调用方已经持有共享文件锁。"""
    from voiceinbox import enrich

    own = box is None
    box = box or enrich.ClientBox(cfg)
    completed = 0
    try:
        for day in pending_days(cfg):
            if _deletions_for_day(state, day):
                continue
            try:
                invalidate_day_locked(cfg, state, day)
                if _row(cfg, day) is None:
                    completed += 1
                    continue
                if not cfg.llm_enabled:
                    raise RuntimeError("本机总结未开启，等待开启后重建摘要")
                if not cfg.obsidian_enabled:
                    raise RuntimeError("Obsidian 写入未开启，等待开启后重建摘要")
                path = enrich.run_digest(cfg, state, box, day, force=True)
                if path is None:
                    raise RuntimeError("剩余录音尚未完成整理，稍后重建摘要")
                completed += 1
            except Exception as exc:
                _failed(cfg, day, exc)
                LOG.warning("删除后的摘要重建暂缓 %s：%s", day, exc)
    finally:
        if own:
            box.close()
    return completed
