"""Build clipped 300m grid GeoJSON from boundary + per-indicator composites.

The composites coming in are already on a snapped UTM grid at 300m resolution
(see imagery_providers.base._build_grid / _resample_with_validity_gate), so
each pixel is exactly one cell. This module's job is purely vector:
build cell polygons, clip the boundary-edge ones, and serialize.
"""
from __future__ import annotations

import numpy as np
import xarray as xr
from pyproj import CRS, Transformer
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as shp_transform
from shapely.prepared import prep


def build_zones_geojson(
    boundary_feature: dict,
    lst_composite: xr.DataArray,
    ndvi_composite: xr.DataArray,
) -> dict:
    """Return a WGS84 GeoJSON FeatureCollection of clipped 300m cells.

    Each feature carries:
      - geometry: WGS84 Polygon or MultiPolygon (boundary-clipped)
      - properties: cell_id, row, col, lst_celsius (float|None), ndvi (float|None)
    """
    if lst_composite.shape != ndvi_composite.shape:
        raise ValueError(
            f"LST/NDVI shape mismatch: {lst_composite.shape} vs {ndvi_composite.shape}"
        )
    if str(lst_composite.rio.crs) != str(ndvi_composite.rio.crs):
        raise ValueError("LST/NDVI composites are in different CRSes")
    if tuple(lst_composite.rio.transform())[:6] != tuple(ndvi_composite.rio.transform())[:6]:
        raise ValueError("LST/NDVI composites are not on the same grid origin")

    target_crs = CRS.from_user_input(lst_composite.rio.crs)
    transform = lst_composite.rio.transform()
    res_x, res_y = transform.a, -transform.e
    minx0, maxy0 = transform.c, transform.f
    height, width = lst_composite.shape

    boundary_wgs = shape(boundary_feature["geometry"])
    to_utm = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True).transform
    to_wgs = Transformer.from_crs(target_crs, "EPSG:4326", always_xy=True).transform
    boundary_utm = shp_transform(to_utm, boundary_wgs)
    boundary_prep = prep(boundary_utm)

    lst_arr = lst_composite.values
    ndvi_arr = ndvi_composite.values

    features: list[dict] = []
    for row in range(height):
        cell_maxy = maxy0 - row * res_y
        cell_miny = cell_maxy - res_y
        for col in range(width):
            cell_minx = minx0 + col * res_x
            cell_maxx = cell_minx + res_x
            square = box(cell_minx, cell_miny, cell_maxx, cell_maxy)

            if not boundary_prep.intersects(square):
                continue
            geom_utm = (
                square
                if boundary_prep.contains(square)
                else square.intersection(boundary_utm)
            )
            if geom_utm.is_empty:
                continue
            geom_wgs = shp_transform(to_wgs, geom_utm)

            features.append({
                "type": "Feature",
                "geometry": mapping(geom_wgs),
                "properties": {
                    "cell_id": f"{row}-{col}",
                    "row": row,
                    "col": col,
                    "lst_celsius": _nan_to_none(lst_arr[row, col]),
                    "ndvi": _nan_to_none(ndvi_arr[row, col]),
                },
            })

    return {"type": "FeatureCollection", "features": features}


def _nan_to_none(v: float) -> float | None:
    f = float(v)
    return None if not np.isfinite(f) else round(f, 4)
