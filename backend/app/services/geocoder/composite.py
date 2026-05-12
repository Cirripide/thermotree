from .base import BoundaryResult, GeocoderProvider, PlaceCandidate


class CompositeGeocoder(GeocoderProvider):
    """Routes search and boundary calls to specialized providers.

    Lets us combine an autocomplete-grade search backend (e.g. Photon) with a
    polygon-capable lookup backend (e.g. Nominatim) behind the single
    GeocoderProvider port. PlacesService and BoundaryService see one provider
    and stay unaware of the multiplexing.
    """

    def __init__(
        self,
        search_provider: GeocoderProvider,
        boundary_provider: GeocoderProvider,
    ):
        self._search = search_provider
        self._boundary = boundary_provider

    async def search(self, q: str, limit: int = 10) -> list[PlaceCandidate]:
        return await self._search.search(q, limit)

    async def fetch_boundary(self, osm_id: str) -> BoundaryResult:
        return await self._boundary.fetch_boundary(osm_id)

    async def aclose(self) -> None:
        # Close both, even if one fails — so a slow shutdown on one provider
        # can't mask the other's cleanup.
        try:
            await self._search.aclose()
        finally:
            await self._boundary.aclose()
