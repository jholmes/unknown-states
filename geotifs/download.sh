#!/bin/bash
# Downloads all USGS 3DEP GeoTIFF tiles listed in dem.csv.
# Run from inside the geotifs/ directory.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cut -d',' -f17 "$SCRIPT_DIR/dem.csv" | xargs -P4 wget --no-clobber --continue
