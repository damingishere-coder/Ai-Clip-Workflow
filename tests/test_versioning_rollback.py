"""阶段 P1-5：产物版本化与失败回滚测试

覆盖 cut_run / subtitle 版本化 / AI 分析 is_active 标记等场景。
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.models.task import TaskCreate, TaskStatus


# ---------------------------------------------------------------------------
# Cut Run 版本化测试
# ---------------------------------------------------------------------------

class TestCutRunVersioning:
    """测试切割流程的 cut_run 版本化和失败回滚"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """每个测试前后清理数据库"""
        from app.db.database import get_connection

        yield
        with get_connection() as conn:
            conn.execute("DELETE FROM output_clip WHERE task_id LIKE 'test-%'")
            conn.execute("DELETE FROM cut_runs WHERE task_id LIKE 'test-%'")
            conn.execute("DELETE FROM clip_candidates WHERE task_id LIKE 'test-%'")
            conn.execute("DELETE FROM tasks WHERE id LIKE 'test-%'")
            conn.commit()

    def _create_task(self, task_id: str) -> dict:
        from app.services.task_lifecycle_service import create_task_record

        payload = TaskCreate(
            task_name=f"版本化测试-{task_id}",
            source_type="upload",
            platform="general",
            max_clip_duration=5,
            candidate_clip_count=5,
            ai_preference="",
        )
        return create_task_record(payload, task_id=task_id)

    def _insert_clip_candidate(self, task_id: str, clip_id: str, title: str, start: str, end: str):
        from app.db.database import get_connection
        from app.services.task_service import _now_iso

        now = _now_iso()
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO clip_candidates (
                    id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                    summary, reason, highlight_reason, spread_value, suggested_editing,
                    confidence_score, selected_by_default, enabled, reviewed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 60, '', '', '', '', '', 0.8, 1, 1, 0, ?, ?)
                """,
                (clip_id, task_id, clip_id, title, start, end, now, now),
            )
            conn.commit()

    def test_create_cut_run_success(self):
        """创建 cut_run 应返回有效记录"""
        from app.services.video_cut_workflow_service import _create_cut_run

        self._create_task("test-cutrun-001")
        run = _create_cut_run("test-cutrun-001")

        assert "id" in run
        assert run["run_number"] >= 1
        assert run["task_id"] == "test-cutrun-001"

    def test_cut_run_number_increments(self):
        """多次创建 cut_run 时 run_number 应递增"""
        from app.services.video_cut_workflow_service import _create_cut_run

        self._create_task("test-cutrun-002")
        run1 = _create_cut_run("test-cutrun-002")
        run2 = _create_cut_run("test-cutrun-002")

        assert run2["run_number"] == run1["run_number"] + 1

    def test_activate_cut_run_deactivates_others(self):
        """激活一个 cut_run 应把同 task 下的其他 run 设为非活跃"""
        from app.services.video_cut_workflow_service import (
            _create_cut_run,
            _activate_cut_run,
        )
        from app.db.database import get_connection

        self._create_task("test-cutrun-003")
        run1 = _create_cut_run("test-cutrun-003")
        run2 = _create_cut_run("test-cutrun-003")

        _activate_cut_run("test-cutrun-003", run2["id"])

        with get_connection() as conn:
            row1 = conn.execute(
                "SELECT is_active FROM cut_runs WHERE id = ?", (run1["id"],)
            ).fetchone()
            row2 = conn.execute(
                "SELECT is_active FROM cut_runs WHERE id = ?", (run2["id"],)
            ).fetchone()

        assert row1["is_active"] == 0, "旧 run 应变为非活跃"
        assert row2["is_active"] == 1, "新 run 应变为活跃"

    def test_fail_cut_run_preserves_old_active(self):
        """失败的 cut_run 不应覆盖旧的活跃 run"""
        from app.services.video_cut_workflow_service import (
            _create_cut_run,
            _activate_cut_run,
            _fail_cut_run,
        )
        from app.db.database import get_connection

        self._create_task("test-cutrun-004")
        run1 = _create_cut_run("test-cutrun-004")
        _activate_cut_run("test-cutrun-004", run1["id"])

        # 第二次切割
        run2 = _create_cut_run("test-cutrun-004")
        _fail_cut_run(run2["id"], "FFmpeg 崩溃")

        with get_connection() as conn:
            row1 = conn.execute(
                "SELECT is_active FROM cut_runs WHERE id = ?", (run1["id"],)
            ).fetchone()
            row2 = conn.execute(
                "SELECT is_active, status FROM cut_runs WHERE id = ?", (run2["id"],)
            ).fetchone()

        # 旧 run 仍然活跃
        assert row1["is_active"] == 1, "失败后旧 run 应保持活跃"
        # 新 run 保持 inactive + failed
        assert row2["is_active"] == 0, "失败 run 不应变为活跃"
        assert row2["status"] == "failed", "失败 run 状态应为 failed"

    def test_output_clip_insert_with_cut_run_id(self):
        """插入 output_clip 应关联 cut_run_id"""
        from app.services.video_cut_workflow_service import (
            _create_cut_run,
            _insert_output_clip_record,
        )
        from app.services.video_cut_service import CutResult
        from app.db.database import get_connection

        self._create_task("test-cutrun-005")
        run = _create_cut_run("test-cutrun-005")

        result = CutResult(
            clip_candidate_id="clip-001",
            output_file_path="/tmp/test.mp4",
            output_file_name="test.mp4",
            status="completed",
        )
        _insert_output_clip_record("test-cutrun-005", run["id"], result)

        with get_connection() as conn:
            row = conn.execute(
                "SELECT cut_run_id, is_active FROM output_clip WHERE task_id = ?",
                ("test-cutrun-005",),
            ).fetchone()

        assert row["cut_run_id"] == run["id"]
        assert row["is_active"] == 1

    def test_activate_cut_run_deactivates_old_output_clips(self):
        """激活新 run 时应将旧 output_clip 标记为非活跃"""
        from app.services.video_cut_workflow_service import (
            _create_cut_run,
            _activate_cut_run,
            _insert_output_clip_record,
        )
        from app.services.video_cut_service import CutResult
        from app.db.database import get_connection

        self._create_task("test-cutrun-006")

        # 第一次切割
        run1 = _create_cut_run("test-cutrun-006")
        result1 = CutResult("clip-001", "/tmp/old.mp4", "old.mp4", "completed")
        _insert_output_clip_record("test-cutrun-006", run1["id"], result1)
        _activate_cut_run("test-cutrun-006", run1["id"])

        # 第二次切割
        run2 = _create_cut_run("test-cutrun-006")
        result2 = CutResult("clip-001", "/tmp/new.mp4", "new.mp4", "completed")
        _insert_output_clip_record("test-cutrun-006", run2["id"], result2)
        _activate_cut_run("test-cutrun-006", run2["id"])

        with get_connection() as conn:
            old_row = conn.execute(
                "SELECT is_active FROM output_clip WHERE cut_run_id = ?",
                (run1["id"],),
            ).fetchone()
            new_row = conn.execute(
                "SELECT is_active FROM output_clip WHERE cut_run_id = ?",
                (run2["id"],),
            ).fetchone()

        assert old_row["is_active"] == 0, "旧 run 的 output_clip 应标记为非活跃"
        assert new_row["is_active"] == 1, "新 run 的 output_clip 应保持活跃"

    def test_list_output_clips_only_shows_active(self):
        """list_output_clips 只应展示活跃的 output_clip"""
        from app.services.video_cut_workflow_service import (
            _create_cut_run,
            _activate_cut_run,
            _insert_output_clip_record,
        )
        from app.services.video_cut_service import CutResult
        from app.services.task_service import list_output_clips

        self._create_task("test-cutrun-007")

        # 第一次切割 → 活跃
        run1 = _create_cut_run("test-cutrun-007")
        _insert_output_clip_record("test-cutrun-007", run1["id"],
            CutResult("clip-001", "/tmp/active.mp4", "active.mp4", "completed"))
        _activate_cut_run("test-cutrun-007", run1["id"])

        # 第二次切割 → 也活跃（旧被覆盖）
        run2 = _create_cut_run("test-cutrun-007")
        _insert_output_clip_record("test-cutrun-007", run2["id"],
            CutResult("clip-002", "/tmp/active2.mp4", "active2.mp4", "completed"))
        _activate_cut_run("test-cutrun-007", run2["id"])

        clips = list_output_clips("test-cutrun-007")
        # 应该只有第二次切割的活跃 clip
        assert len(clips) == 1
        assert clips[0]["output_file_name"] == "active2.mp4"

    def test_failed_run_preserves_old_output_clips(self):
        """切割失败后 list_output_clips 仍应展示旧结果"""
        from app.services.video_cut_workflow_service import (
            _create_cut_run,
            _activate_cut_run,
            _fail_cut_run,
            _insert_output_clip_record,
        )
        from app.services.video_cut_service import CutResult
        from app.services.task_service import list_output_clips, count_output_clips

        self._create_task("test-cutrun-008")

        # 第一次切割成功
        run1 = _create_cut_run("test-cutrun-008")
        _insert_output_clip_record("test-cutrun-008", run1["id"],
            CutResult("clip-001", "/tmp/old_good.mp4", "old_good.mp4", "completed"))
        _activate_cut_run("test-cutrun-008", run1["id"])

        assert count_output_clips("test-cutrun-008") == 1

        # 第二次切割失败
        run2 = _create_cut_run("test-cutrun-008")
        _fail_cut_run(run2["id"], "切割失败")

        # 旧结果应该还在
        clips = list_output_clips("test-cutrun-008")
        assert len(clips) == 1, "失败后旧 output_clip 应仍然可查"
        assert clips[0]["output_file_name"] == "old_good.mp4"
        assert count_output_clips("test-cutrun-008") == 1


# ---------------------------------------------------------------------------
# 字幕版本化测试
# ---------------------------------------------------------------------------

class TestSubtitleVersioning:
    """测试字幕流程的版本化和失败回滚"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        from app.db.database import get_connection

        yield
        with get_connection() as conn:
            conn.execute("DELETE FROM subtitle_jobs WHERE task_id LIKE 'test-%'")
            conn.execute("DELETE FROM output_clip WHERE task_id LIKE 'test-%'")
            conn.execute("DELETE FROM tasks WHERE id LIKE 'test-%'")
            conn.commit()

    def _create_task_with_output(self, task_id: str, output_clip_id: str):
        from app.db.database import get_connection
        from app.services.task_service import _now_iso

        now = _now_iso()
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO tasks (id, task_name, platform, status, created_at, updated_at) VALUES (?, ?, 'general', 'completed', ?, ?)",
                (task_id, f"字幕测试-{task_id}", now, now),
            )
            conn.execute(
                "INSERT INTO output_clip (id, task_id, output_file_path, output_file_name, status, is_active, created_at, updated_at) VALUES (?, ?, '/tmp/test.mp4', 'test.mp4', 'completed', 1, ?, ?)",
                (output_clip_id, task_id, now, now),
            )
            conn.commit()

    def test_create_subtitle_job_inserts_new(self):
        """_create_subtitle_job 应始终创建新记录（不覆盖旧的）"""
        from app.services.subtitle_workflow_service import _create_subtitle_job
        from app.db.database import get_connection

        self._create_task_with_output("test-subver-001", "out-001")
        job1 = _create_subtitle_job("test-subver-001", "out-001", "completed", is_active=1)
        job2 = _create_subtitle_job("test-subver-001", "out-001", "processing", is_active=0)

        # 应该创建了两条记录
        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS total FROM subtitle_jobs WHERE task_id = ? AND output_clip_id = ?",
                ("test-subver-001", "out-001"),
            ).fetchone()

        assert count["total"] == 2, "应创建两条独立的字幕记录"
        assert job1["id"] != job2["id"], "两次应生成不同的 job id"

    def test_activate_subtitle_job(self):
        """激活一个字幕 job 应把同 output_clip 下的其他设为非活跃"""
        from app.services.subtitle_workflow_service import (
            _create_subtitle_job,
            _activate_subtitle_job,
        )
        from app.db.database import get_connection

        self._create_task_with_output("test-subver-002", "out-002")
        job1 = _create_subtitle_job("test-subver-002", "out-002", "completed", is_active=1)
        job2 = _create_subtitle_job("test-subver-002", "out-002", "completed", is_active=0)

        _activate_subtitle_job("test-subver-002", "out-002", job2["id"])

        with get_connection() as conn:
            row1 = conn.execute(
                "SELECT is_active FROM subtitle_jobs WHERE id = ?", (job1["id"],)
            ).fetchone()
            row2 = conn.execute(
                "SELECT is_active FROM subtitle_jobs WHERE id = ?", (job2["id"],)
            ).fetchone()

        assert row1["is_active"] == 0, "旧字幕 job 应变为非活跃"
        assert row2["is_active"] == 1, "新字幕 job 应变为活跃"

    def test_failed_subtitle_preserves_old_active(self):
        """字幕生成失败时旧字幕应保持活跃"""
        from app.services.subtitle_workflow_service import (
            _create_subtitle_job,
            _activate_subtitle_job,
            _update_subtitle_job_status,
        )
        from app.db.database import get_connection

        self._create_task_with_output("test-subver-003", "out-003")
        # 旧字幕（成功，活跃）
        job1 = _create_subtitle_job("test-subver-003", "out-003", "completed",
            subtitle_file_path="/tmp/old.ass", output_file_path="/tmp/old_sub.mp4", is_active=1)
        _activate_subtitle_job("test-subver-003", "out-003", job1["id"])

        # 新字幕（失败）
        job2 = _create_subtitle_job("test-subver-003", "out-003", "processing", is_active=0)
        _update_subtitle_job_status(job2["id"], "failed", error_message="FFmpeg 不可用")

        # 旧字幕应仍然活跃
        with get_connection() as conn:
            row1 = conn.execute(
                "SELECT is_active, status FROM subtitle_jobs WHERE id = ?", (job1["id"],)
            ).fetchone()
            row2 = conn.execute(
                "SELECT is_active, status FROM subtitle_jobs WHERE id = ?", (job2["id"],)
            ).fetchone()

        assert row1["is_active"] == 1, "旧字幕应保持活跃"
        assert row1["status"] == "completed"
        assert row2["is_active"] == 0, "失败字幕不应激活"
        assert row2["status"] == "failed"

    def test_active_only_query_returns_active_job(self):
        """_subtitle_job_for_output(active_only=True) 只返回活跃字幕"""
        from app.services.subtitle_workflow_service import (
            _create_subtitle_job,
            _activate_subtitle_job,
            _subtitle_job_for_output,
        )

        self._create_task_with_output("test-subver-004", "out-004")
        job1 = _create_subtitle_job("test-subver-004", "out-004", "completed",
            subtitle_file_path="/tmp/active.ass", is_active=1)
        _activate_subtitle_job("test-subver-004", "out-004", job1["id"])
        _create_subtitle_job("test-subver-004", "out-004", "failed",
            error_message="error", is_active=0)

        active = _subtitle_job_for_output("test-subver-004", "out-004", active_only=True)
        assert active is not None
        assert active["id"] == job1["id"]
        assert active["status"] == "completed"


# ---------------------------------------------------------------------------
# AI 分析 is_active 测试
# ---------------------------------------------------------------------------

class TestAIAnalysisActive:
    """测试 AI 分析 run 的 is_active 标记"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        from app.db.database import get_connection

        yield
        with get_connection() as conn:
            conn.execute("DELETE FROM ai_analysis_runs WHERE task_id LIKE 'test-%'")
            conn.execute("DELETE FROM clip_candidates WHERE task_id LIKE 'test-%'")
            conn.execute("DELETE FROM tasks WHERE id LIKE 'test-%'")
            conn.commit()

    def _create_task(self, task_id: str):
        from app.db.database import get_connection
        from app.services.task_service import _now_iso

        now = _now_iso()
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO tasks (id, task_name, platform, status, candidate_clip_count, created_at, updated_at) VALUES (?, ?, 'general', 'pending_ai', 5, ?, ?)",
                (task_id, f"AI测试-{task_id}", now, now),
            )
            conn.commit()

    def test_insert_run_sets_active_and_deactivates_old(self):
        """新 AI run 应自动设为 active，旧 run 取消激活"""
        from app.services.ai_analysis_workflow_service import _insert_ai_analysis_run
        from app.db.database import get_connection

        self._create_task("test-aiactive-001")

        payload1 = {"clips": [{"title": "片段1", "clip_id": "c1"}], "analysis_summary": "测试"}
        run1 = _insert_ai_analysis_run(
            task_id="test-aiactive-001",
            analysis_payload=payload1,
            provider="remote", provider_label="远程 AI", model="test-model",
            fallback_notice="", prompt_preset={"id": "p1", "name": "测试"}, requested_clip_count=5,
        )

        payload2 = {"clips": [{"title": "片段2", "clip_id": "c2"}], "analysis_summary": "测试2"}
        run2 = _insert_ai_analysis_run(
            task_id="test-aiactive-001",
            analysis_payload=payload2,
            provider="remote", provider_label="远程 AI", model="test-model",
            fallback_notice="", prompt_preset={"id": "p1", "name": "测试"}, requested_clip_count=5,
        )

        with get_connection() as conn:
            row1 = conn.execute("SELECT is_active FROM ai_analysis_runs WHERE id = ?", (run1["id"],)).fetchone()
            row2 = conn.execute("SELECT is_active FROM ai_analysis_runs WHERE id = ?", (run2["id"],)).fetchone()

        assert row1["is_active"] == 0, "旧的 AI 分析 run 应变为非活跃"
        assert row2["is_active"] == 1, "新的 AI 分析 run 应为活跃"

    def test_get_latest_returns_active_run(self):
        """get_latest_ai_analysis_run 应返回活跃的 run"""
        from app.services.ai_analysis_workflow_service import (
            _insert_ai_analysis_run,
            get_latest_ai_analysis_run,
        )

        self._create_task("test-aiactive-002")

        payload1 = {"clips": [{"title": "旧结果"}], "analysis_summary": "旧"}
        run1 = _insert_ai_analysis_run(
            task_id="test-aiactive-002",
            analysis_payload=payload1,
            provider="remote", provider_label="远程 AI", model="test-model",
            fallback_notice="", prompt_preset={"id": "p1", "name": "测试"}, requested_clip_count=5,
        )

        payload2 = {"clips": [{"title": "新结果"}], "analysis_summary": "新"}
        run2 = _insert_ai_analysis_run(
            task_id="test-aiactive-002",
            analysis_payload=payload2,
            provider="remote", provider_label="远程 AI", model="test-model",
            fallback_notice="", prompt_preset={"id": "p1", "name": "测试"}, requested_clip_count=5,
        )

        latest = get_latest_ai_analysis_run("test-aiactive-002")
        assert latest is not None
        assert latest["id"] == run2["id"], "应返回最新的活跃 run"

    def test_list_runs_includes_all_with_active_flag(self):
        """list_ai_analysis_runs 应返回所有 run，包括活跃标记"""
        from app.services.ai_analysis_workflow_service import (
            _insert_ai_analysis_run,
            list_ai_analysis_runs,
        )

        self._create_task("test-aiactive-003")

        payload1 = {"clips": [], "analysis_summary": ""}
        _insert_ai_analysis_run(
            task_id="test-aiactive-003",
            analysis_payload=payload1,
            provider="remote", provider_label="远程 AI", model="test-model",
            fallback_notice="", prompt_preset={"id": "p1", "name": "测试"}, requested_clip_count=5,
        )
        payload2 = {"clips": [], "analysis_summary": ""}
        _insert_ai_analysis_run(
            task_id="test-aiactive-003",
            analysis_payload=payload2,
            provider="local", provider_label="本地 Ollama", model="llama3",
            fallback_notice="", prompt_preset={"id": "p1", "name": "测试"}, requested_clip_count=5,
        )

        runs = list_ai_analysis_runs("test-aiactive-003")
        assert len(runs) >= 2
        # 不同的 provider/model 验证两次 run 都保存了
        providers = {r["provider"] for r in runs}
        assert "remote" in providers
        assert "local" in providers


# ---------------------------------------------------------------------------
# 数据库迁移测试
# ---------------------------------------------------------------------------

class TestMigrationColumns:
    """测试新增列的迁移"""

    def test_output_clip_has_new_columns(self):
        """output_clip 表应有 cut_run_id 和 is_active 列"""
        from app.db.database import get_connection

        with get_connection() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(output_clip)").fetchall()}

        assert "cut_run_id" in cols
        assert "is_active" in cols

    def test_ai_analysis_runs_has_is_active(self):
        """ai_analysis_runs 表应有 is_active 列"""
        from app.db.database import get_connection

        with get_connection() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(ai_analysis_runs)").fetchall()}

        assert "is_active" in cols

    def test_subtitle_jobs_has_is_active(self):
        """subtitle_jobs 表应有 is_active 列"""
        from app.db.database import get_connection

        with get_connection() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(subtitle_jobs)").fetchall()}

        assert "is_active" in cols

    def test_cut_runs_table_exists(self):
        """cut_runs 表应存在并有全部必要列"""
        from app.db.database import get_connection

        with get_connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cut_runs'"
            ).fetchall()
        assert len(rows) == 1, "cut_runs 表应存在"

        with get_connection() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(cut_runs)").fetchall()}

        for required in ["id", "task_id", "run_number", "status", "is_active", "error_message", "created_at", "updated_at"]:
            assert required in cols, f"cut_runs 应有 {required} 列"
