"""写进 Obsidian 库里的专用子目录（默认 <库>/语音备忘录/）。

- 每条完整录音一篇：<子目录>/YYYY/MM/YYYY-MM-DD HHMM 标题.md
- 闪念追加到 <子目录>/每日/YYYY-MM-DD.md，每条一个受管块
- 当日摘要：<子目录>/每日/YYYY-MM-DD 当日摘要.md

幂等：只替换 <!-- voiceinbox:... --> 标记之间的内容。标记之外用户写的东西、
frontmatter 里用户自己加的键都保留。改动已有文件前先备份到 ~/VoiceInbox/backups/obsidian/。
不碰用户自己的日记目录。
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from datetime import datetime
from pathlib import Path

from voiceinbox.config import Config
from voiceinbox.log import LOG
from voiceinbox.vocab import backup_file

BEGIN = "<!-- voiceinbox:begin 这一段由语音备忘录程序维护，重新生成时会被替换；在这段之外写的内容会保留 -->"
END = "<!-- voiceinbox:end -->"
_BLOCK_RE = re.compile(r"<!-- voiceinbox:begin.*?-->.*?<!-- voiceinbox:end -->", re.S)
_FLASH_RE_T = r"<!-- voiceinbox:flash {id} (\d{{4}}) begin -->.*?<!-- voiceinbox:flash {id} end -->\n?"
_ANY_FLASH_RE = re.compile(r"<!-- voiceinbox:flash ([0-9a-f]{12,64}) (\d{4}) begin -->")
DAILY_DIR = "每日"
_FILE_BAD = re.compile(r'[\\/:*?"<>|#^\[\]\n\r\t]+')

MANAGED_KEYS = ("id", "date", "duration", "duration_s", "type", "title", "tags", "people", "project", "audio", "status", "template", "source")


class ObsidianError(RuntimeError):
    pass


def vault_ok(cfg: Config) -> Path:
    if cfg.obsidian_vault is None:
        raise ObsidianError("没有设置 Obsidian 库路径")
    vault = cfg.obsidian_vault
    if not (vault / ".obsidian").is_dir():
        raise ObsidianError(f"这不是 Obsidian 库（没有 .obsidian）：{vault}")
    root = cfg.obsidian_root
    assert root is not None
    root.mkdir(parents=True, exist_ok=True)
    return root


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def safe_filename(text: str, limit: int = 48) -> str:
    cleaned = _FILE_BAD.sub(" ", text or "")
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned[:limit].rstrip(" .") or "录音"


def yaml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '""'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(json.dumps(str(item), ensure_ascii=False) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def split_frontmatter(text: str) -> tuple[list[tuple[str, list[str]]], str]:
    """返回 [(键, 原始行...)] 和正文。没有 frontmatter 时列表为空。"""
    if not text.startswith("---\n"):
        return [], text
    end = text.find("\n---\n", 4)
    if end < 0:
        if text.endswith("\n---"):
            end = len(text) - 4
        else:
            return [], text
    head = text[4:end]
    body = text[end + 5 :]
    entries: list[tuple[str, list[str]]] = []
    for line in head.split("\n"):
        match = re.match(r"^([^\s#:][^:]*):(?:\s|$)", line)
        if match and not line.startswith((" ", "\t", "-")):
            entries.append((match.group(1).strip(), [line]))
        elif entries:
            entries[-1][1].append(line)
        elif line.strip():
            entries.append(("", [line]))
    return entries, body


def merge_frontmatter(existing: list[tuple[str, list[str]]], managed: dict[str, object]) -> str:
    lines = [f"{key}: {yaml_value(value)}" for key, value in managed.items()]
    for key, raw in existing:
        if key in managed:
            continue
        lines.extend(raw)
    while lines and not lines[-1].strip():
        lines.pop()
    return "---\n" + "\n".join(lines) + "\n---\n"


def replace_block(body: str, block: str) -> str:
    managed = f"{BEGIN}\n{block.rstrip()}\n{END}"
    if _BLOCK_RE.search(body):
        return _BLOCK_RE.sub(lambda _m: managed, body, count=1)
    body = body.rstrip("\n")
    return (body + "\n\n" if body.strip() else "\n") + managed + "\n"


def write_if_changed(cfg: Config, path: Path, text: str) -> bool:
    if path.is_symlink():
        raise ObsidianError(f"拒绝写入符号链接：{path}")
    old = None
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if old == text:
            return False
        backup_file(cfg, path, label="obsidian")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name("." + path.name + ".partial")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)
    return True


def wikilinks(names: list[str]) -> list[str]:
    return [f"[[{name}]]" for name in names if name]


def file_url(path: str | None) -> str:
    if not path:
        return ""
    return "file://" + urllib.parse.quote(path)


def _when(info: dict) -> datetime:
    raw = info.get("recorded_at") or ""
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return datetime.now()


def find_note_by_id(root: Path, rec_id: str) -> Path | None:
    needle = f'id: "{rec_id}"'
    for path in root.rglob("*.md"):
        if path.is_symlink() or DAILY_DIR in path.relative_to(root).parts[:1]:
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                head = handle.read(600)
        except OSError:
            continue
        if needle in head:
            return path
    return None


def note_path_for(cfg: Config, info: dict, previous: str | None) -> Path:
    root = vault_ok(cfg)
    if previous:
        old = Path(previous)
        if old.is_file() and _inside(old, root):
            return old
    found = find_note_by_id(root, info["id"])
    if found is not None:
        return found
    when = _when(info)
    stem = f"{when:%Y-%m-%d %H%M} {safe_filename(info.get('title') or info['id'])}"
    folder = root / f"{when:%Y}" / f"{when:%m}"
    path = folder / f"{stem}.md"
    if path.exists():
        path = folder / f"{stem} {info['id'][:6]}.md"
    if not _inside(path, root):
        raise ObsidianError("笔记路径跑出了 Obsidian 子目录")
    return path


def _quote_callout(title: str, text: str) -> str:
    lines = [f"> [!quote]- {title}"]
    for line in (text or "").strip().splitlines() or ["（空）"]:
        lines.append("> " + line if line.strip() else ">")
    return "\n".join(lines)


def _digest_link(cfg: Config, when: datetime) -> str:
    folder = cfg.obsidian_folder.strip().strip("/")
    return f"[[{folder}/{DAILY_DIR}/{when:%Y-%m-%d} 当日摘要|{when:%Y-%m-%d} 当日摘要]]"


def render_note_block(cfg: Config, info: dict, analysis: dict, summary_md: str, transcript: str) -> str:
    when = _when(info)
    title = analysis.get("title") or info.get("title") or info["id"]
    bits = [f"录音 {when:%Y-%m-%d %H:%M}", info.get("duration_label") or "", analysis.get("type") or ""]
    audio = info.get("audio_path")
    head = [f"# {title}", "", "> [!info] " + " · ".join(b for b in bits if b)]
    if audio:
        head.append(f"> 音频：[打开原音频](<{file_url(audio)}>)")
    links = []
    if analysis.get("people"):
        links.append("人物：" + " ".join(wikilinks(analysis["people"])))
    if analysis.get("projects"):
        links.append("项目：" + " ".join(wikilinks(analysis["projects"])))
    if links:
        head.append("> " + "　".join(links))
    head.append(f"> 当天：{_digest_link(cfg, when)}")
    if not analysis.get("llm"):
        summary_md = "## 概要\n（本机总结没有打开，或还没生成。下面是完整转写。）\n"
    body = "\n".join(head) + "\n\n" + summary_md.strip() + "\n\n"
    body += _quote_callout(f"完整转写（{info.get('char_count', len(transcript.strip()))} 字，已按纠错表替换；原始转写 transcripts/{info['id']}.txt）", transcript)
    return body


def write_recording_note(cfg: Config, info: dict, analysis: dict, summary_md: str, transcript: str) -> Path:
    named = {**info, "title": analysis.get("title") or info.get("title") or ""}
    path = note_path_for(cfg, named, analysis.get("obsidian_path"))
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    entries, body = split_frontmatter(old)
    managed = {
        "id": info["id"],
        "date": info.get("recorded_at") or "",
        "duration": info.get("duration_label") or "",
        "duration_s": round(float(info.get("duration_s") or 0), 1),
        "type": analysis.get("type") or "其他",
        "title": analysis.get("title") or "",
        "tags": ["语音备忘录"] + [t for t in analysis.get("tags") or [] if t != "语音备忘录"],
        "people": wikilinks(analysis.get("people") or []),
        "project": wikilinks(analysis.get("projects") or []),
        "audio": info.get("audio_path") or "",
        "status": "已总结" if analysis.get("llm") else "仅转写",
        "template": analysis.get("template") or "",
        "source": "voiceinbox",
    }
    text = merge_frontmatter(entries, managed) + replace_block(body, render_note_block(cfg, info, analysis, summary_md, transcript)).lstrip("\n")
    if write_if_changed(cfg, path, text):
        LOG.info("已写入 Obsidian：%s", path)
    return path


# ---------- 每日（闪念） ----------

def daily_path(cfg: Config, day: str) -> Path:
    root = vault_ok(cfg)
    path = root / DAILY_DIR / f"{day}.md"
    if not _inside(path, root):
        raise ObsidianError("每日笔记路径异常")
    return path


def _daily_skeleton(cfg: Config, day: str) -> str:
    folder = cfg.obsidian_folder.strip().strip("/")
    return (
        "---\n"
        f"date: {yaml_value(day)}\n"
        'type: "闪念"\n'
        'tags: ["语音备忘录", "闪念"]\n'
        'source: "voiceinbox"\n'
        "---\n"
        f"# {day} 闪念\n\n"
        f"当天摘要：[[{folder}/{DAILY_DIR}/{day} 当日摘要|{day} 当日摘要]]\n"
    )


def render_flash_block(info: dict, analysis: dict, transcript: str) -> str:
    when = _when(info)
    flash = analysis.get("flash") or {}
    note = (flash.get("note") or "").strip() if analysis.get("llm") else ""
    title = analysis.get("title") or "闪念"
    lines = [f"### {when:%H:%M} {title}"]
    lines.append(note or transcript.strip() or "（没有内容）")
    for todo in flash.get("todos") or []:
        lines.append(f"- [ ] {todo}")
    meta = [info.get("duration_label") or "", " ".join("#" + t for t in analysis.get("tags") or [])]
    people = wikilinks((analysis.get("people") or []) + (analysis.get("projects") or []))
    if people:
        meta.append(" ".join(people))
    audio = info.get("audio_path")
    if audio:
        meta.append(f"[音频](<{file_url(audio)}>)")
    lines.append("*" + " · ".join(m for m in meta if m) + "*" if any(meta) else "")
    if note and transcript.strip() and note != transcript.strip():
        lines.append(_quote_callout("原话", transcript))
    return "\n".join(line for line in lines if line != "")


def write_flash(cfg: Config, info: dict, analysis: dict, transcript: str) -> Path:
    when = _when(info)
    day = f"{when:%Y-%m-%d}"
    path = daily_path(cfg, day)
    text = path.read_text(encoding="utf-8") if path.exists() else _daily_skeleton(cfg, day)
    rec_id = info["id"]
    hhmm = f"{when:%H%M}"
    block = (
        f"<!-- voiceinbox:flash {rec_id} {hhmm} begin -->\n"
        f"{render_flash_block(info, analysis, transcript)}\n"
        f"<!-- voiceinbox:flash {rec_id} end -->\n"
    )
    pattern = re.compile(_FLASH_RE_T.format(id=re.escape(rec_id)), re.S)
    if pattern.search(text):
        text = pattern.sub(lambda _m: block, text, count=1)
    else:
        later = None
        for match in _ANY_FLASH_RE.finditer(text):
            if match.group(2) > hhmm:
                later = match.start()
                break
        if later is not None:
            text = text[:later] + block + "\n" + text[later:]
        else:
            text = text.rstrip("\n") + "\n\n" + block
    if write_if_changed(cfg, path, text):
        LOG.info("闪念写入每日笔记：%s", path)
    return path


def remove_flash(cfg: Config, day: str, rec_id: str) -> bool:
    path = daily_path(cfg, day)
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"\n*" + _FLASH_RE_T.format(id=re.escape(rec_id)), re.S)
    new = pattern.sub("\n", text)
    if new == text:
        return False
    new = re.sub(r"\n{3,}", "\n\n", new)
    write_if_changed(cfg, path, new)
    return True


# ---------- 当日摘要 ----------

def digest_path(cfg: Config, day: str) -> Path:
    root = vault_ok(cfg)
    return root / DAILY_DIR / f"{day} 当日摘要.md"


def write_digest(cfg: Config, day: str, digest: dict, items: list[dict]) -> Path:
    path = digest_path(cfg, day)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    entries, body = split_frontmatter(old)
    managed = {
        "date": day,
        "type": "当日摘要",
        "tags": ["语音备忘录", "当日摘要"],
        "people": wikilinks(digest.get("people") or []),
        "project": wikilinks(digest.get("projects") or []),
        "recordings": len(items),
        "source": "voiceinbox",
    }
    folder = cfg.obsidian_folder.strip().strip("/")
    lines = [f"# {day} 当日摘要", "", "## 概要", digest.get("overview") or "（没有概要）", "", "## 要点"]
    lines.extend(f"- {h}" for h in digest.get("highlights") or ["无"])
    lines += ["", "## 待办"]
    todos = digest.get("todos") or []
    lines.extend(f"- [ ] {t}" for t in todos) if todos else lines.append("- 无")
    lines += ["", "## 当天的录音"]
    for item in items:
        target = item.get("note_link")
        label = f"{item.get('time', '')} {item.get('title', '')}（{item.get('type', '')}，{item.get('duration_label', '')}）"
        lines.append(f"- [[{target}|{label}]]" if target else f"- {label}")
    lines += ["", f"闪念原文：[[{folder}/{DAILY_DIR}/{day}|{day} 闪念]]"]
    text = merge_frontmatter(entries, managed) + replace_block(body, "\n".join(lines)).lstrip("\n")
    write_if_changed(cfg, path, text)
    return path


def vault_link(cfg: Config, path: Path) -> str:
    """笔记相对库根目录、去掉 .md 的路径，用于 [[链接]]。"""
    assert cfg.obsidian_vault is not None
    rel = path.resolve().relative_to(cfg.obsidian_vault.resolve())
    return str(rel.with_suffix(""))
