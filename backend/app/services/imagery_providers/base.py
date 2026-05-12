from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Protocol, runtime_checkable

import numpy as np
import planetary_computer
import pystac_client
import requests
import rioxarray  # noqa: F401  registers .rio xarray accessor on import
import shapely.geometry
import shapely.ops
import urllib3
import xarray as xr
from pyproj import CRS, Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_origin

from app.core.config import (
    ELEMENT84_STAC_URL,
    PLANETARY_COMPUTER_STAC_URL,
    get_settings,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class StacBackend:
    """A single STAC API endpoint and the asset-signing function it needs.

    `modifier` is passed to pystac_client.Client.open() to mutate items
    after search (e.g. Planetary Computer's SAS token injection). Element84
    serves public S3 hrefs and needs no modifier."""
    name: str
    api_url: str
    modifier: Callable | None


STAC_BACKENDS: dict[str, StacBackend] = {
    "element84": StacBackend(
        name="element84",
        api_url=ELEMENT84_STAC_URL,
        modifier=None,
    ),
    "planetary_computer": StacBackend(
        name="planetary_computer",
        api_url=PLANETARY_COMPUTER_STAC_URL,
        modifier=planetary_computer.sign_inplace,
    ),
}

# Transport-level failures that should trigger fallback to the next backend.
# Data-quality verdicts (InadequateDataQualityError, KeyError on a missing
# asset, ValueError) are NOT included — those are authoritative answers,
# not provider availability problems.
_RETRYABLE_NETWORK_EXCEPTIONS: tuple[type[BaseException], ...] = (
    pystac_client.exceptions.APIError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.HTTPError,
    urllib3.exceptions.HTTPError,
)

AoiBbox = tuple[float, float, float, float]

SUMMER_MONTHS = (6, 7, 8)
MONTH_NAMES = {6: "June", 7: "July", 8: "August"}


@dataclass(frozen=True)
class SceneMetadata:
    scene_id: str
    acquisition_date: date
    cloud_cover: float
    assets: dict[str, str]
    # Source backend (StacBackend.name). Carried because some indicators
    # apply per-backend corrections at read time — e.g. Sentinel-2 post-
    # baseline-04 BOA offset is already baked into Element84's COGs but
    # not Planetary Computer's, so the indicator must know who produced
    # the asset to decide whether to apply it.
    backend_name: str


class InadequateDataQualityError(Exception):
    """Raised when usable scenes for a composite are too few, too cloudy,
    or fail temporal continuity to produce a reliable summer median."""

    def __init__(
        self,
        indicator_label: str,
        reason: str,
        scene_count: int,
    ) -> None:
        self.indicator_label = indicator_label
        self.reason = reason
        self.scene_count = scene_count
        super().__init__(f"{indicator_label}: {reason}")


@runtime_checkable
class IndicatorProvider(Protocol):
    name: str
    cloud_cover_threshold: float

    def discover_scenes(
        self,
        aoi_bbox: AoiBbox,
        time_range: str,
    ) -> list[SceneMetadata]: ...

    def read_scene_indicator(
        self,
        scene: SceneMetadata,
        aoi_bbox: AoiBbox,
    ) -> xr.DataArray: ...

    def build_summer_composite(
        self,
        aoi_bbox: AoiBbox,
        time_range: str,
    ) -> xr.DataArray: ...


class _StacIndicatorProvider:
    # Static identity / data-source properties — set on subclasses.
    name: ClassVar[str]
    indicator_label: ClassVar[str]               # "heat" | "vegetation"
    cloud_cover_threshold: ClassVar[float]
    # Asset role → backend-specific asset key. Outer key is StacBackend.name;
    # inner mapping is consumed by _to_scene_metadata. Required so a single
    # indicator (e.g. Sentinel-2 NDVI) can run against backends that publish
    # the same bands under different keys (Element84: red/nir/scl;
    # Planetary Computer: B04/B08/SCL).
    asset_role_map_by_backend: ClassVar[dict[str, dict[str, str]]]
    # Backends that cannot serve this indicator regardless of `stac_providers`
    # order — typically because the asset hrefs are unreadable in our runtime
    # (e.g. requester-pays S3 with no AWS credentials). Filtered out at
    # chain resolution time. Empty by default.
    unsupported_backends: ClassVar[frozenset[str]] = frozenset()
    read_overview_level: ClassVar[int]           # COG pyramid index to fetch
    native_overview_resolution: ClassVar[float]  # meters at that overview

    def __init__(self) -> None:
        self.settings = get_settings()

    # --- subclass hooks ---------------------------------------------------

    @property
    def scenes_per_month_per_tile(self) -> int:
        raise NotImplementedError

    def _tile_id(self, item) -> str | None:
        """Return a stable per-tile key from STAC item properties, or None
        if missing (item is then dropped with a warning)."""
        raise NotImplementedError

    def discover_scenes(
        self,
        aoi_bbox: AoiBbox,
        time_range: str,
    ) -> list[SceneMetadata]:
        backends = self._resolve_backend_chain()
        last_network_exc: BaseException | None = None
        for backend in backends:
            try:
                return self._discover_on_backend(backend, aoi_bbox, time_range)
            except _RETRYABLE_NETWORK_EXCEPTIONS as exc:
                log.warning(
                    "STAC backend %s failed (%s: %s) — trying next",
                    backend.name, type(exc).__name__, exc,
                )
                last_network_exc = exc
                continue
        # Every configured backend errored at the transport layer. Re-raise
        # the last exception so the caller sees a real failure (not an empty
        # list, which would be misread as "no scenes available").
        assert last_network_exc is not None
        raise last_network_exc

    def _resolve_backend_chain(self) -> list[StacBackend]:
        chain: list[StacBackend] = []
        for provider_name in self.settings.stac_providers:
            backend = STAC_BACKENDS.get(provider_name)
            if backend is None:
                log.warning(
                    "Unknown STAC provider %r in stac_providers — skipping",
                    provider_name,
                )
                continue
            if provider_name in self.unsupported_backends:
                log.info(
                    "%s: skipping backend %r (declared unsupported for "
                    "this indicator)",
                    self.indicator_label, provider_name,
                )
                continue
            chain.append(backend)
        if not chain:
            raise ValueError(
                f"No usable STAC backends for {self.indicator_label!r} "
                f"in stac_providers={self.settings.stac_providers!r} "
                f"(unsupported for this indicator: "
                f"{sorted(self.unsupported_backends)}; "
                f"valid names: {sorted(STAC_BACKENDS)})"
            )
        return chain

    def _discover_on_backend(
        self,
        backend: StacBackend,
        aoi_bbox: AoiBbox,
        time_range: str,
    ) -> list[SceneMetadata]:
        open_kwargs: dict = {}
        if backend.modifier is not None:
            open_kwargs["modifier"] = backend.modifier
        catalog = pystac_client.Client.open(backend.api_url, **open_kwargs)
        query = {"eo:cloud_cover": {"lt": self.cloud_cover_threshold}}
        query.update(self._extra_query())

        search = catalog.search(
            collections=[self.name],
            bbox=aoi_bbox,
            datetime=time_range,
            query=query,
        )
        items = list(search.items())
        log.info(
            "STAC %s via %s: %d raw items for bbox=%s range=%s",
            self.name, backend.name, len(items), aoi_bbox, time_range,
        )

        if self.settings.scene_selection_enabled and items:
            items = self._select_best_scenes(items, aoi_bbox)

        return [self._to_scene_metadata(item, backend) for item in items]

    # --- scene selection (hybrid) ----------------------------------------

    def _select_best_scenes(self, items: list, aoi_bbox: AoiBbox) -> list:
        full_coverage = self._dates_with_full_aoi_coverage(items, aoi_bbox)
        if full_coverage:
            months_with_coverage = {d.month for d, _ in full_coverage}
            if all(m in months_with_coverage for m in SUMMER_MONTHS):
                log.info(
                    "%s: spatial-coherence mode (%d AOI-complete dates)",
                    self.name, len(full_coverage),
                )
                return self._select_by_date(full_coverage)
            log.info(
                "%s: spatial-coherence has month gaps (covered: %s); "
                "using per-tile mode to preserve continuity",
                self.name, sorted(months_with_coverage),
            )
        else:
            log.info(
                "%s: per-tile fallback (no single-day AOI coverage)",
                self.name,
            )
        return self._select_per_tile(items)

    def _dates_with_full_aoi_coverage(
        self,
        items: list,
        aoi_bbox: AoiBbox,
    ) -> list[tuple[date, list]]:
        aoi_geom = shapely.geometry.box(*aoi_bbox)
        threshold = self.settings.aoi_coverage_threshold

        by_date: dict[date, list] = defaultdict(list)
        for item in items:
            if item.datetime is None:
                log.warning(
                    "%s: item %s missing datetime; dropping",
                    self.name, item.id,
                )
                continue
            by_date[item.datetime.date()].append(item)

        result: list[tuple[date, list]] = []
        for d, day_items in by_date.items():
            footprints = [
                shapely.geometry.shape(it.geometry) for it in day_items
            ]
            covered = (
                shapely.ops.unary_union(footprints)
                .intersection(aoi_geom).area
            )
            if covered / aoi_geom.area >= threshold:
                result.append((d, day_items))
        return result

    def _select_by_date(
        self,
        coverage_dates: list[tuple[date, list]],
    ) -> list:
        cap = self.scenes_per_month_per_tile
        by_month: dict[int, list[tuple[float, date, list]]] = defaultdict(list)
        for d, day_items in coverage_dates:
            mean_cc = sum(
                float(it.properties.get("eo:cloud_cover", float("inf")))
                for it in day_items
            ) / len(day_items)
            by_month[d.month].append((mean_cc, d, day_items))

        selected: list = []
        kept_dates = 0
        for entries in by_month.values():
            entries.sort(key=lambda triple: triple[0])
            for _, _, day_items in entries[:cap]:
                selected.extend(day_items)
                kept_dates += 1

        selected.sort(key=lambda it: it.datetime)
        log.info(
            "%s: selected %d items across %d AOI-complete dates",
            self.name, len(selected), kept_dates,
        )
        return selected

    def _select_per_tile(self, items: list) -> list:
        cap = self.scenes_per_month_per_tile
        by_bucket: dict[tuple[int, str], list] = defaultdict(list)
        dropped = 0
        for item in items:
            if item.datetime is None:
                dropped += 1
                continue
            tile_id = self._tile_id(item)
            if tile_id is None:
                log.warning(
                    "%s: item %s missing tile id; dropping",
                    self.name, item.id,
                )
                dropped += 1
                continue
            by_bucket[(item.datetime.month, tile_id)].append(item)

        selected: list = []
        for bucket in by_bucket.values():
            bucket.sort(
                key=lambda it: float(
                    it.properties.get("eo:cloud_cover", float("inf"))
                )
            )
            selected.extend(bucket[:cap])
        selected.sort(key=lambda it: it.datetime)

        tiles = {tile for _, tile in by_bucket}
        log.info(
            "%s: selected %d items across %d tiles (dropped %d malformed)",
            self.name, len(selected), len(tiles), dropped,
        )
        return selected

    # --- quality validation ----------------------------------------------

    def _validate_scene_quality(
        self,
        scenes: list[SceneMetadata],
    ) -> None:
        min_required = self.settings.min_scenes_for_composite
        if len(scenes) < min_required:
            reason = (
                f"only {len(scenes)} usable scenes after filtering "
                f"(minimum required: {min_required})"
            )
            log.warning(
                "%s: rejecting composite — %s",
                self.indicator_label, reason,
            )
            raise InadequateDataQualityError(
                indicator_label=self.indicator_label,
                reason=reason,
                scene_count=len(scenes),
            )

        max_mean_cc = self.settings.max_mean_cloud_cover_pct
        mean_cc = sum(s.cloud_cover for s in scenes) / len(scenes)
        if mean_cc > max_mean_cc:
            reason = (
                f"mean cloud cover of selected scenes is {mean_cc:.1f}% "
                f"(maximum acceptable: {max_mean_cc}%)"
            )
            log.warning(
                "%s: rejecting composite — %s",
                self.indicator_label, reason,
            )
            raise InadequateDataQualityError(
                indicator_label=self.indicator_label,
                reason=reason,
                scene_count=len(scenes),
            )

    def _validate_monthly_coverage(
        self,
        scenes: list[SceneMetadata],
    ) -> None:
        months_present = {s.acquisition_date.month for s in scenes}
        missing = [
            MONTH_NAMES[m] for m in SUMMER_MONTHS if m not in months_present
        ]
        if missing:
            reason = f"missing monthly continuity for {missing[0]}"
            log.warning(
                "%s: rejecting composite — %s (months present: %s)",
                self.indicator_label, reason, sorted(months_present),
            )
            raise InadequateDataQualityError(
                indicator_label=self.indicator_label,
                reason=reason,
                scene_count=len(scenes),
            )

    def _extra_query(self) -> dict:
        return {}

    def _to_scene_metadata(self, item, backend: StacBackend) -> SceneMetadata:
        asset_role_map = self.asset_role_map_by_backend[backend.name]
        return SceneMetadata(
            scene_id=item.id,
            acquisition_date=item.datetime.date(),
            cloud_cover=float(item.properties.get("eo:cloud_cover", 0.0)),
            assets={
                role: item.assets[asset_key].href
                for role, asset_key in asset_role_map.items()
            },
            backend_name=backend.name,
        )

    @staticmethod
    def _open_clip(
        url: str,
        aoi_bbox: AoiBbox,
        *,
        masked: bool,
        overview_level: int = 0,
    ) -> xr.DataArray:
        kwargs: dict = {"masked": masked}
        if overview_level > 0:
            kwargs["overview_level"] = overview_level - 1
        return (
            rioxarray.open_rasterio(url, **kwargs)
            .squeeze("band", drop=True)
            .rio.clip_box(*aoi_bbox, crs="EPSG:4326")
        )

    def build_summer_composite(
        self,
        aoi_bbox: AoiBbox,
        time_range: str,
    ) -> xr.DataArray:
        import time
        t0 = time.perf_counter()
        scenes = self.discover_scenes(aoi_bbox, time_range)
        t_stac = time.perf_counter()
        self._validate_scene_quality(scenes)
        self._validate_monthly_coverage(scenes)
        log.warning("[timing] %s STAC+select: %.2fs", self.indicator_label, t_stac - t0)

        log.info(
            "Building %s composite from %d scenes "
            "(read overview=%d → native %dm → target %.0fm)",
            self.indicator_label, len(scenes),
            self.read_overview_level,
            int(self.native_overview_resolution),
            self.settings.target_resolution_meters,
        )

        # --- Read scenes at COG overview ---
        # Sequential by design — empirically faster than ThreadPoolExecutor.
        # GDAL/curl reuses one HTTPS connection to Azure Blob across all
        # range reads (warm TCP window + TLS session reuse). Parallel workers
        # each open their own connection → handshake + slow-start per thread.
        t_read_start = time.perf_counter()
        per_scene_data = [
            self.read_scene_indicator(
                scene, aoi_bbox,
                overview_level=self.read_overview_level,
            )
            for scene in scenes
        ]
        t_read_end = time.perf_counter()
        log.warning("[timing] %s COG reads (%d scenes): %.2fs",
                 self.indicator_label, len(scenes), t_read_end - t_read_start)

        # --- Align all scenes to the native UTM grid ---
        target_crs = _utm_crs_for_bbox(aoi_bbox)
        native_grid = _build_grid(
            aoi_bbox, self.native_overview_resolution, target_crs,
        )
        aligned = [
            da.rio.reproject_match(native_grid).expand_dims(time=[s.acquisition_date])
            for da, s in zip(per_scene_data, scenes)
        ]
        cube = xr.concat(aligned, dim="time", join="override")

        # --- Step 1: "Tris rule" — pixel-level temporal continuity ---
        months_per_scene = np.array(
            [s.acquisition_date.month for s in scenes]
        )
        pixel_has_data: dict[int, xr.DataArray] = {}
        for m in SUMMER_MONTHS:
            month_idx = np.where(months_per_scene == m)[0].tolist()
            month_cube = cube.isel(time=month_idx)
            pixel_has_data[m] = np.isfinite(month_cube).any(dim="time")

        full_continuity = (
            pixel_has_data[6] & pixel_has_data[7] & pixel_has_data[8]
        )
        if not bool(full_continuity.any()):
            per_month = {
                MONTH_NAMES[m]: int(mask.sum())
                for m, mask in pixel_has_data.items()
            }
            bottleneck = min(per_month, key=per_month.get)
            log.warning(
                "%s: rejecting composite — no native pixel has valid data "
                "across all summer months (per-month valid counts: %s)",
                self.indicator_label, per_month,
            )
            raise InadequateDataQualityError(
                indicator_label=self.indicator_label,
                reason=f"missing monthly continuity for {bottleneck}",
                scene_count=len(scenes),
            )

        cube_filtered = cube.where(full_continuity)

        # --- Temporal median at native resolution ---
        native_median = cube_filtered.median(dim="time", skipna=True)
        native_median.rio.write_crs(target_crs, inplace=True)
        native_median.rio.write_transform(
            native_grid.rio.transform(), inplace=True,
        )

        t_pipeline = time.perf_counter()
        log.warning("[timing] %s reproject+Step1+median: %.2fs",
                 self.indicator_label, t_pipeline - t_read_end)

        # --- Step 2: Resample to target with area-weighted validity gate ---
        composite = _resample_with_validity_gate(
            native_median,
            aoi_bbox=aoi_bbox,
            target_resolution_m=self.settings.target_resolution_meters,
            target_crs=target_crs,
            max_nan_ratio=self.settings.max_target_cell_nan_ratio,
        )
        t_done = time.perf_counter()
        log.warning("[timing] %s Step2 (resample+gate): %.2fs", self.indicator_label, t_done - t_pipeline)
        log.warning("[timing] %s TOTAL: %.2fs", self.indicator_label, t_done - t0)

        composite.name = per_scene_data[0].name
        dates = sorted(s.acquisition_date for s in scenes)
        composite.attrs.update({
            "indicator": per_scene_data[0].attrs.get("indicator"),
            "units": per_scene_data[0].attrs.get("units"),
            "reduction": "native_median_then_area_weighted_resample_with_gate",
            "scene_count": len(scenes),
            "time_range": time_range,
            "first_acquisition": str(dates[0]),
            "last_acquisition": str(dates[-1]),
            "native_resolution_meters": self.native_overview_resolution,
            "target_resolution_meters": self.settings.target_resolution_meters,
        })

        return composite


# --- module-level grid + geo helpers --------------------------------------

def _build_grid(
    aoi_bbox: AoiBbox,
    resolution_m: float,
    target_crs: CRS,
) -> xr.DataArray:
    """Empty UTM grid at `resolution_m`, snapped so origin is a multiple
    of `resolution_m`. Used for both native and target grids."""
    transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    xs, ys = transformer.transform(
        [aoi_bbox[0], aoi_bbox[2], aoi_bbox[0], aoi_bbox[2]],
        [aoi_bbox[1], aoi_bbox[1], aoi_bbox[3], aoi_bbox[3]],
    )
    minx = float(np.floor(min(xs) / resolution_m) * resolution_m)
    miny = float(np.floor(min(ys) / resolution_m) * resolution_m)
    maxx = float(np.ceil(max(xs) / resolution_m) * resolution_m)
    maxy = float(np.ceil(max(ys) / resolution_m) * resolution_m)

    width = int((maxx - minx) / resolution_m)
    height = int((maxy - miny) / resolution_m)
    transform = from_origin(minx, maxy, resolution_m, resolution_m)

    grid = xr.DataArray(
        np.zeros((height, width), dtype="float32"),
        dims=("y", "x"),
        coords={
            "x": minx + (np.arange(width) + 0.5) * resolution_m,
            "y": maxy - (np.arange(height) + 0.5) * resolution_m,
        },
    )
    grid.rio.write_crs(target_crs, inplace=True)
    grid.rio.write_transform(transform, inplace=True)
    return grid


def _resample_with_validity_gate(
    source: xr.DataArray,
    *,
    aoi_bbox: AoiBbox,
    target_resolution_m: float,
    target_crs: CRS,
    max_nan_ratio: float,
) -> xr.DataArray:
    """Resample `source` to `target_resolution_m` using area-weighted
    averaging. Target cells whose source-area valid fraction is below
    (1 - max_nan_ratio) are set to NaN.

    Works for any source/target ratio — no integer factor required."""
    target_grid = _build_grid(aoi_bbox, target_resolution_m, target_crs)

    src = source.rio.write_nodata(np.nan, inplace=False)
    target_mean = src.rio.reproject_match(
        target_grid, resampling=Resampling.average,
    )

    valid_mask = source.notnull().astype("float32")
    valid_mask.rio.write_crs(source.rio.crs, inplace=True)
    valid_mask.rio.write_transform(source.rio.transform(), inplace=True)
    valid_fraction = valid_mask.rio.reproject_match(
        target_grid, resampling=Resampling.average,
    )

    return target_mean.where(valid_fraction >= (1.0 - max_nan_ratio))


def _utm_crs_for_bbox(aoi_bbox: AoiBbox) -> CRS:
    lon = (aoi_bbox[0] + aoi_bbox[2]) / 2
    lat = (aoi_bbox[1] + aoi_bbox[3]) / 2
    zone = int(np.floor((lon + 180) / 6)) + 1
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return CRS.from_epsg(epsg)


def aoi_bbox_area_km2(aoi_bbox: AoiBbox) -> float:
    """Approximate area of a lat/lon bbox in km², via UTM projection.
    Used by the API layer to enforce max_aoi_area_km2."""
    target_crs = _utm_crs_for_bbox(aoi_bbox)
    transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    xs, ys = transformer.transform(
        [aoi_bbox[0], aoi_bbox[2], aoi_bbox[0], aoi_bbox[2]],
        [aoi_bbox[1], aoi_bbox[1], aoi_bbox[3], aoi_bbox[3]],
    )
    width_m = max(xs) - min(xs)
    height_m = max(ys) - min(ys)
    return abs(width_m * height_m) / 1_000_000.0
