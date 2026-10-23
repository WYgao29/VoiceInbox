"""持久、可重试的录音删除。所有写入与流水线共用一个文件锁。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from voiceinbox.config import Config
from voiceinbox.files import file_sha256
from voiceinbox.lock import FileLock
from voiceinbox.state import Record, State

_ID = re.compile(r"[0-9a-f]{12,64}")


def _absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(path))


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _roots(cfg: Config) -> dict[str, Path]:
    return {name: _absolute(path) for name, path in {
        "inbox": cfg.inbox_dir, "staging": cfg.staging_dir,
        "archive": cfg.archive_dir, "work": cfg.work_dir,
        "failed": cfg.failed_dir, "transcripts": cfg.transcripts_dir,
        "state": cfg.state_dir,
    }.items()}


def _preserved(cfg: Config, path: Path) -> bool:
    return any(_under(_absolute(path), _absolute(root)) for root in (cfg.backup_dir, cfg.logs_dir, cfg.voice_memos_dir))


def _safe_path(cfg: Config, path: Path | str, kind: str) -> Path:
    """只接受指定应用目录中的普通文件；检查缺失文件的现有祖先。"""
    path = _absolute(path)
    root = _roots(cfg)[kind]
    if path == root or not _under(path, root):
        raise ValueError(f"拒绝清理应用管理目录外的文件：{path}")
    if _preserved(cfg, path):
        raise ValueError(f"拒绝清理原录音、历史备份或日志：{path}")
    # 配置目录本身以及其祖先也可能是符号链接，不能只检查最后一层。
    for candidate in (*reversed(path.parents), path):
        if candidate.is_symlink():
            raise ValueError(f"拒绝跟随符号链接：{candidate}")
    if _under(path.resolve(), cfg.voice_memos_dir.resolve()):
        raise ValueError(f"拒绝清理苹果原录音目录：{path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"不是普通文件：{path}")
    return path


def _base_manifest(cfg: Config, rec: Record) -> dict:
    from voiceinbox.enrich import _day_of

    files: list[dict[str, str]] = []
    def add(path, kind):
        if path:
            files.append({"path": str(_absolute(path)), "kind": kind})

    original = _absolute(rec.original_path)
    if _under(original, _absolute(cfg.inbox_dir)):
        add(original, "inbox")
    add(rec.staged_path, "staging")
    add(rec.archive_path, "archive")
    add(rec.text_path, "transcripts")
    add(cfg.transcripts_dir / f"{rec.id}.txt", "transcripts")
    add(cfg.transcripts_dir / f"{rec.id}.json", "transcripts")
    return {"day": _day_of(rec), "title": rec.apple_title or rec.id, "files": files, "notes": [], "prepared": False}


def _prepare_manifest(cfg: Config, state: State, task: dict) -> dict:
    """在任何文件清理前收集完整清单，并保存在状态库中。"""
    from voiceinbox import obsidian

    manifest = task["manifest"]
    if manifest.get("prepared"):
        return manifest
    rec_id, sha = task["id"], task["sha256"]
    files = {(item["path"], item["kind"]) for item in manifest["files"]}
    roots = _roots(cfg)
    # 包括转换、失败、派生和中断时的临时文件。
    for kind in ("staging", "work", "failed", "transcripts"):
        root = roots[kind]
        if root.is_symlink():
            raise ValueError(f"拒绝跟随符号链接：{root}")
        if root.exists():
            for candidate in root.rglob(f"{rec_id}.*"):
                if not _preserved(cfg, candidate):
                    files.add((str(candidate), kind))
            for candidate in root.rglob(f".{rec_id}.*"):
                if not _preserved(cfg, candidate):
                    files.add((str(candidate), kind))
    archive = roots["archive"]
    if archive.is_symlink():
        raise ValueError(f"拒绝跟随符号链接：{archive}")
    if archive.exists():
        for candidate in archive.rglob(f"*_{rec_id}.*"):
            if not _preserved(cfg, candidate):
                files.add((str(candidate), "archive"))
    # 扫描可能见过同一内容的多份 inbox 文件，改名后的副本也应清理。
    inbox = roots["inbox"]
    if inbox.is_symlink():
        raise ValueError(f"拒绝跟随符号链接：{inbox}")
    for raw in state.seen_paths_for_sha(sha):
        path = _absolute(raw)
        if _under(path, inbox) and not _preserved(cfg, path):
            files.add((str(path), "inbox"))
    if inbox.exists():
        for path in inbox.rglob("*"):
            if path.is_symlink() or _preserved(cfg, path):
                # 与本录音有关的符号链接已在显式清单中，其他链接不读取。
                continue
            if path.is_file() and file_sha256(_safe_path(cfg, path, "inbox")) == sha:
                files.add((str(path), "inbox"))

    notes = {(item["path"], item["kind"]) for item in manifest.get("notes", [])}
    analysis_path = cfg.transcripts_dir / "derived" / f"{rec_id}.analysis.json"
    checked_analysis = _safe_path(cfg, analysis_path, "transcripts")
    if checked_analysis.exists():
        analysis = json.loads(checked_analysis.read_text(encoding="utf-8"))
        if not isinstance(analysis, dict):
            raise ValueError("录音分析文件格式无效，无法确认已导出的内容")
        for key, kind in (("obsidian_path", "note"), ("daily_path", "flash")):
            if analysis.get(key):
                notes.add((str(_absolute(analysis[key])), kind))
    root = cfg.obsidian_root
    if root is not None:
        root = _absolute(root)
        if root.is_symlink():
            raise ValueError(f"拒绝跟随符号链接：{root}")
        if root.exists():
            # 用户可能改过生成笔记的文件名，按受管身份查找。
            for path in root.rglob("*.md"):
                if path.is_symlink() or _preserved(cfg, path):
                    continue
                if any(data_root != root and _under(path, data_root) for data_root in roots.values()):
                    continue
                text = obsidian._deletion_text(cfg, path)
                if text is None:
                    continue
                entries, _body = obsidian.split_frontmatter(text)
                if obsidian._field(entries, "id") == rec_id:
                    notes.add((str(path), "note"))
                if f"<!-- voiceinbox:flash {rec_id} " in text:
                    notes.add((str(path), "flash"))
    manifest["files"] = [{"path": path, "kind": kind} for path, kind in sorted(files)]
    manifest["notes"] = [{"path": path, "kind": kind} for path, kind in sorted(notes)]
    manifest["prepared"] = True
    state.save_deletion_manifest(rec_id, manifest)
    return manifest


def _execute(cfg: Config, state: State, task: dict) -> None:
    from voiceinbox import digest_queue, obsidian, search, todos
    from voiceinbox.pipeline import write_index

    manifest = _prepare_manifest(cfg, state, task)
    # 在改动任何录音文件前检查整个清单，避免一处越界导致无谓的部分清理。
    checked = [(_safe_path(cfg, item["path"], item["kind"]), item["kind"]) for item in manifest["files"]]
    for item in manifest["notes"]:
        obsidian._deletion_text(cfg, Path(item["path"]))
    day = manifest.get("day")
    if day:
        digest_queue.invalidate_day_locked(cfg, state, day)
    for item in manifest["notes"]:
        remove = obsidian.remove_recording_note if item["kind"] == "note" else obsidian.remove_flash_for_deletion
        remove(cfg, Path(item["path"]), task["id"])
    for path, kind in checked:
        # 再检查一次，避免前面处理文件期间出现符号链接替换。
        _safe_path(cfg, path, kind)
        # inbox 也允许用户直接放文件。同一路径可能已被另一段音频覆盖，
        # 初次删除和 prepared 任务的重试都必须重新核对内容身份。
        if kind == "inbox" and path.exists() and file_sha256(path) != task["sha256"]:
            continue
        path.unlink(missing_ok=True)
    _safe_path(cfg, todos.done_path(cfg), "state")
    todos.remove_record(cfg, task["id"])
    _safe_path(cfg, cfg.search_db_path, "state")
    search.remove(cfg.search_db_path, task["id"])
    _safe_path(cfg, cfg.transcripts_dir / "index.json", "transcripts")
    _safe_path(cfg, cfg.transcripts_dir / "index.json.partial", "transcripts")
    write_index(cfg, state)
    # 只有全部清理成功才移除录音数据库行及清单，失败保留完整依据。
    state.complete_deletion(task["id"])


def _delete_locked(cfg: Config, state: State, ids: list[str]) -> dict:
    from voiceinbox.digest_queue import pending_days

    _safe_path(cfg, cfg.db_path, "state")
    deleted, failed = [], []
    for rec_id in ids:
        try:
            task = state.deletion_for_id(rec_id)
            if task and task["state"] == "completed":
                deleted.append(rec_id)
                continue
            rec = state.get(rec_id)
            if task is None and rec is None:
                raise ValueError("录音不存在")
            task = state.begin_deletion(rec_id, task["manifest"] if task else _base_manifest(cfg, rec))
            _execute(cfg, state, task)
            deleted.append(rec_id)
        except Exception as exc:
            task = state.deletion_for_id(rec_id)
            if task is not None and task["state"] != "completed":
                state.fail_deletion(rec_id, str(exc))
            failed.append({"id": rec_id, "error": str(exc)})
    return {"ok": not failed, "deleted_ids": deleted, "failed": failed, "digest_days": pending_days(cfg)}


def delete_recordings(cfg: Config, ids: list[str]) -> dict:
    from voiceinbox.enrich import Locked

    if not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in ids):
        raise ValueError("ids 必须是非空的录音 ID 列表")
    with FileLock(cfg.lock_path) as acquired:
        if not acquired:
            raise Locked("正在处理录音，请稍后重试删除")
        _safe_path(cfg, cfg.db_path, "state")
        return _delete_locked(cfg, State(cfg.db_path), list(dict.fromkeys(ids)))


def resume_deletions_locked(cfg: Config, state: State) -> dict:
    return _delete_locked(cfg, state, [task["id"] for task in state.pending_deletions()])
