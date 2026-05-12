import xarray as xr

from app.services.imagery_providers import AoiBbox, IndicatorProvider, SceneMetadata


class IndicatorService:
    """Indicator-agnostic facade over an `IndicatorProvider`. The specific
    indicator (heat / vegetation) is determined by the provider passed in
    (`LandsatLstProvider`, `Sentinel2NdviProvider`, …)."""

    def __init__(self, provider: IndicatorProvider) -> None:
        self.provider = provider

    def list_scenes(self, aoi_bbox: AoiBbox, time_range: str) -> list[SceneMetadata]:
        return self.provider.discover_scenes(aoi_bbox, time_range)

    def read_scene(self, scene: SceneMetadata, aoi_bbox: AoiBbox) -> xr.DataArray:
        return self.provider.read_scene_indicator(scene, aoi_bbox)

    def build_summer_composite(
        self,
        aoi_bbox: AoiBbox,
        time_range: str,
    ) -> xr.DataArray:
        return self.provider.build_summer_composite(aoi_bbox, time_range)
