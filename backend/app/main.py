from contextlib import asynccontextmanager

from fastapi import FastAPI

from .controllers import boundary, geocoding, health, zones
from .core.config import get_settings
from .services.geocoder.composite import CompositeGeocoder
from .services.geocoder.locationiq import LocationIQGeocoder
from .services.geocoder.nominatim import NominatimGeocoder
from .services.geocoder.photon import PhotonGeocoder
from .services.zones_runner import ZonesRunner


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # LOCATIONIQ_API_KEY set -> paid managed provider handles both search and
    # boundary lookups in one backend. Otherwise fall back to the public
    # Photon + Nominatim composite (dev path; risks being banned in prod).
    if settings.locationiq_api_key:
        app.state.geocoder = LocationIQGeocoder(
            api_key=settings.locationiq_api_key,
            base_url=settings.locationiq_base_url,
            user_agent=settings.locationiq_user_agent,
        )
    else:
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
