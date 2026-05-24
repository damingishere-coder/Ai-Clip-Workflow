from fastapi import APIRouter, HTTPException

from app.models.task import PublishJobCreate, PublishJobStatusUpdate
from app.services import publish_service


router = APIRouter(prefix="/api/publish", tags=["publish"])


@router.get("/jobs")
async def list_publish_jobs() -> dict:
    return {
        "status": "ok",
        "jobs": publish_service.list_publish_jobs(),
    }


@router.post("/jobs")
async def create_publish_jobs(payload: PublishJobCreate) -> dict:
    try:
        return publish_service.create_publish_jobs(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/jobs/{job_id}/status")
async def update_publish_job_status(job_id: str, payload: PublishJobStatusUpdate) -> dict:
    try:
        return publish_service.update_publish_job_status(job_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel")
async def cancel_publish_job(job_id: str) -> dict:
    try:
        return publish_service.cancel_publish_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
