from __future__ import annotations

from datetime import timedelta

import pytest

from app.services.publish_time import build_schedule_times, ensure_future, parse_datetime, to_utc_iso, utc_now


def test_naive_datetime_is_interpreted_as_beijing_time():
    parsed = parse_datetime("2026-07-16T09:00", "Asia/Shanghai")
    assert parsed.isoformat() == "2026-07-16T09:00:00+08:00"
    assert to_utc_iso(parsed) == "2026-07-16T01:00:00+00:00"


def test_aware_datetime_is_normalized_to_utc_with_offset():
    assert to_utc_iso("2026-07-16T09:00:00+08:00") == "2026-07-16T01:00:00+00:00"


def test_past_datetime_is_rejected():
    past = (utc_now() - timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError, match="必须晚于当前时间"):
        ensure_future(past, "Asia/Shanghai")


def test_batch_schedule_rolls_to_next_beijing_day():
    assert build_schedule_times(
        3,
        start_at_local="2026-07-16T20:00",
        timezone_name="Asia/Shanghai",
        interval_minutes=180,
        daily_start_time="09:00",
        daily_end_time="21:00",
        reject_past=False,
    ) == [
        "2026-07-16T12:00:00+00:00",
        "2026-07-17T01:00:00+00:00",
        "2026-07-17T04:00:00+00:00",
    ]
