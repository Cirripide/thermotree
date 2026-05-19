from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .schemas import BoundaryError
from ..services.boundary_service import (
    AoiTooLarge,
    BoundaryService,
    InvalidOsmId,
    ValidatedBoundary,
)
from ..services.geocoder.base import GeocoderNotFound


async def aoi_too_large_handler(request: Request, exc: AoiTooLarge) -> JSONResponse:
    body = BoundaryError(
        message=(
            f"AOI area ({exc.aoi_area_km2:.0f} km²) exceeds maximum allowed "
            f"({exc.max_aoi_area_km2:.0f} km²). Consider selecting a smaller place."
        ),
        aoi_area_km2=round(exc.aoi_area_km2, 1),
        max_aoi_area_km2=exc.max_aoi_area_km2,
    )
    return JSONResponse(status_code=400, content=body.model_dump())


async def resolve_boundary_or_raise(
    svc: BoundaryService, osm_id: str
) -> ValidatedBoundary:
    """Returns the boundary on success; raises HTTPException for invalid /
    missing cases. AoiTooLarge bubbles up to the app-level exception handler.
    """
    try:
        return await svc.resolve(osm_id)
    except InvalidOsmId:
        raise HTTPException(status_code=400, detail=f"invalid osm_id: {osm_id}")
    except GeocoderNotFound:
        raise HTTPException(status_code=404, detail=f"no boundary for {osm_id}")
