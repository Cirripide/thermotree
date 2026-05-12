from pydantic import BaseModel


class GeocodeCandidate(BaseModel):
    osm_id: str
    display_name: str
    country: str | None
    type: str
    bbox: tuple[float, float, float, float]


class BoundaryError(BaseModel):
    message: str
    aoi_area_km2: float
    max_aoi_area_km2: float
