from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class PlaceCandidate:
    osm_id: str
    display_name: str
    country: str | None
    type: str
    bbox: tuple[float, float, float, float]
    # Nominatim's high-level category: "place", "boundary", "highway",
    # "amenity", "waterway", "natural", ... Used by PlacesService to filter
    # results to actual populated/administrative places. Not exposed in the
    # delivery DTO — it's an internal classification.
    place_class: str = ""


@dataclass(frozen=True)
class BoundaryResult:
    osm_id: str
    feature: dict
    bbox: tuple[float, float, float, float]


class GeocoderError(Exception):
    pass


class GeocoderNotFound(GeocoderError):
    pass


class GeocoderUpstreamError(GeocoderError):
    pass


class GeocoderProvider(ABC):
    @abstractmethod
    async def search(self, q: str, limit: int = 10) -> list[PlaceCandidate]:
        ...

    @abstractmethod
    async def fetch_boundary(self, osm_id: str) -> BoundaryResult:
        ...

    async def aclose(self) -> None:
        return None
