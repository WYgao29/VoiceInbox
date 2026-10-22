"""命令行能看状态、空跑一轮、拒绝不存在的文件。"""

from __future__ import annotations

import os
import plistlib
from pathlib import Path

from voiceinbox.__main__ import main
from voiceinbox.doctor import doctor
from voiceinbox.monitor import expand_plist_value
from voiceinbox.launcher import (
    IDENTIFIER,
    codesign_fields,
    ensure_launcher,
    inspect_launcher,
    launcher_path,
    missing_link_targets,
    parse_otool_l,
    source_python,
)
from tests.conftest import dump_config, make_cfg


def test_project_python_is_distinct_and_stable():
    first = ensure_launcher()
    info = inspect_launcher(first)
    assert info.ok
    assert info.identifier == IDENTIFIER
    assert first.is_file() and not first.is_symlink()
    _, source_hash = codesign_fields(source_python())
    assert info.cdhash and info.cdhash != source_hash
    inode = first.stat().st_ino
    second = ensure_launcher()
    assert second.stat().st_ino == inode
    assert inspect_launcher(second).cdhash == info.cdhash
    plist_path = launcher_path().parent.parent / "launchd" / "com.wayne.voiceinbox.plist"
    raw = plist_path.read_bytes()
    plist = plistlib.loads(raw)
    assert b"/Users/" not in raw
    assert expand_plist_value(plist["ProgramArguments"][0]) == str(launcher_path())
    assert ".venv/bin/python" not in plist["ProgramArguments"][0]


def test_doctor_passes_on_local_tools(tmp_path: Path):
    ensure_launcher()
    cfg = make_cfg(tmp_path)
    code, text = doctor(cfg)
    assert code == 0, text
    assert "完全磁盘访问" in text
    assert "HF_HUB_OFFLINE" in text
    assert "不要授给 Homebrew" in text
    assert "brew pin python@3.14" in text
    assert "结果不准" in text
    assert "不会自动换掉" not in text
    assert missing_link_targets(launcher_path()) == []
    for line in text.splitlines():
        if line.startswith("授权这个文件："):
            assert line.rstrip().endswith("bin/voiceinbox-python")
            assert "Cellar" not in line
            assert ".venv" not in line
            break
    else:
        raise AssertionError("doctor 没有打印授权文件")


def test_cli_status_run_once_and_missing(tmp_path: Path, capsys):
    cfg = make_cfg(tmp_path, min_duration_s=0)
    path = tmp_path / "config.toml"
    path.write_text(dump_config(cfg), encoding="utf-8")
    assert main(["--config", str(path), "status"]) == 0
    assert "还没有记录" in capsys.readouterr().out
    assert main(["--config", str(path), "run-once"]) == 0
    assert main(["--config", str(path), "process", str(tmp_path / "missing.m4a")]) == 1
    assert main(["--config", str(path), "retry", "deadbeefdead"]) == 1

    blocked = make_cfg(tmp_path / "blocked")
    blocked_path = tmp_path / "blocked.toml"
    blocked_path.write_text(dump_config(blocked), encoding="utf-8")
    os.chmod(blocked.voice_memos_dir, 0)
    try:
        code = main(["--config", str(blocked_path), "run-once"])
    finally:
        os.chmod(blocked.voice_memos_dir, 0o700)
    assert code == 2
    assert "完全磁盘访问" in capsys.readouterr().err


def test_doctor_reports_missing_launcher_library(tmp_path: Path, monkeypatch):
    cfg = make_cfg(tmp_path)
    missing = "/opt/homebrew/Cellar/python@3.14/0.0.0/Frameworks/Python.framework/Versions/3.14/Python"
    monkeypatch.setattr("voiceinbox.doctor.missing_link_targets", lambda path: [missing])
    code, text = doctor(cfg)
    assert code == 1
    assert missing in text
    assert "重新到完全磁盘访问授权" in text
    assert "brew pin python@3.14" in text
    assert "没有用它检查语音备忘录目录" in text


def test_otool_parser_flags_missing_absolute_and_executable_path(tmp_path: Path, monkeypatch):
    sample = """bin/voiceinbox-python:
\t/opt/homebrew/Cellar/python@3.14/0.0.0/Frameworks/Python.framework/Versions/3.14/Python (compatibility version 3.14.0, current version 3.14.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1.0.0)
\t@executable_path/libMissing.dylib (compatibility version 1.0.0, current version 1.0.0)
\t@rpath/libSkip.dylib (compatibility version 1.0.0, current version 1.0.0)
"""
    libs = parse_otool_l(sample)
    assert libs[-1] == "@rpath/libSkip.dylib"
    binary = tmp_path / "voiceinbox-python"
    binary.write_bytes(b"x")
    monkeypatch.setattr("voiceinbox.launcher.linked_libraries", lambda path: libs)
    assert "/usr/lib/libSystem.B.dylib" in libs
    assert missing_link_targets(binary) == [
        "/opt/homebrew/Cellar/python@3.14/0.0.0/Frameworks/Python.framework/Versions/3.14/Python",
        "@executable_path/libMissing.dylib",
    ]


def test_resign_warning_lives_in_install_and_build_scripts():
    root = Path(__file__).resolve().parents[1]
    install = (root / "scripts" / "install_launchd.sh").read_text(encoding="utf-8")
    build = (root / "scripts" / "build_launcher.sh").read_text(encoding="utf-8")
    assert "需要重新到完全磁盘访问授权" in install
    assert "需要重新到完全磁盘访问授权" in build
    assert "brew pin python@3.14" in install
    assert "brew pin python@3.14" in build
    assert "VOICEINBOX_SUPPRESS_RESIGN_WARNING=1" in install
