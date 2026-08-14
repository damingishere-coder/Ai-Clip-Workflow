from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.database import get_connection, init_db
from app.main import app


PREFIX = "test-group-select-all-"


@pytest.fixture(autouse=True)
def clean_group_select_data():
    init_db()
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM clip_candidates WHERE task_id LIKE ?", (f"{PREFIX}%",))
        connection.execute("DELETE FROM tasks WHERE id LIKE ?", (f"{PREFIX}%",))
        connection.commit()


def _seed_clip_review_task() -> str:
    task_id = f"{PREFIX}{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, task_name, task_dir_name, source_type, platform, status,
                max_clip_duration, candidate_clip_count, created_at, updated_at
            ) VALUES (?, '全选测试任务', ?, 'upload', 'general', 'pending_review', 10, 2, ?, ?)
            """,
            (task_id, task_id, now, now),
        )
        for index, enabled in enumerate((1, 0), start=1):
            clip_id = f"{task_id}-clip-{index}"
            connection.execute(
                """
                INSERT INTO clip_candidates (
                    id, task_id, clip_key, title, start_time, end_time, duration_seconds,
                    summary, reason, highlight_reason, confidence_score,
                    selected_by_default, enabled, reviewed, is_deleted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 60, '测试摘要', '测试理由', '测试高光',
                    0.8, 1, ?, 0, 0, ?, ?)
                """,
                (
                    clip_id,
                    task_id,
                    clip_id,
                    f"候选片段 {index}",
                    f"00:0{index}:00",
                    f"00:0{index + 1}:00",
                    enabled,
                    now,
                    now,
                ),
            )
        connection.commit()
    return task_id


def test_clip_review_renders_select_all_control_and_enabled_count() -> None:
    task_id = _seed_clip_review_task()

    response = TestClient(app).get(f"/tasks/{task_id}/clips/review")

    assert response.status_code == 200
    assert 'data-clip-select-all' in response.text
    assert 'data-clip-select-count' in response.text
    assert "全选当前列表" in response.text
    assert "已启用 1 / 2 条" in response.text


def test_clip_select_all_reuses_batch_save_payload() -> None:
    script = (settings.project_root / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "function getClipEnableCheckboxes()" in script
    assert "function updateClipSelectAllUi()" in script
    assert "getClipEnableCheckboxes().forEach" in script
    assert 'enabled: card.querySelector("[name=\'enabled\']").checked' in script
    assert "请点击“保存修改”写入数据库" in script
