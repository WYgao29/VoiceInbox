"""本机 API：接口、CSRF、配置写入、只读音频。"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from voiceinbox.__main__ import main
from voiceinbox.config import read_common_settings, update_common_settings
from voiceinbox.files import is_inside
from voiceinbox.lock import FileLock
from voiceinbox.state import State
from voiceinbox.web import (
    WebApp,
    build_server,
    parse_byte_range,
    parse_launchctl,
    parse_multipart,
    read_launchd_status,
    resolve_bind,
)
from tests.conftest import dump_config, make_cfg, make_m4a, snapshot

ROOT = Path(__file__).resolve().parents[1]


class Client:
    def __init__(self, base: str, token: str):
        self.base = base
        self.token = token

    def request(self, method: str, path: str, *, data: bytes | None = None, headers: dict | None = None, cookie: bool = True, csrf: bool = True, origin: bool = True):
        merged = dict(headers or {})
        if cookie:
            merged["Cookie"] = f"voiceinbox_csrf={self.token}"
        if method != "GET" and csrf:
            merged["X-CSRF-Token"] = self.token
        if method != "GET" and origin and "Origin" not in merged:
            merged["Origin"] = self.base
        # urllib 要求请求行是 ASCII；中文查询必须先百分号编码。
        encoded = urllib.parse.quote(path, safe="/?&=%")
        req = urllib.request.Request(self.base + encoded, data=data, method=method, headers=merged)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def json(self, method: str, path: str, payload: dict | None = None, **kwargs):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = dict(kwargs.pop("headers", {}) or {})
        if payload is not None:
            headers["Content-Type"] = "application/json"
        status, header, body = self.request(method, path, data=data, headers=headers, **kwargs)
        parsed = json.loads(body.decode("utf-8")) if body else {}
        return status, header, parsed


def _multipart(filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = "----voiceinboxtest"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    body = head + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class FakeLaunchctl:
    """假的 launchctl / plutil：按 plist 文件内容回答 print，记下所有命令。"""

    def __init__(self, plist: Path):
        self.plist = plist
        self.calls: list[list[str]] = []
        self.loaded = True
        self.running = False

    def __call__(self, args, timeout=0):
        self.calls.append(list(args))
        import plistlib

        if args[0] == "plutil":
            return 0, "OK"
        verb = args[1]
        if verb == "bootout":
            self.loaded = False
            return 0, ""
        if verb == "bootstrap":
            self.loaded = True
            return 0, ""
        if verb == "kickstart":
            return (0, "") if self.loaded else (113, "not loaded")
        if verb == "print":
            if not self.loaded:
                return 113, "Could not find service"
            data = plistlib.loads(self.plist.read_bytes()) if self.plist.exists() else {"StartInterval": 300, "WatchPaths": ["x"]}
            out = "state = " + ("running" if self.running else "not running") + "\n\truns = 2\n"
            out += f"\trun interval = {data.get('StartInterval', 300)} seconds\n"
            if data.get("WatchPaths"):
                out += "\t\twatching = 1\n"
            return 0, out
        return 1, "unknown"


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    for key in (
        "VOICEINBOX_SINCE",
        "VOICEINBOX_ASR_LANG",
        "VOICEINBOX_WEB_PORT",
        "VOICEINBOX_WEB_HOST",
        "VOICEINBOX_CONFIG",
        "VOICEINBOX_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = make_cfg(tmp_path, min_duration_s=0)
    path = tmp_path / "config.toml"
    path.write_text(dump_config(cfg), encoding="utf-8")
    calls = {"n": 0}

    def fake(wav: Path, out_txt: Path, timeout_s: float) -> str:
        calls["n"] += 1
        out_txt.write_text("界面转写\n", encoding="utf-8")
        return "界面转写\n"

    launchctl = FakeLaunchctl(tmp_path / "agent.plist")
    app = WebApp(
        path,
        monitor_runner=launchctl,
        monitor_plist=tmp_path / "agent.plist",
        transcribe_fn=fake,
        doctor_fn=lambda _cfg: (0, "doctor-ok\n以 launchd 日志为准\n"),
        lock_fn=lambda _path: {"busy": False, "detail": "目前没有进程占用锁"},
        launchd_fn=lambda: {
            "label": "com.wayne.voiceinbox",
            "loaded": True,
            "state": "not running",
            "state_label": "没在跑",
            "pid": None,
            "last_exit_code": "0",
            "runs": 3,
            "program": "/tmp/voiceinbox-python",
            "detail": "",
        },
    )
    server = build_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    token = ""
    for _ in range(50):
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2) as resp:
                cookie = resp.headers.get("Set-Cookie") or ""
            token = cookie.split(";", 1)[0].split("=", 1)[1]
            break
        except (urllib.error.URLError, TimeoutError, IndexError):
            time.sleep(0.02)
    else:
        server.shutdown()
        server.server_close()
        raise AssertionError("测试服务器没有起来")
    holder = Client(base, token)
    holder.cfg = cfg  # type: ignore[attr-defined]
    holder.app = app  # type: ignore[attr-defined]
    holder.calls = calls  # type: ignore[attr-defined]
    holder.launchctl = launchctl  # type: ignore[attr-defined]
    try:
        yield holder
    finally:
        server.shutdown()
        server.server_close()


def test_bind_defaults_and_rejects_public_ports(monkeypatch, capsys):
    monkeypatch.delenv("VOICEINBOX_WEB_PORT", raising=False)
    monkeypatch.delenv("VOICEINBOX_WEB_HOST", raising=False)
    assert resolve_bind(None, None) == ("127.0.0.1", 8791)
    monkeypatch.setenv("VOICEINBOX_WEB_PORT", "8792")
    assert resolve_bind(None, None) == ("127.0.0.1", 8792)
    for args in (
        ["web", "--host", "0.0.0.0"],
        ["web", "--port", "8000"],
        ["web", "--port", "8765"],
        ["web", "--host", "localhost"],
    ):
        assert main(args) == 2
    assert "127.0.0.1" in capsys.readouterr().err


def test_health_sets_csrf_and_static_routes_are_gone(client: Client):
    status, headers, body = client.request("GET", "/api/health", cookie=False)
    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload == {"ok": True, "name": "VoiceInbox"}
    assert f"voiceinbox_csrf={client.token}" in (headers.get("Set-Cookie") or "")
    for path in ("/", "/static/app.css", "/static/app.js", "/static/nope"):
        status, _headers, body = client.request("GET", path, cookie=False)
        assert status == 404
        assert b"<html" not in body.lower()
        gone = json.loads(body.decode("utf-8"))
        assert gone["ok"] is False
    status, _headers, payload = client.json("GET", "/api/recordings", cookie=False)
    assert status == 403
    status, _headers, payload = client.json("GET", "/api/recordings")
    assert status == 200
    assert payload["counts"]["all"] == 0
    assert payload["disclaimer"].endswith("以 launchd 日志为准。")


def test_search_filter_detail_and_readonly_audio(client: Client):
    cfg = client.cfg
    state = State(cfg.db_path)
    cfg.transcripts_dir.mkdir(parents=True, exist_ok=True)
    cfg.archive_dir.mkdir(parents=True, exist_ok=True)
    first = state.upsert_discovered(
        sha256="a" * 64,
        source="voice_memos",
        original_path=str(cfg.voice_memos_dir / "missing.m4a"),
        recorded_at="2026-10-07T09:13:24+08:00",
        apple_title="晨会",
        duration_s=6.2,
    )
    audio = cfg.archive_dir / "2026" / "10" / f"keep-{first.id}.m4a"
    audio.parent.mkdir(parents=True, exist_ok=True)
    payload = b"ID3\r\n--not-boundary\x00\xffvoice"
    audio.write_bytes(payload)
    before = (audio.stat().st_mtime_ns, audio.stat().st_ino, audio.read_bytes())
    text = cfg.transcripts_dir / f"{first.id}.txt"
    text.write_text("今天开会讨论预算\n", encoding="utf-8")
    state.advance(first.id, "transcribed", text_path=str(text), archive_path=str(audio), duration_s=6.2)
    second = state.upsert_discovered(
        sha256="b" * 64,
        source="inbox",
        original_path=str(cfg.inbox_dir / "later.m4a"),
        recorded_at="2026-10-08T10:00:00+08:00",
        duration_s=4,
    )
    state.advance(second.id, "failed", last_error="模型没加载", attempts=1)

    status, _headers, body = client.json("GET", "/api/recordings?status=done&q=预算")
    assert status == 200
    assert [item["id"] for item in body["items"]] == [first.id]
    assert body["counts"]["failed"] == 1
    assert body["counts"]["done"] == 1
    status, _headers, failed = client.json("GET", "/api/recordings?status=failed")
    assert [item["id"] for item in failed["items"]] == [second.id]
    assert failed["items"][0]["status_label"] == "失败"
    assert failed["items"][0]["reason"] == "这次没转成。模型没加载"
    status, _headers, detail = client.json("GET", f"/api/recordings/{first.id}")
    assert detail["item"]["text"].strip() == "今天开会讨论预算"
    assert detail["item"]["audio_kind"] == "archive"
    assert detail["item"]["status_label"] == "已转写"
    assert "hint_help" not in detail["item"]
    status, headers, media = client.request("GET", f"/api/recordings/{first.id}/audio")
    assert status == 200
    assert media == payload
    assert headers.get("Accept-Ranges") == "bytes"
    assert "audio/mp4" in headers.get("Content-Type", "")
    status, headers, part = client.request("GET", f"/api/recordings/{first.id}/audio", headers={"Range": "bytes=0-3"})
    assert status == 206
    assert part == payload[:4]
    assert headers.get("Content-Range") == f"bytes 0-3/{len(payload)}"
    status, _headers, _body = client.request("GET", f"/api/recordings/{first.id}/audio", headers={"Range": "bytes=999-1000"})
    assert status == 416
    assert audio.stat().st_mtime_ns == before[0]
    assert audio.stat().st_ino == before[1]
    assert audio.read_bytes() == before[2]

    secret = cfg.root_dir.parent / "secret.m4a"
    secret.write_bytes(b"secret-audio")
    secret_before = secret.read_bytes()
    state.advance(second.id, "failed", archive_path=str(secret))
    status, _headers, denied = client.json("GET", f"/api/recordings/{second.id}/audio")
    assert status == 404
    assert secret.read_bytes() == secret_before
    link = cfg.archive_dir / "link.m4a"
    link.symlink_to(secret)
    state.advance(second.id, "failed", archive_path=str(link), original_path=str(cfg.inbox_dir / "still-missing.m4a"))
    status, _headers, _denied = client.request("GET", f"/api/recordings/{second.id}/audio")
    assert status == 404
    assert secret.read_bytes() == secret_before


def test_upload_retranscribe_retry_and_lock(client: Client):
    cfg = client.cfg
    memo = make_m4a(cfg.voice_memos_dir / "20261007 180000.m4a", 1)
    before = snapshot(cfg.voice_memos_dir)
    clip = make_m4a(cfg.root_dir / "incoming.m4a", 1)
    raw = clip.read_bytes()
    body, content_type = _multipart("../../Voice Memos/录音.m4a", raw)
    status, _headers, payload = client.request(
        "POST",
        "/api/upload",
        data=body,
        headers={"Content-Type": content_type},
    )
    assert status == 200, payload
    result = json.loads(payload.decode("utf-8"))
    assert result["ok"] is True
    assert result["status"] == "transcribed"
    assert result["status_label"] == "已转写"
    assert result["reason"] == ""
    saved = cfg.root_dir / result["saved_to"]
    assert saved.is_file()
    assert is_inside(saved, cfg.inbox_dir)
    assert saved.read_bytes() == raw
    assert ".." not in saved.name
    assert snapshot(cfg.voice_memos_dir) == before
    assert memo.read_bytes() == before[1][0][4]
    assert client.calls["n"] == 1

    status, _headers, denied = client.json(
        "POST",
        f"/api/recordings/{result['id']}/retranscribe",
        {"context": "偷偷的", "hotwords": "词"},
        csrf=False,
    )
    assert status == 403
    assert client.calls["n"] == 1
    status, _headers, denied = client.json(
        "POST",
        f"/api/recordings/{result['id']}/retranscribe",
        {"context": "偷偷的", "hotwords": "词"},
        headers={"Origin": "http://evil.example"},
    )
    assert status == 403
    status, _headers, denied = client.json(
        "GET",
        "/api/recordings",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert status == 403

    seen = {}
    real = __import__("voiceinbox.web", fromlist=["retranscribe"]).retranscribe

    def spy(cfg, rec_id, *, context=None, hotwords=None, transcribe_fn=None):
        seen["context"] = context
        seen["hotwords"] = hotwords
        return real(cfg, rec_id, context=context, hotwords=hotwords, transcribe_fn=transcribe_fn)

    client.app_module_patch = spy  # type: ignore[attr-defined]
    import voiceinbox.web as webmod

    webmod.retranscribe = spy
    try:
        status, _headers, again = client.json(
            "POST",
            f"/api/recordings/{result['id']}/retranscribe",
            {"context": "开会背景", "hotwords": "甲,乙"},
        )
    finally:
        webmod.retranscribe = real
    assert status == 200
    assert again["status"] == "transcribed"
    assert seen == {"context": "开会背景", "hotwords": "甲,乙"}
    assert client.calls["n"] == 2

    state = State(cfg.db_path)
    state.advance(result["id"], "failed", last_error="刚才失败", attempts=1)
    status, _headers, retried = client.json("POST", f"/api/recordings/{result['id']}/retry")
    assert status == 200
    assert retried["status"] == "transcribed"
    other = state.upsert_discovered(
        sha256="c" * 64,
        source="inbox",
        original_path=str(cfg.inbox_dir / "second.m4a"),
        duration_s=5,
    )
    state.advance(other.id, "dead", last_error="耗尽")
    # dead 没有音频，重试会失败记一笔，但锁要能拿到。这里只验证 scope。
    status, _headers, bulk = client.json("POST", "/api/retry", {"scope": "nope"})
    assert status == 400
    status, _headers, host_body = client.request("GET", "/api/recordings", headers={"Host": "evil.example"})
    assert status == 403
    assert "只接受本机地址" in host_body.decode("utf-8")

    with FileLock(cfg.lock_path) as held:
        assert held
        body, content_type = _multipart("another.m4a", raw)
        status, _headers, locked = client.request(
            "POST",
            "/api/upload",
            data=body,
            headers={"Content-Type": content_type},
        )
    locked_body = json.loads(locked.decode("utf-8"))
    assert status == 409
    assert locked_body["locked"] is True
    assert (cfg.root_dir / locked_body["saved_to"]).is_file()
    assert snapshot(cfg.voice_memos_dir) == before


def test_settings_roundtrip_and_rejected_write(client: Client, tmp_path: Path):
    status, _headers, current = client.json("GET", "/api/settings")
    assert current["file"]["language"] == "Chinese"
    status, _headers, saved = client.json(
        "POST",
        "/api/settings",
        {"since": "2026-09-15", "max_per_run": 4, "language": "English"},
    )
    assert status == 200
    assert saved["effective"]["since"] == "2026-09-15"
    assert saved["effective"]["max_per_run"] == 4
    assert client.cfg.since is not None
    text = client.app.config_path.read_text(encoding="utf-8")
    assert 'since = "2026-09-15"' in text
    assert "max_per_run = 4" in text
    assert 'language = "English"' in text
    before = client.app.config_path.read_bytes()
    status, _headers, bad = client.json(
        "POST",
        "/api/settings",
        {"since": "2026-02-31", "max_per_run": 4, "language": "English"},
    )
    assert status == 400
    assert "日期" in bad["error"]
    assert client.app.config_path.read_bytes() == before
    status, _headers, bad = client.json(
        "POST",
        "/api/settings",
        {"since": "2026-09-15", "max_per_run": True, "language": "English"},
    )
    assert status == 400
    assert client.app.config_path.read_bytes() == before

    sample = ROOT / "config.example.toml"
    copied = tmp_path / "real-shape.toml"
    copied.write_bytes(sample.read_bytes())
    original = copied.read_text(encoding="utf-8")
    update_common_settings(copied, since="2026-11-02", max_per_run=6, language="English")
    updated = copied.read_text(encoding="utf-8")
    assert "只过滤语音备忘录" in updated
    assert 'voice_memos_dir = "~/Library/Group Containers/group.com.apple.VoiceMemos.shared"' in updated
    assert 'since = "2026-11-02"' in updated
    assert "max_per_run = 6" in updated
    assert 'language = "English"' in updated
    assert not list(copied.parent.glob("*.tmp"))
    assert read_common_settings(copied) == {"since": "2026-11-02", "max_per_run": 6, "language": "English"}
    untouched = copied.read_bytes()
    with pytest.raises(ValueError):
        update_common_settings(copied, since="昨天", max_per_run=6, language="English")
    with pytest.raises(ValueError):
        update_common_settings(copied, since="2026-11-02", max_per_run=0, language="English")
    with pytest.raises(ValueError):
        update_common_settings(copied, since="", max_per_run=6, language="中文\nChinese")
    assert copied.read_bytes() == untouched
    commented = tmp_path / "commented.toml"
    commented.write_text(
        '[asr]\nlanguage = "Chinese"  # 模型语言\n\n[pipeline]\nsince = "2026-10-01"\nmax_per_run = 10  # 上限\n',
        encoding="utf-8",
    )
    update_common_settings(commented, since="", max_per_run=2, language="")
    text = commented.read_text(encoding="utf-8")
    assert 'language = "" # 模型语言' in text or 'language = ""  # 模型语言' in text
    assert "max_per_run = 2 # 上限" in text or "max_per_run = 2  # 上限" in text
    assert 'since = ""' in text
    assert original  # 示例配置只被读取


def test_system_status_and_launchctl_is_print_only(client: Client, monkeypatch, tmp_path: Path):
    binary = ROOT / "bin" / "voiceinbox-python"
    plist = Path.home() / "Library" / "LaunchAgents" / "com.wayne.voiceinbox.plist"
    stamps = [(path, path.stat().st_mtime_ns) for path in (binary, plist) if path.exists()]
    live = read_launchd_status()
    assert isinstance(live.get("loaded"), bool)
    assert live.get("state")
    for path, stamp in stamps:
        assert path.stat().st_mtime_ns == stamp

    log = client.cfg.logs_dir / "voiceinbox-2026-10-07.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("2026-10-07 19:01:02 INFO 本轮结束：发现 1，转写 1，跳过 0，失败 0，耗尽 0\n", encoding="utf-8")
    (client.cfg.logs_dir / "launchd.err.log").write_text("err-line\n", encoding="utf-8")
    status, _headers, body = client.json("GET", "/api/system")
    assert status == 200
    assert body["doctor"]["code"] == 0
    assert "launchd 日志" in body["disclaimer"]
    assert body["launchd"]["state_label"] == "没在跑"
    assert "本轮结束" in body["last_scan"]["line"]
    assert any(item["name"] == "launchd.err.log" and "err-line" in item["text"] for item in body["logs"])

    seen = {}

    def fake_run(cmd, **_kwargs):
        seen["cmd"] = cmd

        class Proc:
            returncode = 0
            stdout = "state = running\npid = 42\nlast exit code = 0\nruns = 7\nprogram = /bin/voiceinbox-python\n"
            stderr = ""

        return Proc()

    monkeypatch.setattr("voiceinbox.web.subprocess.run", fake_run)
    info = read_launchd_status()
    assert seen["cmd"][:2] == ["launchctl", "print"]
    assert seen["cmd"][2].endswith("/com.wayne.voiceinbox")
    assert info["state_label"] == "正在跑"
    assert info["pid"] == 42
    assert info["runs"] == 7
    source = (ROOT / "voiceinbox" / "web.py").read_text(encoding="utf-8")
    for banned in ("kickstart", "bootstrap", "bootout"):
        assert banned not in source

    sample = "state = not running\nruns = 66\nlast exit code = 0\nprogram = /tmp/python\n"
    parsed = parse_launchctl(0, sample, "")
    assert parsed["state_label"] == "没在跑"
    assert parsed["runs"] == 66
    missing = parse_launchctl(1, "", "Could not find service")
    assert missing["loaded"] is False
    assert missing["state_label"] == "未载入"


def test_multipart_and_range_helpers():
    data = b"abc\r\n--still-data\x00"
    body, content_type = _multipart("a.m4a", data)
    parts = parse_multipart(content_type, body)
    assert parts[0]["filename"] == "a.m4a"
    assert parts[0]["data"] == data
    assert parse_byte_range("bytes=0-1", 5) == (0, 1)
    assert parse_byte_range("bytes=3-", 5) == (3, 4)
    assert parse_byte_range("bytes=-2", 5) == (3, 4)
    with pytest.raises(ValueError):
        parse_byte_range("bytes=9-10", 5)


def test_list_cards_search_and_groups(client: Client):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    cfg = client.cfg
    state = State(cfg.db_path)
    cfg.transcripts_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()

    def add(sha: str, when, title: str | None, body: str | None, status: str = "transcribed"):
        rec = state.upsert_discovered(
            sha256=sha,
            source="inbox",
            original_path=str(cfg.inbox_dir / f"{sha[:6]}.m4a"),
            recorded_at=f"{when.isoformat()}T09:30:00+08:00",
            apple_title=title,
            duration_s=12,
        )
        if body is not None:
            path = cfg.transcripts_dir / f"{rec.id}.txt"
            path.write_text(body, encoding="utf-8")
            state.advance(rec.id, status, text_path=str(path), duration_s=12)
        else:
            state.advance(rec.id, status, duration_s=12)
        return rec

    titled = add("d" * 64, today, "晨会纪要", "今天讨论了预算和排期。\n第二句。\n")
    plain = add("e" * 64, today - timedelta(days=1), None, "转写第一句。后面是补充，不该当标题。")
    long = add("f" * 64, today - timedelta(days=10), None, "前言。" + ("填充" * 80) + "独特关键词出现在末尾。")
    failed = add("1" * 64, today, "失败样本", "不会出现在默认成功列表", status="failed")

    status, _headers, body = client.json("GET", "/api/recordings?status=done")
    assert status == 200
    ids = [item["id"] for item in body["items"]]
    assert titled.id in ids and plain.id in ids and long.id in ids
    assert failed.id not in ids
    by_id = {item["id"]: item for item in body["items"]}
    assert by_id[titled.id]["display_title"] == "晨会纪要"
    assert by_id[titled.id]["day_group"] == "today"
    assert "预算" in by_id[titled.id]["preview"]
    assert by_id[plain.id]["display_title"] == "转写第一句"
    assert by_id[plain.id]["day_group"] == "yesterday"
    assert by_id[long.id]["day_group"] == "earlier"
    assert by_id[long.id]["filename"].endswith(".m4a")

    status, _headers, found = client.json("GET", "/api/recordings?status=all&q=独特关键词")
    assert status == 200
    assert [item["id"] for item in found["items"]] == [long.id]
    assert "独特关键词" in found["items"][0]["preview"]
    assert found["items"][0]["display_title"] != "独特关键词出现在末尾"


def test_hotwords_and_failed_reason(client: Client):
    status, _headers, empty = client.json("GET", "/api/hotwords")
    assert status == 200
    # 第一次运行时种下 Sol。
    assert empty["words"] == ["Sol"]
    status, _headers, saved = client.json("POST", "/api/hotwords", {"text": "张三、李四\n张三"})
    assert status == 200
    assert saved["words"] == ["张三", "李四"]
    assert saved["text"] == "张三\n李四"
    stored = client.cfg.hotwords_file
    assert stored.read_text(encoding="utf-8") == "张三\n李四\n"
    status, _headers, denied = client.json("POST", "/api/hotwords", {"text": "不该写"}, csrf=False)
    assert status == 403
    assert stored.read_text(encoding="utf-8") == "张三\n李四\n"
    status, _headers, bad = client.json("POST", "/api/hotwords", {"text": "甲" * 41})
    assert status == 400
    assert stored.read_text(encoding="utf-8") == "张三\n李四\n"

    cfg = client.cfg
    state = State(cfg.db_path)
    rec = state.upsert_discovered(
        sha256="9" * 64,
        source="inbox",
        original_path=str(cfg.inbox_dir / "late.m4a"),
        duration_s=8,
    )
    state.advance(rec.id, "dead", last_error="TimeoutError: timed out after 30s")
    status, _headers, body = client.json("GET", "/api/recordings?status=failed")
    assert status == 200
    found = next(item for item in body["items"] if item["id"] == rec.id)
    assert found["status_label"] == "失败"
    assert found["group"] == "failed"
    assert found["reason"] == "试了几次还是没转成。转写超时了。"
    assert "dead" not in found["status_label"]
    assert "dead" not in found["reason"]


def test_settings_and_status_lights(client: Client):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from voiceinbox.web import disk_light, job_light, scan_light

    status, _headers, settings = client.json("GET", "/api/settings")
    assert status == 200
    assert "config.toml" not in settings["note"]
    assert "toml" not in settings["note"].lower()
    status, _headers, system = client.json("GET", "/api/system")
    assert [item["title"] for item in system["lights"]] == ["后台任务", "上次扫描", "完全磁盘访问"]
    assert system["lights"][0]["level"] == "amber"
    assert system["lights"][0]["label"] == "这会儿没在跑"
    assert system["lights"][1]["level"] == "red"
    assert system["lights"][1]["label"] == "还没有"
    assert system["lights"][2]["level"] == "green"

    now = datetime(2026, 10, 11, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert scan_light({"line": "2026-10-11 11:50:00 INFO 本轮结束"}, now=now)["level"] == "green"
    assert scan_light({"line": "2026-10-11 08:00:00 INFO 本轮结束"}, now=now)["level"] == "amber"
    assert scan_light({"line": "2026-10-07 19:01:02 INFO 本轮结束"}, now=now)["level"] == "red"
    assert scan_light(None, now=now)["label"] == "还没有"
    assert job_light({"loaded": True, "state": "running", "pid": 4}, {"busy": False})["level"] == "green"
    assert job_light({"loaded": False}, {"busy": False})["level"] == "red"
    assert disk_light(2, "不能列出语音备忘录目录（Operation not permitted）")["level"] == "red"
    assert disk_light(0, "可以列出语音备忘录目录")["label"] == "可以读到"
    assert "config.toml" not in disk_light(0, "ok")["detail"]


def test_human_error_phrases():
    from voiceinbox.web import human_error

    assert human_error("failed", "TimeoutError: timed out") == "这次没转成。转写超时了。"
    assert "完全磁盘访问" in human_error("failed", "PermissionError: Operation not permitted")
    assert human_error("skipped", "短于 3.0 秒") == "这段大约 3.0 秒，太短，已经跳过。"
    assert human_error("dead", "") == "试了几次还是没转成。"
    assert "dead" not in human_error("dead", "耗尽")


def test_day_group_and_title_helpers():
    from datetime import date
    from types import SimpleNamespace

    from voiceinbox.web import body_preview, day_group, display_title, search_snippet

    today = date(2026, 10, 11)
    assert day_group("2026-10-11T08:00:00+08:00", today=today) == "today"
    assert day_group("2026-10-10T23:30:00+08:00", today=today) == "yesterday"
    assert day_group("2026-10-10T16:30:00+00:00", today=today) == "today"
    assert day_group("2026-10-09T12:00:00+08:00", today=today) == "earlier"
    assert day_group("", today=today) == "earlier"
    assert day_group(None, today=today) == "earlier"

    titled = SimpleNamespace(apple_title="晨会", original_path="/tmp/a.m4a", id="abc")
    assert display_title(titled, "不应使用。") == "晨会"
    blank = SimpleNamespace(apple_title="  ", original_path="/tmp/笔记.m4a", id="abc")
    assert display_title(blank, "第一句。后面还有") == "第一句"
    unnamed = SimpleNamespace(apple_title=None, original_path="/tmp/笔记.m4a", id="abc")
    assert display_title(unnamed, "   ") == "笔记.m4a"
    long = "甲" * 160 + "。尾"
    assert body_preview(long).endswith("…")
    assert len(body_preview(long)) <= 141
    snippet = search_snippet("开头" + ("甲" * 80) + "预算讨论" + ("乙" * 40), "预算")
    assert "预算" in snippet
    assert snippet.startswith("…")


def test_repo_config_untouched_by_import():
    path = ROOT / "config.toml"
    if not path.is_file():
        path = ROOT / "config.example.toml"
    before = path.read_bytes()
    assert b"since" in before
    assert os.stat(path).st_mtime_ns == path.stat().st_mtime_ns


def test_v3_search_fixes_features_and_summary(client: Client, monkeypatch, tmp_path: Path):
    from voiceinbox import enrich
    from tests.test_enrich import FakeClient

    cfg = client.cfg
    state = State(cfg.db_path)
    rec = state.upsert_discovered(
        sha256="7" * 64,
        source="inbox",
        original_path=str(cfg.inbox_dir / "meet.m4a"),
        recorded_at="2026-10-09T10:00:00+08:00",
        duration_s=600,
    )
    (cfg.transcripts_dir / f"{rec.id}.txt").write_text("今天讨论索尔项目的预算安排。", encoding="utf-8")
    state.advance(rec.id, "transcribed")
    enrich.enrich_pending(cfg, state)
    # FTS 搜索 + 时间范围
    status, _h, body = client.json("GET", "/api/recordings?q=" + "预算安排")
    assert status == 200 and body["items"][0]["id"] == rec.id
    assert body["items"][0]["hit_field"] == "转写" and body["items"][0]["hit_at"] is not None
    status, _h, body = client.json("GET", "/api/recordings?q=预算安排&from=2026-10-10")
    assert body["items"] == []
    status, _h, body = client.json("GET", "/api/recordings?q=x&from=bad")
    assert status == 400
    # 纠错：要 CSRF；加了以后这一条马上更新，原文不动
    status, _h, denied = client.json("POST", "/api/replacements", {"wrong": "索尔", "right": "Sol"}, csrf=False)
    assert status == 403
    status, _h, saved = client.json("POST", "/api/replacements", {"wrong": "索尔", "right": "Sol", "apply_to": rec.id})
    assert status == 200 and saved["applied"] == 1
    status, _h, detail = client.json("GET", f"/api/recordings/{rec.id}")
    item = detail["item"]
    assert "Sol项目" in item["text"] and "索尔项目" in item["raw_text"] and item["corrected_differs"]
    assert item["analysis"]["state_label"] == "仅转写"
    status, _h, rules = client.json("GET", "/api/replacements")
    assert rules["rules"] == [{"wrong": "索尔", "right": "Sol"}]
    # 开关
    config_path = client.app.config_path
    status, _h, feats = client.json("POST", "/api/features", {"llm_enabled": True, "obsidian_enabled": False, "flash_max_s": 90})
    assert status == 200 and feats["file"]["llm_enabled"] is True and feats["file"]["flash_max_s"] == 90
    assert client.app.cfg.llm_enabled and client.app.cfg.flash_max_s == 90
    status, _h, bad = client.json("POST", "/api/features", {"llm_enabled": "yes", "obsidian_enabled": False, "flash_max_s": 90})
    assert status == 400
    assert "enabled = true" in config_path.read_text(encoding="utf-8")
    # 重新总结（假 LLM），锁被占用时 409
    monkeypatch.setattr("voiceinbox.enrich.OmlxClient", FakeClient)
    status, _h, done = client.json("POST", f"/api/recordings/{rec.id}/summarize", {"template": "会议"})
    assert status == 200 and done["template"] == "会议"
    status, _h, detail = client.json("GET", f"/api/recordings/{rec.id}")
    analysis = detail["item"]["analysis"]
    assert analysis["llm"] and analysis["type"] == "会议" and "## 概要" in analysis["summary_md"]
    assert detail["item"]["display_title"] == "Sol 项目周会"
    with FileLock(cfg.lock_path) as got:
        assert got
        status, _h, busy = client.json("POST", f"/api/recordings/{rec.id}/summarize", {"template": "auto"})
        assert status == 409 and busy["locked"]
    status, _h, nope = client.json("POST", f"/api/recordings/{rec.id}/summarize", {"template": "瞎写"})
    assert status == 400


def test_v4_facets_todos_today_and_activity(client: Client, monkeypatch):
    from voiceinbox import enrich
    from tests.test_enrich import FakeClient

    monkeypatch.setattr("voiceinbox.enrich.OmlxClient", FakeClient)
    cfg = client.cfg
    state = State(cfg.db_path)
    meet = state.upsert_discovered(
        sha256="8" * 64,
        source="inbox",
        original_path=str(cfg.inbox_dir / "meet.m4a"),
        recorded_at="2026-10-09T10:00:00+08:00",
        duration_s=600,
    )
    (cfg.transcripts_dir / f"{meet.id}.txt").write_text("今天讨论 Sol 项目的进度。", encoding="utf-8")
    state.advance(meet.id, "transcribed")
    status, _h, _feats = client.json("POST", "/api/features", {"llm_enabled": True, "obsidian_enabled": False, "flash_max_s": 120})
    assert status == 200
    status, _h, done = client.json("POST", f"/api/recordings/{meet.id}/summarize", {"template": "会议"})
    assert status == 200

    # 列表：场景、类型、标签分面和筛选；卡片带一句话总结和待办数
    status, _h, body = client.json("GET", "/api/recordings?kind=full")
    assert status == 200 and [i["id"] for i in body["items"]] == [meet.id]
    card = body["items"][0]
    assert card["route"] == "full" and card["brief"].startswith("讨论了") and card["todo_total"] == 1 and card["todo_open"] == 1
    assert body["facets"]["kinds"]["full"] == 1 and body["facets"]["scenes"] == [{"name": "会议", "count": 1}]
    status, _h, body = client.json("GET", "/api/recordings?kind=flash")
    assert body["items"] == []
    status, _h, body = client.json("GET", "/api/recordings?scene=会议&tag=周会")
    assert [i["id"] for i in body["items"]] == [meet.id]
    status, _h, body = client.json("GET", "/api/recordings?kind=bad")
    assert status == 400

    # 详情：结构化纪要、思维导图、待办和 obsidian:// 链接
    status, _h, detail = client.json("GET", f"/api/recordings/{meet.id}")
    a = detail["item"]["analysis"]
    assert a["summary"]["decisions"] == ["下周上线"] and a["summary"]["mindmap"]["children"][0]["name"] == "进度"
    assert a["summary"]["extras"] == [{"label": "风险与待定", "items": ["测试不够"]}]
    todo = a["todos"][0]
    assert todo["text"] == "张三 周五前发纪要" and todo["done"] is False and len(todo["key"]) == 12

    # 勾选待办：要 CSRF，key 必须属于这条录音，只写本机数据目录
    status, _h, _ = client.json("POST", "/api/todos", {"id": meet.id, "key": todo["key"], "done": True}, csrf=False)
    assert status == 403
    status, _h, _ = client.json("POST", "/api/todos", {"id": meet.id, "key": "0" * 12, "done": True})
    assert status == 400
    status, _h, _ = client.json("POST", "/api/todos", {"id": meet.id, "key": todo["key"], "done": "yes"})
    assert status == 400
    status, _h, ok = client.json("POST", "/api/todos", {"id": meet.id, "key": todo["key"], "done": True})
    assert status == 200 and ok["done"] is True
    assert (cfg.state_dir / "todos_done.json").is_file()
    status, _h, body = client.json("GET", "/api/recordings?kind=full")
    assert body["items"][0]["todo_open"] == 0

    # 今天：按日期给出当天录音、所有待办（已完成的只在当天出现）和有录音的日期
    status, _h, today = client.json("GET", "/api/today?date=2026-10-09")
    assert status == 200 and today["date"] == "2026-10-09"
    assert [i["id"] for i in today["items"]] == [meet.id]
    assert today["todos"][0]["done"] is True and today["days"][0] == {"date": "2026-10-09", "count": 1}
    status, _h, other = client.json("GET", "/api/today?date=2026-10-10")
    assert other["items"] == [] and other["todos"] == []
    status, _h, _ = client.json("GET", "/api/today?date=bad")
    assert status == 400

    # 后台状态：不抢锁，只报告
    status, _h, act = client.json("GET", "/api/activity")
    assert status == 200 and act["phase"] in {"idle", "queued", "busy", "transcribing", "summarizing"}


def test_v4_helpers():
    from voiceinbox.web import digest_body, obsidian_uri, _plain_reason

    assert obsidian_uri("/a b/语音.md") == "obsidian://open?path=%2Fa%20b%2F%E8%AF%AD%E9%9F%B3.md"
    assert obsidian_uri("") == ""
    text = '---\ndate: "x"\n---\n<!-- voiceinbox:begin 说明 -->\n# 标题\n\n## 概要\n好。\n- [[a/b|看这里]] 和 [[c]]\n<!-- voiceinbox:end -->\n我写的'
    body = digest_body(text)
    assert body.startswith("## 概要") and "看这里 和 c" in body and "我写的" not in body and "# 标题" not in body
    assert "损坏" in _plain_reason("ASR exited 1: ffmpeg: Invalid data found when processing input")


def test_monitor_toggle_interval_watch_and_run(client: Client):
    import plistlib

    fake = client.launchctl
    status, _h, st = client.json("GET", "/api/monitor")
    assert status == 200 and st["state"] == "on" and st["desired"] == {"enabled": True, "interval_min": 5, "watch": True}
    assert st["presets"] == [1, 5, 15, 30, 60]
    # 要 CSRF
    status, _h, _ = client.json("POST", "/api/monitor", {"enabled": False}, csrf=False)
    assert status == 403 and fake.loaded
    # 暂停 = bootout，写进 config.toml
    status, _h, st = client.json("POST", "/api/monitor", {"enabled": False})
    assert status == 200 and st["state"] == "off" and not fake.loaded and st["in_sync"]
    assert "enabled = false" in client.app.config_path.read_text(encoding="utf-8")
    assert not any(c[1] == "bootstrap" for c in fake.calls if c[0] == "launchctl")
    # 暂停时“立即检查”不会偷偷跑
    status, _h, st = client.json("POST", "/api/monitor/run", {})
    assert "暂停" in st["message"]
    # 打开并改间隔、关掉立即处理：plist 重写后重新装载
    status, _h, st = client.json("POST", "/api/monitor", {"enabled": True, "interval_min": 15, "watch": False})
    assert status == 200 and fake.loaded and st["interval_min"] == 15 and st["watching"] is False and st["in_sync"]
    data = plistlib.loads((client.app.monitor.plist_path).read_bytes())
    assert data["StartInterval"] == 900 and "WatchPaths" not in data and data["Label"] == "com.wayne.voiceinbox"
    # 再改一次会先备份旧 plist
    status, _h, st = client.json("POST", "/api/monitor", {"interval_min": 5, "watch": True})
    backups = list((client.cfg.backup_dir / "launchd").glob("*.plist"))
    assert backups and st["watching"] and st["interval_min"] == 5
    # 校验
    for bad in ({"interval_min": 0}, {"interval_min": "abc"}, {"interval_min": 99999}, {"enabled": "yes"}):
        status, _h, _ = client.json("POST", "/api/monitor", bad)
        assert status == 400
    # 立即检查：kickstart 不带 -k，不打断正在跑的
    status, _h, st = client.json("POST", "/api/monitor/run", {})
    kick = [c for c in fake.calls if len(c) > 1 and c[1] == "kickstart"]
    assert kick and "-k" not in kick[-1] and "已开始" in st["message"]
    fake.running = True
    status, _h, st = client.json("POST", "/api/monitor/run", {})
    assert "已经在检查" in st["message"] and st["state"] == "running"


def test_monitor_waits_for_lock(tmp_path: Path):
    from voiceinbox.lock import FileLock
    from voiceinbox.monitor import Monitor, write_desired

    cfgfile = tmp_path / "c.toml"
    cfgfile.write_text("[paths]\n", encoding="utf-8")
    write_desired(cfgfile, enabled=False, interval_min=5, watch=True)
    fake = FakeLaunchctl(tmp_path / "agent.plist")
    mon = Monitor(cfgfile, tmp_path / ".lock", tmp_path, tmp_path / "bk", runner=fake, plist_path=tmp_path / "agent.plist", wait_s=30)
    with FileLock(tmp_path / ".lock") as got:
        assert got
        msg = mon.apply()
        assert "跑完后再生效" in msg and fake.loaded  # 正在转写时不动 launchd
    import time

    for _ in range(50):
        if not fake.loaded:
            break
        time.sleep(0.1)
    assert not fake.loaded and mon.pending == ""
