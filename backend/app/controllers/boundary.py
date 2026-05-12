from fastapi import APIRouter, Depends, HTTPException

from ..api.errors import aoi_too_large_response
from ..dependencies import get_boundary_service
from ..services.boundary_service import AoiTooLarge, BoundaryService, InvalidOsmId
from ..services.geocoder.base import GeocoderNotFound

router = APIRouter(prefix="/api")


@router.get("/boundary/{osm_id}")
async def get_boundary(
    osm_id: str,
    svc: BoundaryService = Depends(get_boundary_service),
):
    try:
        result = await svc.resolve(osm_id)
    except InvalidOsmId:
        raise HTTPException(status_code=400, detail=f"invalid osm_id: {osm_id}")
    except GeocoderNotFound:
        raise HTTPException(status_code=404, detail=f"no boundary for {osm_id}")
    except AoiTooLarge as e:
        return aoi_too_large_response(e)
    return result.feature
