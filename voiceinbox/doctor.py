"""检查本机能不能转写：ffmpeg、Qwen 脚本、模型缓存、完全磁盘访问。"""

from __future__ import annotations

import os
import plistlib
import shutil
import sys
from pathlib import Path

from voiceinbox.config import Config, ensure_tool_path
from voiceinbox.launcher import (
    IDENTIFIER,
    inspect_launcher,
    launcher_path,
    missing_link_targets,
    probe_directory,
    project_root,
)
from voiceinbox.monitor import expand_plist_value
from voiceinbox.pipeline import ensure_dirs


def find_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        candidate = Path(prefix) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def model_cache_dir(model: str) -> Path:
    slug = "models--" + model.replace("/", "--")
    return Path.home() / ".cache" / "huggingface" / "hub" / slug


def model_is_cached(model: str) -> bool:
    snapshots = model_cache_dir(model) / "snapshots"
    if not snapshots.is_dir():
        return False
    for child in snapshots.iterdir():
        if (child / "model.safetensors").is_file() or (child / "model.safetensors.index.json").is_file():
            return True
    return False


def fda_instructions(target: str) -> str:
    uid = os.getuid()
    return f"""需要 Wayne 做的授权
完全磁盘访问只授给本项目的 Python 副本，不要授给 Homebrew 的 python3.14，
也不要授给 .venv/bin/python（那是指向公用解释器的软链接）。
公用解释器一旦授权，所有使用同一只二进制的程序都会带上完全磁盘访问。

1. 若还没有这份副本，在项目目录执行：
   bash scripts/build_launcher.sh
2. 打开「系统设置 → 隐私与安全性 → 完全磁盘访问权限」，点「+」。
3. 按 ⌘⇧G，粘贴下面这一行。要授权这个文件本身：
   {target}
4. 打开这项的开关。标识是 {IDENTIFIER}。
5. 这份副本是复制出来的，动态库仍链接到
   /opt/homebrew/Cellar/python@3.14/<版本>/... 的绝对路径。
   brew upgrade 或 brew cleanup 删掉旧版本后，副本会启动失败。
   安装脚本发现源路径变了还会自动重签。这两种情况都会让已经授过的完全磁盘访问失效。
   建议锁住版本，避免 Cellar 路径被换掉：
     brew pin python@3.14
   若已经升级或清理过，重新运行 bash scripts/build_launcher.sh 重建副本，
   然后重新到完全磁盘访问授权。签名变了，旧授权无效。
   第一次由 launchd 启动新签名的副本时，系统会先做一次签名检查，这一轮可能要几十秒。
6. 想在「终端」里自己 ls 语音备忘录目录，再把「终端」或 iTerm 加进完全磁盘访问。
7. 改完权限后重新装一次任务，或执行：
   launchctl kickstart -k gui/{uid}/com.wayne.voiceinbox
   能不能读语音备忘录，以这次 kickstart 之后的日志为准：
   ~/VoiceInbox/logs/launchd.err.log，以及当天的 voiceinbox 日志。
   从终端跑 doctor 时，系统可能按「终端」自己的完全磁盘访问来判断，结果不准。
   授权完成前，run-once 读不到目录时退出码是 2。
8. iPhone：设置 → [你的名字] → iCloud → 打开「语音备忘录」。
9. Mac：系统设置 → [你的名字] → iCloud → 打开「语音备忘录」，再打开一次「语音备忘录」App，
   确认手机上的录音已经出现。当前这台 Mac 上 Recordings 目录还不存在，多半是还没打开过这个 App。
本程序只把录音复制到 ~/VoiceInbox/staging，不会改、不会删语音备忘录里的文件。
转写进程用的是 ~/asr/qwen3-asr 里的另一个 Python，它不读语音备忘录，不必给它完全磁盘访问。
转写时强制 HF_HUB_OFFLINE=1，只用本机已缓存的模型，不访问网络。
"""


def _plist_program(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        data = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    args = data.get("ProgramArguments") if isinstance(data, dict) else None
    if not isinstance(args, list) or not args:
        return None
    program = expand_plist_value(str(args[0]))
    return program if isinstance(program, str) else str(args[0])


def doctor(cfg: Config) -> tuple[int, str]:
    ensure_tool_path()
    tool_bad = False
    access_bad = False
    lines: list[str] = []

    def add(kind: str, ok: bool, message: str) -> None:
        nonlocal tool_bad, access_bad
        lines.append(f"[{'通过' if ok else '失败'}] {message}")
        if ok:
            return
        if kind == "tool":
            tool_bad = True
        elif kind == "access":
            access_bad = True

    for name in ("ffmpeg", "ffprobe", "afconvert"):
        found = find_tool(name)
        add("tool", found is not None, f"{name}：{found or '未找到'}")

    script_ok = cfg.asr_script.is_file() and os.access(cfg.asr_script, os.X_OK)
    add("tool", script_ok, f"Qwen 脚本：{cfg.asr_script}")
    cached = model_is_cached(cfg.asr_model)
    add("tool", cached, f"模型缓存 {cfg.asr_model}：{model_cache_dir(cfg.asr_model)}")

    target = launcher_path()
    lines.append(f"授权这个文件：{target}")
    lines.append(f"代码签名标识：{IDENTIFIER}")
    lines.append(f"当前进程：{sys.executable}")
    info = inspect_launcher(target)
    links_broken = False
    if info.ok:
        lines.append(f"CDHash：{info.cdhash}")
        try:
            missing = missing_link_targets(target)
        except (OSError, RuntimeError) as exc:
            links_broken = True
            add("tool", False, f"无法检查项目 Python 的动态库链接：{exc}")
        else:
            if missing:
                links_broken = True
                shown = "\n".join(f"    {item}" for item in missing)
                add(
                    "tool",
                    False,
                    "项目 Python 链接的库已经不在：\n"
                    f"{shown}\n"
                    "    brew 升级或 brew cleanup 会删掉旧的 Cellar 路径，副本因此无法启动，"
                    "已经授过的完全磁盘访问也会失效。"
                    "请重新运行 bash scripts/build_launcher.sh，然后重新到完全磁盘访问授权。"
                    "可用 brew pin python@3.14 锁住版本。",
                )
            else:
                add("tool", True, f"项目 Python：{target}")
    elif not target.exists():
        add("tool", False, f"还没有项目 Python 副本：{target}。先运行 bash scripts/build_launcher.sh")
    else:
        add("tool", False, f"项目 Python 不可用：{info.detail}。重新运行 bash scripts/build_launcher.sh")
    try:
        same = target.exists() and os.path.samefile(sys.executable, target)
    except OSError:
        same = False
    if same:
        lines.append("当前进程就是要授权的副本。")
    else:
        lines.append("当前进程不是上面的副本。")
    lines.append("从终端跑 doctor 时，系统可能按终端自己的完全磁盘访问来判断，结果不准。")
    lines.append("launchd 能不能读语音备忘录，以 kickstart 之后的日志为准。")

    repo_plist = project_root() / "launchd" / "com.wayne.voiceinbox.plist"
    repo_program = _plist_program(repo_plist)
    add("tool", repo_program == str(target), f"plist 启动程序：{repo_program or '无法读取'}")
    installed = Path.home() / "Library/LaunchAgents/com.wayne.voiceinbox.plist"
    installed_program = _plist_program(installed)
    if installed_program and installed_program != str(target):
        lines.append(f"[注意] 已安装的任务还在用 {installed_program}。请重新运行 scripts/install_launchd.sh")

    try:
        ensure_dirs(cfg)
        probe = cfg.state_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("tool", True, f"数据目录可写：{cfg.root_dir}")
    except OSError as exc:
        add("tool", False, f"数据目录不可写：{exc}")

    if links_broken:
        status = "launcher-failed"
        add("tool", False, "项目 Python 启动不了，因此没有用它检查语音备忘录目录。")
        probed_recordings = None
    else:
        probed = probe_directory(cfg.voice_memos_dir)
        status = probed.status
        detail = probed.detail
        probed_recordings = probed.recordings
        if status == "ok":
            add("access", True, f"可以列出语音备忘录目录：{cfg.voice_memos_dir}")
        elif status == "eperm":
            add("access", False, f"不能列出语音备忘录目录（Operation not permitted）：{cfg.voice_memos_dir}")
        elif status == "no-launcher":
            add("tool", False, f"没有项目 Python，无法检查语音备忘录目录：{detail}")
        elif status == "launcher-failed":
            add("tool", False, f"项目 Python 无法启动：{detail}")
        else:
            extra = f"（{detail}）" if detail else ""
            add("access", False, f"语音备忘录目录状态 {status}{extra}：{cfg.voice_memos_dir}")

    recordings = cfg.voice_memos_dir / "Recordings"
    if status == "ok" and probed_recordings:
        lines.append(f"[通过] Recordings 子目录存在：{recordings}")
    elif status == "ok":
        lines.append(f"[注意] Recordings 子目录目前不存在：{recordings}")
        lines.append("    目录一旦被「语音备忘录」创建，下一轮扫描会看到里面的音频。")

    lines.append("")
    lines.append(fda_instructions(str(target)).rstrip())
    code = 1 if tool_bad else 2 if access_bad else 0
    return code, "\n".join(lines) + "\n"
