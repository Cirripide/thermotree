import logging

from fastapi import APIRouter, Depends, HTTPException

from ..api.errors import aoi_too_large_response, resolve_boundary_or_raise
from ..core.config import get_settings
from ..dependencies import get_boundary_service, get_zones_runner
from ..services.boundary_service import AoiTooLarge, BoundaryService
from ..services.imagery_providers.base import InadequateDataQualityError
from ..services.zones_runner import ZonesRunner

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/zones/{osm_id}/{year}")
async def get_zones(
    osm_id: str,
    year: int,
    svc: BoundaryService = Depends(get_boundary_service),
    runner: ZonesRunner = Depends(get_zones_runner),
):
    try:
        boundary = await resolve_boundary_or_raise(svc, osm_id)
    except AoiTooLarge as e:
        return aoi_too_large_response(e)

    settings = get_settings()
    time_range = (
        f"{year}-{settings.summer_window_start}/"
        f"{year}-{settings.summer_window_end}"
    )

    try:
        return await runner.run(osm_id, year, boundary, time_range)
    except InadequateDataQualityError as e:
        log.warning(
            "Zones request rejected for %s/%d: %s (%s, %d scenes)",
            osm_id, year, e.reason, e.indicator_label, e.scene_count,
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "Available satellite imagery for this city/year is of "
                    "inadequate quality for reliable analysis (e.g., "
                    "excessive cloud cover or missing images)."
                ),
                "quality_status": "inadequate",
                "indicator": e.indicator_label,
                "reason": e.reason,
                "scene_count": e.scene_count,
            },
        )
    except ValueError as e:
        log.warning("Zones build failed for %s/%d: %s", osm_id, year, e)
        raise HTTPException(status_code=404, detail=str(e))
