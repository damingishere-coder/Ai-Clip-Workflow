"""统一处理发布排期时间。

数据库统一保存带时区的 UTC ISO 8601；没有时区的前端时间始终按 APP_TIMEZONE
解释，避免依赖 Windows 或浏览器所在机器的隐式时区。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings


def app_zone(timezone_name: str | None = None) -> ZoneInfo:
    name = (timezone_name or settings.app_timezone or "Asia/Shanghai").strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"无效时区：{name}") from exc


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat(timespec="seconds")


def parse_datetime(value: str | None, timezone_name: str | None = None) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("发布时间不能为空")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("发布时间格式无效") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=app_zone(timezone_name))
    return parsed


def to_utc_iso(value: str | datetime, timezone_name: str | None = None) -> str:
    parsed = parse_datetime(value, timezone_name) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=app_zone(timezone_name))
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def ensure_future(value: str | datetime, timezone_name: str | None = None) -> datetime:
    parsed = parse_datetime(value, timezone_name) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=app_zone(timezone_name))
    if parsed <= utc_now():
        raise ValueError("发布时间必须晚于当前时间（北京时间）")
    return parsed


def local_display(value: str | datetime | None, timezone_name: str | None = None) -> str:
    if not value:
        return ""
    parsed = parse_datetime(value, timezone_name) if isinstance(value, str) else value
    return parsed.astimezone(app_zone(timezone_name)).strftime("%Y-%m-%d %H:%M")


def parse_clock(value: str, label: str) -> time:
    try:
        parsed = time.fromisoformat(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{label}格式无效，请使用 HH:MM") from exc
    return parsed.replace(second=0, microsecond=0)


def _next_allowed_schedule_time(cursor: datetime, window_start: time, window_end: time) -> datetime:
    """把后续发布时间顺延到每日允许时段，支持跨午夜窗口。"""
    if window_start == window_end:
        return cursor

    cursor_clock = cursor.time()
    if window_end > window_start:
        day_start = datetime.combine(cursor.date(), window_start, tzinfo=cursor.tzinfo)
        day_end = datetime.combine(cursor.date(), window_end, tzinfo=cursor.tzinfo)
        if cursor < day_start:
            return day_start
        if cursor <= day_end:
            return cursor
        return datetime.combine(cursor.date() + timedelta(days=1), window_start, tzinfo=cursor.tzinfo)

    if cursor_clock >= window_start or cursor_clock <= window_end:
        return cursor
    return datetime.combine(cursor.date(), window_start, tzinfo=cursor.tzinfo)


def next_allowed_schedule_time(
    cursor: datetime,
    *,
    daily_start_time: str = "07:00",
    daily_end_time: str = "00:00",
) -> datetime:
    """把一个候选时间顺延到每日允许发布时段。"""
    window_start = parse_clock(daily_start_time, "每日开始时间")
    window_end = parse_clock(daily_end_time, "每日结束时间")
    return _next_allowed_schedule_time(cursor, window_start, window_end)


def build_schedule_times(
    count: int,
    *,
    start_at_local: str,
    timezone_name: str | None = None,
    interval_minutes: int = 180,
    daily_start_time: str = "07:00",
    daily_end_time: str = "00:00",
    reject_past: bool = True,
) -> list[str]:
    if count <= 0:
        return []
    zone = app_zone(timezone_name)
    cursor = parse_datetime(start_at_local, timezone_name).astimezone(zone)
    if reject_past and cursor <= utc_now().astimezone(zone):
        raise ValueError("排期起始时间必须晚于当前时间（北京时间）")

    interval = timedelta(minutes=max(1, int(interval_minutes)))
    window_start = parse_clock(daily_start_time, "每日开始时间")
    window_end = parse_clock(daily_end_time, "每日结束时间")

    result = [to_utc_iso(cursor)]
    while len(result) < count:
        cursor += interval
        cursor = _next_allowed_schedule_time(cursor, window_start, window_end)
        result.append(to_utc_iso(cursor))
    return result
