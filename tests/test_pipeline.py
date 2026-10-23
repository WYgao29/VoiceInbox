"""转写编排：幂等、过短跳过、失败进 failed、不改语音备忘录。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from voiceinbox.lock import FileLock
from voiceinbox.pipeline import _maybe_notify, retranscribe, retry, retry_statuses, run_once
from voiceinbox.state import State
from tests.conftest import make_cfg, make_m4a, snapshot


def _fake_ok(text: str):
    def fn(wav: Path, out_txt: Path, timeout_s: float) -> str:
        assert wav.exists()
        out_txt.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        return text

    return fn


def test_transcribe_once_and_archive(tmp_path: Path):
    cfg = make_cfg(tmp_path, min_duration_s=0)
    audio = make_m4a(cfg.inbox_dir / "clip.m4a", 1)
    calls = {"n": 0}

    def fake(wav: Path, out_txt: Path, timeout_s: float) -> str:
        calls["n"] += 1
        return _fake_ok("测试转写")(wav, out_txt, timeout_s)

    stats = run_once(cfg, transcribe_fn=fake)
    assert stats.transcribed == 1
    assert stats.failed == 0
    assert calls["n"] == 1
    rec = State(cfg.db_path).recent(1)[0]
    assert rec.status == "transcribed"
    assert rec.attempts == 0
    assert Path(rec.text_path).read_text(encoding="utf-8").strip() == "测试转写"
    meta = json.loads((cfg.transcripts_dir / f"{rec.id}.json").read_text(encoding="utf-8"))
    assert meta["asr_engine"] == "qwen3-asr"
    assert meta["asr_model"] == cfg.asr_model
    assert meta["language"] == "Chinese"
    index = json.loads((cfg.transcripts_dir / "index.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in index] == [rec.id]
    assert not (cfg.work_dir / f"{rec.id}.16k.wav").exists()
    assert list(cfg.archive_dir.rglob(f"*{rec.id}.m4a"))
    assert audio.exists()

    again = run_once(cfg, transcribe_fn=fake)
    assert calls["n"] == 1
    assert again.transcribed == 0
    assert len(list(cfg.transcripts_dir.glob("*.txt"))) == 1


def test_short_audio_skipped(tmp_path: Path):
    cfg = make_cfg(tmp_path, min_duration_s=30)
    make_m4a(cfg.inbox_dir / "short.m4a", 0.4)

    def fake(wav: Path, out_txt: Path, timeout_s: float) -> str:
        raise AssertionError("过短录音不应该调用转写")

    stats = run_once(cfg, transcribe_fn=fake)
    assert stats.skipped == 1
    assert stats.transcribed == 0
    assert State(cfg.db_path).recent(1)[0].status == "skipped"


def test_failure_quarantine_retry_and_readonly(tmp_path: Path):
    cfg = make_cfg(tmp_path, min_duration_s=0, max_attempts=3, retry_base_s=0)
    make_m4a(cfg.voice_memos_dir / "20261007 153000.m4a", 1)
    before = snapshot(cfg.voice_memos_dir)

    def boom(wav: Path, out_txt: Path, timeout_s: float) -> str:
        raise RuntimeError("ffmpeg boom")

    for _ in range(3):
        run_once(cfg, transcribe_fn=boom)
    assert snapshot(cfg.voice_memos_dir) == before
    rec = State(cfg.db_path).recent(1)[0]
    assert rec.status == "dead"
    assert rec.attempts == 3
    err = (cfg.failed_dir / f"{rec.id}.error.txt").read_text(encoding="utf-8")
    assert "ffmpeg boom" in err
    assert list(cfg.failed_dir.glob(f"{rec.id}.m4a"))

    stats = retry(cfg, rec.id, transcribe_fn=_fake_ok("补救成功"))
    assert stats.transcribed == 1
    assert stats.failed == 0
    assert State(cfg.db_path).get(rec.id).status == "transcribed"
    assert "补救成功" in Path(State(cfg.db_path).get(rec.id).text_path).read_text(encoding="utf-8")
    assert snapshot(cfg.voice_memos_dir) == before


class _Killed(BaseException):
    pass


def test_kill_during_transcribe_eventually_dead(tmp_path: Path):
    cfg = make_cfg(tmp_path, min_duration_s=0, max_attempts=3, retry_base_s=0)
    make_m4a(cfg.inbox_dir / "clip.m4a", 1)
    calls = {"n": 0}

    def kill(wav: Path, out_txt: Path, timeout_s: float) -> str:
        calls["n"] += 1
        raise _Killed()

    rec_id = ""
    for expected in (1, 2, 3):
        try:
            run_once(cfg, transcribe_fn=kill)
        except _Killed:
            pass
        else:
            raise AssertionError("被强杀的一轮应该把中断抛出来")
        rec = State(cfg.db_path).recent(1)[0]
        rec_id = rec.id
        assert rec.attempts == expected
        assert rec.status == "processing"
    stats = run_once(cfg, transcribe_fn=kill)
    rec = State(cfg.db_path).get(rec_id)
    assert rec is not None
    assert rec.status == "dead"
    assert rec.attempts == 3
    assert calls["n"] == 3
    assert stats.dead == 1
    err = (cfg.failed_dir / f"{rec.id}.error.txt").read_text(encoding="utf-8")
    assert "重试上限" in err


def test_failure_then_success_clears_attempts(tmp_path: Path):
    cfg = make_cfg(tmp_path, min_duration_s=0, max_attempts=3, retry_base_s=0)
    make_m4a(cfg.inbox_dir / "clip.m4a", 1)
    calls = {"n": 0}

    def flaky(wav: Path, out_txt: Path, timeout_s: float) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("once")
        return _fake_ok("好了")(wav, out_txt, timeout_s)

    run_once(cfg, transcribe_fn=flaky)
    rec = State(cfg.db_path).recent(1)[0]
    assert rec.status == "failed"
    assert rec.attempts == 1
    run_once(cfg, transcribe_fn=flaky)
    done = State(cfg.db_path).get(rec.id)
    assert done is not None
    assert done.status == "transcribed"
    assert done.attempts == 0


def test_missing_transcript_file_is_failure(tmp_path: Path):
    cfg = make_cfg(tmp_path, min_duration_s=0, max_attempts=3, retry_base_s=0)
    make_m4a(cfg.inbox_dir / "clip.m4a", 1)

    def no_file(wav: Path, out_txt: Path, timeout_s: float) -> str:
        return "只有返回值，没有写出文件"

    stats = run_once(cfg, transcribe_fn=no_file)
    assert stats.transcribed == 0
    assert stats.failed == 1
    rec = State(cfg.db_path).recent(1)[0]
    assert rec.status == "failed"
    assert list(cfg.transcripts_dir.glob("*.txt")) == []


def test_quarantine_copy_error_does_not_stop_the_rest(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path, min_duration_s=0, max_attempts=1, retry_base_s=0)
    make_m4a(cfg.inbox_dir / "a.m4a", 0.8)
    make_m4a(cfg.inbox_dir / "b.m4a", 1.3)

    def boom(wav: Path, out_txt: Path, timeout_s: float) -> str:
        raise RuntimeError("boom")

    real_copy = shutil.copy2

    def bad_copy(src, dest, **kwargs):
        if "failed" in Path(dest).parts:
            raise OSError("disk full")
        return real_copy(src, dest, **kwargs)

    monkeypatch.setattr(shutil, "copy2", bad_copy)
    stats = run_once(cfg, transcribe_fn=boom)
    assert stats.failed == 2
    assert stats.dead == 2
    records = State(cfg.db_path).recent(10)
    assert len(records) == 2
    assert {rec.status for rec in records} == {"dead"}
    for rec in records:
        text = (cfg.failed_dir / f"{rec.id}.error.txt").read_text(encoding="utf-8")
        assert "boom" in text


def test_notify_passes_message_as_argv(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path, notify_on_fda_block=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "voiceinbox.pipeline.launcher_path",
        lambda: Path('/tmp/evil" $(say pwned)'),
    )
    seen = {}

    class _Proc:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr("voiceinbox.pipeline.subprocess.run", fake_run)
    _maybe_notify(cfg)
    cmd = seen["cmd"]
    assert cmd[0] == "osascript"
    assert cmd[1] == "-e"
    script = cmd[2]
    assert "on run argv" in script
    assert "item 1 of argv" in script
    assert "evil" not in script
    assert any("evil" in str(part) for part in cmd[3:])
    assert cmd[-1] == "语音备忘录转写"


def test_retranscribe_passes_hints_and_leaves_audio(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path, min_duration_s=0)
    audio = make_m4a(cfg.voice_memos_dir / "20261007 153000.m4a", 1)
    original_bytes = audio.read_bytes()
    before = snapshot(cfg.voice_memos_dir)
    run_once(cfg, transcribe_fn=_fake_ok("第一遍"))
    rec = State(cfg.db_path).recent(1)[0]
    seen = {}

    def fake(wav: Path, out_txt: Path, cfg, *, timeout_s: float, context=None, hotwords=None) -> str:
        seen["context"] = context
        seen["hotwords"] = hotwords
        out_txt.write_text("第二遍\n", encoding="utf-8")
        return "第二遍\n"

    monkeypatch.setattr("voiceinbox.pipeline.transcribe", fake)
    stats, done = retranscribe(cfg, rec.id, context="开会背景", hotwords="张三,Qwen")
    assert stats.locked is False
    assert stats.transcribed == 1
    assert done is not None and done.status == "transcribed"
    assert seen == {"context": "开会背景", "hotwords": "张三,Qwen"}
    assert Path(done.text_path).read_text(encoding="utf-8").strip() == "第二遍"
    assert snapshot(cfg.voice_memos_dir) == before
    assert audio.read_bytes() == original_bytes


def test_retranscribe_uses_archive_when_original_is_gone(tmp_path: Path):
    cfg = make_cfg(tmp_path, min_duration_s=0)
    original = make_m4a(cfg.inbox_dir / "clip.m4a", 1)
    run_once(cfg, transcribe_fn=_fake_ok("第一遍"))
    rec = State(cfg.db_path).recent(1)[0]
    archive = Path(rec.archive_path)
    archived_bytes = archive.read_bytes()
    assert not Path(rec.staged_path).exists()
    original.unlink()

    stats, done = retranscribe(cfg, rec.id, transcribe_fn=_fake_ok("归档重转成功"))

    assert stats.transcribed == 1 and stats.failed == 0
    assert done.status == "transcribed"
    assert Path(done.text_path).read_text(encoding="utf-8").strip() == "归档重转成功"
    assert done.archive_path == rec.archive_path
    assert archive.read_bytes() == archived_bytes


def test_retranscribe_failure_restores_text_and_lock_blocks(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path, min_duration_s=0)
    make_m4a(cfg.inbox_dir / "clip.m4a", 1)
    run_once(cfg, transcribe_fn=_fake_ok("原来的正文"))
    rec = State(cfg.db_path).recent(1)[0]
    original = Path(rec.text_path).read_bytes()

    def boom(wav: Path, out_txt: Path, cfg, *, timeout_s: float, context=None, hotwords=None) -> str:
        raise RuntimeError("再转失败")

    monkeypatch.setattr("voiceinbox.pipeline.transcribe", boom)
    stats, done = retranscribe(cfg, rec.id, context="", hotwords="")
    assert stats.failed == 1
    assert done is not None and done.status == "failed"
    assert Path(rec.text_path).read_bytes() == original
    assert "再转失败" in (done.last_error or "")

    with FileLock(cfg.lock_path) as held:
        assert held
        blocked, row = retranscribe(cfg, rec.id, context="不该发生", hotwords="不该发生")
    assert blocked.locked is True
    assert row is None
    assert State(cfg.db_path).get(rec.id).status == "failed"
    assert Path(rec.text_path).read_bytes() == original


def test_retry_statuses_retries_failed_only(tmp_path: Path):
    cfg = make_cfg(tmp_path, min_duration_s=0, max_attempts=3, retry_base_s=0)
    make_m4a(cfg.inbox_dir / "clip.m4a", 1)

    def boom(wav: Path, out_txt: Path, timeout_s: float) -> str:
        raise RuntimeError("暂时失败")

    run_once(cfg, transcribe_fn=boom)
    rec = State(cfg.db_path).recent(1)[0]
    assert rec.status == "failed"
    stats = retry_statuses(cfg, {"failed"}, transcribe_fn=_fake_ok("补上了"))
    assert stats.locked is False
    assert stats.transcribed == 1
    assert State(cfg.db_path).get(rec.id).status == "transcribed"
    try:
        retry_statuses(cfg, {"skipped"})
    except ValueError:
        pass
    else:
        raise AssertionError("不该接受 skipped")


def test_lock_and_run_once(tmp_path: Path):
    path = tmp_path / "state" / ".lock"
    with FileLock(path) as first:
        assert first
        with FileLock(path) as second:
            assert second is False
    with FileLock(path) as third:
        assert third

    cfg = make_cfg(tmp_path / "run")
    with FileLock(cfg.lock_path) as held:
        assert held
        stats = run_once(cfg)
    assert stats.locked
