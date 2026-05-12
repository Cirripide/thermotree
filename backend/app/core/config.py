from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

import os

current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, "../../../.env")

# Geocoder defaults. Kept as module constants so the validators below can fall
# back to the same value the field declares, in one place.
DEFAULT_NOMINATIM_BASE_URL = "https://nominatim.openstreetmap.org"
DEFAULT_NOMINATIM_USER_AGENT = (
    "thermotree/0.1 (set NOMINATIM_USER_AGENT to your contact email)"
)
DEFAULT_PHOTON_BASE_URL = "https://photon.komoot.io"
DEFAULT_PHOTON_USER_AGENT = (
    "thermotree/0.1 (set PHOTON_USER_AGENT to your contact email)"
)

# STAC backend URLs. The runtime resolves a provider name (e.g. "element84")
# to one of these via the registry in services/imagery_providers/base.py.
ELEMENT84_STAC_URL = "https://earth-search.aws.element84.com/v1"
PLANETARY_COMPUTER_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
DEFAULT_STAC_PROVIDERS = ["planetary_computer", "element84"]


class Settings(BaseSettings):
    # Geocoder backends. Search runs through Photon (autocomplete-grade);
    # polygon retrieval (/api/boundary/{osm_id}) runs through Nominatim. Both
    # are overridable via env for self-hosted instances.
    nominatim_base_url: str = DEFAULT_NOMINATIM_BASE_URL
    nominatim_user_agent: str = DEFAULT_NOMINATIM_USER_AGENT
    photon_base_url: str = DEFAULT_PHOTON_BASE_URL
    photon_user_agent: str = DEFAULT_PHOTON_USER_AGENT

    # STAC backends, in priority order. Planetary Computer is the primary
    # because its Azure-hosted COGs read faster than Element84's AWS S3
    # mirror; Element84 is kept as a fallback for transport-level failures
    # only (timeouts, 5xx, connection errors) — not for empty results or
    # quality rejections.
    stac_providers: list[str] = DEFAULT_STAC_PROVIDERS
    summer_window_start: str = "06-01"
    summer_window_end: str = "08-31"

    # Scene selection (indicator-agnostic tuning)
    scene_selection_enabled: bool = True
    scenes_per_month_vegetation: int = 3
    scenes_per_month_heat: int = 4
    aoi_coverage_threshold: float = 0.80

    # Output grid
    target_resolution_meters: float = 200.0

    # Data quality thresholds
    min_scenes_for_composite: int = 3
    max_mean_cloud_cover_pct: float = 40.0
    max_target_cell_nan_ratio: float = 0.5

    # AOI safety
    max_aoi_area_km2: float = 3000.0

    # Concurrency. Hard cap on simultaneous composite builds per worker. Each
    # build holds hundreds of MB of xarray data while running, so without a
    # cap N concurrent users would OOM the worker. Coalescing in ZonesRunner
    # means two users selecting the same (osm_id, year) count as one slot.
    max_concurrent_zone_builds: int = 2

    model_config = SettingsConfigDict(
        env_file=env_path,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # docker-compose's ${VAR:-} substitution turns unset env vars into the
    # empty string, which Pydantic then accepts as a real value (overriding the
    # field default). These validators coerce empty/whitespace strings back to
    # the documented default so optional vars truly behave as optional.
    @field_validator("nominatim_base_url", mode="before")
    @classmethod
    def _nominatim_base_url_fallback(cls, v: str | None) -> str:
        if v and str(v).strip():
            return str(v)
        return DEFAULT_NOMINATIM_BASE_URL

    @field_validator("nominatim_user_agent", mode="before")
    @classmethod
    def _nominatim_user_agent_fallback(cls, v: str | None) -> str:
        if v and str(v).strip():
            return str(v)
        return DEFAULT_NOMINATIM_USER_AGENT

    @field_validator("photon_base_url", mode="before")
    @classmethod
    def _photon_base_url_fallback(cls, v: str | None) -> str:
        if v and str(v).strip():
            return str(v)
        return DEFAULT_PHOTON_BASE_URL

    @field_validator("photon_user_agent", mode="before")
    @classmethod
    def _photon_user_agent_fallback(cls, v: str | None) -> str:
        if v and str(v).strip():
            return str(v)
        return DEFAULT_PHOTON_USER_AGENT

    # STAC_PROVIDERS arrives from env as a comma-separated string ("a,b").
    # Pydantic doesn't split that natively for list[str], so we parse it here
    # and also normalize entries (lowercase, strip, drop empties). Empty input
    # collapses to the documented default so an unset/blank env var behaves
    # like "use the defaults."
    @field_validator("stac_providers", mode="before")
    @classmethod
    def _stac_providers_parse(cls, v: object) -> list[str]:
        if v is None:
            return list(DEFAULT_STAC_PROVIDERS)
        if isinstance(v, str):
            entries = [item.strip().lower() for item in v.split(",")]
        elif isinstance(v, (list, tuple)):
            entries = [str(item).strip().lower() for item in v]
        else:
            entries = []
        entries = [e for e in entries if e]
        return entries or list(DEFAULT_STAC_PROVIDERS)

@lru_cache
def get_settings() -> Settings:
    return Settings()
