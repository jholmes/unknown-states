#!/usr/bin/env python3
"""
Fetches 1°×1° DEM tile URLs for a given US state and writes them to
geotiffs/dem.csv, ready for download.py.

Primary source: USGS National Map (no API key, no rate limits).
Fallback source: OpenTopography SRTMGL1 (requires API key), used only for
  tiles that are genuinely absent from USGS. Mixing tile sources is fine
  because ridge_map.py uses each tile's actual pixel width rather than
  assuming a fixed FRAME_WIDTH.

Usage:
    ./fetch_dem_urls.py --state California
    ./fetch_dem_urls.py --state Colorado
    ./fetch_dem_urls.py --state "New Mexico"
"""
from __future__ import annotations

import argparse
import csv
import getpass
import math
import os
import sys
from typing import Any

import requests
from shapely.geometry import Point

from utils import state_border_polygon

BOUNDING_BOX_CSV = "US_State_Bounding_Boxes.csv"
OUTPUT_CSV = "geotiffs/dem.csv"

USGS_API    = "https://tnmaccess.nationalmap.gov/api/v1/products"
OT_API_BASE = "https://portal.opentopography.org/API/globaldem"
OT_DEMTYPE  = "SRTMGL1"  # 1 arc-second, matches USGS 3DEP resolution


def load_bounding_box(state_name: str) -> tuple[float, float, float, float]:
    with open(BOUNDING_BOX_CSV, newline="") as f:
        for row in csv.DictReader(f):
            if row["NAME"] == state_name:
                return (
                    float(row["xmin"]),  # west
                    float(row["ymin"]),  # south
                    float(row["xmax"]),  # east
                    float(row["ymax"]),  # north
                )
    raise ValueError(f"State '{state_name}' not found in {BOUNDING_BOX_CSV}")


def expected_tile_ids(
    west: float, south: float, east: float, north: float,
    state_name: str,
) -> set[str]:
    """Return tile IDs whose centers fall inside the state border polygon.

    Uses the convex hull of the full Census geometry so that tiles in concave
    coastal areas and states with complex borders are not incorrectly excluded.
    """
    border_poly = state_border_polygon(state_name)
    tile_south = math.floor(south)
    tile_north = math.floor(north)
    tile_west  = math.floor(west)
    tile_east  = math.floor(east)
    ids = set()
    for lat in range(tile_south, tile_north + 1):
        for lon in range(tile_west, tile_east + 1):
            if border_poly.contains(Point(lon + 0.5, lat + 0.5)):
                lat_tag = f"n{lat + 1:02d}"
                lon_tag = f"w{abs(lon):03d}"
                ids.add(f"{lat_tag}{lon_tag}")
    return ids


def fetch_usgs_tiles(
    west: float, south: float, east: float, north: float,
) -> dict[str, str]:
    """Query USGS National Map with a single bbox request.

    Returns {tile_id: download_url} for all found tiles, keeping only the
    most recent version of each. The API caps results at 200; if we hit that
    limit we warn the user since results may be incomplete.
    """
    params = {
        "datasets": "National Elevation Dataset (NED) 1 arc-second",
        "bbox": f"{west},{south},{east},{north}",
        "prodFormats": "GeoTIFF",
        "outputFormat": "JSON",
        "max": 200,
    }
    print("Querying USGS National Map API ...")
    r = requests.get(USGS_API, params=params)
    r.raise_for_status()
    items = r.json().get("items", [])
    print(f"  USGS returned {len(items)} results", end="")
    if len(items) == 200:
        print(" (WARNING: hit 200-result cap, some tiles may be missing)", end="")
    print()

    latest: dict[str, tuple[str, str]] = {}
    for item in items:
        url = item.get("downloadURL", "")
        if not url.endswith(".tif"):
            continue
        filename = url.split("/")[-1]
        parts = filename.removesuffix(".tif").split("_")
        if len(parts) < 4:
            continue
        tile_id  = parts[2]
        date_str = parts[3]
        if tile_id not in latest or date_str > latest[tile_id][0]:
            latest[tile_id] = (date_str, url)

    return {tile_id: url for tile_id, (_, url) in latest.items()}


def make_ot_url(lat: int, lon: int, api_key: str) -> str:
    return (
        f"{OT_API_BASE}"
        f"?demtype={OT_DEMTYPE}"
        f"&south={lat}&north={lat + 1}"
        f"&west={lon}&east={lon + 1}"
        f"&outputFormat=GTiff"
        f"&API_Key={api_key}"
    )


def prompt_api_key() -> str:
    api_key = os.environ.get("OT_API_KEY", "").strip()
    if api_key:
        print("  Using OT_API_KEY from environment.")
        return api_key
    print()
    print("  An OpenTopography API key is needed for the missing tiles.")
    print("  Register for free at: https://portal.opentopography.org/myopentopo")
    print("  (Set OT_API_KEY in your environment to skip this prompt in future.)")
    print()
    api_key = getpass.getpass("  Enter your OpenTopography API key: ").strip()
    if not api_key:
        print("Error: no API key entered.", file=sys.stderr)
        sys.exit(1)
    return api_key


def build_tiles(
    expected: set[str],
    usgs_urls: dict[str, str],
    west: float, south: float, east: float, north: float,
    api_key_getter: Any,
) -> list[dict[str, Any]]:
    """Build the full tile list, falling back to OT for any tiles missing from USGS."""
    tile_south = math.floor(south)
    tile_north = math.floor(north)
    tile_west  = math.floor(west)
    tile_east  = math.floor(east)

    missing = expected - set(usgs_urls.keys())
    api_key: str | None = None
    if missing:
        print(f"\n  {len(missing)} tile(s) not found in USGS, will use OpenTopography:")
        for tile_id in sorted(missing):
            print(f"    {tile_id}")
        api_key = api_key_getter()

    tiles = []
    for lat in range(tile_south, tile_north + 1):
        for lon in range(tile_west, tile_east + 1):
            lat_tag = f"n{lat + 1:02d}"
            lon_tag = f"w{abs(lon):03d}"
            tile_id = f"{lat_tag}{lon_tag}"
            if tile_id not in expected:
                continue
            if tile_id in usgs_urls:
                url = usgs_urls[tile_id]
                source = "USGS"
            else:
                assert api_key is not None
                url = make_ot_url(lat, lon, api_key)
                source = "OT"
            tiles.append({
                "tile_id": tile_id,
                "south": lat,
                "north": lat + 1,
                "west": lon,
                "east": lon + 1,
                "source": source,
                "url": url,
            })
    return tiles


def write_csv(tiles: list[dict[str, Any]], output_path: str) -> None:
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["tile_id", "south", "north", "west", "east", "source", "url"])
        for tile in tiles:
            writer.writerow([
                tile["tile_id"],
                tile["south"],
                tile["north"],
                tile["west"],
                tile["east"],
                tile["source"],
                tile["url"],
            ])
    usgs_count = sum(1 for t in tiles if t["source"] == "USGS")
    ot_count   = sum(1 for t in tiles if t["source"] == "OT")
    print(f"Wrote {output_path} ({len(tiles)} tiles: {usgs_count} USGS, {ot_count} OpenTopography)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch DEM tile URLs for a US state into geotiffs/dem.csv"
    )
    parser.add_argument(
        "--state", default="California",
        help='State name, e.g. "Colorado" (default: California)'
    )
    args = parser.parse_args()

    bbox_west, bbox_south, bbox_east, bbox_north = load_bounding_box(args.state)
    print(f"{args.state} bbox: W={bbox_west} S={bbox_south} E={bbox_east} N={bbox_north}")

    expected_tiles = expected_tile_ids(bbox_west, bbox_south, bbox_east, bbox_north, args.state)
    print(f"Expected {len(expected_tiles)} tiles for {args.state} (land tiles only)")

    usgs_tile_urls = fetch_usgs_tiles(bbox_west, bbox_south, bbox_east, bbox_north)
    found   = set(usgs_tile_urls.keys()) & expected_tiles
    missing_tiles = expected_tiles - set(usgs_tile_urls.keys())
    print(f"  Found in USGS:   {len(found)}")
    print(f"  Missing in USGS: {len(missing_tiles)}")

    tile_set = build_tiles(expected_tiles, usgs_tile_urls, bbox_west, bbox_south, bbox_east, bbox_north, prompt_api_key)
    write_csv(tile_set, OUTPUT_CSV)

    print(f"\nNext steps:")
    print(f"  ./download.py")
    print(f"  ./preprocess.py --state \"{args.state}\"")
    print(f"  ./ridge_map.py --state \"{args.state}\"")
