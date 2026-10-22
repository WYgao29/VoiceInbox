"""哈希、时间、状态库、发现、Apple 元数据。"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from voiceinbox.applemeta import lookup
from voiceinbox.config import load_config
from voiceinbox.discover import discover
from voiceinbox.files import (
    file_sha256,
    flags_are_dataless,
    is_stable,
    recording_id,
    stage_copy,
)
from voiceinbox.state import State
from voiceinbox.timeutil import datetime_from_filename, interpret_apple_time
from tests.conftest import dump_config, make_cfg

ENV_KEYS = [
    "VOICEINBOX_ROOT",
    "VOICEINBOX_INBOX",
    "VOICEINBOX_VOICE_MEMOS",
    "VOICEINBOX_ASR_SCRIPT",
    "VOICEINBOX_ASR_LANG",
    "VOICEINBOX_SINCE",
]


def test_sha256_and_id(tmp_path: Path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"hello")
    digest = file_sha256(path)
    assert digest == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert recording_id(digest) == "2cf24dba5fb0"


def test_filename_and_cocoa_time():
    tz = "Asia/Shanghai"
    parsed = datetime_from_filename("20261007 093012-ABCD.m4a", tz)
    assert parsed == datetime(2026, 10, 7, 9, 30, 12, tzinfo=ZoneInfo(tz))
    assert datetime_from_filename("20261007_093012.m4a", tz) == parsed
    assert datetime_from_filename("nope.m4a", tz) is None
    assert datetime_from_filename("20261301 093012.m4a", tz) is None

    target = datetime(2026, 10, 7, 1, 30, 12, tzinfo=timezone.utc)
    got = interpret_apple_time(target.timestamp() - 978307200)
    assert got is not None
    assert abs(got.timestamp() - target.timestamp()) < 1
    assert interpret_apple_time("不是数字") is None
    assert flags_are_dataless(0x40000000)
    assert not flags_are_dataless(0)


def test_is_stable_age_and_empty(tmp_path: Path):
    path = tmp_path / "a.m4a"
    path.write_bytes(b"abc")
    assert is_stable(path, min_age_s=0, recheck_s=0)
    assert not is_stable(path, min_age_s=10_000, recheck_s=0)
    empty = tmp_path / "empty.m4a"
    empty.write_bytes(b"")
    assert not is_stable(empty, min_age_s=0, recheck_s=0)


def test_state_backoff(tmp_path: Path):
    state = State(tmp_path / "state.sqlite")
    now = datetime(2026, 10, 7, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    rec = state.upsert_discovered(sha256="a" * 64, source="inbox", original_path="/tmp/a.m4a")
    assert [item.id for item in state.due_for_work(now)] == [rec.id]

    assert state.mark_failed(rec.id, "e1", max_attempts=3, retry_base_s=300, now=now) == "failed"
    failed = state.get(rec.id)
    assert failed is not None
    assert failed.attempts == 1
    assert failed.next_retry_at == "2026-10-07T12:05:00+08:00"
    assert state.due_for_work(now) == []
    assert state.due_for_work(now + timedelta(minutes=5))[0].id == rec.id

    second = now + timedelta(minutes=5)
    assert state.mark_failed(rec.id, "e2", max_attempts=3, retry_base_s=300, now=second) == "failed"
    assert state.get(rec.id).next_retry_at == "2026-10-07T12:15:00+08:00"
    assert state.mark_failed(rec.id, "e3", max_attempts=3, retry_base_s=300, now=second) == "dead"
    dead = state.get(rec.id)
    assert dead.status == "dead"
    assert dead.attempts == 3
    assert dead.next_retry_at is None
    assert state.due_for_work(second + timedelta(days=1)) == []

    state.reset_for_retry(rec.id)
    reset = state.get(rec.id)
    assert reset.status == "stable"
    assert reset.attempts == 0
    assert state.due_for_work(now)[0].id == rec.id


def test_begin_attempt_counts_a_kill_and_then_dies(tmp_path: Path):
    state = State(tmp_path / "state.sqlite")
    now = datetime(2026, 10, 7, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    rec = state.upsert_discovered(sha256="b" * 64, source="inbox", original_path="/tmp/b.m4a")
    for expected in (1, 2, 3):
        assert state.begin_attempt(rec.id, max_attempts=3) == "processing"
        current = state.get(rec.id)
        assert current is not None
        assert current.attempts == expected
        assert current.status == "processing"
        assert state.due_for_work(now)[0].id == rec.id
    assert state.begin_attempt(rec.id, max_attempts=3) == "dead"
    dead = state.get(rec.id)
    assert dead is not None
    assert dead.status == "dead"
    assert dead.attempts == 3
    assert state.due_for_work(now) == []


def test_mark_failed_does_not_double_count_open_attempt(tmp_path: Path):
    state = State(tmp_path / "state.sqlite")
    now = datetime(2026, 10, 7, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    rec = state.upsert_discovered(sha256="c" * 64, source="inbox", original_path="/tmp/c.m4a")
    assert state.begin_attempt(rec.id, max_attempts=3) == "processing"
    assert state.mark_failed(rec.id, "e", max_attempts=3, retry_base_s=300, now=now) == "failed"
    failed = state.get(rec.id)
    assert failed is not None
    assert failed.attempts == 1
    assert failed.next_retry_at == "2026-10-07T12:05:00+08:00"


def test_refuse_write_into_voice_memos(tmp_path: Path):
    vm = tmp_path / "vm"
    vm.mkdir()
    src = vm / "a.m4a"
    src.write_bytes(b"abc")
    try:
        stage_copy(src, vm, "0123456789ab", forbidden=vm)
    except RuntimeError as exc:
        assert "拒绝写入" in str(exc)
    else:
        raise AssertionError("应该拒绝写入语音备忘录目录")


def test_discover_dedup_since_and_cap(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    inbox_a = cfg.inbox_dir / "a.m4a"
    inbox_b = cfg.inbox_dir / "b.m4a"
    inbox_a.write_bytes(b"same-bytes")
    inbox_b.write_bytes(b"same-bytes")
    old_voice = cfg.voice_memos_dir / "20200101 120000.m4a"
    new_voice = cfg.voice_memos_dir / "20261007 120000.m4a"
    old_inbox = cfg.inbox_dir / "20200101 120000.m4a"
    old_voice.write_bytes(b"old-voice")
    new_voice.write_bytes(b"new-voice")
    old_inbox.write_bytes(b"old-inbox")

    state = State(cfg.db_path)
    first = discover(cfg, state)
    assert len(first.new_ids) == 3  # 一份重复内容 + 新语音备忘录 + 旧 inbox
    assert state.get_by_sha(file_sha256(old_voice)) is None
    assert first.skipped_since == 1
    assert state.get_by_sha(file_sha256(new_voice)).status == "discovered"
    assert state.get_by_sha(file_sha256(old_inbox)).status == "discovered"
    assert state.get_by_sha(file_sha256(inbox_a)).status == "discovered"

    second = discover(cfg, state)
    assert second.new_ids == []
    assert second.skipped_since == 1
    assert second.skipped_known >= 4
    assert state.get_by_sha(file_sha256(old_voice)) is None

    reopened = discover(replace(cfg, since=date(2019, 1, 1)), state)
    assert state.get_by_sha(file_sha256(old_voice)).status == "discovered"
    assert reopened.new_ids


def test_discover_max_per_run_and_undated_skip(tmp_path: Path):
    cfg = make_cfg(tmp_path, max_per_run=1)
    for index in range(3):
        (cfg.inbox_dir / f"f{index}.m4a").write_bytes(f"file-{index}".encode())
    state = State(cfg.db_path)
    assert len(discover(cfg, state).new_ids) == 1
    assert len(discover(cfg, state).new_ids) == 1
    assert len(discover(cfg, state).new_ids) == 1
    assert discover(cfg, state).new_ids == []

    skipped = make_cfg(tmp_path / "skip", undated_policy="skip")
    (skipped.voice_memos_dir / "random.m4a").write_bytes(b"xyz")
    state = State(skipped.db_path)
    discover(skipped, state)
    rec = state.recent(1)[0]
    assert rec.status == "skipped"
    assert "没有数据库日期" in (rec.last_error or "")


def test_old_since_skip_row_is_dropped_and_can_return(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    old = cfg.voice_memos_dir / "20200101 120000.m4a"
    old.write_bytes(b"old-voice")
    state = State(cfg.db_path)
    digest = file_sha256(old)
    rec = state.upsert_discovered(
        sha256=digest,
        source="voice_memos",
        original_path=str(old.resolve()),
        recorded_at="2020-01-01T12:00:00+08:00",
    )
    state.advance(rec.id, "skipped", last_error="早于 since 2026-10-01")
    state.remember_seen(str(old.resolve()), old.stat().st_size, old.stat().st_mtime, digest)

    first = discover(cfg, state)
    assert state.get_by_sha(digest) is None
    assert first.new_ids == []
    assert first.skipped_since == 1

    second = discover(replace(cfg, since=date(2019, 1, 1)), state)
    found = state.get_by_sha(digest)
    assert found is not None
    assert found.status == "discovered"
    assert found.id in second.new_ids


def test_discover_permission_error_still_scans_inbox(tmp_path: Path):
    cfg = make_cfg(tmp_path)
    (cfg.voice_memos_dir / "secret.m4a").write_bytes(b"secret")
    (cfg.inbox_dir / "ok.m4a").write_bytes(b"inbox-ok")
    os.chmod(cfg.voice_memos_dir, 0)
    try:
        result = discover(cfg, State(cfg.db_path))
        assert result.fda_blocked
        assert len(result.new_ids) == 1
        assert "Operation not permitted" in " ".join(result.warnings)
    finally:
        os.chmod(cfg.voice_memos_dir, 0o700)


def test_applemeta_lookup(tmp_path: Path):
    root = tmp_path / "vm"
    db_path = root / "Recordings" / "CloudRecordings.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE ZCLOUDRECORDING (
          Z_PK INTEGER PRIMARY KEY,
          ZPATH TEXT,
          ZCUSTOMLABEL TEXT,
          ZENCRYPTEDTITLE BLOB,
          ZDATE REAL,
          ZDURATION REAL
        )
        """
    )
    target = datetime(2026, 10, 7, 1, 30, 12, tzinfo=timezone.utc)
    cocoa = target.timestamp() - 978307200
    conn.execute(
        "INSERT INTO ZCLOUDRECORDING (ZPATH, ZCUSTOMLABEL, ZDATE, ZDURATION) VALUES (?, ?, ?, ?)",
        ("Recordings/20261007 093012.m4a", "买菜清单", cocoa, 12.5),
    )
    conn.execute(
        "INSERT INTO ZCLOUDRECORDING (ZPATH, ZCUSTOMLABEL, ZENCRYPTEDTITLE) VALUES (?, ?, ?)",
        ("b.m4a", None, "只有字节标题".encode()),
    )
    conn.commit()
    conn.close()

    titled = lookup(root, Path("/virtual/20261007 093012.m4a"))
    assert titled.title == "买菜清单"
    assert titled.duration_s == 12.5
    assert titled.recorded_at is not None
    assert abs(titled.recorded_at.timestamp() - target.timestamp()) < 1

    blob = lookup(root, Path("/virtual/b.m4a"))
    assert blob.title == "只有字节标题"

    assert lookup(tmp_path / "empty", Path("a.m4a")).title is None
    junk = tmp_path / "bad"
    junk.mkdir()
    (junk / "CloudRecordings.db").write_bytes(b"this is not sqlite")
    assert lookup(junk, Path("a.m4a")).title is None


def test_load_config_roundtrip(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path, asr_language="", min_duration_s=1.5)
    path = tmp_path / "config.toml"
    path.write_text(dump_config(cfg), encoding="utf-8")
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    loaded = load_config(path)
    assert loaded.voice_memos_dir == cfg.voice_memos_dir
    assert loaded.root_dir == cfg.root_dir
    assert loaded.since == date(2026, 10, 1)
    assert loaded.asr_language == ""
    assert loaded.min_duration_s == 1.5
    assert loaded.undated_policy == "mtime"

    monkeypatch.setenv("VOICEINBOX_SINCE", "2024-05-01")
    overridden = load_config(path)
    assert overridden.since == date(2024, 5, 1)


def _watch_hash_and_stable(monkeypatch):
    calls = {"hash": 0, "stable": 0}
    real_hash = file_sha256
    real_stable = is_stable

    def hashing(path: Path) -> str:
        calls["hash"] += 1
        return real_hash(path)

    def stable(path: Path, **kwargs) -> bool:
        calls["stable"] += 1
        return real_stable(path, **kwargs)

    monkeypatch.setattr("voiceinbox.discover.file_sha256", hashing)
    monkeypatch.setattr("voiceinbox.discover.is_stable", stable)
    return calls


def test_mtime_before_since_is_remembered_until_since_changes(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    audio = cfg.voice_memos_dir / "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE.m4a"
    audio.write_bytes(b"old-by-mtime")
    old = datetime(2020, 1, 15, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    os.utime(audio, (old, old))
    state = State(cfg.db_path)
    calls = _watch_hash_and_stable(monkeypatch)

    first = discover(cfg, state)
    assert first.new_ids == []
    assert first.skipped_since == 1
    assert state.get_by_sha(file_sha256(audio)) is None
    assert calls == {"hash": 1, "stable": 1}
    assert state.matches_since_skip(str(audio.resolve()), audio.stat().st_size, audio.stat().st_mtime, "2026-10-01")

    second = discover(cfg, state)
    assert second.new_ids == []
    assert second.skipped_since == 1
    assert calls == {"hash": 1, "stable": 1}

    still_old = discover(replace(cfg, since=date(2024, 1, 1)), state)
    assert still_old.new_ids == []
    assert still_old.skipped_since == 1
    assert state.get_by_sha(file_sha256(audio)) is None
    assert calls == {"hash": 2, "stable": 2}
    assert state.matches_since_skip(str(audio.resolve()), audio.stat().st_size, audio.stat().st_mtime, "2024-01-01")
    assert not state.matches_since_skip(str(audio.resolve()), audio.stat().st_size, audio.stat().st_mtime, "2026-10-01")

    again = discover(replace(cfg, since=date(2024, 1, 1)), state)
    assert again.skipped_since == 1
    assert calls == {"hash": 2, "stable": 2}

    opened = discover(replace(cfg, since=date(2019, 1, 1)), state)
    found = state.get_by_sha(file_sha256(audio))
    assert found is not None
    assert found.status == "discovered"
    assert found.id in opened.new_ids
    assert calls == {"hash": 3, "stable": 3}

    repeat = discover(replace(cfg, since=date(2019, 1, 1)), state)
    assert repeat.new_ids == []
    assert repeat.skipped_known >= 1
    assert calls == {"hash": 3, "stable": 3}


def test_db_date_before_since_is_remembered_without_a_recording(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    audio = cfg.voice_memos_dir / "no-date-in-name.m4a"
    audio.write_bytes(b"dated-in-db")
    db_path = cfg.voice_memos_dir / "CloudRecordings.db"
    when = datetime(2020, 6, 1, 0, 0, tzinfo=timezone.utc)
    cocoa = when.timestamp() - 978307200
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE ZCLOUDRECORDING (
          Z_PK INTEGER PRIMARY KEY,
          ZPATH TEXT,
          ZDATE REAL
        )
        """
    )
    conn.execute(
        "INSERT INTO ZCLOUDRECORDING (ZPATH, ZDATE) VALUES (?, ?)",
        (audio.name, cocoa),
    )
    conn.commit()
    conn.close()

    state = State(cfg.db_path)
    calls = _watch_hash_and_stable(monkeypatch)
    first = discover(cfg, state)
    assert first.new_ids == []
    assert first.skipped_since == 1
    assert state.counts() == {}
    assert calls == {"hash": 1, "stable": 1}

    second = discover(cfg, state)
    assert second.skipped_since == 1
    assert calls == {"hash": 1, "stable": 1}

    still = discover(replace(cfg, since=date(2021, 1, 1)), state)
    assert still.new_ids == []
    assert state.counts() == {}
    assert calls == {"hash": 2, "stable": 2}

    opened = discover(replace(cfg, since=date(2020, 1, 1)), state)
    found = state.get_by_sha(file_sha256(audio))
    assert found is not None
    assert found.status == "discovered"
    assert found.id in opened.new_ids
    assert calls == {"hash": 3, "stable": 3}


def test_seen_files_since_skip_migrates_and_clears(tmp_path: Path):
    db = tmp_path / "state.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE seen_files (
          path TEXT PRIMARY KEY,
          size INTEGER NOT NULL,
          mtime REAL NOT NULL,
          sha256 TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    state = State(db)
    state.remember_seen("/tmp/a.m4a", 3, 1.0, "ab" * 32, since_skip="2026-10-01")
    assert state.matches_since_skip("/tmp/a.m4a", 3, 1.0, "2026-10-01")
    assert not state.matches_since_skip("/tmp/a.m4a", 3, 1.0, "2020-01-01")
    assert not state.matches_since_skip("/tmp/a.m4a", 4, 1.0, "2026-10-01")
    state.remember_seen("/tmp/a.m4a", 3, 1.0, "ab" * 32)
    assert not state.matches_since_skip("/tmp/a.m4a", 3, 1.0, "2026-10-01")
