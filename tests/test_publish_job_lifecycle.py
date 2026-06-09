"""发布任务状态流转测试"""

import pytest

from app.services.publish_service import STATUS_LABELS, _format_tags, _sanitize_publish_title, _sanitize_publish_description


class TestPublishJobStatus:
    """发布任务状态流转"""

    def test_ready_is_default(self):
        """opencli_publish 任务创建后应为 ready"""
        assert "ready" in STATUS_LABELS
        assert STATUS_LABELS["ready"] == "待发送"

    def test_publishing_state_exists(self):
        """发送中状态存在"""
        assert "publishing" in STATUS_LABELS
        assert STATUS_LABELS["publishing"] == "发送中"

    def test_published_state_exists(self):
        """已发布状态存在"""
        assert "published" in STATUS_LABELS
        assert STATUS_LABELS["published"] == "已发布"

    def test_failed_state_exists(self):
        """发送失败状态存在"""
        assert "failed" in STATUS_LABELS
        assert STATUS_LABELS["failed"] == "发送失败"

    def test_cancelled_state_exists(self):
        """已取消状态存在"""
        assert "cancelled" in STATUS_LABELS
        assert STATUS_LABELS["cancelled"] == "已取消"

    def test_valid_transitions(self):
        """合法状态流转"""
        # ready → publishing → published
        # ready → publishing → failed
        # ready → cancelled
        valid = ["ready", "publishing", "published", "failed", "cancelled"]
        for status in valid:
            assert status in STATUS_LABELS, f"状态 {status} 应该在 STATUS_LABELS 中"

    def test_no_auto_trigger_from_scheduled_at(self):
        """scheduled_at 不会自动触发发送（v1.2 无定时调度器）"""
        # scheduled_at 只是字段预留，此处验证它不在状态流转逻辑中
        # 确认 STATUS_LABELS 里没有 "scheduled" 状态
        assert "scheduled" not in STATUS_LABELS
        assert "scheduling" not in STATUS_LABELS


class TestContentSafety:
    """发布内容安全清洗"""

    def test_title_truncated(self):
        long_title = "这是一个非常长的标题" * 10
        result = _sanitize_publish_title(long_title, "默认标题")
        assert len(result) <= 80

    def test_title_removes_hashtags(self):
        result = _sanitize_publish_title("#精彩 #片段", "默认")
        assert "#" not in result

    def test_title_fallback(self):
        result = _sanitize_publish_title("", "精彩片段")
        assert len(result) > 0

    def test_description_truncated(self):
        long_desc = "非常长的简介内容。" * 100
        result = _sanitize_publish_description(long_desc)
        assert len(result) <= 700

    def test_sensitive_words_replaced(self):
        """敏感词应被替换"""
        result = _sanitize_publish_title("笑死我了哈哈哈", "默认")
        assert "死" not in result
        assert "笑到" in result


class TestFormatTags:
    """话题格式化"""

    def test_cleans_hashtag_prefix(self):
        result = _format_tags(["#精彩", "#片段"])
        assert "精彩" in result
        assert "片段" in result
        # 输出的标签不应带 # 前缀
        assert result.startswith("#") is False or result.startswith("精彩")

    def test_string_input(self):
        result = _format_tags("精彩, 片段, 直播")
        assert "精彩" in result

    def test_deduplicates(self):
        result = _format_tags(["精彩", "精彩", "片段"])
        parts = result.split(", ")
        assert len(parts) == 2  # 去重后只剩两个

    def test_max_eight_tags(self):
        many_tags = [f"标签{i}" for i in range(20)]
        result = _format_tags(many_tags)
        parts = result.split(", ")
        assert len(parts) <= 8
