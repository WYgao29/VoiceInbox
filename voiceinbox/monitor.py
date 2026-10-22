"""后台监控：用 launchd 任务 com.wayne.voiceinbox 盯新录音。

期望状态写在 config.toml 的 [monitor]：
  enabled      是否装载任务（关掉 = launchctl bootout，后台不再自动跑）
  interval_min 兜底定时，写进 plist 的 StartInterval
  watch        有新录音时立即处理（plist 的 WatchPaths）

修改时先备份已安装的 plist，再从仓库里的模板 launchd/com.wayne.voiceinbox.plist 生成。
卸载或重新装载前先拿共享文件锁，等正在跑的一轮结束，不会把转写打断。
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import threading
import time
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

from voiceinbox.config import _atomic_replace, _ensure_section, _parse_bool, set_toml_key
from voiceinbox.lock import FileLock

LABEL = "com.wayne.voiceinbox"
ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "launchd" / f"{LABEL}.plist"
PRESETS = (1, 5, 15, 30, 60)
MIN_INTERVAL = 1
MAX_INTERVAL = 24 * 60
DEFAULTS = {"enabled": True, "interval_min": 5, "watch": True}


def domain() -> str:
    return f"gui/{os.getuid()}"


def installed_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def run_cmd(args: list[str], timeout: float = 20) -> tuple[int, str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ---------- 配置 ----------

def parse_interval(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("间隔必须是分钟数")
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("间隔必须是整数分钟") from None
    if not MIN_INTERVAL <= minutes <= MAX_INTERVAL:
        raise ValueError(f"间隔要在 {MIN_INTERVAL} 到 {MAX_INTERVAL} 分钟之间")
    return minutes


def read_desired(config_path: str | Path) -> dict[str, object]:
    try:
        data = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    section = data.get("monitor") or {}
    out = dict(DEFAULTS)
    if isinstance(section.get("enabled"), bool):
        out["enabled"] = section["enabled"]
    if isinstance(section.get("watch"), bool):
        out["watch"] = section["watch"]
    try:
        out["interval_min"] = parse_interval(section.get("interval_min", DEFAULTS["interval_min"]))
    except ValueError:
        pass
    return out


def write_desired(config_path: str | Path, *, enabled: object, interval_min: object, watch: object) -> dict[str, object]:
    path = Path(config_path)
    on = _parse_bool(enabled, "后台监控")
    minutes = parse_interval(interval_min)
    watch_on = _parse_bool(watch, "有新录音时立即处理")
    original = path.read_text(encoding="utf-8")
    text = _ensure_section(original, "monitor")
    text = set_toml_key(text, "monitor", "enabled", "true" if on else "false")
    text = set_toml_key(text, "monitor", "interval_min", str(minutes))
    text = set_toml_key(text, "monitor", "watch", "true" if watch_on else "false")
    parsed = tomllib.loads(text).get("monitor") or {}
    if parsed.get("enabled") != on or parsed.get("interval_min") != minutes or parsed.get("watch") != watch_on:
        raise ValueError("写入前校验没有对上，没有修改配置文件")
    if text != original:
        _atomic_replace(path, text)
    return {"enabled": on, "interval_min": minutes, "watch": watch_on}


# ---------- plist ----------

def expand_plist_value(value: object, root: Path | None = None) -> object:
    """把模板里的 @VOICEINBOX_ROOT@ 和开头的 ~ 展开。仓库里的 plist 保持占位。"""
    root_text = str(ROOT if root is None else root)
    if isinstance(value, str):
        return os.path.expanduser(value.replace("@VOICEINBOX_ROOT@", root_text))
    if isinstance(value, list):
        return [expand_plist_value(item, root) for item in value]
    if isinstance(value, dict):
        return {key: expand_plist_value(item, root) for key, item in value.items()}
    return value


def render_plist(desired: dict[str, object], template: Path = TEMPLATE) -> bytes:
    with open(template, "rb") as fh:
        data = plistlib.load(fh)
    expanded = expand_plist_value(data)
    if not isinstance(expanded, dict):
        raise ValueError("plist 模板不是字典")
    data = expanded
    data["StartInterval"] = int(desired["interval_min"]) * 60
    if not desired["watch"]:
        data.pop("WatchPaths", None)
    return plistlib.dumps(data, sort_keys=False)


def write_plist(desired: dict[str, object], dest: Path, backup_dir: Path) -> Path | None:
    """写入新 plist。内容没变就不动；变了先备份旧的。返回备份路径。"""
    new = render_plist(desired)
    old = dest.read_bytes() if dest.is_file() else None
    if old is not None:
        try:
            if plistlib.loads(old) == plistlib.loads(new):
                return None
        except Exception:
            pass
    backup = None
    if old is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{LABEL}.{datetime.now():%Y%m%d-%H%M%S}.plist"
        shutil.copy2(dest, backup)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".plist.tmp")
    tmp.write_bytes(new)
    os.replace(tmp, dest)
    return backup


# ---------- 实际状态 ----------

def parse_print(code: int, out: str) -> dict[str, object]:
    info: dict[str, object] = {"loaded": code == 0, "running": False, "pid": None, "runs": None,
                               "last_exit": None, "interval_s": None, "watching": False}
    if code != 0:
        return info

    def grab(pattern: str) -> str | None:
        match = re.search(pattern, out, re.M)
        return match.group(1).strip() if match else None

    info["running"] = grab(r"^\s*state = (.+)$") == "running"
    pid = grab(r"^\s*pid = (\d+)$")
    info["pid"] = int(pid) if pid else None
    runs = grab(r"^\s*runs = (\d+)$")
    info["runs"] = int(runs) if runs else None
    info["last_exit"] = grab(r"^\s*last exit code = (.+)$")
    interval = grab(r"^\s*run interval = (\d+) seconds")
    info["interval_s"] = int(interval) if interval else None
    info["watching"] = bool(re.search(r"^\s*watching = 1", out, re.M)) or "com.apple.fsevents.matching" in out
    return info


_SCAN_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*本轮结束")


def last_run_at(logs_dir: Path) -> datetime | None:
    files = sorted(logs_dir.glob("voiceinbox-*.log"), reverse=True)[:7]
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
        except OSError:
            continue
        for line in reversed(lines):
            match = _SCAN_RE.match(line)
            if match:
                return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    return None


def _fmt(moment: datetime | None) -> str:
    if moment is None:
        return ""
    now = datetime.now()
    if moment.date() == now.date():
        return f"今天 {moment:%H:%M}"
    if moment.date() == (now - timedelta(days=1)).date():
        return f"昨天 {moment:%H:%M}"
    return f"{moment:%m-%d %H:%M}"


class Monitor:
    """把期望状态落到 launchd 上。runner 可替换，测试里不真的调 launchctl。"""

    def __init__(self, config_path: Path, lock_path: Path, logs_dir: Path, backup_dir: Path, *,
                 runner=run_cmd, plist_path: Path | None = None, wait_s: float = 2 * 3600):
        self.config_path = Path(config_path)
        self.lock_path = lock_path
        self.logs_dir = logs_dir
        self.backup_dir = backup_dir
        self.runner = runner
        self.plist_path = plist_path or installed_path()
        self.wait_s = wait_s
        self._lock = threading.Lock()
        self.pending = ""
        self.last_message = ""

    def target(self) -> str:
        return f"{domain()}/{LABEL}"

    def actual(self) -> dict[str, object]:
        code, out = self.runner(["launchctl", "print", self.target()], 5)
        return parse_print(code, out)

    def status(self) -> dict[str, object]:
        desired = read_desired(self.config_path)
        real = self.actual()
        last = last_run_at(self.logs_dir)
        interval_s = real["interval_s"] or int(desired["interval_min"]) * 60
        nxt = None
        if real["loaded"] and last:
            nxt = last + timedelta(seconds=interval_s)
            if nxt < datetime.now():
                nxt = datetime.now() + timedelta(seconds=30)
        if real["running"]:
            state, label = "running", "正在检查"
        elif real["loaded"]:
            state, label = "on", "运行中"
        else:
            state, label = "off", "已暂停"
        return {
            "desired": desired,
            "state": state,
            "state_label": label,
            "loaded": real["loaded"],
            "running": real["running"],
            "interval_min": round(interval_s / 60) if real["loaded"] else desired["interval_min"],
            "watching": real["watching"] if real["loaded"] else False,
            "last_run": _fmt(last),
            "next_run": _fmt(nxt) if nxt else "",
            "runs": real["runs"],
            "presets": list(PRESETS),
            "pending": self.pending,
            "last_apply": self.last_message,
            "in_sync": (not desired["enabled"] and not real["loaded"]) or (
                desired["enabled"] and real["loaded"] and real["interval_s"] == int(desired["interval_min"]) * 60
                and bool(real["watching"]) == bool(desired["watch"])
            ),
        }

    def _wait_lock_then(self, body) -> str:
        """拿到共享锁再动 launchd：正在转写的那一轮会先跑完。"""
        deadline = time.monotonic() + self.wait_s
        while True:
            with FileLock(self.lock_path) as got:
                if got:
                    return body()
            if time.monotonic() > deadline:
                return "等了很久还在转写，这次没有改动后台任务。"
            time.sleep(2)

    def _apply_now(self) -> str:
        desired = read_desired(self.config_path)
        backup = write_plist(desired, self.plist_path, self.backup_dir)
        code, out = self.runner(["plutil", "-lint", str(self.plist_path)], 10)
        if code != 0:
            if backup:
                shutil.copy2(backup, self.plist_path)
            return "新的 plist 校验没通过，已还原：" + out.strip()[:200]
        self.runner(["launchctl", "bootout", self.target()], 20)
        if not desired["enabled"]:
            return "后台监控已暂停。"
        code, out = self.runner(["launchctl", "bootstrap", domain(), str(self.plist_path)], 20)
        if code != 0:
            return "重新装载失败：" + out.strip()[:200]
        return f"后台监控已打开，每 {desired['interval_min']} 分钟检查一次" + ("，有新录音时也会立即处理。" if desired["watch"] else "。")

    def apply(self, *, background: bool = True) -> str:
        """锁空着就马上改；有一轮在跑就在后台等它结束再改。"""
        with FileLock(self.lock_path) as got:
            if got:
                with self._lock:
                    self.last_message = self._apply_now()
                    self.pending = ""
                return self.last_message
        if not background:
            self.last_message = self._wait_lock_then(self._apply_now)
            return self.last_message
        self.pending = "正在转写，这一轮跑完后再生效，不会打断。"

        def work():
            with self._lock:
                self.last_message = self._wait_lock_then(self._apply_now)
                self.pending = ""
        threading.Thread(target=work, daemon=True).start()
        return self.pending

    def run_now(self) -> str:
        real = self.actual()
        if real["running"]:
            return "已经在检查了。"
        with FileLock(self.lock_path) as got:
            if not got:
                return "另一轮正在进行，这次没有再开一轮。"
        if not real["loaded"]:
            return "后台监控是暂停的。先打开，或者用「导入音频」手动处理。"
        # 不带 -k：不会杀掉正在跑的进程。由 launchd 来跑，用的是它的完全磁盘访问授权。
        code, out = self.runner(["launchctl", "kickstart", self.target()], 10)
        if code != 0:
            return "没能触发：" + out.strip()[:200]
        return "已开始检查一次，结果稍后出现在列表里。"
