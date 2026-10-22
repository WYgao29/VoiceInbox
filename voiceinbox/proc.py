"""跑外部命令。超时或本进程收到 SIGTERM 时，把子进程整组停掉。

Qwen 峰值内存大约 9.6GB。子进程单独成组，是为了超时只杀掉这一次转写，
不把扫描进程自己杀掉；父进程退出前也会清掉这一组。
"""

from __future__ import annotations

import os
import signal
import subprocess

from voiceinbox.log import LOG

_CHILDREN: set[int] = set()
_SIGNALS_READY = False


def _kill_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return


def _on_signal(signum: int, _frame) -> None:
    for pid in list(_CHILDREN):
        _kill_group(pid, signal.SIGTERM)
    raise SystemExit(128 + signum)


def _install_signals() -> None:
    global _SIGNALS_READY
    if _SIGNALS_READY:
        return
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    _SIGNALS_READY = True


def run_cmd(
    cmd: list[str],
    *,
    timeout_s: float,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    _install_signals()
    LOG.info("运行 %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _CHILDREN.add(proc.pid)
    try:
        try:
            out, err = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            _kill_group(proc.pid, signal.SIGTERM)
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                _kill_group(proc.pid, signal.SIGKILL)
                out, err = proc.communicate()
            raise TimeoutError(f"超时 {timeout_s:.0f}s：{cmd[0]}\n{(err or '').strip()}") from exc
        return proc.returncode or 0, out or "", err or ""
    finally:
        _CHILDREN.discard(proc.pid)
