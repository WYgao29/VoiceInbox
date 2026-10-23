"""发现新录音：语音备忘录只读扫描，inbox 手动投入。按内容哈希去重。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from voiceinbox import applemeta
from voiceinbox.config import Config
from voiceinbox.files import (
    access_status,
    file_sha256,
    is_stable,
    iter_audio,
    recording_id,
    stage_copy,
)
from voiceinbox.log import LOG
from voiceinbox.state import State
from voiceinbox.timeutil import datetime_from_filename, to_local_iso

@dataclass
class DiscoverResult:
    new_ids: list[str] = field(default_factory=list)
    skipped_known: int = 0
    skipped_unstable: int = 0
    skipped_since: int = 0
    warnings: list[str] = field(default_factory=list)
    fda_blocked: bool = False


def discover(cfg: Config, state: State) -> DiscoverResult:
    result = DiscoverResult()
    candidates: list[tuple[str, Path]] = []

    status = access_status(cfg.voice_memos_dir)
    if status == "ok":
        candidates.extend(("voice_memos", path) for path in iter_audio(cfg.voice_memos_dir))
        recordings = cfg.voice_memos_dir / "Recordings"
        if not recordings.exists():
            result.warnings.append(
                f"未找到 {recordings}。若还没在这台 Mac 上打开过「语音备忘录」，或没开 iCloud 同步，这是正常的。"
            )
    elif status == "eperm":
        result.fda_blocked = True
        result.warnings.append(f"无法列出语音备忘录目录（Operation not permitted）：{cfg.voice_memos_dir}")
    elif status == "missing":
        result.warnings.append(f"语音备忘录目录不存在：{cfg.voice_memos_dir}")
    else:
        result.warnings.append(f"语音备忘录目录不可用（{status}）：{cfg.voice_memos_dir}")

    if cfg.inbox_dir.exists():
        candidates.extend(("inbox", path) for path in iter_audio(cfg.inbox_dir))
    else:
        result.warnings.append(f"inbox 不存在：{cfg.inbox_dir}")

    candidates.sort(key=lambda item: (_mtime(item[1]), str(item[1])))
    for source, path in candidates:
        if len(result.new_ids) >= cfg.max_per_run:
            result.warnings.append(f"本轮新发现已达上限 {cfg.max_per_run}，其余留到下次")
            break
        try:
            _consider(cfg, state, result, source, path)
        except Exception as exc:
            LOG.exception("发现阶段跳过 %s", path)
            result.warnings.append(f"{path}: {exc}")
    for warning in result.warnings:
        LOG.warning(warning)
    LOG.info(
        "发现完成：新 %s，已知 %s，未稳定 %s，跳过 %s",
        len(result.new_ids),
        result.skipped_known,
        result.skipped_unstable,
        result.skipped_since,
    )
    return result


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def _before_since(cfg: Config, moment: datetime) -> bool:
    if cfg.since is None:
        return False
    return moment.astimezone(ZoneInfo(cfg.timezone)).date() < cfg.since


def _consider(
    cfg: Config,
    state: State,
    result: DiscoverResult,
    source: str,
    path: Path,
) -> None:
    stat = path.stat()
    try:
        key = str(path.resolve())
    except OSError:
        key = str(path)
    # 旧版本把 since 之前的录音写成 skipped。删掉这些行，避免改了 since 也永远不再处理。
    state.forget_since_skips(original_path=key)
    known = state.seen_sha_if_unchanged(key, stat.st_size, stat.st_mtime)
    if known and state.is_deleted_sha(known):
        result.skipped_known += 1
        return
    if known and state.get_by_sha(known):
        result.skipped_known += 1
        return
    # 上一轮已按数据库日期或修改时间判定早于当时的 since。since 没变就不必再等稳定、再算哈希。
    if source == "voice_memos" and cfg.since is not None:
        if state.matches_since_skip(key, stat.st_size, stat.st_mtime, cfg.since.isoformat()):
            result.skipped_since += 1
            return
    if source == "voice_memos":
        named = datetime_from_filename(path.name, cfg.timezone)
        if named is not None and _before_since(cfg, named):
            result.skipped_since += 1
            return
    if not is_stable(path, min_age_s=cfg.stable_wait_s, recheck_s=cfg.stable_recheck_s):
        result.skipped_unstable += 1
        return
    stat = path.stat()
    sha = file_sha256(path)
    if state.is_deleted_sha(sha):
        state.remember_seen(key, stat.st_size, stat.st_mtime, sha)
        result.skipped_known += 1
        return
    state.forget_since_skips(sha256=sha)
    if state.get_by_sha(sha):
        state.remember_seen(key, stat.st_size, stat.st_mtime, sha)
        result.skipped_known += 1
        return

    meta = applemeta.AppleMeta()
    if source == "voice_memos":
        meta = applemeta.lookup(cfg.voice_memos_dir, path)
    recorded, reliable = _recorded_at(cfg, path, meta, stat.st_mtime)
    if source == "voice_memos" and not reliable and cfg.undated_policy == "skip":
        rec = state.upsert_discovered(
            sha256=sha,
            source=source,
            original_path=key,
            recorded_at=to_local_iso(recorded, cfg.timezone),
            apple_title=meta.title,
            duration_s=meta.duration_s,
        )
        state.advance(rec.id, "skipped", last_error="没有数据库日期或文件名日期，按 undated_policy=skip 跳过")
        state.remember_seen(key, stat.st_size, stat.st_mtime, sha)
        result.skipped_since += 1
        return
    # 日期来自数据库或修改时间、早于 since：不写 recordings。
    # 记下这次的 since。下一轮对得上就直接跳过；since 改了，记录对不上，会重新判断。
    if source == "voice_memos" and _before_since(cfg, recorded):
        state.remember_seen(
            key,
            stat.st_size,
            stat.st_mtime,
            sha,
            since_skip=cfg.since.isoformat() if cfg.since is not None else None,
        )
        result.skipped_since += 1
        return

    state.remember_seen(key, stat.st_size, stat.st_mtime, sha)
    staged = stage_copy(path, cfg.staging_dir, recording_id(sha), forbidden=cfg.voice_memos_dir)
    rec = state.upsert_discovered(
        sha256=sha,
        source=source,
        original_path=key,
        staged_path=str(staged),
        recorded_at=to_local_iso(recorded, cfg.timezone),
        apple_title=meta.title,
        duration_s=meta.duration_s,
    )
    result.new_ids.append(rec.id)
    LOG.info("发现 %s %s %s", rec.id, source, path.name)


def _recorded_at(cfg: Config, path: Path, meta: applemeta.AppleMeta, mtime: float):
    """返回 (时间, 是否来自数据库或文件名)。"""
    if meta.recorded_at is not None:
        return meta.recorded_at, True
    named = datetime_from_filename(path.name, cfg.timezone)
    if named is not None:
        return named, True
    return datetime.fromtimestamp(mtime, ZoneInfo(cfg.timezone)), False
