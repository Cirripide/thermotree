from fastapi import Depends, Request

from .core.config import Settings, get_settings
from .services.boundary_service import BoundaryService
from .services.geocoder.base import GeocoderProvider
from .services.places_service import PlacesService
from .services.zones_runner import ZonesRunner


# ---- DI graph ----
#
#   route handler --depends--> Service (PlacesService / BoundaryService)
#                                       --depends--> GeocoderProvider (port)
#                                                            ^
#                                                            |
#                                              NominatimGeocoder (adapter)

def get_geocoder(request: Request) -> GeocoderProvider:
    return request.app.state.geocoder


def get_places_service(
    geocoder: GeocoderProvider = Depends(get_geocoder),
) -> PlacesService:
    return PlacesService(geocoder)


def get_boundary_service(
    geocoder: GeocoderProvider = Depends(get_geocoder),
    settings: Settings = Depends(get_settings),
) -> BoundaryService:
    return BoundaryService(geocoder, settings.max_aoi_area_km2)


def get_zones_runner(request: Request) -> ZonesRunner:
    return request.app.state.zones_runner
