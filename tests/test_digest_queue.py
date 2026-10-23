"""删除录音后的持久摘要重建队列；仅使用临时数据和假模型。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from voiceinbox import digest_queue, enrich, obsidian
from voiceinbox.llm import LlmError
from voiceinbox.state import State
from tests.conftest import make_cfg


class FakeClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = "isolated-digest-model"
        self.prompts = []
        self.failure = False
        self.released = False

    def prepare(self):
        pass

    def release(self):
        self.released = True

    def chat_json(self, _system, user, **_kwargs):
        self.prompts.append(user)
        # 模型等待期间不能占用 SQLite 写事务。
        with sqlite3.connect(self.cfg.db_path, timeout=0.05) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS unrelated_write (value TEXT)")
            conn.execute("INSERT INTO unrelated_write VALUES ('model-call')")
        if self.failure:
            raise LlmError("isolated model failure")
        return {"overview": "重新生成的摘要", "highlights": [], "todos": [], "people": [], "projects": []}


@pytest.fixture
def setup(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    cfg = make_cfg(tmp_path, llm_enabled=True, obsidian_enabled=True, obsidian_vault=vault, digest_enabled=False)
    cfg.transcripts_dir.mkdir(parents=True)
    state = State(cfg.db_path)
    client = FakeClient(cfg)
    box = enrich.ClientBox(cfg, factory=lambda _cfg: client)
    return cfg, state, client, box


def add(cfg, state, title, day):
    sha = hashlib.sha256(title.encode()).hexdigest()
    rec = state.upsert_discovered(sha256=sha, source="inbox", original_path=str(cfg.inbox_dir / f"{title}.m4a"), recorded_at=f"{day}T09:00:00+08:00", duration_s=300)
    enrich.raw_path(cfg, rec.id).write_text(title + "内容", encoding="utf-8")
    enrich.corrected_path(cfg, rec.id).write_text(title + "内容", encoding="utf-8")
    enrich.analysis_path(cfg, rec.id).write_text(json.dumps({"id": rec.id, "title": title, "type": "会议", "route": "full", "llm": True, "raw_sha": sha, "summary": {"overview": title + "概要"}}, ensure_ascii=False), encoding="utf-8")
    state.advance(rec.id, "transcribed")
    return state.get(rec.id)


def pending_rows(cfg):
    with sqlite3.connect(cfg.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM pending_digests ORDER BY day")]


def test_historical_days_are_persistent_deduplicated_and_rebuilt_from_remaining_recordings(setup):
    cfg, state, client, box = setup
    days = ["2025-01-02", "2025-02-03"]
    for day in days:
        keep = add(cfg, state, f"保留-{day}", day)
        deleted = add(cfg, state, f"删除-{day}", day)
        enrich.run_digest(cfg, state, box, day, force=True)
        state.advance(deleted.id, "deleting")
        digest_queue.invalidate_day_locked(cfg, state, day)
        digest_queue.invalidate_day_locked(cfg, state, day)
        assert digest_queue.digest_status(cfg, state, day)["state"] == "pending"
        assert keep.id != deleted.id
    assert len(pending_rows(cfg)) == 2
    client.prompts.clear()
    assert digest_queue.process_pending_locked(cfg, State(cfg.db_path), box=box) == 2
    assert len(client.prompts) == 2
    assert all("删除-" not in prompt and "保留-" in prompt for prompt in client.prompts)
    assert pending_rows(cfg) == []
    assert all(digest_queue.digest_status(cfg, state, day)["state"] == "ready" for day in days)


def test_last_recording_removes_generated_digest_preserves_handwritten_content_without_backup(setup):
    cfg, state, client, box = setup
    day = "2025-01-02"
    rec = add(cfg, state, "最后一条", day)
    path = enrich.run_digest(cfg, state, box, day, force=True)
    path.write_text(path.read_text(encoding="utf-8") + "\n我手写的内容\n", encoding="utf-8")
    backups = set(cfg.backup_dir.rglob("*")) if cfg.backup_dir.exists() else set()
    state.advance(rec.id, "deleting")
    digest_queue.invalidate_day_locked(cfg, state, day)
    assert "我手写的内容" in path.read_text(encoding="utf-8")
    assert "重新生成的摘要" not in path.read_text(encoding="utf-8")
    assert pending_rows(cfg) == []
    assert digest_queue.digest_status(cfg, state, day) == {"state": "none", "error": ""}
    assert (set(cfg.backup_dir.rglob("*")) if cfg.backup_dir.exists() else set()) == backups
    assert len(client.prompts) == 1


def test_model_failure_persists_error_and_retries_without_old_digest(setup):
    cfg, state, client, box = setup
    day = "2025-01-02"
    add(cfg, state, "保留", day)
    path = enrich.run_digest(cfg, state, box, day, force=True)
    digest_queue.invalidate_day_locked(cfg, state, day)
    client.failure = True
    assert digest_queue.process_pending_locked(cfg, state, box=box) == 0
    assert digest_queue.digest_status(cfg, state, day) == {"state": "failed", "error": "isolated model failure"}
    assert "重新生成的摘要" not in path.read_text(encoding="utf-8") if path.exists() else True
    client.failure = False
    assert digest_queue.process_pending_locked(cfg, state, box=box) == 1
    assert digest_queue.digest_status(cfg, state, day)["state"] == "ready"


@pytest.mark.parametrize("feature", ["llm_enabled", "obsidian_enabled"])
def test_disabled_feature_keeps_queue_until_enabled_independent_of_digest_schedule(setup, feature):
    cfg, state, _client, box = setup
    day = "2020-01-02"
    add(cfg, state, "仍需重建", day)
    digest_queue.invalidate_day_locked(cfg, state, day)
    disabled = replace(cfg, **{feature: False})
    assert digest_queue.process_pending_locked(disabled, state) == 0
    assert digest_queue.digest_status(cfg, state, day)["state"] == "failed"
    assert len(pending_rows(cfg)) == 1
    assert digest_queue.process_pending_locked(cfg, state, box=box) == 1
    assert pending_rows(cfg) == []


def test_cleanup_failure_is_durable_and_raises_before_deletion_can_complete(setup, monkeypatch):
    cfg, state, _client, box = setup
    day = "2025-01-02"
    add(cfg, state, "仍有录音", day)
    enrich.run_digest(cfg, state, box, day, force=True)
    remove = obsidian.remove_digest_content
    monkeypatch.setattr(obsidian, "remove_digest_content", lambda *_args: (_ for _ in ()).throw(OSError("isolated cleanup failure")))
    with pytest.raises(OSError, match="isolated cleanup failure"):
        digest_queue.invalidate_day_locked(cfg, state, day)
    assert digest_queue.digest_status(cfg, state, day) == {"state": "failed", "error": "isolated cleanup failure"}
    with enrich._db(cfg) as conn:
        assert conn.execute("SELECT * FROM digests WHERE day = ?", (day,)).fetchone() is None
    monkeypatch.setattr(obsidian, "remove_digest_content", remove)
    assert digest_queue.process_pending_locked(cfg, state, box=box) == 1


def test_queue_only_completes_after_file_and_database_save(setup, monkeypatch):
    cfg, state, _client, box = setup
    day = "2025-01-02"
    add(cfg, state, "保留", day)
    digest_queue.invalidate_day_locked(cfg, state, day)
    with sqlite3.connect(cfg.db_path) as conn:
        conn.execute("CREATE TRIGGER refuse_digest BEFORE INSERT ON digests BEGIN SELECT RAISE(ABORT, 'isolated db failure'); END")
    assert digest_queue.process_pending_locked(cfg, state, box=box) == 0
    assert digest_queue.digest_status(cfg, state, day)["state"] == "failed"
    assert len(pending_rows(cfg)) == 1
    with sqlite3.connect(cfg.db_path) as conn:
        conn.execute("DROP TRIGGER refuse_digest")
    assert digest_queue.process_pending_locked(cfg, state, box=box) == 1
    assert pending_rows(cfg) == []
    assert obsidian.digest_path(cfg, day).is_file()


def test_unfinished_deletion_hides_ready_digest_and_defers_consumer(setup, monkeypatch):
    cfg, state, client, box = setup
    day = "2025-01-02"
    add(cfg, state, "保留", day)
    enrich.run_digest(cfg, state, box, day, force=True)
    monkeypatch.setattr(state, "pending_deletions", lambda: [{"state": "delete_failed", "error": "local cleanup failed", "manifest": {"day": day}}], raising=False)
    assert digest_queue.digest_status(cfg, state, day) == {"state": "failed", "error": "local cleanup failed"}
    digest_queue.invalidate_day_locked(cfg, state, day)
    assert digest_queue.process_pending_locked(cfg, state, box=box) == 0
    assert len(client.prompts) == 1
    assert len(pending_rows(cfg)) == 1
    monkeypatch.setattr(state, "pending_deletions", lambda: [])
    assert digest_queue.process_pending_locked(cfg, state, box=box) == 1


def test_enrich_consumer_reuses_client_and_does_not_generate_same_day_twice(setup, monkeypatch):
    cfg, state, client, box = setup
    day = "2026-10-23"
    cfg = replace(cfg, digest_enabled=True)
    add(cfg, state, "保留", day)
    digest_queue.invalidate_day_locked(cfg, state, day)
    monkeypatch.setattr(enrich, "now_in", lambda _tz: datetime(2026, 10, 23, 23, tzinfo=ZoneInfo("Asia/Shanghai")))
    stats = enrich.enrich_pending(cfg, state, box=box, ids=[])
    assert stats.digests == 1
    assert len(client.prompts) == 1
    assert not client.released
    assert pending_rows(cfg) == []


def test_manual_digest_success_clears_pending_queue(setup):
    cfg, state, _client, box = setup
    day = "2025-01-02"
    add(cfg, state, "保留", day)
    digest_queue.invalidate_day_locked(cfg, state, day)
    assert enrich.run_digest(cfg, state, box, day, force=True)
    assert pending_rows(cfg) == []


def test_manual_digest_failure_keeps_actionable_error_for_restart(setup, monkeypatch):
    cfg, state, client, box = setup
    day = "2025-01-02"
    add(cfg, state, "保留", day)
    digest_queue.invalidate_day_locked(cfg, state, day)
    client.failure = True
    monkeypatch.setattr(enrich, "ClientBox", lambda _cfg: box)
    with pytest.raises(LlmError, match="isolated model failure"):
        enrich.digest_now(cfg, day)
    assert digest_queue.digest_status(cfg, State(cfg.db_path), day) == {"state": "failed", "error": "isolated model failure"}
    assert len(pending_rows(cfg)) == 1


def test_manual_digest_cannot_bypass_unfinished_deletion(setup, monkeypatch):
    cfg, state, client, box = setup
    day = "2025-01-02"
    add(cfg, state, "保留", day)
    digest_queue.invalidate_day_locked(cfg, state, day)
    monkeypatch.setattr(state, "pending_deletions", lambda: [{"state": "delete_failed", "manifest": {"day": day}}])
    with pytest.raises(LlmError, match="录音删除尚未完成"):
        enrich.run_digest(cfg, state, box, day, force=True)
    assert not client.prompts
    assert len(pending_rows(cfg)) == 1


@pytest.mark.parametrize("day", ["2026-02-30", "2026-1-2", "../escape"])
def test_digest_rejects_invalid_calendar_day_before_model_or_file_writes(setup, day):
    cfg, state, client, box = setup
    with pytest.raises(ValueError):
        enrich.run_digest(cfg, state, box, day, force=True)
    assert not client.prompts


@pytest.mark.parametrize("folder", ["每日", "收藏/历史"])
def test_renamed_or_moved_digest_is_cleaned_by_identity_and_preserves_handwriting(setup, folder):
    cfg, state, client, box = setup
    day = "2025-01-02"
    add(cfg, state, "留下的录音", day)
    deleted = add(cfg, state, "必须消失的录音", day)
    path = enrich.run_digest(cfg, state, box, day, force=True)
    moved = cfg.obsidian_root / folder / "改过名字的回顾.md"
    moved.parent.mkdir(parents=True, exist_ok=True)
    path.rename(moved)
    text = moved.read_text(encoding="utf-8")
    moved.write_text(text.replace("---\n", "---\ncustom: 手写属性\n", 1) + "\n这是我的手写正文\n", encoding="utf-8")
    state.advance(deleted.id, "deleting")
    digest_queue.invalidate_day_locked(cfg, state, day)
    assert "必须消失的录音" not in moved.read_text(encoding="utf-8")
    assert "custom: 手写属性" in moved.read_text(encoding="utf-8")
    assert "这是我的手写正文" in moved.read_text(encoding="utf-8")
    assert str(moved) in json.loads(pending_rows(cfg)[0]["paths_json"])
    client.prompts.clear()
    assert digest_queue.process_pending_locked(cfg, State(cfg.db_path), box=box) == 1
    assert "必须消失的录音" not in client.prompts[0]
    assert "留下的录音" in client.prompts[0]
    assert moved.read_text(encoding="utf-8") == "---\ncustom: 手写属性\n---\n这是我的手写正文\n"


def test_all_discovered_digest_paths_are_persisted_before_cleanup_can_fail(setup, monkeypatch):
    cfg, state, _client, box = setup
    day = "2025-01-02"
    add(cfg, state, "留下", day)
    path = enrich.run_digest(cfg, state, box, day, force=True)
    moved = cfg.obsidian_root / "改名.md"
    path.rename(moved)
    copied = moved.with_name("另存副本.md")
    copied.write_bytes(moved.read_bytes())
    remove = obsidian.remove_digest_content

    def fail(_cfg, _path, _day):
        assert {str(moved), str(copied)} <= set(json.loads(pending_rows(cfg)[0]["paths_json"]))
        raise PermissionError("isolated renamed digest failure")

    monkeypatch.setattr(obsidian, "remove_digest_content", fail)
    with pytest.raises(PermissionError, match="isolated renamed digest failure"):
        digest_queue.invalidate_day_locked(cfg, state, day)
    assert digest_queue.digest_status(cfg, state, day)["state"] == "failed"
    monkeypatch.setattr(obsidian, "remove_digest_content", remove)
    assert digest_queue.process_pending_locked(cfg, State(cfg.db_path), box=box) == 1
    assert not moved.exists() and not copied.exists()


def test_digest_discovery_preserves_backups_logs_and_apple_originals_under_overlapping_root(setup):
    cfg, state, _client, box = setup
    # 合法目录重叠：应用数据与 Obsidian 生成内容放在同一子目录。
    cfg = replace(cfg, obsidian_vault=cfg.root_dir.parent, obsidian_folder=cfg.root_dir.name,
                  voice_memos_dir=cfg.root_dir / "苹果原录音")
    (cfg.obsidian_vault / ".obsidian").mkdir(exist_ok=True)
    day = "2025-01-02"
    add(cfg, state, "第一条", day)
    path = enrich.run_digest(cfg, state, box, day, force=True)
    add(cfg, state, "第二条", day)
    enrich.run_digest(cfg, state, box, day, force=True)  # 正常更新生成真实历史备份。
    backups = list(cfg.backup_dir.rglob("*.md"))
    assert len(backups) == 1
    for directory in (cfg.logs_dir, cfg.voice_memos_dir):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "保留摘要副本.md").write_bytes(path.read_bytes())
    preserved = {p: p.read_bytes() for p in [*backups, *cfg.logs_dir.glob("*.md"), *cfg.voice_memos_dir.glob("*.md")]}
    moved = cfg.obsidian_root / "改名摘要.md"
    path.rename(moved)
    digest_queue.invalidate_day_locked(cfg, state, day)
    assert not moved.exists()
    assert all(path.read_bytes() == content for path, content in preserved.items())
    assert not (set(map(str, preserved)) & set(json.loads(pending_rows(cfg)[0]["paths_json"])))


@pytest.mark.parametrize("unsafe", ["symlink", "outside", "backup"])
def test_known_unsafe_digest_path_keeps_failed_queue_and_original_file(setup, unsafe):
    cfg, state, _client, box = setup
    day = "2025-01-02"
    add(cfg, state, "保留", day)
    path = enrich.run_digest(cfg, state, box, day, force=True)
    original = path.read_bytes()
    target = cfg.root_dir / "外部原件.md" if unsafe != "backup" else cfg.backup_dir / "保留.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    path.rename(target)
    recorded = target
    if unsafe == "symlink":
        path.symlink_to(target)
        recorded = path
    with enrich._db(cfg) as conn:
        conn.execute("UPDATE digests SET path = ? WHERE day = ?", (str(recorded), day))
    with pytest.raises(obsidian.ObsidianError):
        digest_queue.invalidate_day_locked(cfg, state, day)
    assert target.read_bytes() == original
    assert digest_queue.digest_status(cfg, state, day)["state"] == "failed"
    assert str(recorded) in json.loads(pending_rows(cfg)[0]["paths_json"])
