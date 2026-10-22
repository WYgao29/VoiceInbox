"""测试共用：临时配置、短音频、配置文件往返。"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import date
from pathlib import Path

from voiceinbox.config import Config, ensure_tool_path

ensure_tool_path()

ASR_SCRIPT = Path.home() / "asr" / "qwen3-asr" / "transcribe.sh"


def make_cfg(tmp_path: Path, **overrides) -> Config:
    root = tmp_path / "root"
    params = dict(
        voice_memos_dir=tmp_path / "vm",
        inbox_dir=root / "inbox",
        root_dir=root,
        asr_script=ASR_SCRIPT,
        asr_model="mlx-community/Qwen3-ASR-1.7B-bf16",
        asr_language="Chinese",
        stable_wait_s=0,
        stable_recheck_s=0,
        max_attempts=3,
        retry_base_s=0,
        min_duration_s=3,
        since=date(2026, 10, 1),
        keep_audio_copy=True,
        timezone="Asia/Shanghai",
        notify_on_fda_block=False,
        max_per_run=10,
        undated_policy="mtime",
    )
    params.update(overrides)
    cfg = Config(**params)
    cfg.voice_memos_dir.mkdir(parents=True, exist_ok=True)
    cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def make_m4a(path: Path, seconds: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            str(path),
        ],
        check=True,
    )
    return path


def dump_config(cfg: Config) -> str:
    since = '""' if cfg.since is None else json.dumps(cfg.since.isoformat())
    return f"""
[paths]
voice_memos_dir = {json.dumps(str(cfg.voice_memos_dir))}
inbox_dir = {json.dumps(str(cfg.inbox_dir))}
root_dir = {json.dumps(str(cfg.root_dir))}

[asr]
script = {json.dumps(str(cfg.asr_script))}
model = {json.dumps(cfg.asr_model)}
language = {json.dumps(cfg.asr_language)}

[pipeline]
stable_wait_s = {cfg.stable_wait_s}
stable_recheck_s = {cfg.stable_recheck_s}
max_attempts = {cfg.max_attempts}
retry_base_s = {cfg.retry_base_s}
min_duration_s = {cfg.min_duration_s}
since = {since}
keep_audio_copy = {"true" if cfg.keep_audio_copy else "false"}
timezone = {json.dumps(cfg.timezone)}
notify_on_fda_block = {"true" if cfg.notify_on_fda_block else "false"}
max_per_run = {cfg.max_per_run}
undated_policy = {json.dumps(cfg.undated_policy)}
"""


def snapshot(root: Path):
    """文件内容、inode、mtime，以及目录 mtime。用来证明没有改语音备忘录。"""
    dirs = []
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        folder = Path(dirpath)
        dirs.append((str(folder.relative_to(root)), folder.stat().st_mtime_ns))
        for name in sorted(filenames):
            path = folder / name
            stat = path.stat()
            files.append(
                (
                    str(path.relative_to(root)),
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ino,
                    path.read_bytes(),
                )
            )
    return dirs, files
