"""转码，以及调用 Qwen 脚本时必须离线。"""

from __future__ import annotations

from pathlib import Path

import pytest

from voiceinbox.asr import AsrError, transcribe
from voiceinbox.audio import AudioError, probe, to_wav16k
from tests.conftest import make_cfg, make_m4a


def test_ffmpeg_makes_16k_mono(tmp_path: Path):
    src = make_m4a(tmp_path / "a.m4a", 0.6)
    dst = tmp_path / "a.wav"
    to_wav16k(src, dst, timeout_s=60)
    info = probe(dst)
    assert info.sample_rate == 16000
    assert info.channels == 1
    assert info.duration_s is not None and info.duration_s > 0.3


def test_afconvert_can_write_16k(tmp_path: Path):
    src = make_m4a(tmp_path / "a.m4a", 0.6)
    wav = tmp_path / "a.wav"
    to_wav16k(src, wav, timeout_s=60)
    again = tmp_path / "b.wav"
    import subprocess

    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(wav), str(again)],
        check=True,
    )
    assert probe(again).sample_rate == 16000


def test_ffmpeg_failure_falls_back_to_afconvert(monkeypatch, tmp_path: Path):
    calls = []

    def fake(cmd, timeout_s, env=None):
        calls.append(cmd[0])
        if cmd[0] == "ffmpeg":
            return 1, "", "cannot decode"
        Path(cmd[-1]).write_bytes(b"RIFF")
        return 0, "", ""

    monkeypatch.setattr("voiceinbox.audio.run_cmd", fake)
    src = tmp_path / "a.qta"
    src.write_bytes(b"not-audio")
    dst = tmp_path / "out.wav"
    to_wav16k(src, dst, timeout_s=5)
    assert calls == ["ffmpeg", "afconvert"]
    assert dst.read_bytes() == b"RIFF"


def test_both_converters_fail(monkeypatch, tmp_path: Path):
    def fake(cmd, timeout_s, env=None):
        return 1, "", "nope"

    monkeypatch.setattr("voiceinbox.audio.run_cmd", fake)
    src = tmp_path / "a.qta"
    src.write_bytes(b"x")
    with pytest.raises(AudioError):
        to_wav16k(src, tmp_path / "out.wav", timeout_s=5)


def test_asr_forces_offline(monkeypatch, tmp_path: Path):
    seen = {}

    def fake(cmd, timeout_s, env=None):
        seen["env"] = env
        seen["cmd"] = cmd
        wav = Path(cmd[-1])
        wav.with_suffix(".txt").write_text("你好\n", encoding="utf-8")
        return 0, "你好\n", "[model loaded 1.0s]\n"

    monkeypatch.setattr("voiceinbox.asr.run_cmd", fake)
    cfg = make_cfg(tmp_path)
    wav = tmp_path / "a.16k.wav"
    wav.write_bytes(b"RIFF")
    out = tmp_path / "out.txt"
    text = transcribe(wav, out, cfg, timeout_s=10)
    assert seen["env"]["HF_HUB_OFFLINE"] == "1"
    assert seen["env"]["ASR_LANG"] == "Chinese"
    assert seen["env"]["QWEN3_ASR_MODEL"] == cfg.asr_model
    assert seen["cmd"][0] == str(cfg.asr_script)
    assert text.strip() == "你好"
    assert out.read_text(encoding="utf-8").strip() == "你好"
    assert not wav.with_suffix(".txt").exists()

    def sidecar_wins(cmd, timeout_s, env=None):
        Path(cmd[-1]).with_suffix(".txt").write_text("旁路正文\n", encoding="utf-8")
        return 0, "标准输出不应被采用\n", ""

    monkeypatch.setattr("voiceinbox.asr.run_cmd", sidecar_wins)
    text = transcribe(wav, out, cfg, timeout_s=10)
    assert text.strip() == "旁路正文"
    assert out.read_text(encoding="utf-8").strip() == "旁路正文"


def test_asr_hints_override_ambient(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ASR_CONTEXT", "ambient-context")
    monkeypatch.setenv("ASR_HOTWORDS", "ambient")
    monkeypatch.setenv("ASR_HOTWORDS_FILE", "/tmp/words.txt")
    seen = []

    def fake(cmd, timeout_s, env=None):
        seen.append(env)
        Path(cmd[-1]).with_suffix(".txt").write_text("好\n", encoding="utf-8")
        return 0, "", ""

    monkeypatch.setattr("voiceinbox.asr.run_cmd", fake)
    cfg = make_cfg(tmp_path)
    wav = tmp_path / "a.16k.wav"
    wav.write_bytes(b"RIFF")
    out = tmp_path / "out.txt"
    transcribe(wav, out, cfg, timeout_s=5)
    assert seen[0]["ASR_CONTEXT"] == "ambient-context"
    assert seen[0]["ASR_HOTWORDS"] == "ambient"
    # 热词文件只认数据目录里的词表；词表为空时不传，外部环境里的同名变量也不沿用。
    assert "ASR_HOTWORDS_FILE" not in seen[0]

    transcribe(wav, out, cfg, timeout_s=5, context="", hotwords="")
    assert "ASR_CONTEXT" not in seen[1]
    assert "ASR_HOTWORDS" not in seen[1]
    assert "ASR_HOTWORDS_FILE" not in seen[1]

    transcribe(wav, out, cfg, timeout_s=5, context="开会", hotwords="甲,乙")
    assert seen[2]["ASR_CONTEXT"] == "开会"
    assert seen[2]["ASR_HOTWORDS"] == "甲,乙"
    assert "ASR_HOTWORDS_FILE" not in seen[2]
    assert seen[2]["HF_HUB_OFFLINE"] == "1"

    cfg.vocab_dir.mkdir(parents=True, exist_ok=True)
    cfg.hotwords_file.write_text("Sol\n", encoding="utf-8")
    transcribe(wav, out, cfg, timeout_s=5)
    assert seen[3]["ASR_HOTWORDS_FILE"] == str(cfg.hotwords_file)
    transcribe(wav, out, cfg, timeout_s=5, hotwords="甲")
    assert seen[4]["ASR_HOTWORDS_FILE"] == str(cfg.hotwords_file)
    assert seen[4]["ASR_HOTWORDS"] == "甲"

    with pytest.raises(AsrError):
        transcribe(wav, out, cfg, timeout_s=5, context="a\x00b")


def test_asr_failure_and_empty(monkeypatch, tmp_path: Path):
    def fail(cmd, timeout_s, env=None):
        return 1, "", "model missing"

    monkeypatch.setattr("voiceinbox.asr.run_cmd", fail)
    cfg = make_cfg(tmp_path)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    with pytest.raises(AsrError):
        transcribe(wav, tmp_path / "out.txt", cfg, timeout_s=10)

    def empty(cmd, timeout_s, env=None):
        return 0, "标准输出里的正文\n", ""

    monkeypatch.setattr("voiceinbox.asr.run_cmd", empty)
    with pytest.raises(AsrError, match="没有生成文本文件"):
        transcribe(wav, tmp_path / "out.txt", cfg, timeout_s=10)

    def blank_file(cmd, timeout_s, env=None):
        Path(cmd[-1]).with_suffix(".txt").write_text("  \n", encoding="utf-8")
        return 0, "标准输出\n", ""

    monkeypatch.setattr("voiceinbox.asr.run_cmd", blank_file)
    with pytest.raises(AsrError, match="转写结果为空"):
        transcribe(wav, tmp_path / "out.txt", cfg, timeout_s=10)
