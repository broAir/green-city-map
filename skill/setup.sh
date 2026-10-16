#!/usr/bin/env bash
# One-time setup: a private Python environment with the pipeline's dependencies.
# Usage: skill/setup.sh   (needs Python 3.12 or newer, about 600 MB of disk)
# Choose the Python with PYTHON=..., for example: PYTHON=python3.12 skill/setup.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "No $PY found. Install Python 3.12 or newer, or set PYTHON=/path/to/python3.12."; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Python 3.12 or newer is needed (found %d.%d). Install it, then run: PYTHON=python3.12 skill/setup.sh" % sys.version_info[:2])'
if [ ! -d .venv ]; then
  "$PY" -m venv .venv || { rm -rf .venv; echo "Could not create .venv. On Debian or Ubuntu, install the matching venv package first (for example python3.12-venv)."; exit 1; }
fi
.venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "The existing .venv uses Python %d.%d. Delete .venv and run skill/setup.sh again." % sys.version_info[:2])'
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r skill/requirements.txt
# DuckDB fetches its spatial and S3 extensions on first use; do it now, once
.venv/bin/python -I -c "import duckdb; c = duckdb.connect(); c.execute('INSTALL spatial; INSTALL httpfs')"
echo "Ready. Next: .venv/bin/python -I skill/pipeline/resolve_city.py \"<city>\" <country code> --write"
