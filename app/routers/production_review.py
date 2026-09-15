from fastapi import APIRouter, HTTPException

from app.models.production_review import ProductionReviewConfirm
from app.services import production_review_service as review
from app.services.task_service import get_task

router = APIRouter(prefix="/api/production-review", tags=["production-review"])


def require_task(task_id):
    if not get_task(task_id):
        raise HTTPException(404, "任务不存在")


@router.get("/{task_id}")
def get_state(task_id: str):
    require_task(task_id)
    return review.state(task_id)


@router.post("/{task_id}/confirm")
def confirm(task_id: str, payload: ProductionReviewConfirm):
    require_task(task_id)
    try:
        return review.confirm(task_id, payload)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{task_id}/prepare-subtitles")
def prepare_subtitles(task_id: str):
    require_task(task_id)
    try:
        return review.prepare_subtitles(task_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
