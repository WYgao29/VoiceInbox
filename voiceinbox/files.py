"""找音频、算哈希、只读复制。绝不往语音备忘录目录里写。"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

from voiceinbox.log import LOG

AUDIO_EXTS = {".m4a", ".qta", ".mp3", ".wav", ".aac", ".mp4", ".mov", ".flac", ".ogg", ".opus", ".caf"}
SKIP_DIR_NAMES = {"Caches", "Preferences", "Application Scripts", "Trash", ".Trash", "__MACOSX"}
# macOS 上 iCloud 还没下载到本地的文件会带这个标志。转写占位文件没有意义。
SF_DATALESS = 0x40000000


def flags_are_dataless(flags: int) -> bool:
    return bool(flags & SF_DATALESS)


def is_dataless(path: Path) -> bool:
    try:
        flags = getattr(path.stat(), "st_flags", 0)
    except OSError:
        return False
    return flags_are_dataless(flags)


def is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


def ensure_outside(path: Path, forbidden: Path) -> None:
    """目标落在语音备忘录目录里就拒绝。这个目录只许读。"""
    if is_inside(path, forbidden):
        raise RuntimeError(f"拒绝写入受保护目录：{path}")


def safe_unlink(path: Path, forbidden: Path) -> None:
    if not path.exists():
        return
    ensure_outside(path, forbidden)
    path.unlink()


def file_sha256(path: Path, bufsize: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(bufsize), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recording_id(sha256: str) -> str:
    return sha256[:12]


def access_status(root: Path) -> str:
    """ok / missing / not_dir / eperm / error。eperm 表示被完全磁盘访问拦住。"""
    try:
        if not root.exists():
            return "missing"
        if not root.is_dir():
            return "not_dir"
        next(root.iterdir(), None)
    except PermissionError:
        return "eperm"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "error"
    return "ok"


def is_stable(path: Path, *, min_age_s: float, recheck_s: float) -> bool:
    """修改时间够旧，且隔一段时间大小不变。防止拷到一半的 iCloud 文件。"""
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_size <= 0 or flags_are_dataless(getattr(stat, "st_flags", 0)):
        return False
    if min_age_s > 0 and (time.time() - stat.st_mtime) < min_age_s:
        return False
    if recheck_s > 0:
        size = stat.st_size
        time.sleep(recheck_s)
        try:
            again = path.stat()
        except OSError:
            return False
        if again.st_size != size or flags_are_dataless(getattr(again, "st_flags", 0)):
            return False
    return True


def iter_audio(root: Path, *, max_depth: int = 8):
    """列出音频。不进隐藏目录、废纸篓、Caches。不跟随符号链接。"""

    def walk(directory: Path, depth: int):
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except PermissionError:
            LOG.warning("没有权限列出目录：%s", directory)
            return
        except OSError as exc:
            LOG.warning("列出目录失败 %s：%s", directory, exc)
            return
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name in SKIP_DIR_NAMES:
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if depth + 1 <= max_depth:
                        yield from walk(entry, depth + 1)
                    continue
                if (
                    entry.is_file()
                    and entry.suffix.lower() in AUDIO_EXTS
                    and ".composition" not in name
                ):
                    yield entry
            except OSError as exc:
                LOG.warning("跳过 %s：%s", entry, exc)

    if root.exists():
        yield from walk(root, 0)


def stage_copy(src: Path, staging_dir: Path, rec_id: str, *, forbidden: Path) -> Path:
    """把录音只读复制到 staging。复制过程先写 .partial，再改名。"""
    staging_dir.mkdir(parents=True, exist_ok=True)
    dest = staging_dir / f"{rec_id}{src.suffix.lower()}"
    partial = dest.with_name(dest.name + ".partial")
    ensure_outside(dest, forbidden)
    ensure_outside(partial, forbidden)
    if src.resolve() == dest.resolve():
        raise RuntimeError(f"拒绝覆盖源文件：{src}")
    try:
        if dest.exists() and src.exists() and dest.stat().st_size == src.stat().st_size:
            return dest
    except OSError:
        pass
    try:
        shutil.copy2(src, partial)
        os.replace(partial, dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return dest
