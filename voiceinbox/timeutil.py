"""北京时间、文件名里的录音时间、Core Data 时间。"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Core Data / Cocoa 的纪元是 2001-01-01 UTC，比 Unix 晚 978307200 秒。
COCOA_OFFSET = 978307200
_NAME_RE = re.compile(r"(20\d{2})(\d{2})(\d{2})[ T_\-]?(\d{2})(\d{2})(\d{2})")


def zone(name: str = "Asia/Shanghai") -> ZoneInfo:
    return ZoneInfo(name)


def now(tz_name: str = "Asia/Shanghai") -> datetime:
    return datetime.now(zone(tz_name))


def now_iso(tz_name: str = "Asia/Shanghai") -> str:
    return now(tz_name).isoformat(timespec="seconds")


def to_local_iso(dt: datetime, tz_name: str = "Asia/Shanghai") -> str:
    tz = zone(tz_name)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz).isoformat(timespec="seconds")


def datetime_from_filename(name: str, tz_name: str = "Asia/Shanghai") -> datetime | None:
    """语音备忘录常见文件名：20261007 093012-ABCD.m4a。解析失败返回 None。"""
    match = _NAME_RE.search(name)
    if not match:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=zone(tz_name))
    except ValueError:
        return None


def interpret_apple_time(value: object) -> datetime | None:
    """把 ZDATE 当成 Cocoa 秒（2001-01-01 起）。超出 1995–2100 则视为没有。

    真实库若把毫秒存进来（数值大于 1e10），先除以 1000。
    Unix 秒和 Cocoa 秒在 2026 年附近会歧义，这里按实施方案只认 Cocoa。
    """
    if value is None or value == "":
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number > 10_000_000_000:
        number = number / 1000
    try:
        dt = datetime.fromtimestamp(number + COCOA_OFFSET, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    if not 1995 <= dt.year <= 2100:
        return None
    return dt
