# california-pleasures

Creates an image of California in the style of Joy Division's *Unknown Pleasures* album cover.

## Setup

### 1. Python environment (venv)

```bash
python3 -m venv env
source env/bin/activate          # Windows: env\Scripts\activate
pip install -r requirements.txt
```

### 2. Download elevation data (GeoTIFFs)

The `geotifs/` directory contains `dem.csv` and `download.sh`.  
`download.sh` reads the GeoTIFF URLs out of column 17 of `dem.csv` and fetches them with `wget`.

```bash
cd geotifs
./download.sh      # downloads ~70 1°×1° USGS tiles into geotifs/
cd ..
```

Each tile is roughly 30–60 MB (1 arc-second / ~30 m resolution from USGS 3DEP).  
This will take a while on a slow connection.

> **Alternative (single file, lower resolution):** You can download the entire
> California bounding box as one GeoTIFF from OpenTopography:
> ```bash
> curl -o geotifs/california_srtm.tif \
>   'https://portal.opentopography.org/API/globaldem?demtype=SRTMGL3&south=32.534156&north=42.009518&west=-124.409591&east=-114.131211&outputFormat=GTiff&API_Key=demoapikeyot2022'
> ```
> Note: the demo API key has rate limits. Register at opentopography.org for a free key.

### 3. Preprocess (border + elevation metadata)

`preprocess.py` downloads the California border from the US Census Bureau
Cartographic Boundary files (no manual download needed) and scans the GeoTIFFs
to produce `california_dem.py`, `ca_border_lon.npy`, and `ca_border_lat.npy`.

```bash
./preprocess.py
```

### 4. Generate the SVG

```bash
./ca_pleasures.py
```

Output: `ca_pleasures.svg`

---

## Data Sources

### Elevation
USGS 3D Elevation Program (3DEP), 1 arc-second seamless tiles.  
URLs are listed in `geotifs/dem.csv` (column 17).  
Hosted on `prd-tnm.s3.amazonaws.com` — public, no authentication required.

Direct download UI: https://apps.nationalmap.gov/downloader/

### State Boundary
US Census Bureau Cartographic Boundary Files (TIGER/Line), 2022 vintage, 1:500k.  
Fetched automatically by `preprocess.py` from:  
https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_us_state_500k.zip

If Census releases a newer vintage, update the `CENSUS_CB_URL` constant in `preprocess.py`.

---

## Customize

*COMING SOON*
