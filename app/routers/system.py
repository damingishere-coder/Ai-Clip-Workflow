from fastapi import APIRouter, Query

from app.services.system_readiness_service import build_system_readiness


router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/readiness")
async def get_system_readiness(
    deep: bool = Query(default=False),
) -> dict:
    return build_system_readiness(deep=deep)
