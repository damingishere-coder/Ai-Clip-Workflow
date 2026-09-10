from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
from app.routers import system as system_router
from app.services import system_readiness_service


def _create_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def test_readiness_marks_worker_and_ffmpeg_as_degraded(monkeypatch, tmp_path):
    database_path = tmp_path / "data" / "workflow.sqlite3"
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    _create_database(database_path)
    monkeypatch.setattr(
        system_readiness_service,
        "settings",
        SimpleNamespace(
            database_path=database_path,
            data_dir=database_path.parent,
            tasks_dir=tasks_dir,
        ),
    )
    monkeypatch.setattr(system_readiness_service.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        system_readiness_service,
        "scheduler_health",
        lambda: {
            "enabled": True,
            "running": True,
            "last_error_code": "",
            "worker_available": False,
            "worker_message": "Worker 未启动",
        },
    )

    result = system_readiness_service.build_system_readiness()

    assert result["status"] == "degraded"
    assert result["critical_errors"] == []
    assert "Worker 未启动" in result["degraded_reasons"]
    assert "FFmpeg 不可用" in result["degraded_reasons"]


def test_readiness_marks_database_or_storage_error_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(
        system_readiness_service,
        "settings",
        SimpleNamespace(
            database_path=tmp_path / "missing.sqlite3",
            data_dir=tmp_path / "missing-data",
            tasks_dir=tmp_path / "missing-tasks",
        ),
    )
    monkeypatch.setattr(system_readiness_service.shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(
        system_readiness_service,
        "scheduler_health",
        lambda: {
            "enabled": False,
            "running": False,
            "last_error_code": "",
            "worker_available": True,
        },
    )

    result = system_readiness_service.build_system_readiness(deep=True)

    assert result["status"] == "not_ready"
    assert result["checks"]["database"]["status"] == "error"
    assert len(result["critical_errors"]) >= 3


def test_readiness_api_forwards_deep_flag(monkeypatch):
    monkeypatch.setattr(
        system_router,
        "build_system_readiness",
        lambda *, deep: {"status": "ready", "deep": deep},
    )

    response = TestClient(app).get("/api/system/readiness?deep=1")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "deep": True}
