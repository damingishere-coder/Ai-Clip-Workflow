"""AI JSON 解析测试：正常 / Markdown 包裹 / 尾逗号 / Python 风格 / 非法"""


import pytest

from app.services.ai.ai_clip_analyzer import (
    _loads_ai_json,
    _strip_markdown_code_fence,
    _extract_first_json_value,
    _remove_trailing_commas,
    _quote_unquoted_object_keys,
    _replace_python_literals,
    _normalize_ai_clip_item,
    _normalize_confidence_score,
    _normalize_spread_value,
    AIAnalysisError,
)

# ── 辅助函数直接测试 ──

class TestJsonRepairHelpers:

    def test_strip_markdown_fence(self):
        text = "```json\n{\"a\":1}\n```"
        result = _strip_markdown_code_fence(text)
        assert result == '{"a":1}'

    def test_strip_markdown_fence_no_lang(self):
        text = "```\n{\"a\":1}\n```"
        result = _strip_markdown_code_fence(text)
        assert result == '{"a":1}'

    def test_extract_first_json_object(self):
        text = "一些文字 {\"key\": \"value\"} 更多文字"
        result = _extract_first_json_value(text)
        assert result == '{"key": "value"}'

    def test_extract_first_json_array(self):
        text = "前缀 [1, 2, 3] 后缀"
        result = _extract_first_json_value(text)
        assert result == '[1, 2, 3]'

    def test_remove_trailing_commas_object(self):
        text = '{"a": 1, "b": 2,}'
        result = _remove_trailing_commas(text)
        assert result == '{"a": 1, "b": 2}'

    def test_remove_trailing_commas_array(self):
        text = '[1, 2, 3,]'
        result = _remove_trailing_commas(text)
        assert result == '[1, 2, 3]'

    def test_quote_unquoted_keys(self):
        text = '{name: "hello", age: 30}'
        result = _quote_unquoted_object_keys(text)
        # 应该给 name 和 age 加引号
        assert '"name"' in result
        assert '"age"' in result

    def test_replace_python_literals(self):
        text = '{"a": True, "b": False, "c": None}'
        result = _replace_python_literals(text)
        assert 'true' in result
        assert 'false' in result
        assert 'null' in result
        assert 'True' not in result
        assert 'False' not in result
        assert 'None' not in result


class TestNormalizeConfidenceScore:

    def test_normal_0_to_1(self):
        assert _normalize_confidence_score(0.85) == 0.85

    def test_1_to_10_scale(self):
        """1-10 分制自动除以 10"""
        assert _normalize_confidence_score(8.9) == 0.89

    def test_10_to_100_scale(self):
        """10-100 分制自动除以 100"""
        assert _normalize_confidence_score(92) == 0.92

    def test_string_number(self):
        assert _normalize_confidence_score("0.75") == 0.75

    def test_percentage_string(self):
        assert _normalize_confidence_score("85%") == 0.85

    def test_invalid_fallback(self):
        assert _normalize_confidence_score("abc") == 0.7

    def test_out_of_range_clamped(self):
        result = _normalize_confidence_score(1.5)
        assert 0 <= result <= 1


class TestNormalizeSpreadValue:

    def test_chinese_high(self):
        assert _normalize_spread_value("高") == "高"

    def test_chinese_mid(self):
        assert _normalize_spread_value("中") == "中"

    def test_chinese_low(self):
        assert _normalize_spread_value("低") == "低"

    def test_english_hot(self):
        assert _normalize_spread_value("viral") == "高"

    def test_default_mid(self):
        assert _normalize_spread_value("随便") == "中"


class TestNormalizeAiClipItem:

    def test_old_fields_converted(self):
        """旧字段 clip_key → clip_id, viral_value → spread_value, reason → highlight_reason"""
        old_clip = {
            "clip_key": "clip_001",
            "viral_value": "高",
            "reason": "这是推荐理由",
            "editing_suggestion": "剪掉开头",
        }
        result = _normalize_ai_clip_item(old_clip, index=1)
        assert result["clip_id"] == "clip_001"
        assert result["spread_value"] == "高"
        assert result["highlight_reason"] == "这是推荐理由"
        assert result["suggested_editing"] == "剪掉开头"

    def test_missing_fields_filled(self):
        """缺少必要字段时自动填充默认值"""
        empty = {}
        result = _normalize_ai_clip_item(empty, index=1)
        assert "clip_id" in result
        assert result["clip_id"] == "clip_001"
        assert "title" in result
        assert "summary" in result
        assert "highlight_reason" in result
        assert result["spread_value"] == "中"

    def test_confidence_score_normalized(self):
        clip = {"confidence_score": 8.5}
        result = _normalize_ai_clip_item(clip, index=1)
        assert result["confidence_score"] == 0.85

    def test_duration_calculated_from_times(self):
        """没有 duration_seconds 但有起止时间时自动计算"""
        clip = {"start_time": "00:01:00", "end_time": "00:02:30"}
        result = _normalize_ai_clip_item(clip, index=1)
        assert result["duration_seconds"] == 90


class TestLoadsAiJson:

    # ── 正常 JSON ──

    def test_normal_json_object(self):
        result = _loads_ai_json('{"task_id": "abc", "clips": []}')
        assert isinstance(result, dict)
        assert result["task_id"] == "abc"

    def test_normal_json_array(self):
        result = _loads_ai_json('[{"clip_id": "clip_1", "title": "test"}]')
        assert isinstance(result, list)

    # ── Markdown ```json 包裹 ──

    def test_markdown_fenced_json(self):
        raw = '```json\n{"task_id": "abc", "clips": []}\n```'
        result = _loads_ai_json(raw)
        assert isinstance(result, dict)
        assert result["task_id"] == "abc"

    # ── 带尾逗号 ──

    def test_trailing_comma_in_object(self):
        raw = '{"task_id": "abc", "clips": [],}'
        result = _loads_ai_json(raw)
        assert isinstance(result, dict)

    def test_trailing_comma_in_array(self):
        raw = '{"task_id": "abc", "clips": [{"title": "x"},]}'
        result = _loads_ai_json(raw)
        assert isinstance(result, dict)

    # ── Python 风格 True / False / None ──

    def test_python_true_false_none(self):
        raw = '{"a": True, "b": False, "c": None}'
        result = _loads_ai_json(raw)
        assert result["a"] is True
        assert result["b"] is False
        assert result["c"] is None

    def test_python_literals_with_quotes(self):
        raw = "{'name': 'hello', 'enabled': True}"
        result = _loads_ai_json(raw)
        assert result["name"] == "hello"
        assert result["enabled"] is True

    # ── 未加引号字段名 ──

    def test_unquoted_keys(self):
        raw = '{task_id: "abc", name: "test"}'
        result = _loads_ai_json(raw)
        assert result["task_id"] == "abc"
        assert result["name"] == "test"

    # ── 非法 JSON ──

    def test_completely_invalid(self):
        with pytest.raises(AIAnalysisError, match="JSON 解析失败"):
            _loads_ai_json("这不是 JSON")

    def test_incomplete_json(self):
        with pytest.raises(AIAnalysisError, match="JSON 解析失败"):
            _loads_ai_json('{"task_id": "abc", "clips": [{"title')

    # ── 前端文本包裹 JSON ──

    def test_json_embedded_in_text(self):
        raw = '以下是分析结果：\n{"task_id": "abc", "clips": []}\n分析完成。'
        result = _loads_ai_json(raw)
        assert isinstance(result, dict)
        assert result["task_id"] == "abc"
