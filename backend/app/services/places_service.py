from .geocoder.base import GeocoderProvider, PlaceCandidate

# Nominatim's high-level "class" categorizes records by OSM feature kind:
#   place       → populated places (city/town/village/hamlet/locality/...)
#   boundary    → admin boundaries (the typical polygon for a city/region)
#   highway     → roads/streets
#   amenity     → restaurants, schools, ...
#   waterway    → lakes, rivers
#   natural     → peaks, woods, ...
#
# A city picker should match the first two and reject the rest. Class is a
# more reliable filter than the narrower "type" field because settlement
# vocabulary varies (city / town / village / hamlet / locality /
# isolated_dwelling / municipality / administrative / ...), and filtering on
# any fixed type set drops legitimate frazioni, localities, and admin regions.
_PLACE_CLASSES: frozenset[str] = frozenset({"place", "boundary"})

# Nominatim's documented /search cap is 40; we over-fetch up to that so a
# settlement buried under POIs/water/roads still surfaces after filtering.
_NOMINATIM_MAX_LIMIT = 40


class PlacesService:
    """Application service for place lookup.

    Owns product-level rules: input normalization, server-side limit cap,
    place-class filtering (keep populated/administrative places, drop
    streets/POIs/water/natural features). Depends only on the GeocoderProvider
    port — knows nothing about Nominatim, HTTP, or FastAPI.
    """

    def __init__(self, geocoder: GeocoderProvider, max_results: int = 20):
        self._geocoder = geocoder
        self._max_results = max_results

    async def search(self, q: str, limit: int) -> list[PlaceCandidate]:
        q = q.strip()
        if not q:
            return []
        effective_limit = max(1, min(limit, self._max_results))
        over_fetch = min(_NOMINATIM_MAX_LIMIT, max(effective_limit * 5, 25))
        raw = await self._geocoder.search(q, over_fetch)
        places = [p for p in raw if p.place_class in _PLACE_CLASSES]
        return places[:effective_limit]
