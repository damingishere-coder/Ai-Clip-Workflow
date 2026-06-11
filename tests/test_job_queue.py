"""工作流任务队列测试

覆盖范围：
- TestJobCreate: 创建 job 记录
- TestJobLifecycle: job 状态流转 queued → running → completed / failed
- TestJobSerialization: payload_json / result_json 序列化验证
- TestJobQuery: 查询和过滤
"""

import json
from uuid import uuid4

import pytest


class TestJobCreate:
    """创建 job 记录"""

    def _setup(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "test_jobs.sqlite3"
        monkeypatch.setenv("TASKS_DIR", str(tmp_path))
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.setenv("DATABASE_PATH", str(db_path))

        import importlib
        import app.core.config
        import app.db.database
        import app.services.job_service
        importlib.reload(app.core.config)
        importlib.reload(app.db.database)
        importlib.reload(app.services.job_service)

        from app.db.database import init_db
        init_db()

    def test_create_job_defaults_to_queued(self, tmp_path, monkeypatch):
        """创建 job 后状态应为 queued"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import create_job, JOB_STATUS_QUEUED, JOB_TYPE_VIDEO_CUT

        task_id = f"task_{uuid4().hex[:8]}"
        job = create_job(task_id=task_id, job_type=JOB_TYPE_VIDEO_CUT)

        assert job["id"] is not None
        assert job["task_id"] == task_id
        assert job["job_type"] == JOB_TYPE_VIDEO_CUT
        assert job["status"] == JOB_STATUS_QUEUED
        assert job["progress"] == 0
        # payload_json 应为空字典（默认值）
        assert isinstance(job.get("payload_json"), dict)

    def test_create_job_with_payload(self, tmp_path, monkeypatch):
        """创建 job 时可以传入 payload"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import create_job, JOB_TYPE_VIDEO_CUT

        task_id = f"task_{uuid4().hex[:8]}"
        payload = {"clip_count": 5, "strategy": "segment"}
        job = create_job(task_id=task_id, job_type=JOB_TYPE_VIDEO_CUT, payload=payload)

        assert job["payload_json"] == payload

    def test_create_job_with_custom_id(self, tmp_path, monkeypatch):
        """可以指定 job_id"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import create_job, JOB_TYPE_VIDEO_CUT

        task_id = f"task_{uuid4().hex[:8]}"
        custom_id = "my_custom_job_001"
        job = create_job(task_id=task_id, job_type=JOB_TYPE_VIDEO_CUT, job_id=custom_id)

        assert job["id"] == custom_id


class TestJobLifecycle:
    """job 状态流转验证"""

    def _setup(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "test_jobs_lifecycle.sqlite3"
        monkeypatch.setenv("TASKS_DIR", str(tmp_path))
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.setenv("DATABASE_PATH", str(db_path))

        import importlib
        import app.core.config
        import app.db.database
        import app.services.job_service
        importlib.reload(app.core.config)
        importlib.reload(app.db.database)
        importlib.reload(app.services.job_service)

        from app.db.database import init_db
        init_db()

    def test_normal_flow_queued_to_completed(self, tmp_path, monkeypatch):
        """正常流转：queued → running → completed"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import (
            create_job,
            get_job,
            mark_job_running,
            mark_job_completed,
            JOB_STATUS_QUEUED,
            JOB_STATUS_RUNNING,
            JOB_STATUS_COMPLETED,
            JOB_TYPE_VIDEO_CUT,
        )

        task_id = f"task_{uuid4().hex[:8]}"
        job = create_job(task_id=task_id, job_type=JOB_TYPE_VIDEO_CUT)

        # 步骤 1：初始状态 queued
        assert job["status"] == JOB_STATUS_QUEUED
        assert job["started_at"] is None
        assert job["finished_at"] is None

        # 步骤 2：标记 running
        job = mark_job_running(job["id"])
        assert job["status"] == JOB_STATUS_RUNNING
        assert job["started_at"] is not None
        assert job["progress"] == 10

        # 步骤 3：标记 completed
        result_data = {"output_count": 3, "output_dir": "/tmp/clips"}
        job = mark_job_completed(job["id"], result_data)
        assert job["status"] == JOB_STATUS_COMPLETED
        assert job["progress"] == 100
        assert job["finished_at"] is not None
        assert job["result_json"] == result_data

    def test_failed_flow_records_error(self, tmp_path, monkeypatch):
        """失败流转：queued → running → failed，记录 error_message"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import (
            create_job,
            get_job,
            mark_job_running,
            mark_job_failed,
            JOB_STATUS_FAILED,
            JOB_TYPE_VIDEO_CUT,
        )

        task_id = f"task_{uuid4().hex[:8]}"
        job = create_job(task_id=task_id, job_type=JOB_TYPE_VIDEO_CUT)
        mark_job_running(job["id"])

        error_text = "视频文件已损坏，无法读取"
        job = mark_job_failed(job["id"], error_text)

        assert job["status"] == JOB_STATUS_FAILED
        assert job["error_message"] == error_text
        assert job["finished_at"] is not None
        # 失败时 progress 保持不变（不强制 100）
        assert "任务失败" in job.get("message", "")

    def test_progress_update(self, tmp_path, monkeypatch):
        """进度更新功能"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import (
            create_job,
            mark_job_running,
            update_job_progress,
            JOB_TYPE_VIDEO_CUT,
        )

        task_id = f"task_{uuid4().hex[:8]}"
        job = create_job(task_id=task_id, job_type=JOB_TYPE_VIDEO_CUT)
        mark_job_running(job["id"])

        # 更新进度到 50
        job = update_job_progress(job["id"], 50, "正在处理第 2/4 个片段")
        assert job["progress"] == 50
        assert "第 2/4" in job["message"]

        # 进度不应超过 100
        job = update_job_progress(job["id"], 150, "超出范围")
        assert job["progress"] == 100

        # 进度不应小于 0
        job = update_job_progress(job["id"], -10, "负数")
        assert job["progress"] == 0


class TestJobSerialization:
    """payload_json / result_json 序列化验证"""

    def _setup(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "test_jobs_serial.sqlite3"
        monkeypatch.setenv("TASKS_DIR", str(tmp_path))
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.setenv("DATABASE_PATH", str(db_path))

        import importlib
        import app.core.config
        import app.db.database
        import app.services.job_service
        importlib.reload(app.core.config)
        importlib.reload(app.db.database)
        importlib.reload(app.services.job_service)

        from app.db.database import init_db
        init_db()

    def test_payload_with_nested_json(self, tmp_path, monkeypatch):
        """payload 支持嵌套 JSON 对象"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import create_job, JOB_TYPE_VIDEO_CUT

        task_id = f"task_{uuid4().hex[:8]}"
        payload = {
            "clips": [
                {"id": "clip-1", "title": "片段1", "start": "00:01:00", "end": "00:03:00"},
                {"id": "clip-2", "title": "片段2", "start": "00:05:00", "end": "00:08:00"},
            ],
            "strategy": "segment",
            "metadata": {"version": "1.0", "engine": "ffmpeg"},
        }
        job = create_job(task_id=task_id, job_type=JOB_TYPE_VIDEO_CUT, payload=payload)

        # 验证 payload 能完整恢复
        assert job["payload_json"] == payload
        assert len(job["payload_json"]["clips"]) == 2
        assert job["payload_json"]["metadata"]["engine"] == "ffmpeg"

    def test_result_json_serialization(self, tmp_path, monkeypatch):
        """result_json 能正常序列化复杂结果"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import (
            create_job,
            mark_job_running,
            mark_job_completed,
            JOB_TYPE_VIDEO_CUT,
        )

        task_id = f"task_{uuid4().hex[:8]}"
        job = create_job(task_id=task_id, job_type=JOB_TYPE_VIDEO_CUT)
        mark_job_running(job["id"])

        result = {
            "status": "completed",
            "output_count": 3,
            "outputs": [
                {"file": "clip_001.mp4", "size_mb": 12.5},
                {"file": "clip_002.mp4", "size_mb": 8.3},
                {"file": "clip_003.mp4", "size_mb": 15.1},
            ],
            "errors": [],
        }
        job = mark_job_completed(job["id"], result)

        assert job["result_json"] == result
        assert job["result_json"]["output_count"] == 3
        assert len(job["result_json"]["outputs"]) == 3

    def test_empty_payload_defaults_to_empty_dict(self, tmp_path, monkeypatch):
        """不传 payload 时默认为空字典"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import create_job, JOB_TYPE_VIDEO_CUT

        task_id = f"task_{uuid4().hex[:8]}"
        job = create_job(task_id=task_id, job_type=JOB_TYPE_VIDEO_CUT)

        assert job["payload_json"] == {}
        assert isinstance(job["payload_json"], dict)

    def test_empty_result_defaults_to_empty_dict(self, tmp_path, monkeypatch):
        """不传 result 时默认为空字典"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import (
            create_job,
            mark_job_running,
            mark_job_completed,
            JOB_TYPE_VIDEO_CUT,
        )

        task_id = f"task_{uuid4().hex[:8]}"
        job = create_job(task_id=task_id, job_type=JOB_TYPE_VIDEO_CUT)
        mark_job_running(job["id"])
        job = mark_job_completed(job["id"])

        assert job["result_json"] == {}


class TestJobQuery:
    """查询和过滤"""

    def _setup(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "test_jobs_query.sqlite3"
        monkeypatch.setenv("TASKS_DIR", str(tmp_path))
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        monkeypatch.setenv("DATA_DIR", str(data_dir))
        monkeypatch.setenv("DATABASE_PATH", str(db_path))

        import importlib
        import app.core.config
        import app.db.database
        import app.services.job_service
        importlib.reload(app.core.config)
        importlib.reload(app.db.database)
        importlib.reload(app.services.job_service)

        from app.db.database import init_db
        init_db()

    def test_list_jobs_by_task(self, tmp_path, monkeypatch):
        """按 task_id 过滤 job 列表"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import (
            create_job,
            list_jobs,
            mark_job_completed,
            mark_job_running,
            JOB_TYPE_VIDEO_CUT,
            JOB_TYPE_AI_ANALYSIS,
        )

        task_1 = f"task_{uuid4().hex[:8]}"
        task_2 = f"task_{uuid4().hex[:8]}"

        # 为 task_1 创建 2 个 job
        j1 = create_job(task_id=task_1, job_type=JOB_TYPE_VIDEO_CUT)
        j2 = create_job(task_id=task_1, job_type=JOB_TYPE_AI_ANALYSIS)
        # 为 task_2 创建 1 个 job
        create_job(task_id=task_2, job_type=JOB_TYPE_VIDEO_CUT)

        jobs = list_jobs(task_id=task_1)
        assert len(jobs) == 2
        job_ids = {j["id"] for j in jobs}
        assert j1["id"] in job_ids
        assert j2["id"] in job_ids

    def test_list_jobs_by_status(self, tmp_path, monkeypatch):
        """按 status 过滤 job 列表"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import (
            create_job,
            list_jobs,
            mark_job_running,
            mark_job_completed,
            JOB_STATUS_COMPLETED,
            JOB_STATUS_QUEUED,
            JOB_TYPE_VIDEO_CUT,
        )

        task_id = f"task_{uuid4().hex[:8]}"
        j1 = create_job(task_id=task_id, job_type=JOB_TYPE_VIDEO_CUT)
        j2 = create_job(task_id=task_id, job_type=JOB_TYPE_VIDEO_CUT)

        # 把 j2 完成
        mark_job_running(j2["id"])
        mark_job_completed(j2["id"])

        queued_jobs = list_jobs(task_id=task_id, status=JOB_STATUS_QUEUED)
        assert len(queued_jobs) == 1
        assert queued_jobs[0]["id"] == j1["id"]

        completed_jobs = list_jobs(task_id=task_id, status=JOB_STATUS_COMPLETED)
        assert len(completed_jobs) == 1
        assert completed_jobs[0]["id"] == j2["id"]

    def test_get_nonexistent_job(self, tmp_path, monkeypatch):
        """查询不存在的 job 返回 None"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import get_job

        job = get_job("nonexistent_id")
        assert job is None

    def test_mark_nonexistent_job(self, tmp_path, monkeypatch):
        """操作不存在的 job 返回 None"""
        self._setup(tmp_path, monkeypatch)
        from app.services.job_service import mark_job_running, mark_job_failed, mark_job_completed

        assert mark_job_running("nonexistent") is None
        assert mark_job_completed("nonexistent") is None
        assert mark_job_failed("nonexistent", "error") is None


class TestJobStatusLabels:
    """状态标签枚举"""

    def test_status_labels(self):
        from app.services.job_service import (
            JOB_STATUS_LABELS,
            JOB_STATUS_QUEUED,
            JOB_STATUS_RUNNING,
            JOB_STATUS_COMPLETED,
            JOB_STATUS_FAILED,
        )

        assert JOB_STATUS_LABELS[JOB_STATUS_QUEUED] == "排队中"
        assert JOB_STATUS_LABELS[JOB_STATUS_RUNNING] == "运行中"
        assert JOB_STATUS_LABELS[JOB_STATUS_COMPLETED] == "已完成"
        assert JOB_STATUS_LABELS[JOB_STATUS_FAILED] == "失败"

    def test_type_labels(self):
        from app.services.job_service import (
            JOB_TYPE_LABELS,
            JOB_TYPE_VIDEO_CUT,
            JOB_TYPE_AI_ANALYSIS,
            JOB_TYPE_TRANSCRIPT,
            JOB_TYPE_SUBTITLE,
            JOB_TYPE_PUBLISH,
        )

        assert JOB_TYPE_LABELS[JOB_TYPE_VIDEO_CUT] == "自动切片"
        assert JOB_TYPE_LABELS[JOB_TYPE_AI_ANALYSIS] == "AI 分析"
        assert JOB_TYPE_LABELS[JOB_TYPE_TRANSCRIPT] == "转写"
        assert JOB_TYPE_LABELS[JOB_TYPE_SUBTITLE] == "字幕"
        assert JOB_TYPE_LABELS[JOB_TYPE_PUBLISH] == "发布"
