from fastapi import APIRouter, Depends

from ..api.errors import resolve_boundary_or_raise
from ..dependencies import get_boundary_service
from ..services.boundary_service import BoundaryService

router = APIRouter(prefix="/api")


@router.get("/boundary/{osm_id}")
async def get_boundary(
    osm_id: str,
    svc: BoundaryService = Depends(get_boundary_service),
):
    result = await resolve_boundary_or_raise(svc, osm_id)
    return result.feature
