#!/usr/bin/env python3
"""
Generates a ridgeline SVG of a US state in the style of Joy Division's
'Unknown Pleasures' album cover.

Usage:
    ./ridge_map.py --state California
    ./ridge_map.py --state Colorado
    ./ridge_map.py --state Colorado --spacing 0.15 --output colorado.svg
    ./ridge_map.py --state Colorado --png
    ./ridge_map.py --state Colorado --png --png-scale 3

Run preprocess.py --state <State> first to generate state_dem.py and the
border .npy files for the target state.
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
from typing import Callable
from os import listdir

import numpy as np
import rasterio
from types import ModuleType
from importlib import util, abc
from shapely import get_coordinates

LAT_LON = re.compile(r".*([ns]\d{1,2})([ew]\d{1,3}).*")
BOUNDING_BOX_CSV = "US_State_Bounding_Boxes.csv"


def load_bounding_boxes() -> dict[str, tuple[float, float, float, float]]:
    """Return a dict of {state_name: (lat_min, lat_max, lon_min, lon_max)}."""
    bboxes = {}
    with open(BOUNDING_BOX_CSV, newline="") as f:
        for row in csv.DictReader(f):
            bboxes[row["NAME"]] = (
                float(row["ymin"]),
                float(row["ymax"]),
                float(row["xmin"]),
                float(row["xmax"]),
            )
    return bboxes


def load_state_dem() -> ModuleType:
    """Import the auto-generated state_dem module."""
    spec = util.spec_from_file_location("state_dem", "state_dem.py")
    if spec is None or spec.loader is None:
        raise Exception("Could not load state_dem.py")
    if not isinstance(spec.loader, abc.Loader):
        raise Exception("Loader does not support exec_module")
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def nan_helper(y: np.ndarray) -> tuple[np.ndarray, Callable[[np.ndarray], np.ndarray]]:
    return np.isnan(y), lambda z: z.nonzero()[0]


def calculate_intersections(
    lat_values: np.ndarray,
    border_lat: np.ndarray,
    border_lon: np.ndarray,
    lon_min: float,
    lon_max: float,
) -> np.ndarray:
    """Find the west/east lon clipping bounds for each latitude line.

    Uses shapely to intersect each horizontal scan line with the state border
    polygon. This is robust for any state shape including rectangular states
    like Colorado whose straight borders have almost no polygon vertices,
    which caused the old segment-intersection approach to miss most crossings.

    Falls back to (lon_min, lon_max) for any latitude where no intersection
    is found (e.g. a lat line that clips only a tiny corner of the state).
    """
    from shapely.geometry import LineString, Polygon

    border_poly = Polygon(zip(border_lon, border_lat))
    results = []
    for lat in lat_values:
        scan_line = LineString([(lon_min - 1, lat), (lon_max + 1, lat)])
        intersection = border_poly.intersection(scan_line)
        if intersection.is_empty:
            results.append([lon_min, lon_max])
        else:
            # intersection may be a Point, LineString, or MultiLineString
            xs = []
            coords_array = get_coordinates(intersection)
            if len(coords_array) > 0:
                results.append([coords_array[:, 0].min(), coords_array[:, 0].max()])
            else:
                results.append([lon_min, lon_max])

            if xs:
                results.append([min(xs), max(xs)])
            else:
                results.append([lon_min, lon_max])
    return np.array(results)


def _lon_from_filename(f: str) -> float:
    m = re.search(r'[ew](\d{1,3})_', f)
    if m is None:
        raise ValueError(f"Could not parse longitude from filename: {f}")
    return float(m.group(1))


def find_tiffs(lat: float, d: str = "./geotifs") -> list[str]:
    return sorted(
        [f"{d}/{f}" for f in listdir(d) if re.search(f"n{int(lat + 1)}", f)],
        key=_lon_from_filename,
        reverse=True,
    )


# Cache the open rasterio datasets across calls for the same latitude row.
_cache_lat: int | None = None
_cache_frames: list[tuple[rasterio.DatasetReader, np.ndarray]] | None = None


def get_frames(lat: float) -> list[tuple[rasterio.DatasetReader, np.ndarray]]:
    global _cache_lat, _cache_frames
    frame_lat = int(lat + 1)

    if frame_lat == _cache_lat:
        assert _cache_frames is not None
        return _cache_frames

    if _cache_frames is not None:
        for dataset, _ in _cache_frames:
            dataset.close()

    datasets = [rasterio.open(f) for f in find_tiffs(lat)]
    bands = [ds.read(1) for ds in datasets]
    _cache_lat = frame_lat
    _cache_frames = list(zip(datasets, bands))

    assert _cache_frames is not None
    return _cache_frames


def get_elev(lat: float, mask: np.ndarray, current_dem: ModuleType) -> tuple[np.ndarray, np.ndarray]:
    # Build the output array using FRAME_WIDTH samples per degree as a baseline.
    # Each tile's actual pixel width may differ slightly (USGS=3612, OT=3600),
    # so we use the actual bandwidth per tile rather than assuming uniformity.
    lat_line_length = current_dem.FRAME_WIDTH * (current_dem.FRAMES_LON_MAX - current_dem.FRAMES_LON_MIN + 1)
    elev_scale = 1.0 / (current_dem.ELEV_MAX - current_dem.ELEV_MIN)

    lon = np.linspace(current_dem.FRAMES_LON_MIN, current_dem.FRAMES_LON_MAX + 1, num=lat_line_length)
    elev = np.zeros(lat_line_length)

    for dataset, band in get_frames(lat):
        m = LAT_LON.match(dataset.name)
        if m is None:
            raise ValueError(f"Could not parse lat/lon from filename: {dataset.name}")
        frame_lon = int(m.group(2).replace("w", "-").replace("e", ""))
        offset = (frame_lon - current_dem.FRAMES_LON_MIN) * current_dem.FRAME_WIDTH
        row, _ = dataset.index(
            (dataset.bounds.left + dataset.bounds.right) / 2,
            lat,
        )
        e = band[row].astype(float)
        actual_width = len(e)
        e[e == -999999] = np.nan
        nans, x = nan_helper(e)
        e[nans] = np.interp(x(nans), x(~nans), e[~nans])
        e = e * elev_scale + lat
        # Use actual_width rather than current_dem.FRAME_WIDTH so that tiles
        # from different sources (USGS=3612px, OpenTopography=3600px) both fit
        # correctly without broadcasting errors or gaps.
        elev[offset:offset + actual_width] = e

    elev[lon < mask[0]] = lat
    elev[lon > mask[1]] = lat

    # Clamp: any remaining out-of-range values (e.g. from a missing tile that
    # produced zeros) get set to the baseline lat so mercator() never sees a
    # near-zero latitude, which would produce a wildly wrong y value.
    elev = np.where((elev < lat - 1) | (elev > lat + 1), lat, elev)

    return lon, elev


def subsample(values: np.ndarray, scale: int) -> np.ndarray:
    extra = values.size % scale
    left = extra // 2
    right = extra - left
    trimmed = values[left: values.size - right] if right else values[left:]
    return trimmed.reshape((values.size - extra) // scale, scale).mean(axis=1)


def mercator(y: np.ndarray) -> np.ndarray:
    y = y * np.pi / 180
    y = np.log(np.tan(np.pi / 4 + y / 2))
    return y * 180 / np.pi


def to_path(x: np.ndarray, y: np.ndarray, path_id: str, style: str = "stroke:#ffffff;") -> str:
    points = " ".join([f"{i:.3f},{j:.3f}" for i, j in zip(x, y)])
    return f'<path d="M {points}" id="{path_id}" style="{style}"/>'


def generate_svg(lat_values: np.ndarray, current_dem: ModuleType, img_output_path: str) -> None:
    border_lon = current_dem.STATE_BORDER_LON
    border_lat = current_dem.STATE_BORDER_LAT

    lon_min = float(current_dem.FRAMES_LON_MIN)
    lon_max = float(current_dem.FRAMES_LON_MAX) + 1.0
    intersections = calculate_intersections(lat_values, border_lat, border_lon, lon_min, lon_max)
    print(f"Border intersections computed for {lat_values.size} latitude lines")

    style = ";".join([
        "stroke:#ffffff",
        "stroke-width:0.02",
        "stroke-miterlimit:4",
        "stroke-dasharray:none",
        "fill:#000000",
        "fill-opacity:1",
        "stroke-opacity:1",
    ])

    # Padding in degrees added as explicit flat points on each side of the
    # state border, independent of tile coverage. This guarantees a visible
    # flat buffer even when the tile grid starts right at the state border.
    pad = 1.0

    y_min, y_max = 1e6, -1e6
    paths = []
    for lat, intersection in zip(lat_values, intersections):
        x, y = get_elev(lat, intersection, current_dem)
        x = subsample(x, 100)
        y = subsample(y, 100)

        # Prepend and append flat padding points at the baseline lat value.
        # These sit outside the state border and give the Unknown Pleasures
        # flat-line buffer on both sides regardless of tile extent.
        pad_west = np.linspace(lon_min - pad, x[0], 10, endpoint=False)
        pad_east = np.linspace(x[-1], lon_max + pad, 10)[1:]
        x = np.concatenate([pad_west, x, pad_east])
        y = np.concatenate([
            np.full(len(pad_west), lat),
            y,
            np.full(len(pad_east), lat),
        ])

        y = mercator(y)
        y_min = min(y_min, y.min())
        y_max = max(y_max, y.max())
        paths.append(to_path(x, y, f"lat_{lat:.3f}", style))

    x_min = lon_min - pad - 0.5
    x_max = lon_max + pad + 0.5
    paths_str = "\n".join(paths)
    y_min -= 0.5
    y_max += 0.5
    width  = x_max - x_min
    height = y_max - y_min

    rect = (
        f'<rect style="fill:#000000;" id="bg" '
        f'width="{width}" height="{height}" x="0" y="0" />'
    )
    transform = f"scale(1, -1) translate({-x_min} {-y_max})"

    svg_text = f"""<?xml version="1.0" encoding="utf-8" standalone="no"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
<svg viewBox="0 0 {width} {height}" width="{100 * width:.0f}" height="{100 * height:.0f}" xmlns="http://www.w3.org/2000/svg">
    {rect}
    <g id="figure_1" transform="{transform}">
{paths_str}
    </g>
</svg>
"""
    with open(img_output_path, "w") as f:
        f.write(svg_text)
    print(f"Wrote {img_output_path}")


def svg_to_png(svg_path: str, png_path: str, scale: float = 2.0) -> None:
    """Convert an SVG file to PNG using rsvg-convert.

    The scale factor multiplies the SVG's intrinsic pixel dimensions.
    Default of 2.0 produces a 2× / retina-ready image.

    Requires rsvg-convert from librsvg:
        brew install librsvg
    """
    cmd = ["rsvg-convert", f"--zoom={scale}", "--output", png_path, svg_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError(
            "rsvg-convert not found. Install it with:\n"
            "\tbrew install librsvg (on macOS)\n"
            "\tsudo apt-get install librsvg2-bin (on debian/ubuntu)"
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"rsvg-convert failed (exit {result.returncode}):\n{result.stderr.strip()}"
        )
    print(f"Wrote {png_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a ridgeline SVG elevation map for a US state."
    )
    parser.add_argument(
        "--state", default=None,
        help=(
            'State name, e.g. "Colorado". Defaults to the state recorded in '
            "state_dem.py (set by the last preprocess.py run)."
        ),
    )
    parser.add_argument(
        "--spacing", type=float, default=0.2,
        help="Latitude spacing between ridgelines in degrees (default: 0.2)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output SVG filename (default: <state_name>.svg)",
    )
    parser.add_argument(
        "--png", action="store_true",
        help="Also convert the SVG to PNG after generation (requires cairosvg)",
    )
    parser.add_argument(
        "--png-scale", type=float, default=2.0, dest="png_scale",
        help="Scale factor for PNG output (default: 2.0 for retina-ready)",
    )
    args = parser.parse_args()

    dem = load_state_dem()

    state_name = args.state or dem.STATE_NAME
    if args.state and args.state != dem.STATE_NAME:
        print(
            f"WARNING: --state '{args.state}' does not match state_dem.py "
            f"('{dem.STATE_NAME}'). Run preprocess.py --state '{args.state}' first."
        )

    # Use bounding box CSV for lat range if available, otherwise fall back to dem bounds.
    try:
        boxes = load_bounding_boxes()
        lat_min, lat_max, _, _ = boxes[state_name]
        print(f"Lat range from bounding box CSV: {lat_min:.3f} – {lat_max:.3f}")
    except (FileNotFoundError, KeyError):
        lat_min, lat_max = dem.LAT_MIN, dem.LAT_MAX
        print(f"Lat range from state_dem.py: {lat_min:.3f} – {lat_max:.3f}")

    # Step slightly inside the bbox edges so border intersections always exist.
    lats = np.arange(lat_max - args.spacing, lat_min + args.spacing, -args.spacing)
    print(f"Plotting {lats.size} ridgelines for {state_name}")

    output_path = args.output or f"{state_name.lower().replace(' ', '_')}.svg"
    generate_svg(lats, dem, output_path)

    if args.png:
        png_out_path = output_path.removesuffix(".svg") + ".png"
        svg_to_png(output_path, png_out_path, scale=args.png_scale)
