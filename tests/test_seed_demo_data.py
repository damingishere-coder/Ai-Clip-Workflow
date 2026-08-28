from __future__ import annotations

from app.core.config import settings
from app.db.database import get_connection
from app.services.database_backup_service import sqlite_diagnostic_report
from scripts import seed_demo_data as seed_module


def test_demo_seed_preserves_content_review_attribution(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setattr(seed_module, "_create_demo_video", lambda _path: False)

    try:
        seed_module.seed_demo_data(reset=True)

        with get_connection() as connection:
            analysis = connection.execute(
                """
                SELECT prompt_version_id, prompt_text_sha256
                FROM ai_analysis_runs
                WHERE id = 'demo_analysis_001'
                """
            ).fetchone()
            candidates_without_source = connection.execute(
                """
                SELECT COUNT(*)
                FROM clip_candidates
                WHERE id LIKE 'demo_%' AND source_analysis_run_id IS NULL
                """
            ).fetchone()[0]

        assert analysis is not None
        assert analysis["prompt_version_id"]
        assert analysis["prompt_text_sha256"]
        assert candidates_without_source == 0

        report = sqlite_diagnostic_report(settings.database_path, deep=True)
        assert report["status"] == "ok", report
    finally:
        with get_connection() as connection:
            seed_module._clear_demo_rows(connection)
            connection.commit()
