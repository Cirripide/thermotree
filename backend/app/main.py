from contextlib import asynccontextmanager

from fastapi import FastAPI

from .controllers import boundary, geocoding, health, zones
from .core.config import get_settings
from .services.geocoder.composite import CompositeGeocoder
from .services.geocoder.nominatim import NominatimGeocoder
from .services.geocoder.photon import PhotonGeocoder
from .services.zones_runner import ZonesRunner


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Photon handles search (autocomplete-grade prefix matching).
    # Nominatim handles boundary lookups (relation -> polygon).
    photon = PhotonGeocoder(
        base_url=settings.photon_base_url,
        user_agent=settings.photon_user_agent,
    )
    nominatim = NominatimGeocoder(
        base_url=settings.nominatim_base_url,
        user_agent=settings.nominatim_user_agent,
    )
    app.state.geocoder = CompositeGeocoder(
        search_provider=photon,
        boundary_provider=nominatim,
    )
    app.state.zones_runner = ZonesRunner(
        max_concurrent=settings.max_concurrent_zone_builds,
    )
    try:
        yield
    finally:
        await app.state.geocoder.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(health.router)
app.include_router(geocoding.router)
app.include_router(boundary.router)
app.include_router(zones.router)
