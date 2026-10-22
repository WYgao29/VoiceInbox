"""项目专用的 Python 副本。

完全磁盘访问按可执行文件的代码签名计算。Homebrew 的 python3.14
是公用解释器，授权给它等于授权给所有使用同一只二进制的程序。
这里复制那只解释器，再用单独的 ad-hoc 标识重新签名。launchd 和
手动扫描都执行这份副本，系统设置里只加这一份。

副本的动态库仍是 Cellar 里的绝对路径，例如
/opt/homebrew/Cellar/python@3.14/3.14.7/.../Python。
brew upgrade 或 brew cleanup 删掉旧版本后副本启动失败；源路径一变，
构建会重签，已经授过的完全磁盘访问也会失效。可用 brew pin python@3.14 锁版本。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

IDENTIFIER = "com.wayne.voiceinbox.python"
_PROBE = """
import os, sys
root = sys.argv[1]
status = "error"
try:
    if not os.path.exists(root):
        status = "missing"
    elif not os.path.isdir(root):
        status = "not_dir"
    else:
        os.listdir(root)
        status = "ok"
except PermissionError:
    status = "eperm"
except FileNotFoundError:
    status = "missing"
except OSError:
    status = "error"
print(status)
if status == "ok":
    recordings = os.path.join(root, "Recordings")
    try:
        print("yes" if os.path.isdir(recordings) else "no")
    except OSError:
        print("no")
"""


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def launcher_path() -> Path:
    return project_root() / "bin" / "voiceinbox-python"


def source_python() -> Path:
    """复制用的解释器。跳过已经指向本项目副本的路径。"""
    launcher = launcher_path()
    candidates = [
        project_root() / ".venv" / "bin" / "python",
        Path("/opt/homebrew/bin/python3.14"),
        Path("/usr/local/bin/python3.14"),
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            real = Path(os.path.realpath(candidate))
        except OSError:
            continue
        if real == launcher or not real.is_file():
            continue
        return real
    raise FileNotFoundError("找不到用来复制的 Python。请先在项目里创建 .venv。")


@dataclass(frozen=True)
class BinaryInfo:
    ok: bool
    identifier: str | None
    cdhash: str | None
    detail: str


def codesign_fields(path: Path) -> tuple[str | None, str | None]:
    proc = subprocess.run(
        ["codesign", "-dv", "--verbose=4", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    identifier = None
    cdhash = None
    for line in (proc.stderr or "").splitlines():
        if line.startswith("Identifier="):
            identifier = line.split("=", 1)[1].strip()
        elif line.startswith("CDHash="):
            cdhash = line.split("=", 1)[1].strip()
    return identifier, cdhash


def inspect_launcher(path: Path | None = None) -> BinaryInfo:
    target = path or launcher_path()
    if not target.exists():
        return BinaryInfo(False, None, None, "文件不存在")
    if target.is_symlink() or not target.is_file():
        return BinaryInfo(False, None, None, "必须是普通可执行文件，不能是软链接")
    if not os.access(target, os.X_OK):
        return BinaryInfo(False, None, None, "不可执行")
    identifier, cdhash = codesign_fields(target)
    if identifier != IDENTIFIER or not cdhash:
        return BinaryInfo(
            False,
            identifier,
            cdhash,
            f"签名标识应为 {IDENTIFIER}",
        )
    return BinaryInfo(True, identifier, cdhash, "")


def _stamp_text(source: Path) -> str:
    stat = source.stat()
    return (
        f"source={source}\n"
        f"size={stat.st_size}\n"
        f"mtime_ns={stat.st_mtime_ns}\n"
        f"identifier={IDENTIFIER}\n"
    )


def _same_signature_as_source(dest: Path, source: Path) -> bool:
    _, dest_hash = codesign_fields(dest)
    _, source_hash = codesign_fields(source)
    return bool(dest_hash and source_hash and dest_hash == source_hash)


def _reusable(dest: Path, stamp_path: Path, expected: str, source: Path) -> bool:
    if not dest.is_file() or dest.is_symlink() or not stamp_path.is_file():
        return False
    try:
        current = stamp_path.read_text(encoding="utf-8")
    except OSError:
        return False
    if current != expected:
        return False
    if not inspect_launcher(dest).ok:
        return False
    if _same_signature_as_source(dest, source):
        return False
    return True


def _sign(path: Path) -> None:
    proc = subprocess.run(
        ["codesign", "--force", "--sign", "-", "--identifier", IDENTIFIER, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"codesign 失败：{detail}")


def _otool() -> str:
    bundled = Path("/usr/bin/otool")
    if bundled.is_file():
        return str(bundled)
    return "otool"


def parse_otool_l(output: str) -> list[str]:
    """解析 `otool -L` 的标准输出，返回依赖库路径。第一行是文件自己。"""
    libs: list[str] = []
    for index, raw in enumerate(output.splitlines()):
        if index == 0:
            continue
        line = raw.strip()
        if not line:
            continue
        lib = line.split(" (", 1)[0].strip()
        if lib:
            libs.append(lib)
    return libs


def _link_target(binary: Path, lib: str) -> Path | None:
    if lib.startswith("/"):
        return Path(lib)
    for prefix in ("@executable_path/", "@loader_path/"):
        if lib.startswith(prefix):
            return binary.parent / lib.removeprefix(prefix)
    return None


def linked_libraries(path: Path) -> list[str]:
    proc = subprocess.run(
        [_otool(), "-L", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"退出码 {proc.returncode}"
        raise RuntimeError(detail)
    return parse_otool_l(proc.stdout or "")


def _system_library(lib: str) -> bool:
    """系统库在 dyld 共享缓存里，磁盘上常常没有对应文件，不能当成链接断了。"""
    return lib.startswith("/usr/lib/") or lib.startswith("/System/")


def missing_link_targets(path: Path) -> list[str]:
    """副本链接到、但磁盘上已经没有的库。

    不报 @rpath（解析不了）。也不报 /usr/lib 和 /System：它们由 dyld 共享缓存提供。
    Homebrew 的 Cellar 绝对路径删掉之后会报出来。
    """
    missing: list[str] = []
    for lib in linked_libraries(path):
        if _system_library(lib):
            continue
        target = _link_target(path, lib)
        if target is not None and not target.exists():
            missing.append(lib)
    return missing


def ensure_launcher() -> Path:
    """复制并重签项目 Python。源文件没变且签名仍有效时不重做。

    源路径变了（brew 升级换掉 Cellar 目录）会重签。重签会改变代码签名，
    已经授过的完全磁盘访问失效，安装脚本会提示重新授权。
    """
    dest = launcher_path()
    source = source_python()
    stamp_path = dest.parent / f"{dest.name}.stamp"
    expected = _stamp_text(source)
    if _reusable(dest, stamp_path, expected, source):
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f"{dest.name}.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    shutil.copyfile(source, tmp)
    os.chmod(tmp, 0o755)
    subprocess.run(["xattr", "-c", str(tmp)], capture_output=True, check=False)
    try:
        _sign(tmp)
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        os.replace(tmp, dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    if not inspect_launcher(dest).ok:
        raise RuntimeError(f"签名后仍不可用：{dest}")
    if _same_signature_as_source(dest, source):
        raise RuntimeError("项目 Python 的签名和 Homebrew Python 相同，拒绝使用")
    stamp_path.write_text(expected, encoding="utf-8")
    return dest


@dataclass(frozen=True)
class DirectoryProbe:
    status: str
    detail: str = ""
    recordings: bool | None = None


def probe_directory(directory: Path) -> DirectoryProbe:
    """用授权对象列出目录。状态与 files.access_status 相同，另加 no-launcher、launcher-failed。"""
    launcher = launcher_path()
    try:
        same = launcher.exists() and os.path.samefile(sys.executable, launcher)
    except OSError:
        same = False
    if same:
        from voiceinbox.files import access_status

        status = access_status(directory)
        recordings = None
        if status == "ok":
            try:
                recordings = (directory / "Recordings").is_dir()
            except OSError:
                recordings = False
        return DirectoryProbe(status, "", recordings)
    info = inspect_launcher(launcher)
    if not info.ok:
        if not launcher.exists():
            return DirectoryProbe("no-launcher", str(launcher))
        return DirectoryProbe("launcher-failed", info.detail)
    try:
        proc = subprocess.run(
            [str(launcher), "-c", _PROBE, str(directory)],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DirectoryProbe("launcher-failed", str(exc))
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = lines[-1][:400] if lines else f"退出码 {proc.returncode}"
        return DirectoryProbe("launcher-failed", detail)
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    status = lines[0] if lines else "error"
    if status not in {"ok", "missing", "not_dir", "eperm", "error"}:
        return DirectoryProbe("error", status)
    recordings = None
    if len(lines) >= 2 and lines[1] in {"yes", "no"}:
        recordings = lines[1] == "yes"
    return DirectoryProbe(status, "", recordings)
