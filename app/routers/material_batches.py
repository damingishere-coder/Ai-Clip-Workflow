from fastapi import APIRouter, HTTPException, Query

from app.models.material_batch import MaterialBatchCreate
from app.services import material_batch_service as batches
from app.services.material_catalog_service import MaterialError

router = APIRouter(prefix="/api/material-batches", tags=["materials"])


@router.post("")
def create_batch(payload: MaterialBatchCreate):
    try:
        return batches.create_batch(payload)
    except MaterialError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("")
def list_batches(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0)):
    try:
        return batches.list_batches(limit, offset)
    except MaterialError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/{batch_id}")
def get_batch(batch_id: str):
    try:
        return batches.get_batch(batch_id)
    except MaterialError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
