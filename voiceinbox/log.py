"""按天写日志，并保留 30 天。时间用 Asia/Shanghai。"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
LOG = logging.getLogger("voiceinbox")
LOG.addHandler(logging.NullHandler())


def _shanghai(seconds: float | None = None) -> time.struct_time:
    moment = seconds if seconds is not None else time.time()
    return datetime.fromtimestamp(moment, TZ).timetuple()


def setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(TZ)
    path = logs_dir / f"voiceinbox-{today:%Y-%m-%d}.log"
    cutoff = today - timedelta(days=30)
    for old in logs_dir.glob("voiceinbox-*.log"):
        stamp = old.name.removeprefix("voiceinbox-").removesuffix(".log")
        try:
            logged_on = datetime.strptime(stamp, "%Y-%m-%d").replace(tzinfo=TZ)
        except ValueError:
            continue
        if logged_on < cutoff:
            old.unlink(missing_ok=True)

    LOG.setLevel(logging.INFO)
    LOG.handlers.clear()
    LOG.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    formatter.converter = _shanghai  # type: ignore[assignment]
    file_handler = logging.FileHandler(path, encoding="utf-8")
    stream_handler = logging.StreamHandler()
    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)
    LOG.addHandler(file_handler)
    LOG.addHandler(stream_handler)


def clip(text: str, limit: int = 2000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
