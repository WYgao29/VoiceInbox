"""外部命令可在线程中运行，Web 启动时仍保留进程退出处理。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_first_command_can_run_in_worker_thread():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from concurrent.futures import ThreadPoolExecutor
from voiceinbox.proc import run_cmd

with ThreadPoolExecutor(max_workers=1) as pool:
    code, out, err = pool.submit(
        run_cmd, [sys.executable, '-c', 'print("worker-ok")'], timeout_s=5,
    ).result(timeout=10)
assert (code, out.strip(), err) == (0, 'worker-ok', '')
""",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("ambient_overrides", [False, True], ids=["clean-env", "path-overrides"])
def test_web_initializes_exit_handlers_before_requests(tmp_path: Path, monkeypatch, ambient_overrides):
    ambient_dir = tmp_path / "ambient-data"
    if ambient_overrides:
        for key, value in {
            "VOICEINBOX_ROOT": str(ambient_dir / "root"),
            "VOICEINBOX_INBOX": str(ambient_dir / "inbox"),
            "VOICEINBOX_VOICE_MEMOS": str(ambient_dir / "voice-memos"),
            "VOICEINBOX_CONFIG": str(ambient_dir / "config.toml"),
            "VOICEINBOX_ASR_SCRIPT": str(ambient_dir / "transcribe.sh"),
            "VOICEINBOX_ASR_LANG": "English",
            "VOICEINBOX_SINCE": "2026-01-01",
        }.items():
            monkeypatch.setenv(key, value)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import signal
import sys
from pathlib import Path
from tests.conftest import make_cfg, dump_config
from voiceinbox.web import WebApp, build_server

folder = Path(sys.argv[1])
cfg = make_cfg(folder)
config_path = folder / 'config.toml'
config_path.write_text(dump_config(cfg), encoding='utf-8')
app = WebApp(config_path)
for name in ('root_dir', 'inbox_dir', 'voice_memos_dir', 'asr_script', 'asr_language', 'since'):
    assert getattr(app.cfg, name) == getattr(cfg, name), name
server = build_server(app)
try:
    for signum in (signal.SIGTERM, signal.SIGINT):
        assert getattr(signal.getsignal(signum), '__module__', '') == 'voiceinbox.proc'
finally:
    server.server_close()
""",
            str(tmp_path),
        ],
        env={key: value for key, value in os.environ.items() if not key.startswith("VOICEINBOX_")},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "root" / "state" / "state.sqlite").is_file()
    assert not ambient_dir.exists()
