from fastapi import APIRouter, HTTPException

from app.models.task import AIPromptPresetUpdate
from app.services.ai_prompt_preset_service import (
    list_ai_prompt_presets,
    update_ai_prompt_preset,
)


router = APIRouter(prefix="/api/ai-prompt-presets", tags=["ai-prompt-presets"])


@router.get("")
async def get_ai_prompt_presets() -> list[dict]:
    return list_ai_prompt_presets()


@router.patch("/{preset_id}")
async def patch_ai_prompt_preset(preset_id: str, payload: AIPromptPresetUpdate) -> dict:
    try:
        return update_ai_prompt_preset(preset_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
