"""ffprobe 看时长，ffmpeg 转到 16kHz 单声道 wav。ffmpeg 失败再用 afconvert。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from voiceinbox.log import LOG
from voiceinbox.proc import run_cmd

_OFFSET_RE = re.compile(r"([+-]\d{2})(\d{2})$")


class AudioError(RuntimeError):
    pass


@dataclass
class AudioInfo:
    duration_s: float | None
    creation_time: datetime | None
    sample_rate: int | None
    channels: int | None


def probe(path: Path) -> AudioInfo:
    code, out, err = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout_s=60,
    )
    if code != 0:
        raise AudioError((err or out).strip() or f"ffprobe 退出码 {code}")
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError as exc:
        raise AudioError(f"ffprobe 输出无法解析：{exc}") from exc
    fmt = data.get("format") or {}
    duration = _float_or_none(fmt.get("duration"))
    creation = None
    for key, value in (fmt.get("tags") or {}).items():
        if str(key).lower() == "creation_time":
            creation = _parse_creation(str(value))
            break
    streams = data.get("streams") or []
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if audio is None and streams:
        audio = streams[0]
    audio = audio or {}
    rate = audio.get("sample_rate")
    channels = audio.get("channels")
    return AudioInfo(
        duration_s=duration,
        creation_time=creation,
        sample_rate=int(rate) if rate else None,
        channels=int(channels) if channels else None,
    )


def convert_timeout(duration_s: float | None) -> float:
    if not duration_s or duration_s <= 0:
        return 600
    return max(120.0, duration_s * 0.5 + 30)


def asr_timeout(duration_s: float | None) -> float:
    """Qwen 的实时因子大约 0.06，这里按 2 倍实时再加 3 分钟，留足余量。"""
    if not duration_s or duration_s <= 0:
        return 900
    return max(900.0, duration_s * 2 + 180)


def to_wav16k(src: Path, dst: Path, *, timeout_s: float) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(dst),
    ]
    try:
        code, _out, err = run_cmd(cmd, timeout_s=timeout_s)
    except TimeoutError as exc:
        raise AudioError(str(exc)) from exc
    if code == 0 and dst.exists() and dst.stat().st_size > 0:
        return dst
    LOG.warning("ffmpeg 转码失败，改用 afconvert：%s", (err or "").strip())
    dst.unlink(missing_ok=True)
    _afconvert(src, dst, timeout_s=timeout_s)
    return dst


def _afconvert(src: Path, dst: Path, *, timeout_s: float) -> None:
    # .qta 若 ffmpeg 解不了，用系统自带的 afconvert。这台机器上还没有真实 .qta 样本。
    cmd = ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(src), str(dst)]
    try:
        code, _out, err = run_cmd(cmd, timeout_s=timeout_s)
    except TimeoutError as exc:
        raise AudioError(str(exc)) from exc
    if code != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise AudioError((err or "afconvert 失败").strip())


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_creation(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = text.replace(" ", "T")
    match = _OFFSET_RE.search(text)
    if match and ":" not in match.group(0):
        text = text[: match.start()] + match.group(1) + ":" + match.group(2)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
