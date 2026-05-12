import asyncio
import logging
import time

import httpx

from .base import (
    BoundaryResult,
    GeocoderProvider,
    GeocoderUpstreamError,
    PlaceCandidate,
)

log = logging.getLogger(__name__)


class PhotonGeocoder(GeocoderProvider):
    """Photon adapter — autocomplete-grade /api/ search.

    Photon does not expose polygon retrieval; fetch_boundary raises so a
    misconfigured composite fails loudly instead of returning a centroid.
    """

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        min_interval_s: float = 0.1,
        timeout_s: float = 10.0,
    ):
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
    def _bbox_from_photon(extent: list[float]) -> tuple[float, float, float, float]:
        # Photon: [minLon, maxLat, maxLon, minLat] — LAT order is reversed.
        # Normalize to RFC 7946: (minLon, minLat, maxLon, maxLat).
        return (float(extent[0]), float(extent[3]), float(extent[2]), float(extent[1]))

    @staticmethod
    def _compose_display_name(props: dict) -> str:
        parts = [
            props.get("name"),
            props.get("county"),
            props.get("state"),
            props.get("country"),
        ]
        return ", ".join(p for p in parts if p)

    @staticmethod
    def _prefixed_osm_id(osm_type: str, osm_id: int) -> str:
        # Photon already gives osm_type as "N"/"W"/"R". Defensively map long
        # form just in case.
        t = osm_type.upper()
        if t in ("NODE", "WAY", "RELATION"):
            t = t[0]
        return f"{t}{osm_id}"

    async def search(self, q: str, limit: int = 10) -> list[PlaceCandidate]:
        await self._throttle()
        # osm_tag=place&osm_tag=boundary pre-filters at Photon side; the
        # downstream PlacesService still applies the same class filter as
        # defense in depth.
        try:
            r = await self._client.get(
                f"{self._base_url}/api/",
                params=[
                    ("q", q),
                    ("limit", str(limit)),
                    ("osm_tag", "place"),
                    ("osm_tag", "boundary"),
                ],
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("Photon /api/ failed: %s", e)
            raise GeocoderUpstreamError(str(e)) from e

        body = r.json()
        out: list[PlaceCandidate] = []
        for feat in body.get("features", []):
            props = feat.get("properties") or {}
            extent = props.get("extent")
            if extent and len(extent) == 4:
                bbox = self._bbox_from_photon(extent)
            else:
                # Photon omits extent for some small features. Fall back to a
                # tiny bbox around the centroid so the FE still has something
                # to fitBounds on — Nominatim's /lookup returns the real
                # polygon on selection anyway.
                geom = feat.get("geometry") or {}
                coords = geom.get("coordinates")
                if not coords or len(coords) != 2:
                    continue
                lon, lat = float(coords[0]), float(coords[1])
                d = 0.01
                bbox = (lon - d, lat - d, lon + d, lat + d)
            try:
                out.append(
                    PlaceCandidate(
                        osm_id=self._prefixed_osm_id(
                            props["osm_type"], int(props["osm_id"])
                        ),
                        display_name=self._compose_display_name(props),
                        country=props.get("country"),
                        type=props.get("osm_value", ""),
                        bbox=bbox,
                        place_class=props.get("osm_key", ""),
                    )
                )
            except (KeyError, ValueError) as e:
                log.debug("skipping malformed photon feature: %s", e)
        return out

    async def fetch_boundary(self, osm_id: str) -> BoundaryResult:
        raise NotImplementedError(
            "Photon does not support polygon retrieval; "
            "wire boundary lookups through Nominatim via CompositeGeocoder."
        )
