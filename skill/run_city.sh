#!/usr/bin/env bash
# Run the Green City Map pipeline for one city: skill/run_city.sh <cc>/<city> [--refresh]
# Downloads are cached: a step whose output exists is skipped unless --refresh is given.
# Logs go to data/raw/<city>/logs/. The run stops at the first failing step.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: skill/run_city.sh <cc>/<city> [--refresh]"; exit 2; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REG="registry/$1"
CITY="$(basename "$1")"
REFRESH="${2:-}"
[ -f "$REG/sources.yaml" ] || { echo "No $REG/sources.yaml. Run skill/pipeline/resolve_city.py first."; exit 2; }
PY=("${GCM_PYTHON:-$ROOT/.venv/bin/python}" -I)
RAW="data/raw/$CITY"; OUT="out/$CITY"; LOG="$RAW/logs"
CFG="skill/config/street_metrics.yaml"
export GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR GDAL_HTTP_MULTIPLEX=YES GDAL_HTTP_MAX_RETRY=5 GDAL_HTTP_RETRY_DELAY=3
mkdir -p "$RAW/overture" "$RAW/rasters" "$RAW/osm" "$OUT" "$LOG"

step() {   # step <name> <command...>: run, keep a log, stop on failure
  local name="$1"; shift
  echo "== $name"
  "$@" 2>&1 | tee "$LOG/$name.log"
}
fresh() { [ "$REFRESH" = "--refresh" ] || [ ! -f "$1" ]; }

if fresh "$RAW/overture/meta.json"; then step overture "${PY[@]}" skill/pipeline/fetch_overture.py "$REG/sources.yaml" "$RAW/overture"; else echo "== overture (cached)"; fi
if fresh "$RAW/rasters/rasters.npz"; then step rasters "${PY[@]}" skill/pipeline/fetch_rasters.py "$REG/sources.yaml" "$RAW/overture" "$RAW/rasters"; else echo "== rasters (cached)"; fi
step osm "${PY[@]}" skill/pipeline/fetch_osm.py "$REG/sources.yaml" "$CFG" "$RAW/overture" "$RAW/osm"
step streets "${PY[@]}" skill/pipeline/compute_streets.py "$REG/sources.yaml" "$CFG" "$RAW" "$OUT"
step basemap "${PY[@]}" skill/pipeline/render_basemap.py "$REG/sources.yaml" "$CFG" "$RAW" "$OUT"
step manifest "${PY[@]}" skill/pipeline/build_manifest.py "$REG" "$RAW" "$OUT"
step lander "${PY[@]}" skill/viewer/lander_data.py "$CFG" out "$CITY"
step extras "${PY[@]}" skill/pipeline/extras.py "$CFG" out data/raw "$CITY"
step districts "${PY[@]}" skill/pipeline/districts.py "$CFG" out data/raw "$CITY"
step safety "${PY[@]}" skill/pipeline/safety.py "$CFG" out data/raw "$CITY"      # optional keys: REDDIT_CLIENT_ID/SECRET, NUMBEO_API_KEY
step validate "${PY[@]}" skill/pipeline/validate_city.py "$OUT"
echo "Done: $OUT. Check the map, then share with: ${PY[*]} skill/pipeline/package_city.py $OUT share"
