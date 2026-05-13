import asyncio
import logging
import time

import httpx

from .base import (
    BoundaryResult,
    GeocoderNotFound,
    GeocoderProvider,
    GeocoderUpstreamError,
    PlaceCandidate,
)

log = logging.getLogger(__name__)

_OSM_TYPE_PREFIX = {"node": "N", "way": "W", "relation": "R"}


class LocationIQGeocoder(GeocoderProvider):
    """LocationIQ adapter — paid managed Nominatim+Photon-equivalent.

    Replaces the public Photon/Nominatim composite when an API key is set:
    autocomplete-grade search via /autocomplete, boundary polygons via
    /lookup?osm_ids=...&polygon_geojson=1 (Nominatim-compatible /lookup
    mirror — /search would 400 on osm_ids, that param is /lookup-only).

    Same outward contract as NominatimGeocoder so PlacesService and
    BoundaryService are agnostic to which backend is wired.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        user_agent: str,
        min_interval_s: float = 0.5,
        timeout_s: float = 15.0,
    ):
        if not api_key:
            raise ValueError("LocationIQGeocoder requires a non-empty api_key")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            headers={"User-Agent": user_agent, "Accept-Language": "en"},
        )
        self._rate_lock = asyncio.Lock()
        self._last_call_ts = 0.0
        self._min_interval_s = min_interval_s

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._rate_lock:
            elapsed = time.monotonic() - self._last_call_ts
            if elapsed < self._min_interval_s:
                await asyncio.sleep(self._min_interval_s - elapsed)
            self._last_call_ts = time.monotonic()

    @staticmethod
    def _to_prefixed_osm_id(osm_type: str, osm_id: int) -> str:
        prefix = _OSM_TYPE_PREFIX.get(osm_type)
        if prefix is None:
            raise ValueError(f"unknown osm_type: {osm_type!r}")
        return f"{prefix}{osm_id}"

    @staticmethod
    def _bbox_from_li(bb: list[str]) -> tuple[float, float, float, float]:
        # LocationIQ boundingbox follows Nominatim: [min_lat, max_lat, min_lon, max_lon].
        # Normalize to RFC 7946: (west, south, east, north).
        return (float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1]))

    async def search(self, q: str, limit: int = 10) -> list[PlaceCandidate]:
        await self._throttle()
        try:
            r = await self._client.get(
                f"{self._base_url}/autocomplete",
                params={
                    "key": self._api_key,
                    "q": q,
                    "limit": limit,
                    "format": "json",
                    "tag": "place:city,place:town,place:village,boundary:administrative",
                    "addressdetails": 1,
                    "normalizecity": 1,
                },
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("LocationIQ /autocomplete failed: %s", e)
            raise GeocoderUpstreamError(str(e)) from e

        out: list[PlaceCandidate] = []
        for item in r.json():
            try:
                out.append(
                    PlaceCandidate(
                        osm_id=self._to_prefixed_osm_id(
                            item["osm_type"], int(item["osm_id"])
                        ),
                        display_name=item["display_name"],
                        country=(item.get("address") or {}).get("country"),
                        type=item.get("type", ""),
                        bbox=self._bbox_from_li(item["boundingbox"]),
                        place_class=item.get("class") or "",
                    )
                )
            except (KeyError, ValueError) as e:
                log.debug("skipping malformed locationiq item: %s", e)
        return out

    async def fetch_boundary(self, osm_id: str) -> BoundaryResult:
        await self._throttle()
        try:
            r = await self._client.get(
                f"{self._base_url}/lookup",
                params={
                    "key": self._api_key,
                    "osm_ids": osm_id,
                    "format": "json",
                    "polygon_geojson": 1,
                    "addressdetails": 1,
                },
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("LocationIQ /lookup failed for %s: %s", osm_id, e)
            raise GeocoderUpstreamError(str(e)) from e

        items = r.json()
        if not items:
            raise GeocoderNotFound(f"no place for osm_id={osm_id}")
        item = items[0]
        geom = item.get("geojson")
        if geom and geom.get("type") in ("Polygon", "MultiPolygon"):
            return self._build_boundary(osm_id, item, geom)

        # Same upgrade path as NominatimGeocoder: autocomplete sometimes
        # surfaces a Node for a city (Point geometry), so /search by osm_id
        # returns no polygon. Fall back to a name+country forward query and
        # take the first admin polygon. The outward osm_id stays the input
        # one so the caller's cached id keeps working for /api/zones.
        upgraded = await self._upgrade_to_admin_relation(item)
        if upgraded is None:
            raise GeocoderNotFound(f"no polygon for osm_id={osm_id}")
        return self._build_boundary(osm_id, upgraded, upgraded["geojson"])

    async def _upgrade_to_admin_relation(self, node_item: dict) -> dict | None:
        address = node_item.get("address") or {}
        name = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or node_item.get("display_place")
            or node_item.get("name")
        )
        country = address.get("country")
        if not name or not country:
            return None

        await self._throttle()
        try:
            r = await self._client.get(
                f"{self._base_url}/search",
                params={
                    "key": self._api_key,
                    "city": name,
                    "country": country,
                    "format": "json",
                    "limit": 5,
                    "polygon_geojson": 1,
                    "addressdetails": 1,
                },
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning(
                "LocationIQ /search upgrade failed for %s, %s: %s", name, country, e
            )
            return None

        for candidate in r.json():
            geom = candidate.get("geojson")
            if geom and geom.get("type") in ("Polygon", "MultiPolygon"):
                return candidate
        return None

    def _build_boundary(self, osm_id: str, item: dict, geom: dict) -> BoundaryResult:
        bbox = self._bbox_from_li(item["boundingbox"])
        return BoundaryResult(
            osm_id=osm_id,
            feature={
                "type": "Feature",
                "bbox": list(bbox),
                "geometry": geom,
                "properties": {
                    "osm_id": osm_id,
                    "display_name": item.get("display_name", ""),
                },
            },
            bbox=bbox,
        )
