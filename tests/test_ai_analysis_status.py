import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import ai_analysis_workflow_service as service, task_service


@pytest.mark.parametrize(
    "task_status,incomplete,coverage,expected,running",
    [
        ("FAILED_AI_ANALYZING", True, 94.44, "incomplete", False),
        ("FAILED_AI_ANALYZING", True, 0, "incomplete", False),
        ("FAILED_AI_ANALYZING", False, 100, "failed", False),
        ("AI_ANALYZING", True, 94.44, "running", True),
        ("ai_analyzing", True, 94.44, "running", True),
        ("pending_review", True, 94.44, "incomplete", False),
        ("pending_review", False, 100, "completed", False),
    ],
)
def test_status_does_not_treat_partial_or_stale_artifact_as_completed(
    monkeypatch, tmp_path, task_status, incomplete, coverage, expected, running,
):
    analysis = tmp_path / "candidate_clips.json"
    analysis.write_text(json.dumps({
        "clips": [],
        "analysis_meta": {"analysis_incomplete": incomplete, "coverage_percent": coverage},
    }), encoding="utf-8")
    monkeypatch.setattr(task_service, "get_task", lambda *_args, **_kw: {
        "status": task_status, "selection_profile": "variety_comedy",
        "error_message": "仍有 AI 单元未完成",
    })
    monkeypatch.setattr(service, "get_artifact_paths", lambda _id: {
        "analysis_path": analysis, "log_path": tmp_path / "task.log",
    })
    monkeypatch.setattr(service, "_load_active_analysis_payload", lambda _id: {})
    monkeypatch.setattr(service, "read_task_log_tail", lambda _id: [])
    response = TestClient(app).get("/api/tasks/test-ai-status/ai-analysis-status")
    assert response.status_code == 200
    status = response.json()
    assert status["status"] == expected
    assert status["is_running"] is running
    assert status["analysis_exists"] is True
    if expected == "incomplete":
        assert status["percent"] == int(coverage)
        assert f"{coverage:.2f}%" in status["message"]
    if expected == "failed":
        assert status["message"] == "仍有 AI 单元未完成"
