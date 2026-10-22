"""用系统语音合成一段中文，走真实的 Qwen3-ASR。不要和其他转写并行。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from voiceinbox.audio import probe
from voiceinbox.pipeline import process_path
from voiceinbox.state import State
from tests.conftest import make_cfg

PHRASE = "今天天气很好。下午三点开会。这是语音备忘录转写测试。"
KEYWORDS = ["今天", "天气", "三点", "开会", "语音", "备忘", "测试"]


def _say_m4a(dest: Path) -> None:
    aiff = dest.with_suffix(".aiff")
    voices = ["Flo (中文（中国大陆）)", "Meijia", "Tingting"]
    last_error = ""
    spoke = False
    for voice in voices:
        proc = subprocess.run(
            ["say", "-v", voice, "-r", "160", "-o", str(aiff), PHRASE],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and aiff.exists() and aiff.stat().st_size > 0:
            spoke = True
            break
        last_error = proc.stderr
    if not spoke:
        proc = subprocess.run(["say", "-o", str(aiff), PHRASE], capture_output=True, text=True)
        if proc.returncode != 0 or not aiff.exists():
            raise RuntimeError(last_error or proc.stderr or "say 失败")
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(aiff),
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            str(dest),
        ],
        check=True,
    )


@pytest.mark.e2e
def test_qwen_transcribes_short_speech(tmp_path: Path):
    audio = tmp_path / "sample.m4a"
    _say_m4a(audio)
    info = probe(audio)
    assert info.duration_s is not None and info.duration_s >= 2

    cfg = make_cfg(tmp_path, min_duration_s=0.5, since=None)
    stats, rec = process_path(cfg, audio)
    assert not stats.locked
    assert rec is not None
    assert rec.status == "transcribed", rec.last_error
    text = Path(rec.text_path).read_text(encoding="utf-8")
    assert re.search(r"[\u4e00-\u9fff]", text), text
    hit = [word for word in KEYWORDS if word in text]
    assert len(hit) >= 2, text

    again, rec2 = process_path(cfg, audio)
    assert rec2 is not None and rec2.id == rec.id
    assert again.transcribed == 0
    assert len(list(cfg.transcripts_dir.glob("*.txt"))) == 1
    stored = State(cfg.db_path).get_by_sha(rec.sha256)
    assert stored is not None and stored.status == "transcribed"
