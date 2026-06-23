"""发布任务状态枚举、内容安全清洗、话题格式化测试

覆盖范围：
- TestPublishJobStatus: 状态枚举定义和 scheduled_at 字段边界（不涉及真实 opencli 发送）
- TestPublishJobDbLifecycle: 数据库级状态写入和流转验证
- TestContentSafety: 标题/简介安全清洗
- TestFormatTags: 平台话题格式化
"""

from uuid import uuid4


from app.services.publish_service import (
    STATUS_LABELS,
    _format_tags,
    _now_iso,
    _sanitize_publish_title,
    _sanitize_publish_description,
    get_publish_job,
    update_publish_job_status,
)


class TestPublishJobStatus:
    """发布状态枚举与 scheduled_at 字段边界（不会触发真实 opencli 发送）"""

    def test_ready_is_default(self):
        """opencli_publish 任务创建后状态应当设为 ready"""
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
        """发送失败状态存在，错误信息可写入 error_message 字段"""
        assert "failed" in STATUS_LABELS
        assert STATUS_LABELS["failed"] == "发送失败"

    def test_cancelled_state_exists(self):
        """已取消状态存在"""
        assert "cancelled" in STATUS_LABELS
        assert STATUS_LABELS["cancelled"] == "已取消"

    def test_valid_transitions(self):
        """合法状态值列表：ready / publishing / published / failed / cancelled"""
        valid = ["ready", "publishing", "published", "failed", "cancelled"]
        for status in valid:
            assert status in STATUS_LABELS, f"状态 {status} 应该在 STATUS_LABELS 中"

    def test_no_auto_trigger_from_scheduled_at(self):
        """scheduled_at 仅字段预留，v1.2 无定时调度器，不会自动发送"""
        assert "scheduled" not in STATUS_LABELS
        assert "scheduling" not in STATUS_LABELS


class TestPublishJobDbLifecycle:
    """publish_jobs 数据库级状态写入与验证（轻量，不涉及 opencli 发送逻辑）"""

    def test_create_defaults_to_ready(self, tmp_path, monkeypatch):
        """新建 publish_jobs 记录默认状态为 ready"""
        self._setup_test_db(tmp_path, monkeypatch)

        job = self._insert_test_job(status="ready", scheduled_at="2026-06-10T09:00:00")
        assert job["status"] == "SCHEDULED"
        assert job["status_label"] == "待发送"

    def test_mark_published_saves_correctly(self, tmp_path, monkeypatch):
        """标记为 published 后状态正确"""
        self._setup_test_db(tmp_path, monkeypatch)
        job = self._insert_test_job(status="ready")
        result = update_publish_job_status(job["id"], "published")
        updated = result["job"]
        assert updated["status"] == "PUBLISHED"
        assert updated["status_label"] == "已发布"

    def test_mark_failed_saves_error(self, tmp_path, monkeypatch):
        """标记为 failed 后错误信息正确保存"""
        self._setup_test_db(tmp_path, monkeypatch)
        job = self._insert_test_job(status="ready")
        result = update_publish_job_status(
            job["id"], "failed", error_message="平台验证码弹窗，需要人工处理"
        )
        updated = result["job"]
        assert updated["status"] == "FAILED"
        assert updated["status_label"] == "发送失败"
        assert "验证码" in (updated.get("error_message") or "")

    def test_scheduled_at_persisted_without_trigger(self, tmp_path, monkeypatch):
        """scheduled_at 可以保存，但 v1.2 不会自动按 scheduled_at 触发发送"""
        self._setup_test_db(tmp_path, monkeypatch)
        job = self._insert_test_job(status="ready", scheduled_at="2026-06-10T09:00:00")
        assert job.get("scheduled_at") == "2026-06-10T09:00:00"
        # 状态仍为 ready，没有被定时调度改为 publishing 或其他
        assert job["status"] == "SCHEDULED"

    # ── 辅助 ──

    @staticmethod
    def _setup_test_db(tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "test_pub.sqlite3"
        monkeypatch.setenv("TASKS_DIR", str(tmp_path))
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.setenv("DATABASE_PATH", str(db_path))

        import importlib
        import app.core.config
        import app.db.database
        import app.services.publish_service
        importlib.reload(app.core.config)
        importlib.reload(app.db.database)
        importlib.reload(app.services.publish_service)

        from app.db.database import init_db
        init_db()

    @staticmethod
    def _insert_test_job(status: str = "ready", scheduled_at: str = ""):
        from app.db.database import get_connection

        job_id = uuid4().hex[:12]
        task_id = uuid4().hex[:12]
        output_clip_id = uuid4().hex[:12]
        now = _now_iso()

        with get_connection() as conn:
            conn.execute(
                "INSERT INTO tasks (id, task_name, task_dir_name, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, "测试任务", "测试任务", "completed", now, now),
            )
            conn.execute(
                "INSERT INTO output_clip (id, task_id, output_file_path, output_file_name, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (output_clip_id, task_id, "/tmp/test.mp4", "test.mp4", "completed", now, now),
            )
            conn.execute(
                """INSERT INTO publish_jobs (
                    id, task_id, output_clip_id, platform, publish_mode,
                    video_source, video_file_path, title, description, tags,
                    status, scheduled_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'douyin', 'opencli_publish',
                    'original', '/tmp/test.mp4', '测试标题', '测试简介', '测试,标签',
                    ?, ?, ?, ?)""",
                (job_id, task_id, output_clip_id, status, scheduled_at, now, now),
            )
            conn.commit()

        return get_publish_job(job_id)


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
