"""Adapters: Sentinel-2 L2A summer NDVI (Planetary Computer mirror, no account) and
ESA WorldCover 2021 v200 (AWS open data, CC BY 4.0).

Both are resampled onto one 10 m grid in the city's UTM CRS, covering the
padded Overture bbox. Output: rasters.npz with ndvi (float32, NaN = no data),
worldcover (uint8 class codes), plus grid transform and CRS.

Usage: python -I fetch_rasters.py <sources.yaml> <overture_dir> <out_dir>
"""
import calendar
import datetime as dt
import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window, from_bounds
from pystac_client import Client
import yaml

RES = 10.0
PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel2l2a01/sentinel2-l2"
WC_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
          "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")
# SCL classes kept: 4 vegetation, 5 bare, 6 water, 7 unclassified
SCL_OK = (4, 5, 6, 7)
PER_TILE = 4  # lowest-cloud scenes read per (MGRS tile, relative orbit)
MAX_GRID_PX = 150_000_000   # ~ 120 x 120 km at 10 m; larger areas need clipping first
CLOUD_STEPS = (10, 30, 60)  # max scene cloud cover %, relaxed step by step when nothing is found


def seasons(months, n=2, today=None):
    """The n most recent complete leaf-on seasons as STAC datetime ranges.
    Handles seasons that cross New Year (southern summer [12, 1, 2]) and the whole year."""
    today = today or dt.date.today()
    wraps = months[0] > months[-1]
    out = []
    for end_year in range(today.year, today.year - 6, -1):
        start_year = end_year - 1 if wraps else end_year
        start = dt.date(start_year, months[0], 1)
        end = dt.date(end_year, months[-1], calendar.monthrange(end_year, months[-1])[1])
        if end < today:
            out.append(f"{start.isoformat()}/{end.isoformat()}")
        if len(out) == n:
            break
    return out


def city_grid(bbox, crs):
    x0, y0, x1, y1 = transform_bounds("EPSG:4326", crs, *bbox)
    x0, y0 = math.floor(x0 / RES) * RES, math.floor(y0 / RES) * RES
    x1, y1 = math.ceil(x1 / RES) * RES, math.ceil(y1 / RES) * RES
    w, h = int((x1 - x0) / RES), int((y1 - y0) / RES)
    return from_origin(x0, y1, RES, RES), w, h


def warp_into(src_path, dst_shape, dst_transform, dst_crs, resampling, dtype, nodata):
    """Read only the source window covering the destination grid, then reproject."""
    dst = np.full(dst_shape, nodata, dtype=dtype)
    h, w = dst_shape
    left, top = dst_transform * (0, 0)
    right, bottom = dst_transform * (w, h)
    with rasterio.open(src_path) as src:
        sb = transform_bounds(dst_crs, src.crs, left, bottom, right, top, densify_pts=21)
        win = from_bounds(*sb, transform=src.transform).round_offsets().round_lengths()
        win = win.intersection(Window(0, 0, src.width, src.height))
        arr = src.read(1, window=win)
        reproject(source=arr, destination=dst,
                  src_transform=src.window_transform(win), src_crs=src.crs, src_nodata=src.nodata,
                  dst_transform=dst_transform, dst_crs=dst_crs, dst_nodata=nodata,
                  resampling=resampling)
    return dst


def worldcover(bbox, shape, transform, crs):
    tiles = set()
    for lat in (bbox[1], bbox[3]):
        for lon in (bbox[0], bbox[2]):
            la, lo = math.floor(lat / 3) * 3, math.floor(lon / 3) * 3
            tiles.add(f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}")
    out = np.zeros(shape, dtype=np.uint8)
    for t in sorted(tiles):
        try:
            part = warp_into(WC_URL.format(tile=t), shape, transform, crs, Resampling.nearest, np.uint8, 0)
        except rasterio.errors.RasterioIOError:
            print(f"  worldcover {t}: no tile (open sea), skipped", flush=True)
            continue
        out = np.where(part > 0, part, out)
        print(f"  worldcover {t}: {(part > 0).mean():.2%} of grid", flush=True)
    return out


def retry(fn, tries=6):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # transient 5xx / timeouts from the public APIs
            if i == tries - 1:
                raise
            print(f"  retry {i + 1} after {type(e).__name__}: {e}", flush=True)
            time.sleep(5 * (i + 1))


def pc_token():
    def get():
        with urllib.request.urlopen(PC_TOKEN, timeout=60) as r:
            return json.load(r)["token"]
    return retry(get)


def sentinel2_ndvi(bbox, shape, transform, crs, months):
    """Median leaf-on NDVI from Sentinel-2 L2A (Copernicus data, Planetary Computer
    West Europe mirror; anonymous SAS token), over the last two complete seasons."""
    client = retry(lambda: Client.open(PC_STAC))
    ranges = seasons(months)
    items = []
    for cloud in CLOUD_STEPS:
        for rng in ranges:
            items += retry(lambda: list(client.search(collections=["sentinel-2-l2a"], bbox=bbox, datetime=rng,
                                                      query={"eo:cloud_cover": {"lt": cloud}}, max_items=500).items()))
        if items:
            break
        print(f"  sentinel-2: no scenes under {cloud}% cloud in {ranges}, relaxing", flush=True)
    if not items:
        raise SystemExit(f"No Sentinel-2 scenes found for {ranges}. Check leaf_on_months in sources.yaml.")
    items.sort(key=lambda it: it.properties.get("eo:cloud_cover", 100))
    # best scenes per tile and orbit, so every part of the city grid gets coverage
    per_tile = {}
    for it in items:
        key = (it.properties.get("s2:mgrs_tile", "?"), it.properties.get("sat:relative_orbit", "?"))
        per_tile.setdefault(key, []).append(it)  # orbit too: a swath edge can cut through a tile
    items = [it for tile_items in per_tile.values() for it in tile_items[:PER_TILE]]
    print(f"  sentinel-2: {len(items)} candidate scenes", flush=True)
    token = pc_token()

    def one(it):
        try:
            red_h, nir_h, scl_h = (it.assets[k].href + "?" + token for k in ("B04", "B08", "SCL"))
        except KeyError:
            return None
        # processing baseline >= 04.00 adds a -1000 DN offset (BOA_ADD_OFFSET)
        off = -1000.0 if str(it.properties.get("s2:processing_baseline", "00.00")) >= "04.00" else 0.0
        red = warp_into(red_h, shape, transform, crs, Resampling.bilinear, np.float32, np.nan)
        cover = np.isfinite(red) & (red > 0)
        if cover.mean() < 0.01:   # the scene misses the grid; small corners still help fill gaps
            return None
        nir = warp_into(nir_h, shape, transform, crs, Resampling.bilinear, np.float32, np.nan)
        scl = warp_into(scl_h, shape, transform, crs, Resampling.nearest, np.uint8, 0)
        red, nir = red + off, nir + off
        ok = np.isin(scl, SCL_OK) & cover & (red > 0) & (nir > 0)    # negative reflectance after the offset is noise
        ndvi = np.where(ok, (nir - red) / np.maximum(nir + red, 1e-6), np.nan).astype(np.float32)
        meta = {"id": it.id, "date": it.properties["datetime"][:10],
                "cloud": it.properties.get("eo:cloud_cover"), "valid_share": round(float(ok.mean()), 3)}
        print(f"  sentinel-2 {it.id}: valid {ok.mean():.0%}", flush=True)
        return ndvi, meta

    stack, used = [], []
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(one, items):
            if res is not None:
                stack.append(res[0])
                used.append(res[1])
    if not stack:
        raise SystemExit("No usable Sentinel-2 scenes could be read. Try again later or widen leaf_on_months.")
    # median in row blocks, so memory stays near one copy of the scene stack
    med = np.full(shape, np.nan, np.float32)
    block = max(1, 4_000_000 // max(shape[1], 1))
    with np.errstate(all="ignore"):
        for r0 in range(0, shape[0], block):
            med[r0:r0 + block] = np.nanmedian(np.stack([a[r0:r0 + block] for a in stack]), axis=0)
    return med, used


def main(sources_path, overture_dir, out_dir):
    cfg = yaml.safe_load(Path(sources_path).read_text())
    meta = json.loads((Path(overture_dir) / "meta.json").read_text())
    bbox, crs = meta["bbox_padded"], cfg["crs"]
    transform, w, h = city_grid(bbox, crs)
    shape = (h, w)
    if w * h > MAX_GRID_PX:
        raise SystemExit(f"The city's area is too large for one run ({w}x{h} cells at {RES} m). "
                         "Clip the boundary to the urban area (see SKILL.md, large cities).")
    print(f"{cfg['city']}: grid {w}x{h} @ {RES} m {crs}", flush=True)
    wc = worldcover(bbox, shape, transform, crs)
    ndvi, used = sentinel2_ndvi(bbox, shape, transform, crs, months=cfg.get("leaf_on_months", [6, 7, 8]))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "rasters.npz", ndvi=ndvi, worldcover=wc,
                        transform=np.array(transform)[:6], crs=crs)
    land = (wc > 0) & (wc != 80)
    coverage = float(np.isfinite(ndvi[land]).mean()) if land.any() else 0.0
    (out / "rasters_meta.json").write_text(json.dumps({
        "sentinel2_scenes": used, "sentinel2_seasons": seasons(cfg.get("leaf_on_months", [6, 7, 8])),
        "ndvi_land_coverage": round(coverage, 4),
        "worldcover": "ESA WorldCover 10m 2021 v200"}, indent=1), encoding="utf-8")
    print(f"  ndvi valid on land {coverage:.1%}, median {np.nanmedian(ndvi):.2f}", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
