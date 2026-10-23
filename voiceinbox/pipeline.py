"""扫描一次、转写一条、失败退避。转写之后交给 enrich：纠错、闪念/完整路由、本机总结、Obsidian、搜索索引。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from voiceinbox import applemeta
from voiceinbox.asr import AsrError, transcribe
from voiceinbox.audio import asr_timeout, convert_timeout, probe, to_wav16k
from voiceinbox.config import Config, ensure_tool_path
from voiceinbox.discover import discover
from voiceinbox.files import (
    AUDIO_EXTS,
    ensure_outside,
    file_sha256,
    is_inside,
    recording_id,
    safe_unlink,
    stage_copy,
)
from voiceinbox.launcher import launcher_path
from voiceinbox.lock import FileLock
from voiceinbox.log import LOG, clip, setup_logging
from voiceinbox.state import Record, State
from voiceinbox.vocab import ensure_vocab
from voiceinbox.timeutil import datetime_from_filename, now as now_in, to_local_iso


@dataclass
class RunStats:
    locked: bool = False
    discovered: int = 0
    transcribed: int = 0
    failed: int = 0
    dead: int = 0
    skipped: int = 0
    fda_blocked: bool = False
    warnings: list[str] = field(default_factory=list)


def ensure_dirs(cfg: Config) -> None:
    dirs = (
        cfg.inbox_dir,
        cfg.staging_dir,
        cfg.work_dir,
        cfg.transcripts_dir,
        cfg.failed_dir,
        cfg.state_dir,
        cfg.logs_dir,
        cfg.archive_dir,
        cfg.vocab_dir,
    )
    for directory in dirs:
        if is_inside(directory, cfg.voice_memos_dir):
            raise RuntimeError(f"数据目录不能放进语音备忘录目录：{directory}")
        directory.mkdir(parents=True, exist_ok=True)
    ensure_vocab(cfg)


def run_once(cfg: Config, *, transcribe_fn=None) -> RunStats:
    ensure_tool_path()
    ensure_dirs(cfg)
    setup_logging(cfg.logs_dir)
    stats = RunStats()
    with FileLock(cfg.lock_path) as locked:
        if not locked:
            LOG.info("另一实例正在运行，本次退出")
            stats.locked = True
            return stats
        _run_locked(cfg, stats, transcribe_fn)
    return stats


def _run_locked(cfg: Config, stats: RunStats, transcribe_fn) -> None:
    from voiceinbox.deletion import resume_deletions_locked

    state = State(cfg.db_path)
    resume_deletions_locked(cfg, state)
    found = discover(cfg, state)
    stats.discovered = len(found.new_ids)
    stats.fda_blocked = found.fda_blocked
    stats.warnings.extend(found.warnings)
    if found.fda_blocked:
        LOG.error(
            "读不到语音备忘录。请给这个程序完全磁盘访问：%s 。运行 python -m voiceinbox doctor 查看步骤。",
            launcher_path(),
        )
        _maybe_notify(cfg)
    moment = now_in(cfg.timezone)
    due = state.due_for_work(moment)
    if len(due) > cfg.max_per_run:
        LOG.warning("待处理 %s 条，本轮只处理 %s 条", len(due), cfg.max_per_run)
    for rec in due[: cfg.max_per_run]:
        _process_safe(cfg, state, rec, stats, transcribe_fn)
    write_index(cfg, state)
    _enrich_safe(cfg, state, limit=cfg.max_per_run)
    LOG.info(
        "本轮结束：发现 %s，转写 %s，跳过 %s，失败 %s，耗尽 %s",
        stats.discovered,
        stats.transcribed,
        stats.skipped,
        stats.failed,
        stats.dead,
    )


def process_path(cfg: Config, path: Path, *, transcribe_fn=None) -> tuple[RunStats, Record | None]:
    """手动处理一个文件。不等稳定时间，也不套用 since。"""
    ensure_tool_path()
    ensure_dirs(cfg)
    setup_logging(cfg.logs_dir)
    stats = RunStats()
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() not in AUDIO_EXTS:
        raise ValueError(f"不支持的音频扩展名：{path.suffix}")
    with FileLock(cfg.lock_path) as locked:
        if not locked:
            LOG.info("另一实例正在运行")
            stats.locked = True
            return stats, None
        state = State(cfg.db_path)
        sha = file_sha256(path)
        rec = _record_for_manual(cfg, state, path, sha)
        if rec.status != "transcribed":
            _process_safe(cfg, state, rec, stats, transcribe_fn)
        else:
            LOG.info("已经转写过 %s → %s", rec.id, rec.text_path)
        write_index(cfg, state)
        _enrich_safe(cfg, state, ids=[rec.id])
        return stats, state.get(rec.id)


def _prepare(cfg: Config) -> None:
    ensure_tool_path()
    ensure_dirs(cfg)
    setup_logging(cfg.logs_dir)


def _with_transcription_lock(cfg: Config, stats: RunStats, body) -> None:
    with FileLock(cfg.lock_path) as locked:
        if not locked:
            LOG.info("另一实例正在运行")
            stats.locked = True
            return
        state = State(cfg.db_path)
        body(state)
        write_index(cfg, state)


def _retry_ids(cfg: Config, state: State, ids: list[str], stats: RunStats, transcribe_fn) -> None:
    if not ids:
        LOG.info("没有需要重试的记录")
    for rec_id in ids:
        state.reset_for_retry(rec_id)
        rec = state.get(rec_id)
        if rec is None:
            raise KeyError(rec_id)
        _process_safe(cfg, state, rec, stats, transcribe_fn)


def retry(cfg: Config, target: str, *, transcribe_fn=None) -> RunStats:
    _prepare(cfg)
    stats = RunStats()

    def body(state: State) -> None:
        if target in {"--all-dead", "all-dead", "--all"}:
            ids = [rec.id for rec in state.list_by_status("dead")]
        else:
            ids = [target]
        _retry_ids(cfg, state, ids, stats, transcribe_fn)

    _with_transcription_lock(cfg, stats, body)
    return stats


def retry_statuses(cfg: Config, statuses: set[str], *, transcribe_fn=None) -> RunStats:
    """重试失败或 dead。同一把锁里做完，避免和 launchd 各加载一份 Qwen。"""
    allowed = {"failed", "dead"}
    if not statuses or set(statuses) - allowed:
        raise ValueError("只能重试失败或 dead")
    _prepare(cfg)
    stats = RunStats()

    def body(state: State) -> None:
        ids: list[str] = []
        for name in ("failed", "dead"):
            if name in statuses:
                ids.extend(rec.id for rec in state.list_by_status(name))
        _retry_ids(cfg, state, ids, stats, transcribe_fn)

    _with_transcription_lock(cfg, stats, body)
    return stats


_HINT_LIMIT = 4000


def _check_hint(value: str | None, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"{label}必须是字符串")
    if "\x00" in value:
        raise ValueError(f"{label}含有非法字符")
    if len(value) > _HINT_LIMIT:
        raise ValueError(f"{label}超过 {_HINT_LIMIT} 字")


def _hinted_transcribe(cfg: Config, *, context: str | None, hotwords: str | None):
    def fn(wav: Path, out_txt: Path, timeout_s: float) -> str:
        return transcribe(
            wav,
            out_txt,
            cfg,
            timeout_s=timeout_s,
            context=context,
            hotwords=hotwords,
        )

    return fn


def _transcript_file(cfg: Config, rec_id: str) -> Path:
    path = cfg.transcripts_dir / f"{rec_id}.txt"
    ensure_outside(path, cfg.voice_memos_dir)
    return path


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_bytes(data)
    os.replace(partial, path)


def retranscribe(
    cfg: Config,
    rec_id: str,
    *,
    context: str | None = None,
    hotwords: str | None = None,
    transcribe_fn=None,
) -> tuple[RunStats, Record | None]:
    """再转一条已有记录。先清掉旧文本，失败时把旧文本放回去。

    context / hotwords 传给 transcribe.sh 的 ASR_CONTEXT 和 ASR_HOTWORDS。
    传入 transcribe_fn 时由调用方自己处理这两个参数，这里不再套一层。
    原录音只读。没抢到锁则什么都不改。
    """
    _check_hint(context, "背景说明")
    _check_hint(hotwords, "热词")
    _prepare(cfg)
    stats = RunStats()
    with FileLock(cfg.lock_path) as locked:
        if not locked:
            LOG.info("另一实例正在运行")
            stats.locked = True
            return stats, None
        state = State(cfg.db_path)
        rec = state.get(rec_id)
        if rec is None:
            raise KeyError(rec_id)
        if state.deletion_pending(rec.id):
            raise ValueError("这条录音删除未完成，请先重试删除")
        txt = _transcript_file(cfg, rec.id)
        if txt.is_symlink():
            raise RuntimeError(f"拒绝跟随转写文本的符号链接：{txt}")
        backup: bytes | None = None
        if txt.is_file():
            backup = txt.read_bytes()
            txt.unlink()
        try:
            state.reset_for_retry(rec.id)
            fresh = state.get(rec.id)
            if fresh is None:
                raise KeyError(rec_id)
            fn = transcribe_fn or _hinted_transcribe(cfg, context=context, hotwords=hotwords)
            LOG.info(
                "手动重转 %s，背景 %s 字，热词 %s 字",
                rec.id,
                len((context or "").strip()),
                len((hotwords or "").strip()),
            )
            _process_safe(cfg, state, fresh, stats, fn)
        finally:
            done = state.get(rec.id)
            if backup is not None and (done is None or done.status != "transcribed"):
                _atomic_write_bytes(txt, backup)
            write_index(cfg, state)
        if done is not None and done.status == "transcribed":
            _enrich_safe(cfg, state, ids=[rec.id])
        return stats, state.get(rec.id)


def _enrich_safe(cfg: Config, state: State, *, limit: int | None = None, ids: list[str] | None = None) -> None:
    """已经拿着锁。整理出错只记日志，不影响转写结果。"""
    from voiceinbox.enrich import enrich_pending

    try:
        enrich_pending(cfg, state, limit=limit, ids=ids)
    except Exception:
        LOG.exception("转写后的整理失败，下一轮再试")


def status_text(cfg: Config) -> str:
    ensure_dirs(cfg)
    state = State(cfg.db_path)
    counts = state.counts()
    lines = ["语音备忘录转写", f"数据目录：{cfg.root_dir}", ""]
    if not counts:
        lines.append("还没有记录。")
    else:
        for name in ("discovered", "stable", "processing", "converted", "transcribed", "failed", "dead", "skipped"):
            if counts.get(name):
                lines.append(f"{name:12} {counts[name]}")
    lines.append("")
    lines.append("最近 10 条：")
    recent = state.recent(10)
    if not recent:
        lines.append("（空）")
    for rec in recent:
        err = ""
        if rec.last_error:
            err = clip(rec.last_error.splitlines()[-1], 80)
        when = rec.recorded_at or "-"
        lines.append(f"- {rec.id}  {rec.status:12}  {rec.source:12}  {when}  {err}")
    return "\n".join(lines) + "\n"


def _record_for_manual(cfg: Config, state: State, path: Path, sha: str) -> Record:
    state.clear_completed_deletion(sha)
    existing = state.get_by_sha(sha)
    if existing and existing.status == "transcribed":
        return existing
    if existing:
        state.advance(
            existing.id,
            "stable",
            original_path=str(path),
            attempts=0,
            last_error=None,
            next_retry_at=None,
        )
        return state.get(existing.id)  # type: ignore[return-value]
    source = "voice_memos" if is_inside(path, cfg.voice_memos_dir) else "inbox"
    meta = applemeta.AppleMeta()
    if source == "voice_memos":
        meta = applemeta.lookup(cfg.voice_memos_dir, path)
    if meta.recorded_at is not None:
        recorded = meta.recorded_at
    else:
        recorded = datetime_from_filename(path.name, cfg.timezone) or datetime.fromtimestamp(
            path.stat().st_mtime, ZoneInfo(cfg.timezone)
        )
    staged = stage_copy(path, cfg.staging_dir, recording_id(sha), forbidden=cfg.voice_memos_dir)
    stat = path.stat()
    state.remember_seen(str(path), stat.st_size, stat.st_mtime, sha)
    return state.upsert_discovered(
        sha256=sha,
        source=source,
        original_path=str(path),
        staged_path=str(staged),
        recorded_at=to_local_iso(recorded, cfg.timezone),
        apple_title=meta.title,
        duration_s=meta.duration_s,
    )


def _process_safe(cfg: Config, state: State, rec: Record, stats: RunStats, transcribe_fn) -> None:
    try:
        outcome = process_record(cfg, state, rec, transcribe_fn=transcribe_fn)
    except Exception as exc:
        err = "".join(traceback.format_exception(exc))
        LOG.error("处理失败 %s：%s", rec.id, clip(err, 2000))
        status = state.mark_failed(
            rec.id,
            clip(err, 4000),
            max_attempts=cfg.max_attempts,
            retry_base_s=cfg.retry_base_s,
            now=now_in(cfg.timezone),
        )
        stats.failed += 1
        if status == "dead":
            stats.dead += 1
            fresh = state.get(rec.id)
            if fresh is not None:
                _quarantine_safe(cfg, fresh, err)
        return
    if outcome == "transcribed":
        stats.transcribed += 1
    elif outcome == "skipped":
        stats.skipped += 1
    elif outcome == "dead":
        stats.dead += 1
        stats.failed += 1


def process_record(cfg: Config, state: State, rec: Record, *, transcribe_fn=None) -> str:
    current = state.get(rec.id)
    if current is None:
        raise KeyError(rec.id)
    if current.status in {"transcribed", "dead", "skipped"}:
        return "unchanged"
    claim = state.begin_attempt(current.id, max_attempts=cfg.max_attempts)
    if claim == "dead":
        fresh = state.get(current.id)
        if fresh is not None:
            _quarantine_safe(cfg, fresh, fresh.last_error or "处理过程中被中断，已达重试上限")
        return "dead"
    if claim != "processing":
        return "unchanged"
    current = state.get(current.id) or current
    if current.source == "voice_memos" and not current.apple_title:
        _refresh_apple(cfg, state, current)
        current = state.get(rec.id) or current

    forbidden = cfg.voice_memos_dir
    staged = _ensure_staged(cfg, current, forbidden)
    state.advance(current.id, current.status, staged_path=str(staged))
    wav = cfg.work_dir / f"{current.id}.16k.wav"
    ensure_outside(wav, forbidden)
    if not wav.exists() or wav.stat().st_size == 0:
        timeout = convert_timeout(_source_duration(staged))
        to_wav16k(staged, wav, timeout_s=timeout)
    info = probe(wav)
    duration = info.duration_s if info.duration_s is not None else 0
    if duration < cfg.min_duration_s:
        safe_unlink(wav, forbidden)
        state.advance(
            current.id,
            "skipped",
            duration_s=duration,
            attempts=0,
            last_error=f"短于 {cfg.min_duration_s} 秒",
        )
        return "skipped"

    state.advance(current.id, "processing", duration_s=duration, staged_path=str(staged))
    txt = cfg.transcripts_dir / f"{current.id}.txt"
    ensure_outside(txt, forbidden)
    elapsed = 0.0
    if txt.exists() and txt.stat().st_size > 0:
        LOG.info("已有转写，跳过 ASR %s", current.id)
    else:
        fn = transcribe_fn or _default_transcribe(cfg)
        started = time.perf_counter()
        fn(wav, txt, asr_timeout(duration))
        elapsed = time.perf_counter() - started
        if not txt.is_file() or txt.stat().st_size == 0:
            raise AsrError("转写没有生成文本文件")
    text = txt.read_text(encoding="utf-8")
    if not text.strip():
        raise AsrError("转写结果为空")
    archive_path = _archive_audio(cfg, current, staged, forbidden)
    current = state.get(current.id) or current
    meta = _meta(cfg, current, txt, text, duration, elapsed, archive_path)
    meta_path = cfg.transcripts_dir / f"{current.id}.json"
    ensure_outside(meta_path, forbidden)
    _atomic_write(meta_path, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    state.advance(
        current.id,
        "transcribed",
        text_path=str(txt),
        archive_path=str(archive_path) if archive_path else None,
        duration_s=duration,
        attempts=0,
        last_error=None,
        next_retry_at=None,
    )
    safe_unlink(wav, forbidden)
    preview = text.strip().replace("\n", " ")[:40]
    LOG.info("转写完成 %s 共 %s 字：%s", current.id, len(text.strip()), preview)
    return "transcribed"


def quarantine(cfg: Config, rec: Record, err: str) -> None:
    cfg.failed_dir.mkdir(parents=True, exist_ok=True)
    err_path = cfg.failed_dir / f"{rec.id}.error.txt"
    ensure_outside(err_path, cfg.voice_memos_dir)
    body = (
        f"id: {rec.id}\n"
        f"sha256: {rec.sha256}\n"
        f"source: {rec.source}\n"
        f"original: {rec.original_path}\n"
        f"attempts: {rec.attempts}\n\n"
        f"{err.strip()}\n"
    )
    _atomic_write(err_path, body)
    src = _audio_source(cfg, rec)
    if src is not None:
        try:
            dest = cfg.failed_dir / f"{rec.id}{src.suffix.lower()}"
            ensure_outside(dest, cfg.voice_memos_dir)
            if src.resolve() != dest.resolve():
                shutil.copy2(src, dest)
        except Exception as exc:
            LOG.error("复制音频到 failed 失败 %s：%s", rec.id, exc)
    LOG.error("已超过重试上限，放入 %s", err_path)


def _quarantine_safe(cfg: Config, rec: Record, err: str) -> None:
    try:
        quarantine(cfg, rec, err)
    except Exception:
        LOG.exception("写入 failed 目录失败 %s，本轮继续处理其他录音", rec.id)


def write_index(cfg: Config, state: State) -> None:
    items = []
    for rec in state.list_by_status("transcribed"):
        meta_path = cfg.transcripts_dir / f"{rec.id}.json"
        if meta_path.exists():
            try:
                items.append(json.loads(meta_path.read_text(encoding="utf-8")))
                continue
            except json.JSONDecodeError:
                pass
        items.append(
            {
                "id": rec.id,
                "sha256": rec.sha256,
                "source": rec.source,
                "text_path": rec.text_path,
                "recorded_at": rec.recorded_at,
            }
        )
    path = cfg.transcripts_dir / "index.json"
    ensure_outside(path, cfg.voice_memos_dir)
    _atomic_write(path, json.dumps(items, ensure_ascii=False, indent=2) + "\n")


def _refresh_apple(cfg: Config, state: State, rec: Record) -> None:
    meta = applemeta.lookup(cfg.voice_memos_dir, Path(rec.original_path))
    fields: dict[str, object] = {}
    if meta.title:
        fields["apple_title"] = meta.title
    if meta.recorded_at and not rec.recorded_at:
        fields["recorded_at"] = to_local_iso(meta.recorded_at, cfg.timezone)
    if fields:
        state.advance(rec.id, rec.status, **fields)


def _ensure_staged(cfg: Config, rec: Record, forbidden: Path) -> Path:
    if rec.staged_path:
        staged = Path(rec.staged_path)
        try:
            if staged.exists() and staged.stat().st_size > 0 and not is_inside(staged, forbidden):
                return staged
        except OSError:
            pass
    src = _audio_source(cfg, rec)
    if src is None:
        raise FileNotFoundError(f"找不到原始音频：{rec.original_path}")
    return stage_copy(src, cfg.staging_dir, rec.id, forbidden=forbidden)


def _audio_source(cfg: Config, rec: Record) -> Path | None:
    original = Path(rec.original_path)
    try:
        if original.exists() and original.stat().st_size > 0:
            return original
    except OSError:
        pass
    if rec.staged_path:
        staged = Path(rec.staged_path)
        try:
            if staged.exists() and staged.stat().st_size > 0:
                return staged
        except OSError:
            pass
    if rec.archive_path:
        archive = Path(rec.archive_path)
        try:
            if archive.is_file() and archive.stat().st_size > 0:
                return archive
        except OSError:
            pass
    if cfg.failed_dir.exists():
        for candidate in sorted(cfg.failed_dir.glob(f"{rec.id}.*")):
            if candidate.suffix == ".txt" or candidate.name.endswith(".error.txt"):
                continue
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
    return None


def _archive_audio(cfg: Config, rec: Record, staged: Path, forbidden: Path) -> Path | None:
    if not staged.exists():
        return Path(rec.archive_path) if rec.archive_path else None
    if not cfg.keep_audio_copy:
        safe_unlink(staged, forbidden)
        return None
    when = _when(rec.recorded_at, cfg.timezone)
    dest_dir = cfg.archive_dir / f"{when:%Y}" / f"{when:%m}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{when:%Y-%m-%d_%H%M}_{rec.id}{staged.suffix.lower()}"
    ensure_outside(dest, forbidden)
    if dest.exists():
        safe_unlink(staged, forbidden)
        return dest
    shutil.move(str(staged), str(dest))
    return dest


def _when(recorded_at: str | None, tz_name: str) -> datetime:
    if recorded_at:
        try:
            return datetime.fromisoformat(recorded_at)
        except ValueError:
            pass
    return now_in(tz_name)


def _source_duration(path: Path) -> float | None:
    try:
        return probe(path).duration_s
    except Exception:
        return None


def _default_transcribe(cfg: Config):
    def fn(wav: Path, out_txt: Path, timeout_s: float) -> str:
        return transcribe(wav, out_txt, cfg, timeout_s=timeout_s)

    return fn


def _meta(
    cfg: Config,
    rec: Record,
    txt: Path,
    text: str,
    duration: float,
    elapsed: float,
    archive_path: Path | None,
) -> dict:
    return {
        "id": rec.id,
        "sha256": rec.sha256,
        "source": rec.source,
        "original_path": rec.original_path,
        "original_name": Path(rec.original_path).name,
        "archive_path": str(archive_path) if archive_path else None,
        "recorded_at": rec.recorded_at,
        "duration_s": duration,
        "apple_title": rec.apple_title,
        "language": cfg.asr_language or "auto",
        "asr_engine": "qwen3-asr",
        "asr_model": cfg.asr_model,
        "elapsed_s": round(elapsed, 3),
        "text_path": str(txt),
        "char_count": len(text.strip()),
        "created_at": now_in(cfg.timezone).isoformat(timespec="seconds"),
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)


def _maybe_notify(cfg: Config) -> None:
    if not cfg.notify_on_fda_block:
        return
    today = now_in(cfg.timezone).date().isoformat()
    stamp = cfg.state_dir / f"fda-notified-{today}"
    if stamp.exists():
        return
    message = f"读不到语音备忘录目录。请给这个程序完全磁盘访问：{launcher_path()}"
    script = "on run argv\ndisplay notification (item 1 of argv) with title (item 2 of argv)\nend run"
    try:
        proc = subprocess.run(
            ["osascript", "-e", script, message, "语音备忘录转写"],
            timeout=8,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        LOG.warning("发送通知失败：%s", exc)
        return
    if proc.returncode == 0:
        stamp.write_text(message + "\n", encoding="utf-8")
    else:
        LOG.warning("通知未发出：%s", (proc.stderr or "").strip())
