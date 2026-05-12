import numpy as np
import rasterio
import xarray as xr

from .base import AoiBbox, SceneMetadata, _StacIndicatorProvider

_LANDSAT_K_SCALE = 0.00341802
_LANDSAT_K_OFFSET = 149.0
_KELVIN_TO_CELSIUS = -273.15

# qa_pixel: bit 0 fill, 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow,
# 5 snow. Water (bit 7) is intentionally NOT masked — water cells keep their
# LST values and are counted as valid.
_LANDSAT_INVALID_BITS = 0b0011_1111


class LandsatLstProvider(_StacIndicatorProvider):
    name = "landsat-c2-l2"
    indicator_label = "heat"
    cloud_cover_threshold = 30.0
    # Both providers expose Landsat C2-L2 with identical asset keys; we keep
    # the per-backend shape for symmetry with Sentinel-2 and to document the
    # parity assertion explicitly.
    asset_role_map_by_backend = {
        "element84":          {"thermal": "lwir11", "qa": "qa_pixel"},
        "planetary_computer": {"thermal": "lwir11", "qa": "qa_pixel"},
    }
    # Element84's Landsat C2-L2 assets are s3:// hrefs into the
    # usgs-landsat requester-pays bucket with no `alternate` https
    # variant, so anonymous reads fail. Until we wire AWS credentials
    # we can only serve Landsat through Planetary Computer (which
    # signs Azure-hosted https hrefs).
    unsupported_backends = frozenset({"element84"})
    # Landsat C2-L2 Surface Temperature is published on a 30 m grid with
    # overview pyramid [×2, ×4, ×8, ×16]. Level 2 = ×4 ≈ 120 m. We sit one
    # step shallower than the deepest overview so that the 200 m output
    # cells still average a meaningful number of source pixels (~2.8 each)
    # instead of being sub-pixel of the source.
    read_overview_level = 2              # ~120 m on Landsat C2-L2 ST grid
    native_overview_resolution = 120.0

    @property
    def scenes_per_month_per_tile(self) -> int:
        return self.settings.scenes_per_month_heat

    def _tile_id(self, item) -> str | None:
        path = item.properties.get("landsat:wrs_path")
        row = item.properties.get("landsat:wrs_row")
        if path is None or row is None:
            return None
        return f"{path}-{row}"

    def _extra_query(self) -> dict:
        return {
            "platform": {"in": ["landsat-8", "landsat-9"]},
            "landsat:collection_category": {"in": ["T1"]},
            # Daytime only — Landsat occasionally does night thermal-only
            # acquisitions which would corrupt summer LST medians. (S2 doesn't
            # need this filter; its L2A products are daytime by construction.)
            "view:sun_elevation": {"gt": 0},
        }

    def read_scene_indicator(
        self,
        scene: SceneMetadata,
        aoi_bbox: AoiBbox,
        *,
        overview_level: int = 0,
    ) -> xr.DataArray:
        thermal = self._open_clip(scene.assets["thermal"], aoi_bbox, masked=True, overview_level=overview_level)
        qa = self._open_clip(scene.assets["qa"], aoi_bbox, masked=False, overview_level=overview_level)

        qa_aligned = qa.rio.reproject_match(
            thermal, resampling=rasterio.enums.Resampling.nearest
        )

        kelvin = thermal.astype("float32") * _LANDSAT_K_SCALE + _LANDSAT_K_OFFSET
        celsius = kelvin + _KELVIN_TO_CELSIUS

        valid = (
            (qa_aligned.astype("uint16") & _LANDSAT_INVALID_BITS) == 0
        ) & np.isfinite(celsius)
        celsius = celsius.where(valid)

        celsius.rio.write_crs(thermal.rio.crs, inplace=True)
        celsius.name = "lst_celsius"
        celsius.attrs.update({
            "indicator": "lst",
            "units": "celsius",
            "scene_id": scene.scene_id,
            "acquisition_date": str(scene.acquisition_date),
        })
        return celsius
