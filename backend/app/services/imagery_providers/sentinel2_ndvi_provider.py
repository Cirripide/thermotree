from datetime import date

import numpy as np
import rasterio
import xarray as xr

from .base import AoiBbox, SceneMetadata, _StacIndicatorProvider

_S2_REFL_SCALE = 0.0001
_S2_BASELINE_04_CUTOVER = date(2022, 1, 25)
_S2_VALID_SCL_CLASSES = (4, 5, 6, 7)
_S2_MAX_NODATA_PCT = 10.0

# Backends that publish post-baseline-04 L2A products with the BOA -0.1
# offset already applied to the COG pixels. Reading from these requires
# NO additional offset in our code (applying one would double-correct).
# Planetary Computer ships raw post-baseline-04 reflectance, so the
# offset is applied there as usual.
_S2_BACKENDS_WITH_BAKED_BOA_OFFSET = frozenset({"element84"})


def _s2_reflectance_offset(acquisition_date: date, backend_name: str) -> float:
    if backend_name in _S2_BACKENDS_WITH_BAKED_BOA_OFFSET:
        return 0.0
    return -0.1 if acquisition_date >= _S2_BASELINE_04_CUTOVER else 0.0


class Sentinel2NdviProvider(_StacIndicatorProvider):
    name = "sentinel-2-l2a"
    indicator_label = "vegetation"
    cloud_cover_threshold = 20.0
    # Element84 publishes Sentinel-2 L2A assets under common names
    # (red/nir/scl); Planetary Computer keeps the original band codes
    # (B04/B08/SCL). The underlying pixels are the same — only the STAC
    # asset key differs.
    asset_role_map_by_backend = {
        "element84":          {"red": "red", "nir": "nir", "scl": "scl"},
        "planetary_computer": {"red": "B04", "nir": "B08", "scl": "SCL"},
    }
    # COG overview level used for the per-scene read. Sentinel-2 publishes
    # overviews [×2, ×4, ×8, ×16] from a 10 m native; level 3 = ×8 ≈ 80 m.
    # The deepest level (×16 / 160 m) is observably noisier because each
    # provider generates its overview pyramid with its own resampling kernel,
    # and at the deepest pyramid step PC and Element84 disagree by ~0.13
    # NDVI per pixel on the same scene. At ×8 the pyramid-kernel disagreement
    # is then averaged out by our 300 m area-weighted resample, so per-cell
    # values become provider-independent. Measured wall-clock cost on Milan:
    # +1.6 s vs ×16 (well under the 5 s budget).
    read_overview_level = 3              # ~80 m on Sentinel-2 10 m bands
    native_overview_resolution = 80.0

    @property
    def scenes_per_month_per_tile(self) -> int:
        return self.settings.scenes_per_month_vegetation

    def _tile_id(self, item) -> str | None:
        return item.properties.get("s2:mgrs_tile")

    def _extra_query(self) -> dict:
        return {"s2:nodata_pixel_percentage": {"lt": _S2_MAX_NODATA_PCT}}

    def read_scene_indicator(
        self,
        scene: SceneMetadata,
        aoi_bbox: AoiBbox,
        *,
        overview_level: int = 0,
    ) -> xr.DataArray:
        red = self._open_clip(scene.assets["red"], aoi_bbox, masked=True, overview_level=overview_level)
        nir = self._open_clip(scene.assets["nir"], aoi_bbox, masked=True, overview_level=overview_level)
        scl = self._open_clip(scene.assets["scl"], aoi_bbox, masked=False, overview_level=overview_level)

        scl_aligned = scl.rio.reproject_match(
            red, resampling=rasterio.enums.Resampling.nearest
        )

        offset = _s2_reflectance_offset(scene.acquisition_date, scene.backend_name)
        red_refl = red.astype("float32") * _S2_REFL_SCALE + offset
        nir_refl = nir.astype("float32") * _S2_REFL_SCALE + offset

        denom = nir_refl + red_refl
        with np.errstate(divide="ignore", invalid="ignore"):
            ndvi = xr.where(denom != 0, (nir_refl - red_refl) / denom, np.nan)

        valid = scl_aligned.isin(list(_S2_VALID_SCL_CLASSES)) & np.isfinite(ndvi)
        ndvi = ndvi.where(valid).clip(-1.0, 1.0)

        ndvi.rio.write_crs(red.rio.crs, inplace=True)
        ndvi.name = "ndvi"
        ndvi.attrs.update({
            "indicator": "ndvi",
            "units": "dimensionless",
            "scene_id": scene.scene_id,
            "acquisition_date": str(scene.acquisition_date),
            "boa_offset_applied": offset,
        })
        return ndvi
