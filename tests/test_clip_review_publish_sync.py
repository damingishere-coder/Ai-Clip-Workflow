from __future__ import annotations

from unittest.mock import Mock

from app.main import app
from app.models.task import ClipCandidateBatchItem, TaskStatus
from app.services import task_service


def _payload() -> list[ClipCandidateBatchItem]:
    return [
        ClipCandidateBatchItem(
            id="clip-review-sync-001",
            title="值得发送的片段",
            start_time="00:01:00",
            end_time="00:02:00",
            enabled=True,
            summary="完整笑点片段",
        )
    ]


def test_review_sync_regenerates_when_active_outputs_do_not_match(monkeypatch) -> None:
    save = Mock(return_value={"changed_count": 0, "message": "已保存", "clips": [], "task": {}})
    cut = Mock(
        return_value={
            "publish_sync": {
                "status": "ok",
                "message": "发送中心同步完成。",
                "link_state": {"linked_count": 1},
            }
        }
    )
    monkeypatch.setattr(task_service, "update_clip_candidates_batch", save)
    monkeypatch.setattr(task_service, "_active_outputs_match_enabled_candidates", lambda _task_id: False)
    monkeypatch.setattr(task_service, "process_task_video_cuts", cut)

    result = task_service.sync_reviewed_clips_to_publish_center("task-review-sync", _payload())

    assert result["regenerated"] is True
    assert result["link_state"]["linked_count"] == 1
    assert "重新生成最新切片" in result["message"]
    save.assert_called_once()
    cut.assert_called_once_with("task-review-sync")


def test_review_sync_reuses_matching_outputs_when_review_is_unchanged(monkeypatch) -> None:
    save = Mock(return_value={"changed_count": 0, "message": "已保存", "clips": [], "task": {}})
    sync = Mock(
        return_value={
            "status": "ok",
            "message": "发送中心同步完成。",
            "link_state": {"linked_count": 1},
        }
    )
    monkeypatch.setattr(task_service, "update_clip_candidates_batch", save)
    monkeypatch.setattr(task_service, "_active_outputs_match_enabled_candidates", lambda _task_id: True)
    monkeypatch.setattr("app.services.publish_service.sync_task_publish_jobs", sync)

    result = task_service.sync_reviewed_clips_to_publish_center("task-review-sync", _payload())

    assert result["regenerated"] is False
    assert "无需重复生成" in result["message"]
    sync.assert_called_once_with(
        "task-review-sync",
        prefer_subtitled=False,
        restore_removed=True,
    )


def test_review_sync_route_is_available() -> None:
    assert "/api/tasks/{task_id}/clips/sync-publish" in app.openapi()["paths"]


def test_unchanged_review_marks_pending_review_completed_after_full_link(monkeypatch) -> None:
    monkeypatch.setattr(
        task_service,
        "update_clip_candidates_batch",
        Mock(return_value={"changed_count": 0, "message": "已保存", "clips": [], "task": {}}),
    )
    monkeypatch.setattr(task_service, "_active_outputs_match_enabled_candidates", lambda _task_id: True)
    monkeypatch.setattr(
        "app.services.publish_service.sync_task_publish_jobs",
        Mock(
            return_value={
                "status": "ok",
                "message": "发送中心同步完成。",
                "errors": [],
                "link_state": {"state": "linked", "linked_count": 1, "missing_count": 0},
            }
        ),
    )
    monkeypatch.setattr(
        task_service,
        "get_task",
        lambda *_args, **_kwargs: {"status": TaskStatus.pending_review.value},
    )
    transition = Mock()
    monkeypatch.setattr(task_service, "transition_task_status", transition)

    task_service.sync_reviewed_clips_to_publish_center("task-review-sync", _payload())

    transition.assert_called_once_with("task-review-sync", TaskStatus.completed)


def test_partial_publish_sync_keeps_review_state_after_recut(monkeypatch) -> None:
    monkeypatch.setattr(
        task_service,
        "update_clip_candidates_batch",
        Mock(return_value={"changed_count": 1, "message": "已保存", "clips": [], "task": {}}),
    )
    monkeypatch.setattr(task_service, "_active_outputs_match_enabled_candidates", lambda _task_id: False)
    monkeypatch.setattr(
        task_service,
        "process_task_video_cuts",
        Mock(
            return_value={
                "publish_sync": {
                    "status": "partial",
                    "message": "关联不完整",
                    "errors": ["同步失败"],
                    "link_state": {"state": "partial", "missing_count": 1},
                }
            }
        ),
    )
    monkeypatch.setattr(
        task_service,
        "get_task",
        lambda *_args, **_kwargs: {"status": TaskStatus.completed.value},
    )
    update = Mock()
    monkeypatch.setattr(task_service, "update_task_status", update)

    task_service.sync_reviewed_clips_to_publish_center("task-review-sync", _payload())

    update.assert_called_once_with("task-review-sync", TaskStatus.pending_review)
