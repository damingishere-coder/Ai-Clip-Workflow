from fastapi import APIRouter

from app.models.settings import AIConfigUpdate
from app.services.ai_config_service import get_ai_config_context, save_ai_config


router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/ai")
async def get_ai_config() -> dict:
    return get_ai_config_context()


@router.post("/ai")
async def update_ai_config(payload: AIConfigUpdate) -> dict:
    return save_ai_config(payload)
