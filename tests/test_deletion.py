"""删除操作只使用临时文件、合成内容，不访问个人录音或真实模型。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from tests.conftest import make_cfg, snapshot
from voiceinbox import deletion, enrich, obsidian, search, todos
from voiceinbox.lock import FileLock
from voiceinbox.pipeline import write_index
from voiceinbox.state import State


def setup_record(tmp_path, *, seed="first", status="transcribed", original="inbox", day="2026-10-10"):
    cfg = make_cfg(tmp_path, obsidian_vault=tmp_path / "vault")
    (cfg.obsidian_vault / ".obsidian").mkdir(parents=True, exist_ok=True)
    state = State(cfg.db_path)
    content = f"synthetic audio {seed}".encode()
    sha = hashlib.sha256(content).hexdigest()
    original_root = {"inbox": cfg.inbox_dir, "apple": cfg.voice_memos_dir, "external": tmp_path / "imports"}[original]
    original_root.mkdir(exist_ok=True)
    src = original_root / f"{seed}.m4a"
    src.write_bytes(content)
    rec = state.upsert_discovered(sha256=sha, source="voice_memos" if original == "apple" else "inbox", original_path=str(src), recorded_at=f"{day}T10:30:00+08:00", apple_title=f"测试 {seed}")
    cfg.transcripts_dir.mkdir(parents=True, exist_ok=True)
    raw = cfg.transcripts_dir / f"{rec.id}.txt"
    raw.write_text(f"转写正文 {seed}", encoding="utf-8")
    state.advance(rec.id, status, text_path=str(raw))
    return cfg, state, state.get(rec.id), src


def write(path, content="generated"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize("status", ["discovered", "stable", "processing", "failed", "dead", "skipped", "transcribed"])
def test_all_states_delete_all_managed_files_and_keep_minimal_tombstone(tmp_path, status):
    cfg, state, rec, src = setup_record(tmp_path, status=status)
    paths = [
        write(cfg.staging_dir / f"{rec.id}.m4a"),
        write(cfg.archive_dir / "2026" / "10" / f"2026-10-10_1030_{rec.id}.m4a"),
        write(cfg.work_dir / f"{rec.id}.16k.wav"),
        write(cfg.failed_dir / f"{rec.id}.error.txt"),
        write(cfg.transcripts_dir / f"{rec.id}.json", json.dumps({"id": rec.id})),
        write(cfg.transcripts_dir / "derived" / f"{rec.id}.corrected.txt"),
        write(cfg.transcripts_dir / "derived" / f"{rec.id}.summary.md.partial"),
    ]
    state.advance(rec.id, status, staged_path=str(paths[0]), archive_path=str(paths[1]))
    todos.set_done(cfg, rec.id, "key", True)
    todos.set_done(cfg, "other", "key", True)
    enrich._save_row(cfg, rec.id, llm=1)
    search.upsert(cfg.search_db_path, rec_id=rec.id, recorded_at=rec.recorded_at, duration_s=30, title="测试标题", tags="", transcript="删除目标正文", summary="")
    write_index(cfg, state)

    result = deletion.delete_recordings(cfg, [rec.id, rec.id])

    assert result["deleted_ids"] == [rec.id] and not result["failed"]
    assert state.get(rec.id) is None
    assert not src.exists() and not Path(rec.text_path).exists()
    assert all(not path.exists() for path in paths)
    assert enrich.get_row(cfg, rec.id) is None
    assert todos.load_done(cfg) == {"other:key": todos.load_done(cfg)["other:key"]}
    assert not search.search(cfg.search_db_path, "删除目标")
    assert json.loads((cfg.transcripts_dir / "index.json").read_text()) == []
    task = state.deletion_for_id(rec.id)
    assert task["sha256"] == rec.sha256 and task["state"] == "completed"
    assert task["manifest"] == {} and task["last_error"] is None
    assert state.is_deleted_sha(rec.sha256)
    assert deletion.delete_recordings(cfg, [rec.id])["deleted_ids"] == [rec.id]


@pytest.mark.parametrize("original", ["apple", "external"])
def test_original_recordings_are_unchanged(tmp_path, original):
    cfg, state, rec, src = setup_record(tmp_path, original=original)
    before = snapshot(src.parent)
    assert deletion.delete_recordings(cfg, [rec.id])["ok"]
    assert snapshot(src.parent) == before


def test_inbox_copies_are_found_by_sha_and_external_seen_paths_survive(tmp_path):
    cfg, state, rec, src = setup_record(tmp_path)
    duplicate = cfg.inbox_dir / "renamed.m4a"
    duplicate.write_bytes(src.read_bytes())
    external = tmp_path / "same-content.m4a"
    external.write_bytes(src.read_bytes())
    for path in (src, external):
        stat = path.stat()
        state.remember_seen(str(path), stat.st_size, stat.st_mtime, rec.sha256)
    other = write(cfg.inbox_dir / "unrelated.m4a", "other recording")
    assert deletion.delete_recordings(cfg, [rec.id])["ok"]
    assert not duplicate.exists() and external.exists() and other.exists()
    with pytest.raises(ValueError, match="已标记删除"):
        state.upsert_discovered(sha256=rec.sha256, source="inbox", original_path=str(external))
    state.clear_completed_deletion(rec.sha256)
    assert not state.is_deleted_sha(rec.sha256)
    assert state.upsert_discovered(sha256=rec.sha256, source="inbox", original_path=str(external)).id == rec.id


def test_partial_batch_failure_is_retryable_and_cannot_reenter_processing(tmp_path, monkeypatch):
    cfg, state, first, src = setup_record(tmp_path, seed="first")
    _, _, second, _ = setup_record(tmp_path, seed="second")
    original_unlink = Path.unlink
    def fail_selected(path, *args, **kwargs):
        if path == src:
            raise PermissionError("测试没有删除权限")
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_selected)
        result = deletion.delete_recordings(cfg, [first.id, second.id])
    assert result["deleted_ids"] == [second.id]
    assert result["failed"] == [{"id": first.id, "error": "测试没有删除权限"}]
    task = state.deletion_for_id(first.id)
    assert task["manifest"]["prepared"] and task["manifest"]["day"] == "2026-10-10"
    assert task["state"] == "delete_failed" and state.get(first.id).status == "delete_failed"
    assert state.get(first.id) not in state.due_for_work(datetime.now().astimezone())
    for action in (lambda: state.begin_attempt(first.id, max_attempts=3), lambda: state.reset_for_retry(first.id), lambda: state.clear_completed_deletion(first.sha256)):
        with pytest.raises(ValueError):
            action()
    assert deletion.delete_recordings(cfg, [first.id])["ok"]
    assert state.get(first.id) is None and not src.exists()


def test_interrupted_cleanup_keeps_manifest_and_resumes_after_reopening_state(tmp_path, monkeypatch):
    cfg, state, rec, src = setup_record(tmp_path)
    work = write(cfg.work_dir / f"{rec.id}.16k.wav")
    original_unlink = Path.unlink
    def interrupted(path, *args, **kwargs):
        if path == work:
            raise KeyboardInterrupt()
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupted)
        with pytest.raises(KeyboardInterrupt):
            deletion.delete_recordings(cfg, [rec.id])
    reopened = State(cfg.db_path)
    assert reopened.deletion_for_id(rec.id)["state"] == "deleting"
    assert reopened.deletion_for_id(rec.id)["manifest"]["prepared"]
    with FileLock(cfg.lock_path) as acquired:
        assert acquired
        assert deletion.resume_deletions_locked(cfg, reopened)["ok"]
    assert not src.exists() and not work.exists() and reopened.get(rec.id) is None


def test_lock_conflict_does_not_create_task_or_delete_anything(tmp_path):
    cfg, state, rec, src = setup_record(tmp_path)
    with FileLock(cfg.lock_path) as acquired:
        assert acquired
        with pytest.raises(enrich.Locked):
            deletion.delete_recordings(cfg, [rec.id])
    assert src.exists() and state.deletion_for_id(rec.id) is None


@pytest.mark.parametrize("prepared", [False, True])
def test_replaced_inbox_path_belongs_to_new_recording_and_survives(tmp_path, monkeypatch, prepared):
    cfg, state, rec, src = setup_record(tmp_path)
    if prepared:
        original_unlink = Path.unlink
        def fail(path, *args, **kwargs):
            if path == src:
                raise PermissionError("先保留清单，稍后重试")
            return original_unlink(path, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", fail)
            assert not deletion.delete_recordings(cfg, [rec.id])["ok"]
        assert state.deletion_for_id(rec.id)["manifest"]["prepared"]
    replacement = b"a different synthetic recording"
    src.write_bytes(replacement)
    sha = hashlib.sha256(replacement).hexdigest()
    other = state.upsert_discovered(sha256=sha, source="inbox", original_path=str(src))
    stat = src.stat()
    state.remember_seen(str(src), stat.st_size, stat.st_mtime, sha)
    assert deletion.delete_recordings(cfg, [rec.id])["ok"]
    assert src.read_bytes() == replacement and state.get(other.id) is not None
    assert not state.is_deleted_sha(sha)


@pytest.mark.parametrize("failure", ["index", "completion"])
def test_cleanup_success_followed_by_state_failure_keeps_manifest_for_retry(tmp_path, monkeypatch, failure):
    from voiceinbox import pipeline

    cfg, state, rec, src = setup_record(tmp_path)
    def fail(*args, **kwargs):
        raise OSError("模拟清理后写入失败")
    with monkeypatch.context() as patch:
        patch.setattr(pipeline if failure == "index" else State, "write_index" if failure == "index" else "complete_deletion", fail)
        result = deletion.delete_recordings(cfg, [rec.id])
    assert not result["ok"] and not src.exists()
    assert state.get(rec.id) is not None and state.deletion_for_id(rec.id)["manifest"]["prepared"]
    assert deletion.delete_recordings(cfg, [rec.id])["ok"]
    assert state.get(rec.id) is None


@pytest.mark.parametrize("unsafe", ["outside", "file_symlink", "directory_symlink", "apple"])
def test_unsafe_managed_path_is_not_success_and_does_not_touch_original(tmp_path, unsafe):
    cfg, state, rec, src = setup_record(tmp_path)
    target = write((cfg.voice_memos_dir if unsafe == "apple" else tmp_path) / "keep.txt", "preserve")
    if unsafe == "file_symlink":
        raw = cfg.transcripts_dir / "link.txt"
        raw.symlink_to(target)
    elif unsafe == "directory_symlink":
        link = cfg.transcripts_dir / "linked"
        link.symlink_to(target.parent, target_is_directory=True)
        raw = link / target.name
    else:
        raw = target
    state.advance(rec.id, rec.status, text_path=str(raw))
    result = deletion.delete_recordings(cfg, [rec.id])
    assert not result["ok"] and result["failed"][0]["id"] == rec.id
    assert target.read_text() == "preserve" and src.exists()
    assert state.deletion_for_id(rec.id)["state"] == "delete_failed"


def _info(rec):
    return {"id": rec.id, "recorded_at": rec.recorded_at, "title": rec.apple_title, "duration_s": 180, "audio_path": rec.original_path}


def test_recording_note_preserves_user_body_and_frontmatter_without_new_backup(tmp_path):
    cfg, state, rec, _ = setup_record(tmp_path)
    note = obsidian.write_recording_note(cfg, _info(rec), {"title": "测试", "type": "会议"}, "生成总结", "生成转写")
    note.write_text(note.read_text().replace('source: "voiceinbox"', 'source: "voiceinbox"\ncustom:\n  nested: 用户值') + "\n我的手写正文\n")
    write(enrich.analysis_path(cfg, rec.id), json.dumps({"obsidian_path": str(note)}))
    assert deletion.delete_recordings(cfg, [rec.id])["ok"]
    text = note.read_text()
    assert "我的手写正文" in text and "custom:\n  nested: 用户值" in text
    assert "生成总结" not in text and "生成转写" not in text and 'id: "' not in text
    assert not cfg.backup_dir.exists()


def test_pure_generated_recording_note_is_deleted_after_user_renames_it(tmp_path):
    cfg, state, rec, _ = setup_record(tmp_path)
    note = obsidian.write_recording_note(cfg, _info(rec), {"title": "测试"}, "生成总结", "生成转写")
    renamed = note.with_name("用户改过文件名.md")
    note.rename(renamed)
    assert deletion.delete_recordings(cfg, [rec.id])["ok"]
    assert not renamed.exists()


def test_historical_backups_inside_obsidian_root_are_not_scanned_or_removed(tmp_path):
    from dataclasses import replace

    cfg, state, rec, _ = setup_record(tmp_path)
    cfg = replace(cfg, obsidian_vault=tmp_path, obsidian_folder="root")
    (tmp_path / ".obsidian").mkdir()
    note = obsidian.write_recording_note(cfg, _info(rec), {"title": "测试", "llm": True}, "第一版总结", "生成转写")
    obsidian.write_recording_note(cfg, _info(rec), {"title": "测试", "llm": True}, "第二版总结", "生成转写")
    before = snapshot(cfg.backup_dir)
    assert before[1]
    assert deletion.delete_recordings(cfg, [rec.id])["ok"]
    assert not note.exists() and snapshot(cfg.backup_dir) == before


def test_explicit_analysis_path_cannot_delete_a_historical_backup(tmp_path):
    from dataclasses import replace

    cfg, state, rec, _ = setup_record(tmp_path)
    cfg = replace(cfg, obsidian_vault=tmp_path, obsidian_folder="root")
    backup = write(cfg.backup_dir / "obsidian" / "historical.md", f'---\nid: "{rec.id}"\nsource: "voiceinbox"\n---\n{obsidian.BEGIN}\n旧内容\n{obsidian.END}\n')
    write(enrich.analysis_path(cfg, rec.id), json.dumps({"obsidian_path": str(backup)}))
    before = backup.read_bytes()
    result = deletion.delete_recordings(cfg, [rec.id])
    assert not result["ok"] and backup.read_bytes() == before
    assert state.deletion_pending(rec.id)


def test_last_flash_removes_empty_generated_daily_file(tmp_path):
    cfg, state, rec, _ = setup_record(tmp_path)
    daily = obsidian.write_flash(cfg, _info(rec), {"title": "测试"}, "生成闪念")
    assert deletion.delete_recordings(cfg, [rec.id])["ok"]
    assert not daily.exists()


def test_shared_daily_note_only_removes_target_flash_and_keeps_handwriting(tmp_path):
    cfg, state, first, _ = setup_record(tmp_path, seed="first")
    _, _, second, _ = setup_record(tmp_path, seed="second")
    daily = obsidian.write_flash(cfg, _info(first), {"title": "第一条"}, "第一条闪念正文")
    obsidian.write_flash(cfg, _info(second), {"title": "第二条"}, "第二条闪念正文")
    daily.write_text(daily.read_text().replace('source: "voiceinbox"', 'source: "voiceinbox"\nmy_custom: 保留') + "\n手写日记\n")
    # 已有的历史备份允许保留，本次删除不应新增。
    before_backups = snapshot(cfg.backup_dir)
    assert deletion.delete_recordings(cfg, [first.id])["ok"]
    text = daily.read_text()
    assert "第一条闪念正文" not in text and "第二条闪念正文" in text and "手写日记" in text
    assert deletion.delete_recordings(cfg, [second.id])["ok"]
    assert "第二条闪念正文" not in daily.read_text()
    assert "my_custom: 保留" in daily.read_text() and "手写日记" in daily.read_text()
    assert snapshot(cfg.backup_dir) == before_backups


def test_malformed_note_marker_keeps_task_and_file_for_retry(tmp_path):
    cfg, state, rec, _ = setup_record(tmp_path)
    note = obsidian.write_recording_note(cfg, _info(rec), {"title": "测试"}, "生成总结", "生成转写")
    bad = note.read_text().replace(obsidian.END, "")
    note.write_text(bad)
    result = deletion.delete_recordings(cfg, [rec.id])
    assert not result["ok"] and note.read_text() == bad
    assert state.deletion_pending(rec.id)


@pytest.mark.parametrize("ids", [None, [], "abcdefabcdef", [12], ["../outside"], ["z" * 12]])
def test_invalid_ids_are_rejected_before_mutation(tmp_path, ids):
    cfg = make_cfg(tmp_path)
    with pytest.raises(ValueError):
        deletion.delete_recordings(cfg, ids)
    assert not cfg.db_path.exists()


def test_unknown_id_has_per_item_failure(tmp_path):
    cfg = make_cfg(tmp_path)
    result = deletion.delete_recordings(cfg, ["a" * 12])
    assert result["failed"] == [{"id": "a" * 12, "error": "录音不存在"}]
