"""本机 API。只绑定 127.0.0.1。界面是 VoiceInbox 桌面应用。

转写复用 pipeline 里的文件锁，因此不会和 launchd 各加载一份 Qwen。
原录音只读。读取定时任务状态时只运行 launchctl print。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from voiceinbox.config import (
    Config,
    load_config,
    read_common_settings,
    read_feature_settings,
    update_common_settings,
    update_feature_settings,
)
from voiceinbox.doctor import doctor as default_doctor
from voiceinbox.files import AUDIO_EXTS, ensure_outside
from voiceinbox.pipeline import (
    ensure_dirs,
    process_path,
    retranscribe,
    retry,
    retry_statuses,
)
from voiceinbox.state import Record, State
from voiceinbox.proc import install_signal_handlers
from voiceinbox.lock import FileLock
from voiceinbox import deletion, digest_queue, enrich, search, todos, vocab
from voiceinbox.monitor import Monitor
from voiceinbox.llm import LlmError
from voiceinbox.summarize import SCENES, TEMPLATES

LOG = logging.getLogger("voiceinbox.web")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8791
BLOCKED_PORTS = {8000, 8765}
MAX_JSON = 256 * 1024
MAX_UPLOAD = 200 * 1024 * 1024
_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")

DISCLAIMER = (
    "从这里拉起的后端，权限判断可能和 launchd 不同。"
    "能不能读到语音备忘录，以 launchd 日志为准。"
)
LOCKED_MESSAGE = "另一轮转写正在进行。同一时间只能跑一轮，峰值大约 9.6GB。这次没有开始。"
SETTINGS_NOTE = (
    "保存后，后台定时任务下一轮会用新的起始日期、条数和语言。"
    "如果启动时另有临时指定，你在这里看到的实际值可能和刚保存的不一样。"
)

GROUPS = (
    ("pending", "排队中", frozenset({"discovered", "stable", "converted"})),
    ("processing", "转写中", frozenset({"processing"})),
    ("done", "已转写", frozenset({"transcribed"})),
    ("failed", "失败", frozenset({"failed", "dead", "deleting", "delete_failed"})),
    ("skipped", "太短已跳过", frozenset({"skipped"})),
)
_GROUP_OF = {name: key for key, _label, names in GROUPS for name in names}
_LABEL_OF = {name: label for _key, label, names in GROUPS for name in names}
_LABEL_OF.update({"deleting": "删除未完成", "delete_failed": "删除未完成"})
_RAW_STATUS = set(_GROUP_OF)
_GROUP_NAMES = {key for key, _label, _names in GROUPS}

AUDIO_MIME = {
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".qta": "audio/mp4",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".caf": "audio/x-caf",
}
AUDIO_KIND_LABEL = {
    "original": "语音备忘录原件",
    "archive": "归档副本",
    "staged": "暂存副本",
    "failed": "失败目录里的副本",
    "work": "16kHz 转码",
}


def resolve_bind(host: str | None, port: object | None) -> tuple[str, int]:
    if host is None or str(host).strip() == "":
        host = os.environ.get("VOICEINBOX_WEB_HOST", DEFAULT_HOST)
    if port is None or str(port).strip() == "":
        port = os.environ.get("VOICEINBOX_WEB_PORT", str(DEFAULT_PORT))
    host = str(host).strip()
    if host != DEFAULT_HOST:
        raise ValueError("只允许绑定 127.0.0.1")
    try:
        number = int(str(port))
    except (TypeError, ValueError) as exc:
        raise ValueError("端口必须是整数") from exc
    if number in BLOCKED_PORTS:
        raise ValueError("不能使用端口 8000 或 8765")
    if not 1 <= number <= 65535:
        raise ValueError("端口超出范围")
    return host, number


def group_of(status: str) -> str:
    return _GROUP_OF.get(status, "pending")


def label_of(status: str) -> str:
    return _LABEL_OF.get(status, status)


def _plain_reason(text: str) -> str:
    if not text:
        return ""
    if "Operation not permitted" in text or "Permission denied" in text:
        return "读不到这段录音，多半是没有完全磁盘访问权限。"
    lowered = text.lower()
    if "timeout" in lowered or "timed out" in lowered or "超时" in text:
        return "转写超时了。"
    if "没有生成文本" in text:
        return "转写结束了，但没有写出文字。"
    if "No such file" in text or "找不到" in text:
        return "找不到音频文件。"
    if "invalid data found" in lowered or "moov atom not found" in lowered or "could not find codec" in lowered:
        return "音频文件读不了，可能损坏了，或者格式不支持。"
    if "out of memory" in lowered or "memoryerror" in lowered:
        return "内存不够，没转完。等别的任务结束再试。"
    line = text.splitlines()[-1].strip()
    line = re.sub(r"^(?:[\w.]+(?:Error|Exception|TimeoutExpired)):\s*", "", line)
    line = " ".join(line.split())
    if not line or line.startswith("Traceback"):
        return "转写中途出错了。"
    if len(line) > 80:
        line = line[:80].rstrip() + "…"
    return line


def human_error(status: str, last_error: str | None) -> str:
    """给界面看的一句原因。不出现内部状态名。"""
    text = (last_error or "").strip()
    if status == "skipped":
        match = re.search(r"短于\s*([0-9.]+)\s*秒", text)
        if match:
            return f"这段大约 {match.group(1)} 秒，太短，已经跳过。"
        if text.startswith("早于"):
            return "这段录音早于设定的起始日期，已经跳过。"
        if "没有数据库日期" in text or "undated" in text:
            return "这段录音没有可用的日期，已经跳过。"
        if text:
            return "已经跳过。"
        return "已经跳过。"
    if status == "dead":
        reason = _plain_reason(text)
        return f"试了几次还是没转成。{reason}" if reason else "试了几次还是没转成。"
    if status == "failed":
        reason = _plain_reason(text)
        return f"这次没转成。{reason}" if reason else "这次没转成，可以再试一次。"
    return ""


def hotwords_path(cfg: Config) -> Path:
    """常用热词唯一来源：数据目录的 vocab/hotwords.txt，每次转写都会带上。"""
    return cfg.hotwords_file


def parse_hotwords(value: object) -> list[str]:
    return vocab.parse_hotwords(value)


def read_hotwords(cfg: Config) -> list[str]:
    return vocab.read_hotwords(cfg)


def write_hotwords(cfg: Config, value: object) -> list[str]:
    return vocab.write_hotwords(cfg, value)


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_when(value: str | None) -> str:
    if not value:
        return "—"
    text = value.strip().replace("T", " ")
    text = re.sub(r"(Z|[+-]\d{2}:\d{2})$", "", text).strip()
    return text[:19]


def _recorded_date(value: str | None, tz_name: str) -> date | None:
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        if "T" in text:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
            return parsed.astimezone(ZoneInfo(tz_name)).date()
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def day_group(value: str | None, *, today: date | None = None, tz_name: str = "Asia/Shanghai") -> str:
    """today / yesterday / earlier。没有录音时间的归到更早。"""
    if today is None:
        today = datetime.now(ZoneInfo(tz_name)).date()
    day = _recorded_date(value, tz_name)
    if day is None:
        return "earlier"
    if day == today:
        return "today"
    if day == today - timedelta(days=1):
        return "yesterday"
    return "earlier"


def first_sentence(text: str | None, *, limit: int = 42) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    line = " ".join(raw.splitlines()[0].split())
    match = re.search(r"[。！？!?]", line)
    sentence = line[: match.start()].strip() if match else line
    if not sentence:
        return ""
    if len(sentence) > limit:
        return sentence[:limit].rstrip() + "…"
    return sentence


_ISO_TITLE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:?\d{2})?$")


def display_title(rec: Record, text: str | None, analysis: dict | None = None) -> str:
    if analysis and analysis.get("llm") and str(analysis.get("title") or "").strip():
        return str(analysis["title"]).strip()
    apple = (rec.apple_title or "").strip()
    # 语音备忘录没起名时，数据库里的标题只是一串 UTC 时间，不拿来当标题。
    if apple and not _ISO_TITLE.fullmatch(apple):
        return apple
    sentence = first_sentence(text)
    if sentence:
        return sentence
    name = Path(rec.original_path).name.strip()
    return name or rec.id


def body_preview(text: str | None, *, limit: int = 140) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit].rstrip() + "…"


def search_snippet(text: str | None, query: str, *, radius: int = 48) -> str:
    flat = " ".join((text or "").split())
    needle = query.strip()
    if not flat or not needle:
        return ""
    folded = flat.casefold()
    idx = folded.find(needle.casefold())
    if idx < 0:
        return ""
    start = max(0, idx - radius)
    end = min(len(flat), idx + len(needle) + radius)
    snippet = flat[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(flat):
        snippet = snippet + "…"
    return snippet


def env_override_names() -> list[str]:
    found = []
    if os.environ.get("VOICEINBOX_SINCE"):
        found.append("VOICEINBOX_SINCE")
    if "VOICEINBOX_ASR_LANG" in os.environ:
        found.append("VOICEINBOX_ASR_LANG")
    return found


def parse_byte_range(header: str, size: int) -> tuple[int, int]:
    """返回闭区间。无法满足时抛出 ValueError。"""
    if size <= 0:
        raise ValueError("空文件")
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match:
        raise ValueError("Range 无法识别")
    start_text, end_text = match.group(1), match.group(2)
    if start_text == "" and end_text == "":
        raise ValueError("Range 为空")
    if start_text == "":
        length = int(end_text)
        if length <= 0:
            raise ValueError("Range 长度无效")
        length = min(length, size)
        return size - length, size - 1
    start = int(start_text)
    end = int(end_text) if end_text else size - 1
    if start >= size or start > end:
        raise ValueError("Range 超出文件")
    return start, min(end, size - 1)


def parse_launchctl(code: int, out: str, err: str) -> dict[str, object]:
    info: dict[str, object] = {
        "label": "com.wayne.voiceinbox",
        "loaded": code == 0,
        "state": "未载入" if code != 0 else "未知",
        "state_label": "未载入" if code != 0 else "未知",
        "pid": None,
        "last_exit_code": None,
        "runs": None,
        "program": None,
        "detail": (err or out or "").strip()[:800] if code != 0 else "",
    }
    if code != 0:
        return info

    def grab(pattern: str) -> str | None:
        match = re.search(pattern, out, re.M)
        return match.group(1).strip() if match else None

    state = grab(r"^\s*state = (.+)$")
    if state:
        info["state"] = state
        labels = {"running": "正在跑", "not running": "没在跑"}
        info["state_label"] = labels.get(state, state)
    pid = grab(r"^\s*pid = (\d+)$")
    if pid:
        info["pid"] = int(pid)
    exit_code = grab(r"^\s*last exit code = (.+)$")
    if exit_code is not None:
        info["last_exit_code"] = exit_code
    runs = grab(r"^\s*runs = (\d+)$")
    if runs:
        info["runs"] = int(runs)
    program = grab(r"^\s*program = (.+)$")
    if program:
        info["program"] = program
    return info


def read_launchd_status() -> dict[str, object]:
    """只读。命令固定为 launchctl print，不装载、不卸掉、不重启。"""
    label = f"gui/{os.getuid()}/com.wayne.voiceinbox"
    command = ["launchctl", "print", label]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return parse_launchctl(1, "", str(exc))
    return parse_launchctl(proc.returncode, proc.stdout or "", proc.stderr or "")


def describe_lock(path: Path) -> dict[str, object]:
    """看谁打开了锁文件。不自己去抢锁，避免把 launchd 挤掉。"""
    if not path.exists():
        return {"busy": False, "detail": "还没有锁文件"}
    try:
        proc = subprocess.run(
            ["lsof", "-t", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"busy": None, "detail": f"没能检查锁：{exc}"}
    pids = [int(item) for item in (proc.stdout or "").split() if item.isdigit()]
    if pids:
        shown = ", ".join(str(pid) for pid in pids[:6])
        return {"busy": True, "detail": f"锁被进程 {shown} 占用"}
    return {"busy": False, "detail": "目前没有进程占用锁"}


def tail_text(path: Path, *, max_bytes: int = 12_000, max_lines: int = 80) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            data = handle.read(max_bytes)
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    if size > max_bytes and "\n" in text:
        text = text.split("\n", 1)[1]
    return "\n".join(text.splitlines()[-max_lines:])


def last_scan(logs_dir: Path) -> dict[str, str] | None:
    if not logs_dir.is_dir():
        return None
    files = sorted(logs_dir.glob("voiceinbox-*.log"), reverse=True)
    for path in files[:14]:
        text = tail_text(path, max_bytes=200_000, max_lines=400)
        for line in reversed(text.splitlines()):
            if "本轮结束" in line:
                return {"file": path.name, "line": line.strip()}
    return None


def recent_logs(logs_dir: Path) -> list[dict[str, str]]:
    named: list[Path] = []
    if logs_dir.is_dir():
        daily = sorted(path for path in logs_dir.glob("voiceinbox-*.log") if not path.is_symlink())
        if daily:
            named.append(daily[-1])
    for name in ("launchd.err.log", "launchd.out.log"):
        named.append(logs_dir / name)
    items = []
    seen: set[str] = set()
    for path in named:
        if path.name in seen:
            continue
        seen.add(path.name)
        text = tail_text(path)
        items.append({"name": path.name, "text": text or "（还没有这份日志）"})
    return items


def transcript_text(cfg: Config, rec: Record, *, limit: int = 200_000) -> str | None:
    path = cfg.transcripts_dir / f"{rec.id}.txt"
    try:
        if path.is_symlink() or not path.is_file():
            return None
        resolved = path.resolve()
        resolved.relative_to(cfg.transcripts_dir.resolve())
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except (OSError, ValueError):
        return None


def audio_candidates(cfg: Config, rec: Record) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = [("original", Path(rec.original_path))]
    if rec.archive_path:
        items.append(("archive", Path(rec.archive_path)))
    if rec.staged_path:
        items.append(("staged", Path(rec.staged_path)))
    failed = cfg.failed_dir
    try:
        if failed.is_dir():
            for candidate in sorted(failed.glob(f"{rec.id}.*")):
                if candidate.is_symlink():
                    continue
                if candidate.suffix == ".txt" or candidate.name.endswith(".error.txt"):
                    continue
                items.append(("failed", candidate))
    except OSError:
        pass
    items.append(("work", cfg.work_dir / f"{rec.id}.16k.wav"))
    return items


def path_allowed(cfg: Config, rec: Record, kind: str, path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        resolved = path.resolve()
        root = cfg.root_dir.resolve()
    except OSError:
        return False
    try:
        resolved.relative_to(root)
        return True
    except ValueError:
        pass
    if kind != "original":
        return False
    try:
        return resolved == Path(rec.original_path).resolve()
    except OSError:
        return False


def can_read(path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            return False
        with path.open("rb") as handle:
            handle.read(1)
        return True
    except OSError:
        return False


def find_audio(cfg: Config, rec: Record) -> tuple[str, Path] | None:
    for kind, path in audio_candidates(cfg, rec):
        if path_allowed(cfg, rec, kind, path) and can_read(path):
            return kind, path
    return None


def audio_note(kind: str) -> str:
    if kind == "original":
        return "正在播放语音备忘录原件。只读打开，不会改这个文件。"
    if kind == "work":
        return "原件和副本暂时都读不到，先播放数据目录里的 16kHz 转码。"
    return f"正在播放数据目录里的{AUDIO_KIND_LABEL.get(kind, '副本')}。语音备忘录原件没有被写入。"


def obsidian_uri(path: str | Path | None) -> str:
    """obsidian://open?path=… 让 Obsidian 直接打开这篇笔记。只拼链接，不碰文件。"""
    if not path:
        return ""
    return "obsidian://open?path=" + urllib.parse.quote(str(path), safe="")


def _vault_rel(cfg: Config, path: str) -> str:
    vault = cfg.obsidian_vault
    if not path or not vault:
        return path or ""
    try:
        return str(Path(path).relative_to(Path(vault)))
    except ValueError:
        return path


def summary_public(analysis: dict) -> dict[str, object] | None:
    summary = analysis.get("summary")
    if not isinstance(summary, dict):
        return None
    template = TEMPLATES.get(str(analysis.get("template") or ""))
    labels = {key: label for key, label, _desc in (template.extras if template else ())}
    extras = []
    for key, items in (summary.get("extras") or {}).items():
        if isinstance(items, list) and items:
            extras.append({"label": labels.get(key, key), "items": [str(i) for i in items]})
    mind = summary.get("mindmap") if isinstance(summary.get("mindmap"), dict) else {}
    return {
        "overview": str(summary.get("overview") or ""),
        "segments": [seg for seg in summary.get("segments") or [] if isinstance(seg, dict)],
        "decisions": [str(i) for i in summary.get("decisions") or []],
        "decisions_label": template.decisions_label if template else "决策",
        "extras": extras,
        "mindmap": {"root": str(mind.get("root") or analysis.get("title") or ""), "children": mind.get("children") or []},
    }


def brief_of(analysis: dict, *, limit: int = 90) -> str:
    text = ((analysis.get("summary") or {}).get("overview") or (analysis.get("flash") or {}).get("note") or "")
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _analysis_public(cfg: Config, rec: Record, analysis: dict, done: dict[str, str] | None = None) -> dict[str, object]:
    row = enrich.get_row(cfg, rec.id) or {}
    if done is None:
        done = todos.load_done(cfg)
    flash = analysis.get("flash") or {}
    obsidian_path = analysis.get("obsidian_path") or analysis.get("daily_path") or ""
    if row.get("llm"):
        state_label = "已总结"
    elif row and row.get("last_error"):
        state_label = "总结没成功"
    elif row:
        state_label = "仅转写" if not cfg.llm_enabled else "等待总结"
    else:
        state_label = "还没整理"
    return {
        "route": analysis.get("route") or enrich.route_of(cfg, rec),
        "route_label": "闪念" if (analysis.get("route") or enrich.route_of(cfg, rec)) == "flash" else "完整总结",
        "type": analysis.get("type") or "",
        "title": analysis.get("title") or "",
        "tags": analysis.get("tags") or [],
        "people": analysis.get("people") or [],
        "projects": analysis.get("projects") or [],
        "template": analysis.get("template") or "",
        "llm": bool(analysis.get("llm")),
        "model": analysis.get("model") or "",
        "generated_at": format_when(analysis.get("generated_at")) if analysis.get("generated_at") else "",
        "flash_note": flash.get("note") or "",
        "flash_todos": flash.get("todos") or [],
        "summary_md": enrich.read_summary(cfg, rec.id) if analysis.get("route") == "full" else "",
        "obsidian_path": obsidian_path,
        "obsidian_label": "每日笔记" if analysis.get("daily_path") and not analysis.get("obsidian_path") else "录音笔记",
        "state_label": state_label,
        "error": _plain_reason(row.get("last_error") or "") if row.get("last_error") else "",
        "summary": summary_public(analysis),
        "todos": todos.with_state(rec.id, analysis, done),
        "obsidian_uri": obsidian_uri(obsidian_path),
        "obsidian_rel": _vault_rel(cfg, obsidian_path),
    }


def public_record(
    cfg: Config,
    rec: Record,
    *,
    with_detail: bool = False,
    query: str = "",
    hit: dict | None = None,
    done: dict[str, str] | None = None,
) -> dict[str, object]:
    deletion_pending = rec.status in {"deleting", "delete_failed"}
    raw_text = None if deletion_pending else transcript_text(cfg, rec)
    corrected = enrich.read_corrected(cfg, rec.id) if raw_text is not None else None
    text = corrected if corrected is not None else raw_text
    analysis = enrich.read_analysis(cfg, rec.id) if rec.status == "transcribed" else {}
    preview = body_preview(text)
    if analysis.get("llm"):
        brief = ((analysis.get("summary") or {}).get("overview") or (analysis.get("flash") or {}).get("note") or "")
        if brief:
            preview = body_preview(brief)
    if query.strip():
        snippet = (hit or {}).get("snippet") or search_snippet(text, query)
        if snippet:
            preview = snippet
    name = Path(rec.original_path).name or rec.id
    shown = display_title(rec, text, analysis)
    item: dict[str, object] = {
        "id": rec.id,
        "deletion_pending": deletion_pending,
        "raw_status": rec.status,
        "status_label": label_of(rec.status),
        "group": group_of(rec.status),
        "source": rec.source,
        "source_label": "语音备忘录" if rec.source == "voice_memos" else "手动放入",
        "title": shown,
        "filename": name,
        "display_title": shown,
        "apple_title": rec.apple_title or "",
        "recorded_at": rec.recorded_at or "",
        "recorded_label": format_when(rec.recorded_at),
        "day_group": day_group(rec.recorded_at, tz_name=cfg.timezone),
        "duration_s": rec.duration_s,
        "duration_label": format_duration(rec.duration_s),
        "attempts": rec.attempts,
        "last_error": rec.last_error or "",
        "reason": ("删除未完成。" + _plain_reason(rec.last_error or "请重试删除")) if deletion_pending else human_error(rec.status, rec.last_error),
        "next_retry_at": format_when(rec.next_retry_at) if rec.next_retry_at else "",
        "preview": preview,
        "updated_at": format_when(rec.updated_at),
        "scene": analysis.get("type") or "",
        "tags": analysis.get("tags") or [],
        "route": (analysis.get("route") or enrich.route_of(cfg, rec)) if rec.status == "transcribed" else "",
        "brief": brief_of(analysis) if analysis.get("llm") else "",
        "summarized": bool(analysis.get("llm")),
    }
    if analysis:
        if done is None:
            done = todos.load_done(cfg)
        todo_rows = todos.with_state(rec.id, analysis, done)
        item["todo_total"] = len(todo_rows)
        item["todo_open"] = sum(1 for row in todo_rows if not row["done"])
    else:
        item["todo_total"] = 0
        item["todo_open"] = 0
    if hit:
        at = search.approx_seconds(hit)
        item["hit_field"] = {"transcript": "转写", "summary": "总结", "title": "标题", "tags": "标签"}.get(hit.get("field"), "")
        item["hit_at"] = at
        item["hit_at_label"] = format_duration(at) if at is not None else ""
    if with_detail:
        found = None if deletion_pending else find_audio(cfg, rec)
        item["text"] = text or ""
        item["raw_text"] = raw_text or ""
        item["corrected_differs"] = corrected is not None and raw_text is not None and corrected != raw_text
        item["has_text"] = bool(text and text.strip())
        item["analysis"] = _analysis_public(cfg, rec, analysis, done) if rec.status == "transcribed" else None
        item["has_audio"] = found is not None
        if found is None:
            item["audio_kind"] = ""
            item["audio_label"] = ""
            item["audio_note"] = "没有可读的音频。若原件在语音备忘录目录里，当前这个后端进程可能读不到；有归档副本时会改播副本。"
        else:
            item["audio_kind"] = found[0]
            item["audio_label"] = AUDIO_KIND_LABEL.get(found[0], found[0])
            item["audio_note"] = audio_note(found[0])
    return item


def filter_records(cfg: Config, records: list[Record], *, status: str, query: str) -> list[Record]:
    chosen = records
    if status and status != "all":
        if status in _GROUP_NAMES:
            names = next(names for key, _label, names in GROUPS if key == status)
            chosen = [rec for rec in chosen if rec.status in names]
        elif status in _RAW_STATUS:
            chosen = [rec for rec in chosen if rec.status == status]
        else:
            raise ValueError("不认识的状态筛选")
    needle = query.casefold().strip()
    if not needle:
        return chosen
    matched = []
    for rec in chosen:
        item = public_record(cfg, rec)
        full = transcript_text(cfg, rec) or ""
        haystack = " ".join(
            str(item[key])
            for key in (
                "id",
                "raw_status",
                "status_label",
                "source_label",
                "title",
                "filename",
                "display_title",
                "apple_title",
                "recorded_label",
                "last_error",
                "preview",
            )
        )
        if needle in f"{haystack}\n{full}".casefold():
            matched.append(rec)
    return matched


def list_facets(cfg: Config, records: list[Record], analyses: dict[str, dict]) -> dict[str, object]:
    kinds = {"flash": 0, "full": 0}
    scenes: dict[str, int] = {}
    tags: dict[str, int] = {}
    for rec in records:
        analysis = analyses.get(rec.id) or {}
        route = analysis.get("route") or enrich.route_of(cfg, rec)
        kinds[route] = kinds.get(route, 0) + 1
        scene = analysis.get("type") or ""
        if scene and route == "full":
            scenes[scene] = scenes.get(scene, 0) + 1
        for tag in analysis.get("tags") or []:
            tags[tag] = tags.get(tag, 0) + 1
    top = sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))[:16]
    return {
        "kinds": kinds,
        "scenes": [{"name": name, "count": count} for name, count in sorted(scenes.items(), key=lambda kv: -kv[1])],
        "tags": [{"name": name, "count": count} for name, count in top],
    }


_MANAGED_RE = re.compile(r"<!-- voiceinbox:begin[^>]*-->\n?(.*?)<!-- voiceinbox:end -->", re.S)


def digest_body(text: str) -> str:
    """当日摘要笔记里程序写的那一段，去掉 frontmatter、标题和 wikilink 括号。"""
    found = _MANAGED_RE.search(text)
    body = found.group(1) if found else re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
    lines = [line for line in body.splitlines() if not line.startswith("# ")]
    body = "\n".join(lines).strip()
    body = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", body)
    body = re.sub(r"\[\[([^\]]*)\]\]", r"\1", body)
    return body


def _date_param(query: dict[str, list[str]], name: str) -> str | None:
    value = (query.get(name) or [""])[0].strip()
    if not value:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("日期必须是 YYYY-MM-DD")
    date.fromisoformat(value)
    return value


def _in_range(recorded_at: str | None, date_from: str | None, date_to: str | None, tz_name: str) -> bool:
    if not date_from and not date_to:
        return True
    day = _recorded_date(recorded_at, tz_name)
    if day is None:
        return False
    if date_from and day.isoformat() < date_from:
        return False
    if date_to and day.isoformat() > date_to:
        return False
    return True


def job_light(launchd: dict, lock: dict) -> dict[str, str]:
    loaded = bool(launchd.get("loaded"))
    state = str(launchd.get("state") or "")
    running = state == "running" or bool(launchd.get("pid"))
    busy = lock.get("busy") is True
    if not loaded:
        return {
            "level": "red",
            "title": "后台任务",
            "label": "还没装上",
            "detail": "定时任务没有在运行。录音不会自动转写。",
        }
    if running:
        return {
            "level": "green",
            "title": "后台任务",
            "label": "正在跑",
            "detail": "这一轮正在处理。",
        }
    if busy:
        return {
            "level": "green",
            "title": "后台任务",
            "label": "正在转写",
            "detail": str(lock.get("detail") or "有一轮转写占用着锁。"),
        }
    return {
        "level": "amber",
        "title": "后台任务",
        "label": "这会儿没在跑",
        "detail": "任务已装上。要等下一轮，或有新录音才会再跑。",
    }


_SCAN_TIME_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")


def scan_light(last: dict | None, *, now: datetime | None = None, tz_name: str = "Asia/Shanghai") -> dict[str, str]:
    if now is None:
        now = datetime.now(ZoneInfo(tz_name))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(tz_name))
    if not last or not last.get("line"):
        return {
            "level": "red",
            "title": "上次扫描",
            "label": "还没有",
            "detail": "日志里还没有扫完的记录。",
        }
    line = str(last["line"])
    match = _SCAN_TIME_RE.search(line)
    if not match:
        return {"level": "amber", "title": "上次扫描", "label": "有记录", "detail": line}
    try:
        when = datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}").replace(tzinfo=ZoneInfo(tz_name))
    except ValueError:
        return {"level": "amber", "title": "上次扫描", "label": "有记录", "detail": line}
    age = now - when
    clock = when.strftime("%m-%d %H:%M")
    if age < timedelta(0):
        level, label = "green", "刚刚扫过"
    elif age <= timedelta(minutes=20):
        level, label = "green", "刚刚扫过"
    elif age <= timedelta(hours=6):
        level, label = "amber", "有一会儿了"
    else:
        level, label = "red", "很久没扫"
    return {"level": level, "title": "上次扫描", "label": label, "detail": f"{clock}  {line}"}


def disk_light(code: int, text: str) -> dict[str, str]:
    body = text or ""
    note = "这里看到的权限判断可能和后台任务不一样。"
    blocked = code == 2 or "不能列出语音备忘录" in body or "Operation not permitted" in body
    if blocked:
        return {
            "level": "red",
            "title": "完全磁盘访问",
            "label": "读不到录音",
            "detail": "后台要读语音备忘录，需要给项目专用的 Python 完全磁盘访问。" + note,
        }
    if code == 0:
        if "可以列出语音备忘录目录" in body:
            detail = "目前可以读到语音备忘录目录。" + note
        else:
            detail = "这次检查通过。" + note
        return {"level": "green", "title": "完全磁盘访问", "label": "可以读到", "detail": detail}
    return {
        "level": "amber",
        "title": "完全磁盘访问",
        "label": "还不确定",
        "detail": "检查没做完。" + note,
    }


def counts_of(records: list[Record]) -> dict[str, int]:
    counts = {key: 0 for key, _label, _names in GROUPS}
    counts["all"] = len(records)
    for rec in records:
        counts[group_of(rec.status)] += 1
    return counts


def parse_multipart(content_type: str, body: bytes) -> list[dict[str, object]]:
    match = re.search(r"""boundary="?([^";]+)""", content_type, re.I)
    if not match:
        raise ValueError("缺少 multipart boundary")
    boundary = match.group(1).encode("utf-8", "surrogateescape")
    parts = body.split(b"--" + boundary)
    found = []
    for part in parts[1:]:
        if part.startswith(b"--"):
            break
        if part.startswith(b"\r\n"):
            part = part[2:]
        elif part.startswith(b"\n"):
            part = part[1:]
        header_blob, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            header_blob, sep, data = part.partition(b"\n\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        elif data.endswith(b"\n"):
            data = data[:-1]
        headers = header_blob.decode("utf-8", "replace")
        name_match = re.search(r'name="([^"]*)"', headers, re.I)
        file_match = re.search(r'filename="([^"]*)"', headers, re.I)
        found.append(
            {
                "name": name_match.group(1) if name_match else "",
                "filename": file_match.group(1) if file_match else "",
                "data": data,
            }
        )
    return found


def save_upload(cfg: Config, filename: str, data: bytes) -> Path:
    if not data:
        raise ValueError("文件是空的")
    if len(data) > MAX_UPLOAD:
        raise ValueError("文件超过 200MB")
    base = Path(filename.replace("\\", "/")).name.replace("\x00", "")
    suffix = Path(base).suffix.lower()
    if suffix not in AUDIO_EXTS:
        raise ValueError(f"不支持的音频扩展名：{suffix or '（无）'}")
    stem = Path(base).stem
    stem = re.sub(r"[^\w.\- \u4e00-\u9fff]+", "_", stem, flags=re.UNICODE).strip(" .") or "audio"
    stem = stem[:80]
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    dest = cfg.inbox_dir / f"web-{stamp}-{secrets.token_hex(3)}-{stem}{suffix}"
    try:
        ensure_outside(dest, cfg.voice_memos_dir)
        partial = dest.with_name(dest.name + ".partial")
        ensure_outside(partial, cfg.voice_memos_dir)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        partial.write_bytes(data)
        os.replace(partial, dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return dest


def _cookie_value(header: str | None, name: str) -> str:
    if not header:
        return ""
    for part in header.split(";"):
        piece = part.strip()
        if piece.startswith(name + "="):
            return piece[len(name) + 1 :]
    return ""


def _same_token(left: str, right: str) -> bool:
    if not left or not right:
        return False
    try:
        return secrets.compare_digest(left, right)
    except Exception:
        return False


class WebApp:
    def __init__(self, config_path: str | Path | None = None, *, transcribe_fn=None, doctor_fn=None, lock_fn=None, launchd_fn=None, monitor_runner=None, monitor_plist=None):
        from voiceinbox.config import default_config_path

        self.config_path = Path(config_path) if config_path else default_config_path()
        self.transcribe_fn = transcribe_fn
        self.doctor_fn = doctor_fn or default_doctor
        self.lock_fn = lock_fn or describe_lock
        self.launchd_fn = launchd_fn or read_launchd_status
        self.csrf_token = secrets.token_urlsafe(32)
        if not _TOKEN_RE.fullmatch(self.csrf_token):
            raise RuntimeError("CSRF token 异常")
        self._mu = threading.Lock()
        self._maintenance_mu = threading.Lock()
        self._maintenance_thread: threading.Thread | None = None
        self._maintenance_requested = False
        self._maintenance_stop = threading.Event()
        self._doctor_cache: tuple[float, tuple[int, str]] | None = None
        self._cfg = load_config(self.config_path)
        ensure_dirs(self._cfg)
        kwargs = {"plist_path": monitor_plist} if monitor_plist else {}
        if monitor_runner:
            kwargs["runner"] = monitor_runner
        self.monitor = Monitor(self.config_path, self._cfg.lock_path, self._cfg.logs_dir, self._cfg.backup_dir / "launchd", **kwargs)
        try:
            with FileLock(self._cfg.lock_path) as acquired:
                if acquired:
                    enrich.sync_index(self._cfg, State(self._cfg.db_path))
        except Exception:
            LOG.exception("补搜索索引失败")

    @property
    def cfg(self) -> Config:
        with self._mu:
            return self._cfg

    def reload(self) -> Config:
        cfg = load_config(self.config_path)
        with self._mu:
            self._cfg = cfg
            self._doctor_cache = None
        self.kick_maintenance()
        return cfg

    def kick_maintenance(self) -> None:
        """恢复持久任务；HTTP 响应无需等待模型。锁忙时等待当前流水线结束。"""
        cfg = self.cfg
        if not State(cfg.db_path).pending_deletions() and not digest_queue.pending_days(cfg):
            return
        with self._maintenance_mu:
            if self._maintenance_stop.is_set():
                return
            self._maintenance_requested = True
            if self._maintenance_thread is not None:
                return
            self._maintenance_thread = threading.Thread(target=self._maintain, name="voiceinbox-maintenance", daemon=True)
            self._maintenance_thread.start()

    def _maintain(self) -> None:
        while not self._maintenance_stop.is_set():
            with self._maintenance_mu:
                self._maintenance_requested = False
            cfg = self.cfg
            try:
                with FileLock(cfg.lock_path) as acquired:
                    if not acquired:
                        if self._maintenance_stop.wait(0.25):
                            break
                        continue
                    state = State(cfg.db_path)
                    deletion.resume_deletions_locked(cfg, state)
                    digest_queue.process_pending_locked(cfg, state)
            except Exception:
                LOG.exception("恢复删除或摘要任务失败，保留任务供下次重试")
            with self._maintenance_mu:
                if self._maintenance_requested:
                    continue
                self._maintenance_thread = None
                return
        with self._maintenance_mu:
            self._maintenance_thread = None

    def close(self) -> None:
        self._maintenance_stop.set()
        with self._maintenance_mu:
            thread = self._maintenance_thread
        if thread is not None:
            thread.join(timeout=1)

    def doctor_result(self) -> tuple[int, str]:
        now = time.monotonic()
        with self._mu:
            cached = self._doctor_cache
            if cached and now - cached[0] < 20:
                return cached[1]
        try:
            result = self.doctor_fn(self.cfg)
            code, text = int(result[0]), str(result[1])
        except Exception as exc:
            code, text = 1, f"doctor 无法完成：{exc}\n"
        with self._mu:
            self._doctor_cache = (now, (code, text))
        return code, text


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_close(self) -> None:
        app = getattr(self, "app", None)
        if app is not None:
            app.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "voiceinbox"

    def setup(self):
        super().setup()
        self.app: WebApp = self.server.app  # type: ignore[attr-defined]
        self.bind_port = int(self.server.server_address[1])

    def log_message(self, fmt: str, *args) -> None:
        LOG.info("%s - " + fmt, self.address_string(), *args)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:
        self._send_bytes(405, b"", "text/plain; charset=utf-8")

    def _dispatch(self, method: str) -> None:
        try:
            if not self._host_ok():
                self._send_json(403, {"ok": False, "error": "只接受本机地址"})
                return
            parsed = urllib.parse.urlsplit(self.path)
            route = urllib.parse.unquote(parsed.path)
            if "\\" in route or ".." in route.split("/"):
                self._send_json(404, {"ok": False, "error": "没有这个地址"})
                return
            query = urllib.parse.parse_qs(parsed.query)
            if method == "GET":
                self._get(route, query)
            else:
                self._post(route)
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except KeyError:
            self._send_json(404, {"ok": False, "error": "没有这条记录"})
        except Exception:
            LOG.exception("web 请求失败")
            self._send_json(500, {"ok": False, "error": "服务器内部错误"})

    def _get(self, route: str, query: dict[str, list[str]]) -> None:
        if route == "/favicon.ico":
            self._send_bytes(204, b"", "image/x-icon")
            return
        if route == "/api/health":
            self._send_json(200, {"ok": True, "name": "VoiceInbox"}, cookie=True)
            return
        if not route.startswith("/api/"):
            self._send_json(404, {"ok": False, "error": "没有这个地址"})
            return
        if not self._read_allowed():
            self._send_json(403, {"ok": False, "error": "请从本机应用访问"})
            return
        if route == "/api/recordings":
            self._list(query)
            return
        if route == "/api/system":
            self._system()
            return
        if route == "/api/settings":
            self._settings_get()
            return
        if route == "/api/hotwords":
            self._hotwords_get()
            return
        if route == "/api/replacements":
            self._replacements_get()
            return
        if route == "/api/features":
            self._features_get()
            return
        if route == "/api/today":
            self._today(query)
            return
        if route == "/api/activity":
            self._activity()
            return
        if route == "/api/monitor":
            self._send_json(200, {"ok": True, **self.app.monitor.status()}, cookie=True)
            return
        audio = re.fullmatch(r"/api/recordings/([0-9a-f]{12,64})/audio", route)
        if audio:
            self._audio(audio.group(1))
            return
        detail = re.fullmatch(r"/api/recordings/([0-9a-f]{12,64})", route)
        if detail:
            self._detail(detail.group(1))
            return
        self._send_json(404, {"ok": False, "error": "没有这个地址"})

    def _post(self, route: str) -> None:
        if not self._write_allowed():
            self._send_json(403, {"ok": False, "error": "请求被拒绝。请重新打开应用后再试。"})
            return
        if route == "/api/upload":
            self._upload()
            return
        if route == "/api/retry":
            self._retry_many()
            return
        if route == "/api/recordings/delete":
            self._delete_recordings()
            return
        if route == "/api/settings":
            self._settings_post()
            return
        if route == "/api/hotwords":
            self._hotwords_post()
            return
        if route == "/api/replacements":
            self._replacements_post()
            return
        if route == "/api/replacements/remove":
            self._replacements_remove()
            return
        if route == "/api/features":
            self._features_post()
            return
        if route == "/api/digest":
            self._digest_post()
            return
        if route == "/api/todos":
            self._todos_post()
            return
        if route == "/api/monitor":
            body = self._json_object()
            current = self.app.monitor.status()["desired"]
            from voiceinbox.monitor import write_desired

            write_desired(
                self.app.config_path,
                enabled=body.get("enabled", current["enabled"]),
                interval_min=body.get("interval_min", current["interval_min"]),
                watch=body.get("watch", current["watch"]),
            )
            message = self.app.monitor.apply()
            self._send_json(200, {"ok": True, "message": message, **self.app.monitor.status()})
            return
        if route == "/api/monitor/run":
            self._json_object(allow_empty=True)
            message = self.app.monitor.run_now()
            self._send_json(200, {"ok": True, "message": message, **self.app.monitor.status()})
            return
        summ = re.fullmatch(r"/api/recordings/([0-9a-f]{12,64})/summarize", route)
        if summ:
            self._summarize(summ.group(1))
            return
        retry_one = re.fullmatch(r"/api/recordings/([0-9a-f]{12,64})/retry", route)
        if retry_one:
            self._retry_one(retry_one.group(1))
            return
        again = re.fullmatch(r"/api/recordings/([0-9a-f]{12,64})/retranscribe", route)
        if again:
            self._retranscribe(again.group(1))
            return
        self._send_json(404, {"ok": False, "error": "没有这个地址"})

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.bind_port}", f"localhost:{self.bind_port}"}
        return host in allowed

    def _origin_values(self) -> set[str]:
        return {f"http://127.0.0.1:{self.bind_port}", f"http://localhost:{self.bind_port}"}

    def _origin_ok(self, *, require: bool) -> bool:
        allowed = self._origin_values()
        origin = self.headers.get("Origin")
        if origin:
            if origin.rstrip("/") not in allowed:
                return False
        elif require:
            return False
        referer = self.headers.get("Referer")
        if referer:
            bare = referer.split("?", 1)[0].split("#", 1)[0].rstrip("/")
            if not any(bare == item or bare.startswith(item + "/") for item in allowed):
                return False
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in {"same-origin", "none"}:
            return False
        return True

    def _read_allowed(self) -> bool:
        if not self._origin_ok(require=False):
            return False
        cookie = _cookie_value(self.headers.get("Cookie"), "voiceinbox_csrf")
        return _same_token(cookie, self.app.csrf_token)

    def _write_allowed(self) -> bool:
        if not self._origin_ok(require=True):
            return False
        cookie = _cookie_value(self.headers.get("Cookie"), "voiceinbox_csrf")
        header = self.headers.get("X-CSRF-Token", "")
        token = self.app.csrf_token
        return _same_token(cookie, token) and _same_token(header, token)

    def _cookie_header(self) -> str:
        token = self.app.csrf_token
        return f"voiceinbox_csrf={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; media-src 'self'; connect-src 'self'; "
            "font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")

    def _send_bytes(self, code: int, body: bytes, content_type: str, *, cookie: bool = False, extra: list[tuple[str, str]] | None = None) -> None:
        self.send_response(code)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", self._cookie_header())
        for key, value in extra or []:
            self.send_header(key, value)
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

    def _send_json(self, code: int, payload: dict, *, cookie: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(code, body, "application/json; charset=utf-8", cookie=cookie)

    def _read_body(self, limit: int) -> bytes:
        length = self.headers.get("Content-Length")
        if length is None:
            raise ValueError("缺少 Content-Length")
        try:
            size = int(length)
        except ValueError as exc:
            raise ValueError("Content-Length 无效") from exc
        if size < 0 or size > limit:
            raise ValueError("请求体过大")
        data = self.rfile.read(size) if size else b""
        if len(data) != size:
            raise ValueError("请求体不完整")
        return data

    def _json_object(self, *, allow_empty: bool = False) -> dict:
        raw = self._read_body(MAX_JSON)
        if not raw:
            if allow_empty:
                return {}
            raise ValueError("需要 JSON 对象")
        ctype = self.headers.get("Content-Type", "")
        if "application/json" not in ctype.lower():
            raise ValueError("需要 application/json")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON 无法解析") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON 必须是对象")
        return data

    def _list(self, query: dict[str, list[str]]) -> None:
        status = (query.get("status") or ["all"])[0].strip() or "all"
        text = (query.get("q") or [""])[0]
        if len(text) > 200:
            raise ValueError("搜索词过长")
        date_from = _date_param(query, "from")
        date_to = _date_param(query, "to")
        cfg = self.app.cfg
        records = State(cfg.db_path).all_records()
        done = todos.load_done(cfg)
        hits: dict[str, dict] = {}
        if text.strip():
            try:
                for hit in search.search(cfg.search_db_path, text, date_from=date_from, date_to=date_to, limit=200):
                    hits.setdefault(hit["id"], hit)
            except Exception:
                LOG.exception("全文索引查询失败，退回逐条匹配")
        in_range = [rec for rec in records if _in_range(rec.recorded_at, date_from, date_to, cfg.timezone)]
        chosen = filter_records(cfg, in_range, status=status, query="")
        kind = (query.get("kind") or [""])[0].strip()
        scene = (query.get("scene") or [""])[0].strip()
        tag = (query.get("tag") or [""])[0].strip()
        if kind not in ("", "flash", "full"):
            raise ValueError("kind 只能是 flash 或 full")
        analyses = {rec.id: enrich.read_analysis(cfg, rec.id) for rec in records if rec.status == "transcribed"}
        facets = list_facets(cfg, [rec for rec in in_range if rec.id in analyses], analyses)
        if kind:
            chosen = [rec for rec in chosen if rec.id in analyses and (analyses[rec.id].get("route") or enrich.route_of(cfg, rec)) == kind]
        if scene:
            chosen = [rec for rec in chosen if (analyses.get(rec.id) or {}).get("type") == scene]
        if tag:
            chosen = [rec for rec in chosen if tag in ((analyses.get(rec.id) or {}).get("tags") or [])]
        if text.strip():
            chosen = [rec for rec in chosen if rec.status not in {"deleting", "delete_failed"}]
            plain = {rec.id for rec in filter_records(cfg, chosen, status="all", query=text)}
            chosen = [rec for rec in chosen if rec.id in hits or rec.id in plain]
            order = {rec_id: index for index, rec_id in enumerate(hits)}
            chosen.sort(key=lambda rec: order.get(rec.id, len(order)))
        self._send_json(
            200,
            {
                "ok": True,
                "counts": counts_of(records),
                "items": [public_record(cfg, rec, query=text, hit=hits.get(rec.id), done=done) for rec in chosen],
                "facets": facets,
                "disclaimer": DISCLAIMER,
                "search": {"engine": "fts5", "from": date_from or "", "to": date_to or ""} if text.strip() else None,
            },
            cookie=True,
        )

    def _detail(self, rec_id: str) -> None:
        if not _ID_RE.fullmatch(rec_id):
            self._send_json(404, {"ok": False, "error": "没有这条记录"})
            return
        cfg = self.app.cfg
        rec = State(cfg.db_path).get(rec_id)
        if rec is None:
            raise KeyError(rec_id)
        self._send_json(200, {"ok": True, "item": public_record(cfg, rec, with_detail=True)}, cookie=True)

    def _audio(self, rec_id: str) -> None:
        if not _ID_RE.fullmatch(rec_id):
            self._send_json(404, {"ok": False, "error": "没有这条记录"})
            return
        cfg = self.app.cfg
        rec = State(cfg.db_path).get(rec_id)
        if rec is None:
            raise KeyError(rec_id)
        if rec.status in {"deleting", "delete_failed"}:
            self._send_json(404, {"ok": False, "error": "这条录音删除未完成，无法播放"})
            return
        found = find_audio(cfg, rec)
        if found is None:
            self._send_json(404, {"ok": False, "error": "没有可读的音频"})
            return
        kind, path = found
        try:
            size = path.stat().st_size
            handle = path.open("rb")
        except OSError:
            self._send_json(404, {"ok": False, "error": "音频读不了"})
            return
        try:
            range_header = self.headers.get("Range")
            if range_header:
                try:
                    start, end = parse_byte_range(range_header, size)
                except ValueError:
                    self.send_response(416)
                    self._security_headers()
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()
                    return
                code = 206
                length = end - start + 1
                extra = [("Content-Range", f"bytes {start}-{end}/{size}")]
            else:
                start = 0
                length = size
                code = 200
                extra = []
            mime = AUDIO_MIME.get(path.suffix.lower(), "application/octet-stream")
            filename = re.sub(r"[^A-Za-z0-9._-]", "_", path.name).strip("._") or "audio"
            self.send_response(code)
            self._security_headers()
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Disposition", f'inline; filename="{filename[:80]}"')
            for key, value in extra:
                self.send_header(key, value)
            self.end_headers()
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            handle.close()

    def _system(self) -> None:
        cfg = self.app.cfg
        code, text = self.app.doctor_result()
        try:
            launchd = self.app.launchd_fn()
        except Exception as exc:
            launchd = parse_launchctl(1, "", str(exc))
        try:
            lock = self.app.lock_fn(cfg.lock_path)
        except Exception as exc:
            lock = {"busy": None, "detail": str(exc)}
        launchd_info = launchd if isinstance(launchd, dict) else {}
        lock_info = lock if isinstance(lock, dict) else {"busy": None, "detail": str(lock)}
        scan = last_scan(cfg.logs_dir)
        self._send_json(
            200,
            {
                "ok": True,
                "disclaimer": DISCLAIMER,
                "doctor": {"code": code, "text": text},
                "launchd": launchd,
                "lock": lock,
                "last_scan": scan,
                "lights": [
                    job_light(launchd_info, lock_info),
                    scan_light(scan, tz_name=cfg.timezone),
                    disk_light(code, text),
                ],
                "logs": recent_logs(cfg.logs_dir),
                "data_dir": str(cfg.root_dir),
                "log_dir": str(cfg.logs_dir),
            },
            cookie=True,
        )

    def _settings_get(self) -> None:
        cfg = self.app.cfg
        self._send_json(
            200,
            {
                "ok": True,
                "file": read_common_settings(self.app.config_path),
                "effective": {
                    "since": "" if cfg.since is None else cfg.since.isoformat(),
                    "max_per_run": cfg.max_per_run,
                    "language": cfg.asr_language,
                },
                "env_overrides": env_override_names(),
                "note": SETTINGS_NOTE,
            },
            cookie=True,
        )

    def _settings_post(self) -> None:
        body = self._json_object()
        if "since" not in body or "max_per_run" not in body or "language" not in body:
            raise ValueError("需要 since、max_per_run 和 language")
        saved = update_common_settings(
            self.app.config_path,
            since=body["since"],
            max_per_run=body["max_per_run"],
            language=body["language"],
        )
        cfg = self.app.reload()
        self._send_json(
            200,
            {
                "ok": True,
                "file": saved,
                "effective": {
                    "since": "" if cfg.since is None else cfg.since.isoformat(),
                    "max_per_run": cfg.max_per_run,
                    "language": cfg.asr_language,
                },
                "env_overrides": env_override_names(),
            },
            cookie=True,
        )

    def _hotwords_get(self) -> None:
        words = read_hotwords(self.app.cfg)
        self._send_json(200, {"ok": True, "words": words, "text": "\n".join(words)}, cookie=True)

    def _hotwords_post(self) -> None:
        body = self._json_object()
        if "text" in body:
            value = body["text"]
        elif "words" in body:
            value = body["words"]
        else:
            raise ValueError("需要热词文本")
        words = write_hotwords(self.app.cfg, value)
        self._send_json(200, {"ok": True, "words": words, "text": "\n".join(words)})

    def _replacements_get(self) -> None:
        rules = vocab.read_replacements(self.app.cfg)
        self._send_json(200, {"ok": True, "rules": [{"wrong": w, "right": r} for w, r in rules.items()]}, cookie=True)

    def _replacements_post(self) -> None:
        body = self._json_object()
        rules = vocab.add_replacement(self.app.cfg, body.get("wrong"), body.get("right"))
        target = body.get("apply_to")
        applied = 0
        locked = False
        if target is not None:
            if target != "all" and not (isinstance(target, str) and _ID_RE.fullmatch(target)):
                raise ValueError("apply_to 只能是记录 id 或 all")
            try:
                applied = enrich.reapply_corrections(self.app.cfg, None if target == "all" else [target])
            except enrich.Locked:
                locked = True
        self._send_json(
            200,
            {
                "ok": True,
                "rules": [{"wrong": w, "right": r} for w, r in rules.items()],
                "applied": applied,
                "locked": locked,
                "message": "已保存。另一轮正在跑，这条稍后会在下次整理时更新。" if locked else "",
            },
        )

    def _replacements_remove(self) -> None:
        body = self._json_object()
        rules = vocab.remove_replacement(self.app.cfg, body.get("wrong"))
        self._send_json(200, {"ok": True, "rules": [{"wrong": w, "right": r} for w, r in rules.items()]})

    def _features_payload(self) -> dict[str, object]:
        cfg = self.app.cfg
        return {
            "ok": True,
            "file": read_feature_settings(self.app.config_path),
            "model": cfg.llm_model,
            "llm_url": cfg.llm_base_url,
            "vault": str(cfg.obsidian_root or ""),
            "digest_time": cfg.digest_time,
            "templates": list(TEMPLATES),
            "scenes": list(SCENES),
        }

    def _features_get(self) -> None:
        self._send_json(200, self._features_payload(), cookie=True)

    def _features_post(self) -> None:
        body = self._json_object()
        for key in ("llm_enabled", "obsidian_enabled", "flash_max_s"):
            if key not in body:
                raise ValueError("需要 llm_enabled、obsidian_enabled 和 flash_max_s")
        update_feature_settings(
            self.app.config_path,
            llm_enabled=body["llm_enabled"],
            obsidian_enabled=body["obsidian_enabled"],
            flash_max_s=body["flash_max_s"],
            digest_enabled=body.get("digest_enabled"),
        )
        self.app.reload()
        self._send_json(200, self._features_payload())

    def _delete_recordings(self) -> None:
        body = self._json_object()
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(rec_id, str) or not _ID_RE.fullmatch(rec_id) for rec_id in ids):
            raise ValueError("ids 必须是非空的录音 id 数组")
        try:
            result = deletion.delete_recordings(self.app.cfg, ids)
        except enrich.Locked:
            self._send_json(409, {"ok": False, "locked": True, "error": "另一轮转写、总结或删除正在进行，这次没有开始。"})
            return
        self._send_json(200, result)
        self.app.kick_maintenance()

    def _today(self, query: dict[str, list[str]]) -> None:
        cfg = self.app.cfg
        tz = ZoneInfo(cfg.timezone)
        day = _date_param(query, "date") or datetime.now(tz).strftime("%Y-%m-%d")
        state = State(cfg.db_path)
        done = todos.load_done(cfg)
        records = [rec for rec in state.all_records() if rec.status == "transcribed"]
        records.sort(key=lambda rec: rec.recorded_at or "", reverse=True)
        days: dict[str, int] = {}
        items = []
        open_todos = []
        for rec in records:
            when = _recorded_date(rec.recorded_at, cfg.timezone)
            key = when.isoformat() if when else ""
            if key:
                days[key] = days.get(key, 0) + 1
            analysis = enrich.read_analysis(cfg, rec.id)
            title = display_title(rec, None, analysis)
            for row in todos.with_state(rec.id, analysis, done):
                if not row["done"] or key == day:
                    open_todos.append({**row, "id": rec.id, "title": title, "date": key, "when": format_when(rec.recorded_at)})
            if key == day:
                items.append(public_record(cfg, rec, done=done))
        digest = {"md": "", "path": "", "uri": "", "updated": "", **digest_queue.digest_status(cfg, state, day)}
        try:
            with enrich._db(cfg) as conn:
                row = conn.execute("SELECT path, updated_at FROM digests WHERE day = ?", (day,)).fetchone()
        except Exception:
            row = None
        if digest["state"] == "ready" and row and row["path"]:
            path = Path(row["path"])
            try:
                if path.is_file() and not path.is_symlink():
                    digest = {
                        "md": digest_body(path.read_text(encoding="utf-8")),
                        "path": _vault_rel(cfg, str(path)),
                        "uri": obsidian_uri(path),
                        "updated": format_when(row["updated_at"]),
                        "state": "ready",
                        "error": "",
                    }
            except OSError:
                pass
        today = datetime.now(tz).date()
        # 历史摘要重建失败时，也必须能从日期菜单进入重试。
        day_choices = dict(sorted(days.items(), reverse=True)[:30])
        for queued_day in digest_queue.pending_days(cfg):
            day_choices[queued_day] = days.get(queued_day, 0)
        for task in state.pending_deletions():
            queued_day = (task.get("manifest") or {}).get("day")
            if queued_day:
                day_choices[queued_day] = days.get(queued_day, 0)
        self._send_json(
            200,
            {
                "ok": True,
                "date": day,
                "is_today": day == today.isoformat(),
                "digest": digest,
                "digest_ready": bool(cfg.llm_enabled and cfg.obsidian_enabled),
                "digest_time": cfg.digest_time,
                "items": items,
                "todos": open_todos[:200],
                "days": [{"date": key, "count": count} for key, count in sorted(day_choices.items(), reverse=True)],
            },
            cookie=True,
        )

    def _todos_post(self) -> None:
        body = self._json_object()
        rec_id = body.get("id")
        key = body.get("key")
        done = body.get("done")
        if not isinstance(rec_id, str) or not _ID_RE.fullmatch(rec_id):
            raise ValueError("录音 id 不对")
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{12}", key):
            raise ValueError("待办 key 不对")
        if not isinstance(done, bool):
            raise ValueError("done 必须是 true 或 false")
        cfg = self.app.cfg
        with FileLock(cfg.lock_path) as acquired:
            if not acquired:
                self._send_json(409, {"ok": False, "locked": True, "error": "另一轮正在进行，这次没有修改待办。"})
                return
            rec = State(cfg.db_path).get(rec_id)
            if rec is None:
                raise KeyError(rec_id)
            if rec.status in {"deleting", "delete_failed"}:
                raise ValueError("这条录音删除未完成，请先重试删除")
            known = {todos.todo_key(text) for text in todos.todos_of(enrich.read_analysis(cfg, rec_id))}
            if key not in known:
                raise ValueError("这条录音里没有这个待办")
            todos.set_done(cfg, rec_id, key, done)
        self._send_json(200, {"ok": True, "id": rec_id, "key": key, "done": done})

    def _activity(self) -> None:
        """轻量的后台状态：锁有没有人占、哪条在转写、还有几条排队或等着总结。"""
        cfg = self.app.cfg
        records = State(cfg.db_path).all_records()
        try:
            lock = self.app.lock_fn(cfg.lock_path)
        except Exception as exc:
            lock = {"busy": None, "detail": str(exc)}
        processing = [rec for rec in records if rec.status == "processing"]
        pending = [rec for rec in records if group_of(rec.status) == "pending"]
        waiting = []
        digest_days = digest_queue.pending_days(cfg)
        if cfg.llm_enabled or cfg.obsidian_enabled:
            for rec in records:
                if rec.status == "transcribed":
                    try:
                        if enrich.needs_work(cfg, rec):
                            waiting.append(rec)
                    except Exception:
                        continue
        def title(rec: Record) -> str:
            return display_title(rec, None, enrich.read_analysis(cfg, rec.id))
        if processing:
            phase, text = "transcribing", "正在转写：" + "、".join(title(rec) for rec in processing[:2])
        elif lock.get("busy") and waiting:
            phase, text = "summarizing", "正在整理：" + title(waiting[0])
        elif lock.get("busy") and digest_days:
            phase, text = "summarizing", "正在更新删除后的摘要"
        elif lock.get("busy"):
            phase, text = "busy", "后台正在跑一轮"
        elif pending or waiting or digest_days:
            bits = []
            if pending:
                bits.append(f"{len(pending)} 条等转写")
            if waiting:
                bits.append(f"{len(waiting)} 条等整理")
            if digest_days:
                bits.append(f"{len(digest_days)} 天摘要待更新")
            phase, text = "queued", "，".join(bits) + "，下一轮处理"
        else:
            phase, text = "idle", "都处理完了"
        self._send_json(
            200,
            {
                "ok": True,
                "phase": phase,
                "text": text,
                "busy": bool(lock.get("busy")),
                "processing": [rec.id for rec in processing],
                "pending": len(pending),
                "waiting": len(waiting),
            },
            cookie=True,
        )

    def _summarize(self, rec_id: str) -> None:
        body = self._json_object(allow_empty=True)
        template = body.get("template", "auto")
        if not isinstance(template, str):
            raise ValueError("模板必须是文本")
        try:
            result = enrich.regenerate(self.app.cfg, rec_id, template=template)
        except enrich.Locked:
            self._send_json(409, {"ok": False, "locked": True, "error": "另一轮转写或总结正在进行，这次没有开始。"})
            return
        except LlmError as exc:
            self._send_json(502, {"ok": False, "error": f"本机总结没成功：{_plain_reason(str(exc))}"})
            return
        self._send_json(200, {"ok": True, "id": rec_id, "title": result.get("title"), "template": result.get("template")})

    def _digest_post(self) -> None:
        body = self._json_object(allow_empty=True)
        day = body.get("date") or datetime.now(ZoneInfo(self.app.cfg.timezone)).strftime("%Y-%m-%d")
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("日期必须是 YYYY-MM-DD")
        cfg = self.app.cfg
        if not (cfg.llm_enabled and cfg.obsidian_enabled):
            raise ValueError("要先打开本机总结和写入 Obsidian")
        try:
            path = enrich.digest_now(cfg, day)
        except enrich.Locked:
            self._send_json(409, {"ok": False, "locked": True, "error": "另一轮正在进行，这次没有开始。"})
            return
        except LlmError as exc:
            self._send_json(502, {"ok": False, "error": f"本机总结没成功：{_plain_reason(str(exc))}"})
            return
        self._send_json(200, {"ok": True, "path": str(path) if path else "", "message": "" if path else f"{day} 没有可以汇总的录音"})

    def _retranscribe(self, rec_id: str) -> None:
        body = self._json_object(allow_empty=True)
        context = body.get("context", "")
        hotwords = body.get("hotwords", "")
        if not isinstance(context, str) or not isinstance(hotwords, str):
            raise ValueError("背景说明和热词必须是字符串")
        stats, rec = retranscribe(
            self.app.cfg,
            rec_id,
            context=context,
            hotwords=hotwords,
            transcribe_fn=self.app.transcribe_fn,
        )
        self._job(stats, rec)

    def _retry_one(self, rec_id: str) -> None:
        if self.headers.get("Content-Length") not in (None, "0"):
            self._read_body(MAX_JSON)
        try:
            stats = retry(self.app.cfg, rec_id, transcribe_fn=self.app.transcribe_fn)
        except KeyError:
            raise
        rec = State(self.app.cfg.db_path).get(rec_id) if not stats.locked else None
        self._job(stats, rec)

    def _retry_many(self) -> None:
        body = self._json_object()
        scope = body.get("scope")
        scopes = {"failed": {"failed"}, "dead": {"dead"}, "failed-and-dead": {"failed", "dead"}}
        if scope not in scopes:
            raise ValueError("scope 只能是 failed、dead 或 failed-and-dead")
        stats = retry_statuses(self.app.cfg, scopes[scope], transcribe_fn=self.app.transcribe_fn)
        if stats.locked:
            self._send_json(409, {"ok": False, "locked": True, "error": LOCKED_MESSAGE})
            return
        self._send_json(
            200,
            {
                "ok": stats.failed == 0,
                "locked": False,
                "transcribed": stats.transcribed,
                "failed": stats.failed,
                "dead": stats.dead,
                "skipped": stats.skipped,
                "message": "没有需要重试的记录" if stats.transcribed == stats.failed == stats.dead == stats.skipped == 0 else "",
                "error": "" if stats.failed == 0 else "本轮仍有失败",
            },
        )

    def _upload(self) -> None:
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype.lower():
            raise ValueError("需要 multipart/form-data")
        body = self._read_body(MAX_UPLOAD)
        parts = parse_multipart(ctype, body)
        chosen = next((part for part in parts if part.get("filename")), None)
        if chosen is None:
            raise ValueError("没有上传文件")
        data = chosen["data"]
        if not isinstance(data, bytes):
            raise ValueError("文件内容无效")
        saved = save_upload(self.app.cfg, str(chosen.get("filename") or ""), data)
        try:
            relative = str(saved.relative_to(self.app.cfg.root_dir))
        except ValueError:
            relative = saved.name
        try:
            stats, rec = process_path(self.app.cfg, saved, transcribe_fn=self.app.transcribe_fn)
        except (OSError, ValueError, FileNotFoundError, RuntimeError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc), "saved_to": relative})
            return
        if stats.locked:
            self._send_json(
                409,
                {
                    "ok": False,
                    "locked": True,
                    "saved_to": relative,
                    "error": "文件已放到数据目录的 inbox。锁被占用，这次没有开始转写。定时任务下一轮会看到它。",
                },
            )
            return
        payload = self._job_payload(stats, rec)
        payload["saved_to"] = relative
        code = 200 if rec is not None else 500
        self._send_json(code, payload)

    def _job(self, stats, rec: Record | None) -> None:
        if stats.locked:
            self._send_json(409, {"ok": False, "locked": True, "error": LOCKED_MESSAGE})
            return
        self._send_json(200 if rec is not None else 500, self._job_payload(stats, rec))

    def _job_payload(self, stats, rec: Record | None) -> dict[str, object]:
        if rec is None:
            return {"ok": False, "locked": False, "error": "没有产生记录"}
        finished = rec.status in {"transcribed", "skipped"}
        return {
            "ok": finished and stats.failed == 0,
            "locked": False,
            "id": rec.id,
            "status": rec.status,
            "status_label": label_of(rec.status),
            "reason": human_error(rec.status, rec.last_error),
            "error": rec.last_error or "",
        }


def build_server(app: WebApp, host: str = DEFAULT_HOST, port: int = 0) -> WebServer:
    if host != DEFAULT_HOST:
        raise ValueError("只允许绑定 127.0.0.1")
    install_signal_handlers()
    server = WebServer((host, port), Handler)
    server.app = app  # type: ignore[attr-defined]
    app.kick_maintenance()
    return server


def serve_forever(config_path: str | None = None, host: str | None = None, port: object | None = None) -> int:
    try:
        bind_host, bind_port = resolve_bind(host, port)
    except ValueError as exc:
        print(f"无法启动：{exc}", file=sys.stderr)
        return 2
    try:
        app = WebApp(config_path)
    except Exception as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1
    try:
        server = build_server(app, bind_host, bind_port)
    except OSError as exc:
        print(f"无法监听 {bind_host}:{bind_port}：{exc}", file=sys.stderr)
        return 1
    actual = server.server_address[1]
    print(f"本机接口：http://127.0.0.1:{actual}/api/health")
    print("只监听 127.0.0.1。界面请打开 VoiceInbox 桌面应用。按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0
