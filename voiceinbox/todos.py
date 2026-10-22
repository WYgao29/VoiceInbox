"""界面上勾掉的待办，只记在本机数据目录里。

不回写 Obsidian：受管块每次重新生成都会整段替换，回写勾选会和用户自己的编辑打架。
键是「录音 id + 待办文字的哈希」，同一条待办重新生成后文字不变就还算勾过。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from voiceinbox.config import Config

_LOCK = threading.Lock()
MAX_ITEMS = 5000


def todo_key(text: str) -> str:
    return hashlib.sha1(" ".join(str(text).split()).encode("utf-8")).hexdigest()[:12]


def done_path(cfg: Config) -> Path:
    return cfg.state_dir / "todos_done.json"


def load_done(cfg: Config) -> dict[str, str]:
    path = done_path(cfg)
    try:
        if path.is_file() and not path.is_symlink():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except (OSError, ValueError):
        pass
    return {}


def set_done(cfg: Config, rec_id: str, key: str, done: bool) -> dict[str, str]:
    with _LOCK:
        data = load_done(cfg)
        name = f"{rec_id}:{key}"
        if done:
            data[name] = datetime.now().astimezone().isoformat(timespec="seconds")
        else:
            data.pop(name, None)
        if len(data) > MAX_ITEMS:
            for old in sorted(data, key=data.get)[: len(data) - MAX_ITEMS]:
                data.pop(old, None)
        path = done_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".todos.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return data


def todos_of(analysis: dict) -> list[str]:
    summary = analysis.get("summary") or {}
    flash = analysis.get("flash") or {}
    out: list[str] = []
    for text in list(summary.get("todos") or []) + list(flash.get("todos") or []):
        text = " ".join(str(text).split())
        if text and text not in out:
            out.append(text)
    return out


def with_state(rec_id: str, analysis: dict, done: dict[str, str]) -> list[dict]:
    return [
        {"text": text, "key": todo_key(text), "done": f"{rec_id}:{todo_key(text)}" in done}
        for text in todos_of(analysis)
    ]
