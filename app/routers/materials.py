from fastapi import APIRouter, HTTPException, Query

from app.models.material import MaterialRegistration, MaterialScan
from app.services import material_catalog_service as catalog

router = APIRouter(prefix="/api/materials", tags=["materials"])


@router.post("/scan")
def scan_materials(payload: MaterialScan):
    # Existing admin/same-origin middleware protects this explicit local action.
    try:
        return catalog.scan_directory(payload.directory)
    except catalog.MaterialError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/register")
def register_materials(payload: MaterialRegistration):
    try:
        return catalog.register_materials(payload)
    except catalog.MaterialError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("")
def list_materials(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    try:
        return catalog.list_materials(limit, offset)
    except catalog.MaterialError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/{material_id}")
def get_material(material_id: str):
    try:
        return catalog.get_material(material_id)
    except catalog.MaterialError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
