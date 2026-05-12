from fastapi import APIRouter, Depends, Query

from ..api.schemas import GeocodeCandidate
from ..dependencies import get_places_service
from ..services.places_service import PlacesService

router = APIRouter(prefix="/api")


@router.get("/geocode/search", response_model=list[GeocodeCandidate])
async def geocode_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=20),
    svc: PlacesService = Depends(get_places_service),
):
    candidates = await svc.search(q, limit)
    return [
        GeocodeCandidate(
            osm_id=c.osm_id,
            display_name=c.display_name,
            country=c.country,
            type=c.type,
            bbox=c.bbox,
        )
        for c in candidates
    ]
