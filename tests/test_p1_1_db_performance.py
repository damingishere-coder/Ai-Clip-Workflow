"""P1-1 数据库性能优化测试：索引检查和批量聚合查询"""

import importlib
from datetime import datetime, timezone

import pytest


# ── 辅助 ──

def _setup_db(tmp_path, monkeypatch):
    """初始化独立测试数据库。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    storage_dir = tmp_path / "test_storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "test_p1_1.sqlite3"
    monkeypatch.setenv("STORAGE_ROOT", str(storage_dir))
    monkeypatch.setenv("TASKS_DIR", str(storage_dir))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    import app.core.config
    import app.db.database
    import app.services.task_service
    importlib.reload(app.core.config)
    importlib.reload(app.db.database)
    importlib.reload(app.services.task_service)

    from app.db.database import init_db

    init_db()


def _insert_task(conn, task_id: str, status: str = "pending_video"):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO tasks (id, task_name, task_dir_name, source_type, platform,
           original_video_path, max_clip_duration, candidate_clip_count,
           ai_prompt_preset_id, status, progress, is_deleted, created_at, updated_at)
           VALUES (?, ?, ?, 'upload', 'general', '', 3, 5, 'preset_001', ?, 0, 0, ?, ?)""",
        (task_id, f"测试任务-{task_id}", task_id, status, now, now),
    )


def _insert_clip_candidate(conn, clip_id: str, task_id: str, enabled: int = 1):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO clip_candidates (id, task_id, clip_key, title, start_time, end_time,
           duration_seconds, summary, reason, highlight_reason, confidence_score,
           selected_by_default, enabled, reviewed, is_deleted, created_at, updated_at)
           VALUES (?, ?, ?, '片段标题', '00:01:00', '00:02:00', 60, '摘要', '理由', '高光理由',
           0.8, 1, ?, 0, 0, ?, ?)""",
        (clip_id, task_id, clip_id, enabled, now, now),
    )


def _insert_output_clip(conn, oc_id: str, task_id: str, clip_candidate_id: str, status: str = "completed"):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO output_clip (id, task_id, clip_candidate_id, output_file_path, output_file_name,
           status, created_at, updated_at)
           VALUES (?, ?, ?, '', '输出切片.mp4', ?, ?, ?)""",
        (oc_id, task_id, clip_candidate_id, status, now, now),
    )


# ════════════════════════════════════════════════════════
# 1. 索引存在性
# ════════════════════════════════════════════════════════


class TestIndexesExist:
    """init_db 后所有必要索引存在"""

    @staticmethod
    def _setup(tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection

        with get_connection() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        return {row["name"] for row in rows}

    def test_tasks_indexes(self, tmp_path, monkeypatch):
        """tasks 表有 status+created_at 和 is_deleted+created_at 索引"""
        index_names = self._setup(tmp_path, monkeypatch)
        assert "idx_tasks_status_created" in index_names
        assert "idx_tasks_is_deleted_created" in index_names

    def test_clip_candidates_indexes(self, tmp_path, monkeypatch):
        """clip_candidates 表有 task+enabled+deleted 复合索引"""
        index_names = self._setup(tmp_path, monkeypatch)
        assert "idx_clip_candidates_task_enabled_deleted" in index_names

    def test_output_clip_indexes(self, tmp_path, monkeypatch):
        """output_clip 表有 task+status 索引"""
        index_names = self._setup(tmp_path, monkeypatch)
        assert "idx_output_clip_task_status" in index_names

    def test_ai_analysis_runs_indexes(self, tmp_path, monkeypatch):
        """ai_analysis_runs 表有 task+created_at 索引"""
        index_names = self._setup(tmp_path, monkeypatch)
        assert "idx_ai_analysis_runs_task_created" in index_names

    def test_subtitle_jobs_indexes(self, tmp_path, monkeypatch):
        """subtitle_jobs 表有 task+output_clip+status 索引"""
        index_names = self._setup(tmp_path, monkeypatch)
        assert "idx_subtitle_jobs_task_output_status" in index_names

    def test_publish_jobs_indexes(self, tmp_path, monkeypatch):
        """publish_jobs 表有 status+platform+created_at 和 task+output_clip 索引"""
        index_names = self._setup(tmp_path, monkeypatch)
        assert "idx_publish_jobs_status_platform_created" in index_names
        assert "idx_publish_jobs_task_output" in index_names


# ════════════════════════════════════════════════════════
# 2. 批量聚合查询正确性
# ════════════════════════════════════════════════════════


class TestBatchOutputClipCounts:
    """_batch_output_clip_counts 返回正确的计数"""

    def test_empty_list_returns_empty_dict(self, tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)
        from app.services.task_service import _batch_output_clip_counts

        result = _batch_output_clip_counts([])
        assert result == {}

    def test_single_task_zero_clips(self, tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection
        from app.services.task_service import _batch_output_clip_counts

        with get_connection() as conn:
            _insert_task(conn, "task_a")
            conn.commit()

        result = _batch_output_clip_counts(["task_a"])
        # GROUP BY 不返回计数为 0 的任务，使用 .get 获取默认值
        assert result.get("task_a", 0) == 0

    def test_multiple_tasks_mixed_clips(self, tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection
        from app.services.task_service import _batch_output_clip_counts

        with get_connection() as conn:
            _insert_task(conn, "task_a")
            _insert_task(conn, "task_b")
            _insert_task(conn, "task_c")
            _insert_clip_candidate(conn, "cc_1", "task_a")
            _insert_clip_candidate(conn, "cc_2", "task_b")
            _insert_output_clip(conn, "oc_1", "task_a", "cc_1")
            _insert_output_clip(conn, "oc_2", "task_a", "cc_1")
            _insert_output_clip(conn, "oc_3", "task_b", "cc_2")
            conn.commit()

        result = _batch_output_clip_counts(["task_a", "task_b", "task_c"])
        assert result["task_a"] == 2
        assert result["task_b"] == 1
        # GROUP BY 不返回计数为 0 的任务
        assert result.get("task_c", 0) == 0


class TestBatchCompletedOutputClipCounts:
    """_batch_completed_output_clip_counts 只统计已完成切片"""

    def test_only_counts_completed(self, tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection
        from app.services.task_service import _batch_completed_output_clip_counts

        with get_connection() as conn:
            _insert_task(conn, "task_a")
            _insert_clip_candidate(conn, "cc_1", "task_a")
            _insert_output_clip(conn, "oc_1", "task_a", "cc_1", status="completed")
            _insert_output_clip(conn, "oc_2", "task_a", "cc_1", status="pending")
            _insert_output_clip(conn, "oc_3", "task_a", "cc_1", status="failed")
            conn.commit()

        result = _batch_completed_output_clip_counts(["task_a"])
        assert result["task_a"] == 1  # 只有 1 条 completed


class TestBatchClipCandidateCounts:
    """_batch_clip_candidate_counts 返回 total 和 enabled 计数"""

    def test_counts_total_and_enabled(self, tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection
        from app.services.task_service import _batch_clip_candidate_counts

        with get_connection() as conn:
            _insert_task(conn, "task_a")
            _insert_clip_candidate(conn, "cc_1", "task_a", enabled=1)
            _insert_clip_candidate(conn, "cc_2", "task_a", enabled=1)
            _insert_clip_candidate(conn, "cc_3", "task_a", enabled=0)
            conn.commit()

        result = _batch_clip_candidate_counts(["task_a"])
        assert result["task_a"]["total"] == 3
        assert result["task_a"]["enabled"] == 2

    def test_excludes_deleted(self, tmp_path, monkeypatch):
        """排除 is_deleted=1 的候选片段"""
        _setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection
        from app.services.task_service import _batch_clip_candidate_counts

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with get_connection() as conn:
            _insert_task(conn, "task_a")
            conn.execute(
                """INSERT INTO clip_candidates (id, task_id, clip_key, title, start_time, end_time,
                   duration_seconds, summary, reason, highlight_reason, confidence_score,
                   selected_by_default, enabled, reviewed, is_deleted, created_at, updated_at)
                   VALUES (?, ?, ?, '标题', '00:01:00', '00:02:00', 60, '', '', '',
                   0.5, 1, 1, 0, 1, ?, ?)""",
                ("cc_deleted", "task_a", "cc_deleted", now, now),
            )
            _insert_clip_candidate(conn, "cc_ok", "task_a", enabled=1)
            conn.commit()

        result = _batch_clip_candidate_counts(["task_a"])
        assert result["task_a"]["total"] == 1  # 不包括已删除的


class TestBatchAllOutputClips:
    """_batch_all_output_clips 一次查询返回所有输出切片"""

    def test_groups_by_task_id(self, tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection
        from app.services.task_service import _batch_all_output_clips

        with get_connection() as conn:
            _insert_task(conn, "task_a")
            _insert_task(conn, "task_b")
            _insert_clip_candidate(conn, "cc_1", "task_a")
            _insert_clip_candidate(conn, "cc_2", "task_b")
            _insert_output_clip(conn, "oc_1", "task_a", "cc_1")
            _insert_output_clip(conn, "oc_2", "task_a", "cc_1")
            _insert_output_clip(conn, "oc_3", "task_b", "cc_2")
            conn.commit()

        result = _batch_all_output_clips(["task_a", "task_b"])
        assert len(result["task_a"]) == 2
        assert len(result["task_b"]) == 1

    def test_empty_ids_returns_empty(self, tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)
        from app.services.task_service import _batch_all_output_clips

        result = _batch_all_output_clips([])
        assert result == {}


# ════════════════════════════════════════════════════════
# 3. list_tasks 返回字段
# ════════════════════════════════════════════════════════


class TestListTasksOutputClipCount:
    """list_tasks 返回正确聚合字段，不破坏原有字段结构"""

    def test_output_clip_count_in_tasks(self, tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection
        from app.services.task_service import list_tasks

        with get_connection() as conn:
            _insert_task(conn, "task_a", status="completed")
            _insert_task(conn, "task_b", status="pending_video")
            _insert_clip_candidate(conn, "cc_1", "task_a")
            _insert_output_clip(conn, "oc_1", "task_a", "cc_1")
            _insert_output_clip(conn, "oc_2", "task_a", "cc_1")
            conn.commit()

        tasks = list_tasks()
        # task_a 有 2 条 output_clip
        task_a = next(t for t in tasks if t["id"] == "task_a")
        task_b = next(t for t in tasks if t["id"] == "task_b")
        assert task_a["output_clip_count"] == 2
        assert task_b["output_clip_count"] == 0

    def test_list_tasks_keeps_required_fields(self, tmp_path, monkeypatch):
        """不破坏原有返回字段结构"""
        _setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection
        from app.services.task_service import list_tasks

        with get_connection() as conn:
            _insert_task(conn, "task_a")
            conn.commit()

        tasks = list_tasks()
        assert len(tasks) == 1
        t = tasks[0]
        required_keys = [
            "id", "title", "task_name", "status", "status_label",
            "platform", "platform_label", "source_type_label",
            "source_exists", "progress", "candidate_count",
            "output_clip_count", "created_at", "updated_at",
            "error_message", "is_deleted",
        ]
        for key in required_keys:
            assert key in t, f"缺少字段：{key}"


# ════════════════════════════════════════════════════════
# 4. Dashboard / Clips Overview 聚合
# ════════════════════════════════════════════════════════


class TestDashboardContext:
    """get_dashboard_context 统计正确"""

    def test_ready_for_subtitle_count(self, tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection
        from app.services.task_service import get_dashboard_context

        with get_connection() as conn:
            _insert_task(conn, "task_a", status="completed")
            _insert_clip_candidate(conn, "cc_1", "task_a")
            _insert_output_clip(conn, "oc_1", "task_a", "cc_1", status="completed")
            _insert_output_clip(conn, "oc_2", "task_a", "cc_1", status="completed")
            _insert_output_clip(conn, "oc_3", "task_a", "cc_1", status="pending")
            conn.commit()

        ctx = get_dashboard_context()
        # "待加字幕" = 已完成切片数 = 2
        ready_stat = next(s for s in ctx["stats"] if s["label"] == "待加字幕")
        assert ready_stat["value"] == 2

    def test_output_clip_count_in_focus_stats(self, tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection
        from app.services.task_service import get_dashboard_context

        with get_connection() as conn:
            _insert_task(conn, "task_a", status="completed")
            _insert_clip_candidate(conn, "cc_1", "task_a")
            _insert_output_clip(conn, "oc_1", "task_a", "cc_1")
            conn.commit()

        ctx = get_dashboard_context()
        focus_output = next(s for s in ctx["focus_stats"] if s["label"] == "输出切片")
        assert focus_output["value"] == 1


class TestClipsOverviewContext:
    """get_clips_overview_context 候选片段数量正确"""

    def test_clip_counts_per_task(self, tmp_path, monkeypatch):
        _setup_db(tmp_path, monkeypatch)
        from app.db.database import get_connection
        from app.services.task_service import get_clips_overview_context

        with get_connection() as conn:
            _insert_task(conn, "task_a", status="pending_review")
            _insert_task(conn, "task_b", status="pending_ai")
            _insert_clip_candidate(conn, "cc_a1", "task_a", enabled=1)
            _insert_clip_candidate(conn, "cc_a2", "task_a", enabled=1)
            _insert_clip_candidate(conn, "cc_a3", "task_a", enabled=0)
            _insert_clip_candidate(conn, "cc_b1", "task_b", enabled=1)
            conn.commit()

        ctx = get_clips_overview_context()
        task_a = next(t for t in ctx["tasks"] if t["id"] == "task_a")
        task_b = next(t for t in ctx["tasks"] if t["id"] == "task_b")
        assert task_a["real_clip_count"] == 3
        assert task_a["enabled_clip_count"] == 2
        assert task_b["real_clip_count"] == 1
        assert task_b["enabled_clip_count"] == 1
