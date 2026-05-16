from fastapi import APIRouter

from app.services.storage_service import browse_video_directory


router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("/browse")
async def browse_files(path: str | None = None) -> dict:
    return browse_video_directory(path)
