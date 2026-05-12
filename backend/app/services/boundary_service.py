import re
from dataclasses import dataclass

from .geocoder.base import BoundaryResult, GeocoderProvider
from .imagery_providers.base import aoi_bbox_area_km2

_OSM_ID_RE = re.compile(r"^[NWR]\d+$")


class InvalidOsmId(Exception):
    def __init__(self, osm_id: str):
        self.osm_id = osm_id
        super().__init__(f"invalid osm_id: {osm_id!r}")


class AoiTooLarge(Exception):
    def __init__(self, aoi_area_km2: float, max_aoi_area_km2: float):
        self.aoi_area_km2 = aoi_area_km2
        self.max_aoi_area_km2 = max_aoi_area_km2
        super().__init__(
            f"AOI {aoi_area_km2:.0f} km² exceeds maximum {max_aoi_area_km2:.0f} km²"
        )


@dataclass(frozen=True)
class ValidatedBoundary:
    osm_id: str
    feature: dict
    bbox: tuple[float, float, float, float]
    area_km2: float  # bbox area, in km² — matches what the imagery pipeline processes


class BoundaryService:
    """Application service for boundary resolution.

    Owns product policy: osm_id shape validation, AOI cap enforcement.
    Knows nothing about Nominatim, HTTP, or FastAPI.

    The area metric is the *bbox area* (not the polygon area) because the
    downstream imagery pipeline reads pixels over the bbox — making the bbox
    area the right thing to bound. Slice 2's grid endpoint will reuse this
    service so the cap and validation live in one place.
    """

    def __init__(self, geocoder: GeocoderProvider, max_aoi_area_km2: float):
        self._geocoder = geocoder
        self._max_aoi_area_km2 = max_aoi_area_km2

    async def resolve(self, osm_id: str) -> ValidatedBoundary:
        if not _OSM_ID_RE.match(osm_id):
            raise InvalidOsmId(osm_id)
        result: BoundaryResult = await self._geocoder.fetch_boundary(osm_id)
        area_km2 = aoi_bbox_area_km2(result.bbox)
        if area_km2 > self._max_aoi_area_km2:
            raise AoiTooLarge(area_km2, self._max_aoi_area_km2)
        return ValidatedBoundary(
            osm_id=result.osm_id,
            feature=result.feature,
            bbox=result.bbox,
            area_km2=area_km2,
        )
