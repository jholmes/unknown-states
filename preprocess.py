#!/usr/bin/env python3
"""
Preprocesses elevation and border data for california-pleasures.

Border data is fetched from the US Census Bureau's Cartographic Boundary shapefiles (cb_2022_us_state_500k),
which are stable and publicly available.
Make sure that GeoTIFFs exist in ./geotifs/ (downloaded via geotifs/download.sh) or the whole thing blows up.
"""

from os import listdir
import re
import requests
import zipfile
import io

import geopandas as gpd
import rasterio
import numpy as np

TIF_DIR = './geotifs'
LAT_LON_RE = re.compile(".*([ns]\\d{1,2})([ew]\\d{1,3}).*")

# Census Cartographic Boundary file (500k generalized — plenty of detail for this use)
# This URL is stable; update the year (2022) if Census releases a newer vintage.
CENSUS_CB_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_us_state_500k.zip"
)


def fetch_california_border():
    """Download Census state boundary shapefile and return CA border lon/lat arrays."""
    print(f"Downloading state boundaries from {CENSUS_CB_URL} ...")
    response = requests.get(CENSUS_CB_URL)
    response.raise_for_status()
    zip_bytes = response.content

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall("/tmp/cb_states")

    states = gpd.read_file("/tmp/cb_states/cb_2022_us_state_500k.shp")
    ca = states[states["NAME"] == "California"].iloc[0].geometry

    # Use the exterior of the largest polygon (the mainland)
    if ca.geom_type == "MultiPolygon":
        ca_poly = max(ca.geoms, key=lambda p: p.area)
    else:
        ca_poly = ca

    coords = list(ca_poly.exterior.coords)
    lon = np.array([c[0] for c in coords])
    lat = np.array([c[1] for c in coords])
    return lon, lat


if __name__ == "__main__":
    # --- Border ---
    lon, lat = fetch_california_border()
    np.save("ca_border_lon.npy", lon)
    np.save("ca_border_lat.npy", lat)
    print(f"Saved CA border: {len(lon)} vertices")

    # --- Elevation tiles ---
    elev_max = -1_000_000
    elev_min =  1_000_000
    lon_max  = -1_000_000
    lon_min  =  1_000_000
    lat_max  = -1_000_000
    lat_min  =  1_000_000

    frames_lon_min =  1000
    frames_lon_max = -1000
    frames_lat_min =  1000
    frames_lat_max = -1000

    frame_width = frame_height = None

    tifs = [f for f in sorted(listdir(TIF_DIR)) if re.search(r'\.tif$', f, re.IGNORECASE)]
    if not tifs:
        raise FileNotFoundError(
            f"No .tif files found in {TIF_DIR}. "
            "Run geotifs/download.sh first."
        )

    for f in tifs:
        path = f"{TIF_DIR}/{f}"
        print(path)
        dataset = rasterio.open(path)
        m = LAT_LON_RE.match(dataset.name)
        if not m:
            print(f"  WARNING: could not parse lat/lon from filename, skipping {f}")
            dataset.close()
            continue

        frame_lon = int(m.group(2).replace('w', '-').replace('e', ''))
        frames_lon_min = min(frames_lon_min, frame_lon)
        frames_lon_max = max(frames_lon_max, frame_lon)
        frame_lat = int(m.group(1).replace('s', '-').replace('n', ''))
        frames_lat_min = min(frames_lat_min, frame_lat)
        frames_lat_max = max(frames_lat_max, frame_lat)

        frame_width  = dataset.width
        frame_height = dataset.height
        print(f"  bounds: {dataset.bounds}  size: {frame_width}x{frame_height}")

        band = dataset.read(1).astype(float)
        band[band == -999999] = np.nan

        elev_max = max(elev_max, float(np.nanmax(band)))
        elev_min = min(elev_min, float(np.nanmin(band)))
        lon_max  = max(lon_max,  dataset.bounds.right)
        lon_min  = min(lon_min,  dataset.bounds.left)
        lat_max  = max(lat_max,  dataset.bounds.top)
        lat_min  = min(lat_min,  dataset.bounds.bottom)
        dataset.close()

    if frame_width is None:
        raise RuntimeError("No valid GeoTIFF tiles were processed.")

    with open("california_dem.py", "w") as f:
        f.write(f"""import numpy as np

ELEV_MAX       = {elev_max}
ELEV_MIN       = {elev_min}
LON_MAX        = {lon_max}
LON_MIN        = {lon_min}
LAT_MAX        = {lat_max}
LAT_MIN        = {lat_min}
CA_BORDER_LON  = np.load('ca_border_lon.npy')
CA_BORDER_LAT  = np.load('ca_border_lat.npy')
FRAME_WIDTH    = {frame_width}
FRAME_HEIGHT   = {frame_height}
FRAMES_LON_MIN = {frames_lon_min}
FRAMES_LON_MAX = {frames_lon_max}
FRAMES_LAT_MIN = {frames_lat_min}
FRAMES_LAT_MAX = {frames_lat_max}
""")
    print("Wrote california_dem.py")
