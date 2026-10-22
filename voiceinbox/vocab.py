"""两张词表。

1. 热词（vocab/hotwords.txt，一行一个）：每次转写都通过 ASR_HOTWORDS_FILE 交给 Qwen。
   这是唯一来源，本机界面里的「常用热词」读写的就是这份文件。
2. 纠错表（vocab/replacements.toml）：转写之后做精确替换（错 → 对）。
   原始转写 <id>.txt 永远不改，纠错后的文本另存为 <id>.corrected.txt。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tomllib
from datetime import datetime
from pathlib import Path

from voiceinbox.config import Config

SEED_HOTWORDS = ("Sol",)
HOTWORD_SPLIT = re.compile(r"[\n,，、;；]+")
MAX_HOTWORDS = 200
MAX_HOTWORD_LEN = 40
MAX_RULES = 500
MAX_RULE_LEN = 80

_REPLACEMENTS_HEADER = """# 转写后的精确替换：左边是 Qwen 常听错的写法，右边是正确写法。
# 原始转写 transcripts/<id>.txt 不会被改，纠错结果写到 <id>.corrected.txt。
# 本机界面详情页可以直接加一条。也可以手改这个文件，一行一条："错" = "对"
"""


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"拒绝写入符号链接：{path}")
    partial = path.with_name(path.name + ".partial")
    if partial.is_symlink():
        partial.unlink()
    try:
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def backup_file(cfg: Config, path: Path, *, label: str) -> Path | None:
    """覆盖前先复制一份到 ~/VoiceInbox/backups/<label>/。"""
    try:
        if not path.is_file() or path.is_symlink():
            return None
    except OSError:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest_dir = cfg.backup_dir / label
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stamp}-{path.name}"
    shutil.copy2(path, dest)
    return dest


# ---------- 热词 ----------

def split_hotwords(raw: str) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    for piece in HOTWORD_SPLIT.split(raw or ""):
        word = piece.strip()
        if word.startswith("#"):
            continue
        if not word or word in seen:
            continue
        seen.add(word)
        words.append(word)
    return words


def parse_hotwords(value: object) -> list[str]:
    if isinstance(value, list):
        raw = "\n".join(str(item) for item in value)
    elif isinstance(value, str):
        raw = value
    else:
        raise ValueError("热词必须是文本")
    if "\x00" in raw:
        raise ValueError("热词含有非法字符")
    if len(raw) > 8000:
        raise ValueError("热词太长")
    words = split_hotwords(raw)
    for word in words:
        if len(word) > MAX_HOTWORD_LEN:
            raise ValueError(f"单个热词不能超过 {MAX_HOTWORD_LEN} 字")
    if len(words) > MAX_HOTWORDS:
        raise ValueError(f"常用热词最多 {MAX_HOTWORDS} 个")
    return words


def _legacy_hotwords(cfg: Config) -> Path:
    # Web v2 时期放在 state/hotwords.txt。
    return cfg.state_dir / "hotwords.txt"


def ensure_vocab(cfg: Config, *, seed: bool = True) -> None:
    """第一次运行时建 vocab/。旧的 state/hotwords.txt 合并进来后改名留底，不删。"""
    cfg.vocab_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.hotwords_file
    legacy = _legacy_hotwords(cfg)
    if not path.exists():
        words: list[str] = []
        if legacy.is_file() and not legacy.is_symlink():
            words.extend(split_hotwords(legacy.read_text(encoding="utf-8")))
        if seed:
            for word in SEED_HOTWORDS:
                if word not in words:
                    words.append(word)
        _atomic_text(path, ("\n".join(words) + "\n") if words else "")
        if legacy.is_file() and not legacy.is_symlink():
            legacy.rename(legacy.with_name("hotwords.txt.migrated"))
    if not cfg.replacements_file.exists():
        _atomic_text(cfg.replacements_file, _REPLACEMENTS_HEADER + "\n[replace]\n")


def read_hotwords(cfg: Config) -> list[str]:
    path = cfg.hotwords_file
    try:
        if path.is_symlink() or not path.is_file():
            return []
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [word[:MAX_HOTWORD_LEN] for word in split_hotwords(raw)][:MAX_HOTWORDS]


def write_hotwords(cfg: Config, value: object) -> list[str]:
    words = parse_hotwords(value)
    path = cfg.hotwords_file
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.resolve().relative_to(cfg.root_dir.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError("热词无法保存") from exc
    if path.is_symlink():
        path.unlink()
    _atomic_text(path, ("\n".join(words) + "\n") if words else "")
    return words


def hotwords_file_for_asr(cfg: Config) -> Path | None:
    """有词时返回文件路径，交给 ASR_HOTWORDS_FILE。"""
    return cfg.hotwords_file if read_hotwords(cfg) else None


# ---------- 纠错表 ----------

def _check_rule(wrong: object, right: object) -> tuple[str, str]:
    if not isinstance(wrong, str) or not isinstance(right, str):
        raise ValueError("纠错的两边都必须是文本")
    w = wrong.strip()
    r = right.strip()
    if not w:
        raise ValueError("要替换的写法不能为空")
    if "\x00" in w or "\x00" in r or "\n" in w or "\n" in r:
        raise ValueError("纠错里不能有换行或非法字符")
    if len(w) > MAX_RULE_LEN or len(r) > MAX_RULE_LEN:
        raise ValueError(f"纠错每边最多 {MAX_RULE_LEN} 字")
    if w == r:
        raise ValueError("两边一样，不需要纠错")
    return w, r


def read_replacements(cfg: Config) -> dict[str, str]:
    path = cfg.replacements_file
    try:
        if path.is_symlink() or not path.is_file():
            return {}
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    table = data.get("replace") or {}
    rules: dict[str, str] = {}
    for wrong, right in table.items():
        if isinstance(wrong, str) and isinstance(right, str) and wrong.strip():
            rules[wrong] = right
    return rules


def _render_replacements(rules: dict[str, str]) -> str:
    lines = [_REPLACEMENTS_HEADER, "[replace]"]
    for wrong, right in rules.items():
        lines.append(f"{json.dumps(wrong, ensure_ascii=False)} = {json.dumps(right, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def write_replacements(cfg: Config, rules: dict[str, str]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for wrong, right in rules.items():
        w, r = _check_rule(wrong, right)
        clean[w] = r
    if len(clean) > MAX_RULES:
        raise ValueError(f"纠错最多 {MAX_RULES} 条")
    text = _render_replacements(clean)
    parsed = tomllib.loads(text).get("replace") or {}
    if parsed != clean:
        raise ValueError("纠错表写入前校验没有对上")
    backup_file(cfg, cfg.replacements_file, label="vocab")
    _atomic_text(cfg.replacements_file, text)
    return clean


def add_replacement(cfg: Config, wrong: object, right: object) -> dict[str, str]:
    w, r = _check_rule(wrong, right)
    rules = read_replacements(cfg)
    rules[w] = r
    return write_replacements(cfg, rules)


def remove_replacement(cfg: Config, wrong: object) -> dict[str, str]:
    if not isinstance(wrong, str):
        raise ValueError("要删除的写法必须是文本")
    rules = read_replacements(cfg)
    if wrong.strip() not in rules:
        raise KeyError(wrong)
    rules.pop(wrong.strip())
    return write_replacements(cfg, rules)


def apply_replacements(text: str, rules: dict[str, str]) -> str:
    """精确替换。长的写法先替换，避免短词把长词拆坏。一次扫描，替换结果不会再被替换。"""
    if not text or not rules:
        return text
    keys = sorted((k for k in rules if k), key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(k) for k in keys))
    return pattern.sub(lambda m: rules[m.group(0)], text)
