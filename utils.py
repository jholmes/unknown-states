"""
Shared utilities for unknown-states.
"""
from __future__ import annotations

import io
import zipfile

import geopandas as gpd
import numpy as np
import requests

CENSUS_CB_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_us_state_500k.zip"
)

# Cache the shapefile in memory so multiple callers in the same process
# (e.g. fetch_dem_urls.py calling both fetch_state_border and
# expected_tile_ids) only download it once.
_states_gdf: gpd.GeoDataFrame | None = None


def _load_states() -> gpd.GeoDataFrame:
    global _states_gdf
    if _states_gdf is None:
        print("Downloading state boundaries from Census Bureau ...")
        response = requests.get(CENSUS_CB_URL)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            zf.extractall("/tmp/cb_states")
        _states_gdf = gpd.read_file("/tmp/cb_states/cb_2022_us_state_500k.shp")
    assert _states_gdf is not None
    return _states_gdf


def fetch_state_border(state_name: str) -> tuple[np.ndarray, np.ndarray]:
    """Download Census state boundary and return (lon, lat) arrays.

    Uses the largest polygon for states with islands or non-contiguous parts.
    """
    states = _load_states()
    matches = states[states["NAME"] == state_name]
    if matches.empty:
        available = sorted(str(n) for n in states["NAME"].tolist())
        raise ValueError(
            f"State '{state_name}' not found. Available names:\n  " +
            "\n  ".join(available)
        )

    geom = matches.iloc[0].geometry
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda p: p.area)

    coords = list(geom.exterior.coords)
    lon = np.array([c[0] for c in coords])
    lat = np.array([c[1] for c in coords])
    return lon, lat


def state_border_polygon(state_name: str):
    """Return a shapely geometry for the state suitable for spatial queries.

    Uses the full Census geometry (which may be a MultiPolygon) so that
    point-in-polygon tile filtering works correctly for states like California
    whose Census representation splits the mainland across multiple polygons.
    The convex hull is returned so that tiles whose centers fall in concave
    coastal indentations are not incorrectly excluded.
    """
    states = _load_states()
    matches = states[states["NAME"] == state_name]
    if matches.empty:
        raise ValueError(f"State '{state_name}' not found in Census shapefile.")
    return matches.iloc[0].geometry.convex_hull
