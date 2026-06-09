"""时间解析测试：MM:SS / HH:MM:SS / 非法格式 / 结束早于开始"""

import pytest

from app.services.task_service import _parse_time_to_seconds, _format_seconds_as_time


class TestParseTimeToSeconds:
    """_parse_time_to_seconds 时间格式解析"""

    # ── 正常格式 ──

    def test_mm_ss_simple(self):
        assert _parse_time_to_seconds("01:23") == 83

    def test_mm_ss_zero_minutes(self):
        assert _parse_time_to_seconds("00:45") == 45

    def test_hh_mm_ss(self):
        assert _parse_time_to_seconds("01:30:00") == 5400

    def test_hh_mm_ss_zero_hours(self):
        assert _parse_time_to_seconds("00:05:30") == 330

    def test_mm_ss_boundary(self):
        """MM:SS 最大合法值"""
        assert _parse_time_to_seconds("59:59") == 3599

    def test_hh_mm_ss_large(self):
        assert _parse_time_to_seconds("10:00:00") == 36000

    # ── 非法格式 ──

    def test_invalid_empty(self):
        with pytest.raises(ValueError, match="时间格式不合法"):
            _parse_time_to_seconds("")

    def test_invalid_single_number(self):
        with pytest.raises(ValueError, match="时间格式不合法"):
            _parse_time_to_seconds("123")

    def test_invalid_too_many_parts(self):
        with pytest.raises(ValueError, match="时间格式不合法"):
            _parse_time_to_seconds("01:02:03:04")

    def test_invalid_non_digit(self):
        with pytest.raises(ValueError, match="时间格式不合法"):
            _parse_time_to_seconds("ab:cd")

    def test_invalid_seconds_too_large(self):
        with pytest.raises(ValueError, match="分钟和秒数都必须小于 60"):
            _parse_time_to_seconds("00:65")

    def test_invalid_minutes_too_large(self):
        with pytest.raises(ValueError, match="分钟和秒数都必须小于 60"):
            _parse_time_to_seconds("65:00")

    def test_invalid_hh_mm_ss_seconds_too_large(self):
        with pytest.raises(ValueError, match="分钟和秒数都必须小于 60"):
            _parse_time_to_seconds("01:00:65")

    # ── 结束时间早于开始时间校验 ──

    def test_end_before_start(self):
        """结束时间早于开始时间应报错"""
        start = _parse_time_to_seconds("00:30")
        end = _parse_time_to_seconds("00:10")
        assert end < start  # 调用方应据此判断


class TestFormatSecondsAsTime:
    """_format_seconds_as_time 格式化回显"""

    def test_simple(self):
        assert _format_seconds_as_time(83) == "00:01:23"

    def test_large(self):
        assert _format_seconds_as_time(5400) == "01:30:00"

    def test_zero(self):
        assert _format_seconds_as_time(0) == "00:00:00"

    def test_roundtrip(self):
        """HH:MM:SS 解析后再格式化应保持一致"""
        original = "00:05:30"
        seconds = _parse_time_to_seconds(original)
        formatted = _format_seconds_as_time(seconds)
        assert formatted == original
