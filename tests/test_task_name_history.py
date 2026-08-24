from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import task_service


def _headers() -> dict[str, str]:
    if not settings.local_admin_token:
        return {}
    return {"Authorization": f"Bearer {settings.local_admin_token}"}


def _install_test_database(tmp_path, monkeypatch, rows: list[dict]) -> None:
    database_path = tmp_path / "task-name-history.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                task_name TEXT NOT NULL,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO tasks (id, task_name, is_deleted, created_at) VALUES (?, ?, ?, ?)",
            [
                (
                    row["id"],
                    row["name"],
                    1 if row.get("is_deleted") else 0,
                    row["created_at"],
                )
                for row in rows
            ],
        )

    @contextmanager
    def get_test_connection():
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    monkeypatch.setattr(task_service, "get_connection", get_test_connection)


def test_list_task_name_history_returns_newest_first(tmp_path, monkeypatch):
    _install_test_database(
        tmp_path,
        monkeypatch,
        [
            {
                "id": "history-old",
                "name": "八月一日直播高光切片",
                "created_at": "2026-08-01T09:00:00+00:00",
            },
            {
                "id": "history-mid",
                "name": "八月二日综艺访谈片段",
                "created_at": "2026-08-02T09:00:00+00:00",
            },
            {
                "id": "history-new",
                "name": "八月三日新品发布回放",
                "created_at": "2026-08-03T09:00:00+00:00",
            },
        ],
    )
    assert task_service.list_task_name_history() == [
        "八月三日新品发布回放",
        "八月二日综艺访谈片段",
        "八月一日直播高光切片",
    ]


def test_list_task_name_history_excludes_deleted(tmp_path, monkeypatch):
    _install_test_database(
        tmp_path,
        monkeypatch,
        [
            {
                "id": "history-keep-old",
                "name": "保留任务甲",
                "created_at": "2026-08-01T09:00:00+00:00",
            },
            {
                "id": "history-deleted",
                "name": "已隐藏任务乙",
                "created_at": "2026-08-02T09:00:00+00:00",
                "is_deleted": 1,
            },
            {
                "id": "history-keep-new",
                "name": "保留任务丙",
                "created_at": "2026-08-03T09:00:00+00:00",
            },
        ],
    )
    assert task_service.list_task_name_history() == ["保留任务丙", "保留任务甲"]


def test_list_task_name_history_deduplicates_non_empty_names_and_limits_results(tmp_path, monkeypatch):
    rows = [
        {
            "id": "history-duplicate-old",
            "name": "重复任务",
            "created_at": "2026-08-01T09:00:00+00:00",
        },
        {
            "id": "history-duplicate-new",
            "name": "  重复任务  ",
            "created_at": "2026-08-20T09:00:00+00:00",
        },
        {
            "id": "history-empty",
            "name": "   ",
            "created_at": "2026-08-21T09:00:00+00:00",
        },
    ]
    rows.extend(
        {
            "id": f"history-{index:03d}",
            "name": f"任务 {index:03d}",
            "created_at": f"2026-{(index // 31) + 1:02d}-{(index % 31) + 1:02d}T09:00:00+00:00",
        }
        for index in range(105)
    )
    _install_test_database(tmp_path, monkeypatch, rows)

    history = task_service.list_task_name_history()

    assert len(history) == 5
    assert history[0] == "重复任务"
    assert history.count("重复任务") == 1
    assert "" not in history
    assert "   " not in history
    assert history[-1] == "任务 101"
    assert "任务 100" not in history


def test_new_task_page_renders_name_history_candidates(tmp_path, monkeypatch):
    _install_test_database(
        tmp_path,
        monkeypatch,
        [
            {
                "id": "history-old",
                "name": "八月的第一次直播全程高光切片",
                "created_at": "2026-08-01T09:00:00+00:00",
            },
            {
                "id": "history-new",
                "name": "九月的第二次综艺访谈精华回顾合集",
                "created_at": "2026-08-03T09:00:00+00:00",
            },
        ],
    )
    response = TestClient(app).get("/tasks/new", headers=_headers())
    assert response.status_code == 200
    assert 'name="task_name"' in response.text
    assert 'list="task-name-history"' in response.text
    assert 'autocomplete="off"' in response.text
    assert "NAS" not in response.text
    assert 'name="source_type"' not in response.text
    assert 'name="video_file"' in response.text
    assert 'id="long-live-settings" hidden' in response.text
    assert "九月的第二次综艺访谈精华回顾合集" in response.text
    assert "八月的第一次直播全程高光切片" in response.text
    newest_position = response.text.index("九月的第二次综艺访谈精华回顾合集")
    oldest_position = response.text.index("八月的第一次直播全程高光切片")
    assert newest_position < oldest_position
