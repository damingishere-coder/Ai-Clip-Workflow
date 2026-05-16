from fastapi import APIRouter

from app.models.task import TaskCreate
from app.services.task_service import create_task_record, list_mock_tasks


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
async def list_tasks() -> list[dict]:
    return list_mock_tasks()


@router.post("")
async def create_task(payload: TaskCreate) -> dict:
    return create_task_record(payload)
