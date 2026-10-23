"""删除接口、后台维护与扫描/手动导入之间的集成。只使用临时数据。"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
from pathlib import Path

import pytest

from tests.conftest import dump_config, make_cfg, make_m4a
from tests.test_web import Client, client
from voiceinbox import search, todos
from voiceinbox.lock import FileLock
from voiceinbox.pipeline import process_path, run_once
from voiceinbox.state import State
from voiceinbox.web import WebApp, build_server


def _record(client, *, status="failed", sha="a"):
    cfg = client.cfg
    source = cfg.inbox_dir / f"{sha}.m4a"
    source.write_bytes(f"synthetic audio {sha}".encode())
    state = State(cfg.db_path)
    rec = state.upsert_discovered(sha256=hashlib.sha256(source.read_bytes()).hexdigest(), source="inbox", original_path=str(source), apple_title="待删除录音")
    state.advance(rec.id, status)
    return state.get(rec.id)


@pytest.mark.parametrize("payload", [{}, {"ids": []}, {"ids": "a" * 12}, {"ids": [123]}, {"ids": ["../escape"]}])
def test_delete_rejects_invalid_requests(client: Client, payload):
    status, _headers, body = client.json("POST", "/api/recordings/delete", payload)
    assert status == 400
    assert body["ok"] is False


def test_delete_api_preserves_security_and_lock(client: Client):
    rec = _record(client)
    data = {"ids": [rec.id]}
    for extra in ({"csrf": False}, {"headers": {"Origin": "http://attacker.example"}}):
        status, _headers, _body = client.json("POST", "/api/recordings/delete", data, **extra)
        assert status == 403
    with FileLock(client.cfg.lock_path) as locked:
        assert locked
        status, _headers, body = client.json("POST", "/api/recordings/delete", data)
        assert status == 409 and body["locked"] is True
    assert State(client.cfg.db_path).get(rec.id) is not None


def test_delete_api_repeats_and_refreshes_reads(client: Client):
    rec = _record(client)
    cfg = client.cfg
    search.upsert(cfg.search_db_path, rec_id=rec.id, recorded_at=None, duration_s=None,
                  title="待删除录音", tags="", transcript="特殊搜索内容", summary="")
    for _ in range(2):
        status, _headers, result = client.json("POST", "/api/recordings/delete", {"ids": [rec.id, rec.id]})
        assert status == 200
        assert result["deleted_ids"] == [rec.id]
        assert result["failed"] == []
    assert client.json("GET", f"/api/recordings/{rec.id}")[0] == 404
    assert client.request("GET", f"/api/recordings/{rec.id}/audio")[0] == 404
    listing = client.json("GET", "/api/recordings")[2]
    assert listing["items"] == [] and listing["counts"]["all"] == 0
    assert search.search(cfg.search_db_path, "特殊搜索内容") == []


def test_failed_deletion_hides_content_and_prevents_other_operations(client: Client, monkeypatch):
    from voiceinbox import deletion

    rec = _record(client, status="transcribed")
    cfg = client.cfg
    raw = cfg.transcripts_dir / f"{rec.id}.txt"
    raw.write_text("不应再展示的原文", encoding="utf-8")
    State(cfg.db_path).advance(rec.id, "transcribed", text_path=str(raw))
    actual = Path.unlink

    def fail_unlink(path, *args, **kwargs):
        if path == raw:
            raise PermissionError("synthetic permission error")
        return actual(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", fail_unlink)
        result = deletion.delete_recordings(cfg, [rec.id])
    assert result["failed"]
    detail = client.json("GET", f"/api/recordings/{rec.id}")[2]["item"]
    assert detail["deletion_pending"] is True
    assert detail["group"] == "failed" and detail["status_label"] == "删除未完成"
    assert not detail["has_audio"] and not detail["has_text"] and not detail.get("analysis")
    assert client.request("GET", f"/api/recordings/{rec.id}/audio")[0] == 404
    assert not client.json("GET", "/api/recordings?q=不应再展示")[2]["items"]
    for action in ("retry", "retranscribe", "summarize"):
        assert client.json("POST", f"/api/recordings/{rec.id}/{action}", {})[0] in (400, 409)
    assert client.json("POST", "/api/recordings/delete", {"ids": [rec.id]})[2]["failed"] == []


def test_todo_cannot_be_written_while_deletion_has_lock(client: Client):
    rec = _record(client, status="transcribed")
    cfg = client.cfg
    derived = cfg.transcripts_dir / "derived"
    derived.mkdir(exist_ok=True)
    (derived / f"{rec.id}.analysis.json").write_text(json.dumps({"flash": {"todos": ["测试待办"]}}), encoding="utf-8")
    with FileLock(cfg.lock_path) as got:
        assert got
        status, _headers, body = client.json("POST", "/api/todos", {"id": rec.id, "key": todos.todo_key("测试待办"), "done": True})
    assert status == 409 and body["locked"] is True
    assert not todos.load_done(cfg)


def test_deleted_audio_is_skipped_by_scan_but_manual_import_can_return(tmp_path: Path):
    from voiceinbox import deletion

    cfg = make_cfg(tmp_path, min_duration_s=0)
    audio = make_m4a(cfg.voice_memos_dir / "20261023 100000.m4a", 1)
    original = audio.read_bytes()
    calls = []

    def fake(wav, out_txt, timeout_s):
        calls.append(wav)
        out_txt.write_text("测试正文", encoding="utf-8")
        return "测试正文"

    run_once(cfg, transcribe_fn=fake)
    state = State(cfg.db_path)
    rec = state.recent(1)[0]
    assert deletion.delete_recordings(cfg, [rec.id])["deleted_ids"] == [rec.id]
    assert run_once(cfg, transcribe_fn=fake).transcribed == 0
    renamed = audio.with_name("20261023 100001.m4a")
    audio.rename(renamed)
    assert run_once(cfg, transcribe_fn=fake).transcribed == 0
    assert renamed.read_bytes() == original and len(calls) == 1
    stats, fresh = process_path(cfg, renamed, transcribe_fn=fake)
    assert stats.transcribed == 1 and fresh.status == "transcribed" and len(calls) == 2
    assert not state.is_deleted_sha(rec.sha256)


def test_delete_response_does_not_wait_for_digest(client: Client, monkeypatch):
    from voiceinbox import digest_queue

    rec = _record(client)
    started = threading.Event()
    release = threading.Event()

    def fake_process(cfg, state):
        started.set()
        assert release.wait(5), "测试必须释放假模型"

    monkeypatch.setattr(digest_queue, "pending_days", lambda cfg: ["2026-10-23"])
    monkeypatch.setattr(digest_queue, "process_pending_locked", fake_process)
    try:
        status, _, result = client.json("POST", "/api/recordings/delete", {"ids": [rec.id]})
        assert status == 200 and result["deleted_ids"] == [rec.id]
        assert started.wait(2)
    finally:
        release.set()
        client.app.close()


def test_maintenance_waits_for_shared_lock(client: Client, monkeypatch):
    from voiceinbox import digest_queue

    processed = threading.Event()
    monkeypatch.setattr(digest_queue, "pending_days", lambda cfg: ["2026-10-23"])
    monkeypatch.setattr(digest_queue, "process_pending_locked", lambda cfg, state: processed.set())
    with FileLock(client.cfg.lock_path) as acquired:
        assert acquired
        client.app.kick_maintenance()
        assert not processed.wait(0.1)
    assert processed.wait(2)
    client.app.close()


def test_startup_index_sync_respects_shared_lock(client: Client, monkeypatch):
    from voiceinbox import enrich

    calls = []
    monkeypatch.setattr(enrich, "sync_index", lambda cfg, state: calls.append(True))
    with FileLock(client.cfg.lock_path) as acquired:
        assert acquired
        app = WebApp(client.app.config_path)
        app.close()
    assert calls == []
    app = WebApp(client.app.config_path)
    app.close()
    assert calls == [True]


def test_occupied_port_preserves_bind_error(client: Client):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with pytest.raises(OSError):
            build_server(client.app, port=occupied.getsockname()[1])


def test_server_start_resumes_interrupted_deletion(tmp_path, monkeypatch):
    from voiceinbox import deletion

    cfg = make_cfg(tmp_path)
    raw = cfg.inbox_dir / "resume.m4a"
    raw.write_bytes(b"resume audio")
    state = State(cfg.db_path)
    rec = state.upsert_discovered(sha256=hashlib.sha256(raw.read_bytes()).hexdigest(), source="inbox", original_path=str(raw))
    actual = Path.unlink

    def fail(path, *args, **kwargs):
        if path == raw:
            raise PermissionError("isolated interrupted cleanup")
        return actual(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", fail)
        assert deletion.delete_recordings(cfg, [rec.id])["failed"]
    completed = threading.Event()
    resume = deletion.resume_deletions_locked

    def observed_resume(cfg, state):
        try:
            return resume(cfg, state)
        finally:
            completed.set()

    monkeypatch.setattr(deletion, "resume_deletions_locked", observed_resume)
    config = tmp_path / "config.toml"
    config.write_text(dump_config(cfg), encoding="utf-8")
    app = WebApp(config)
    server = build_server(app)
    try:
        assert completed.wait(2)
        assert State(cfg.db_path).get(rec.id) is None
        assert not raw.exists()
        assert State(cfg.db_path).is_deleted_sha(rec.sha256)
    finally:
        server.server_close()


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_today_does_not_expose_invalidated_digest(client: Client, monkeypatch, status):
    from voiceinbox import digest_queue, enrich

    path = client.cfg.root_dir / "old-digest.md"
    path.write_text("已删除内容不应继续展示", encoding="utf-8")
    with enrich._db(client.cfg) as conn:
        conn.execute("INSERT INTO digests (day, signature, path, updated_at) VALUES (?, ?, ?, ?)",
                     ("2026-10-23", "old", str(path), "2026-10-23T12:00:00+08:00"))
    monkeypatch.setattr(digest_queue, "digest_status", lambda *args: {"state": status, "error": "isolated error" if status == "failed" else ""})
    digest = client.json("GET", "/api/today?date=2026-10-23")[2]["digest"]
    assert digest["state"] == status and digest["md"] == "" and digest["uri"] == ""
    assert digest["error"] == ("isolated error" if status == "failed" else "")


def test_historical_digest_jobs_remain_accessible_in_date_menu(client: Client, monkeypatch):
    from voiceinbox import digest_queue

    monkeypatch.setattr(digest_queue, "pending_days", lambda cfg: ["2020-01-02"])
    days = client.json("GET", "/api/today")[2]["days"]
    assert {"date": "2020-01-02", "count": 0} in days
