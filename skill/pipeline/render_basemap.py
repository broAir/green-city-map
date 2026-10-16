"""Render a lightweight basemap (buildings, water, green) for the review viewer.

The viewer cannot load third-party map tiles, so the basemap is drawn from the
same open data: Overture buildings, water and green land use / land cover.
Output: palette PNG chunks (<= 2048 px) plus basemap.json with each chunk's
extent in the same local metre coordinates as streets.json.

Also renders green.png, the "greenery" map layer: summer greenness from the
10 m rasters (WorldCover trees, or Sentinel-2 NDVI ramped from 0.3 to 0.85),
lightly smoothed, as 16 alpha levels of one green at 20 m per pixel.

Usage: python -I render_basemap.py <sources.yaml> <street_metrics.yaml> <raw_dir> <out_dir>
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import shapely
import yaml
from PIL import Image
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import Affine, from_origin
from scipy.ndimage import gaussian_filter

RES = 4.0
CHUNK = 2048
# index: 0 transparent, 1 green, 2 water, 3 building (RGBA; mid-tones that read on light and dark)
PALETTE = [(0, 0, 0, 0), (104, 168, 96, 80), (84, 146, 206, 120), (128, 128, 140, 150)]


def load_geoms(path, classes=None, tr=None):
    t = pq.read_table(path)
    g = shapely.from_wkb(np.array(t["wkb"].to_pylist(), dtype=object))
    if classes is not None:
        cl = np.array([c or "" for c in t["class"].to_pylist()], dtype=object)
        g = g[np.isin(cl, classes)]
    return shapely.transform(g, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))


def main(sources_path, metrics_path, raw_dir, out_dir):
    src = yaml.safe_load(Path(sources_path).read_text())
    cfg = yaml.safe_load(Path(metrics_path).read_text())
    ov, out = Path(raw_dir) / "overture", Path(out_dir)
    tr = Transformer.from_crs("EPSG:4326", src["crs"], always_xy=True)
    boundary = load_geoms(ov / "boundary.parquet", tr=tr)[0]
    x0, y0 = math.floor(boundary.bounds[0]), math.floor(boundary.bounds[1])
    x1, y1 = math.ceil(boundary.bounds[2]), math.ceil(boundary.bounds[3])
    w, h = math.ceil((x1 - x0) / RES), math.ceil((y1 - y0) / RES)
    T = from_origin(x0, y1, RES, RES)

    green_cls = cfg["nature"]["land_use_classes"] + cfg["nature"]["land_classes"] + ["grass", "cemetery", "allotments"]
    layers = [
        (1, np.concatenate([load_geoms(ov / "land_use.parquet", green_cls, tr),
                            load_geoms(ov / "land.parquet", green_cls, tr)])),
        (2, load_geoms(ov / "water.parquet", None, tr)),
        (3, load_geoms(ov / "buildings.parquet", None, tr)),
    ]
    img = np.zeros((h, w), np.uint8)
    for val, geoms in layers:
        geoms = geoms[shapely.is_valid(geoms) & ~shapely.is_empty(geoms)]
        geoms = geoms[np.isin(shapely.get_type_id(geoms), [3, 6])]
        if len(geoms):
            r = rasterize(((g, val) for g in geoms), out_shape=(h, w), transform=T, fill=0, dtype="uint8")
            img = np.where(r > 0, r, img)

    pal = []
    for c in PALETTE:
        pal += list(c[:3])
    alpha = bytes(c[3] for c in PALETTE)
    chunks = []
    for r0 in range(0, h, CHUNK):
        for c0 in range(0, w, CHUNK):
            sub = img[r0:r0 + CHUNK, c0:c0 + CHUNK]
            if not sub.any():
                continue
            name = f"base_{r0 // CHUNK}_{c0 // CHUNK}.png"
            im = Image.fromarray(sub, mode="P")
            im.putpalette(pal)
            im.save(out / name, optimize=True, transparency=alpha)
            # extent in local coords (origin x0,y0; y up): left, bottom, right, top
            left = c0 * RES
            top = (y1 - y0) - r0 * RES
            chunks.append({"file": name, "x": left, "y": top - sub.shape[0] * RES,
                           "w": sub.shape[1] * RES, "h": sub.shape[0] * RES})
    green = render_green(Path(raw_dir) / "rasters" / "rasters.npz", cfg, x0, y0, x1, y1, out)
    (out / "basemap.json").write_text(json.dumps({"res_m": RES, "chunks": chunks, "green": green}))
    total = sum((out / c["file"]).stat().st_size for c in chunks)
    print(f"{src['city']}: basemap {w}x{h}px in {len(chunks)} chunks, {total / 1e6:.1f} MB")


GREEN_RES = 20.0
GREEN_RGB = (46, 140, 74)


def render_green(npz_path, cfg, x0, y0, x1, y1, out):
    z = np.load(npz_path, allow_pickle=True)
    ndvi, wc = z["ndvi"], z["worldcover"]
    T = Affine(*z["transform"])
    rc = cfg["rasters"]
    g = np.clip((np.nan_to_num(ndvi, nan=0.0) - 0.3) / 0.55, 0, 1)
    g = np.maximum(g, np.isin(wc, rc["worldcover_tree_classes"]).astype(float))
    g[wc == rc["worldcover_water_class"]] = 0
    g = gaussian_filter(g, 1.0)
    # crop to the city bbox and average 2x2 cells to 20 m
    inv = ~T
    c0, r0 = (int(np.floor(v)) for v in inv * (x0, y1))
    c1, r1 = (int(np.ceil(v)) for v in inv * (x1, y0))
    c0, r0 = max(c0, 0), max(r0, 0)
    sub = g[r0:r1, c0:c1]
    f = int(GREEN_RES / abs(T.a))
    h, w = sub.shape[0] // f * f, sub.shape[1] // f * f
    sub = sub[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3))
    lvl = np.round(sub * 15).astype(np.uint8)
    pal = list(GREEN_RGB) * 16
    alpha = bytes(int(round(200 * k / 15)) for k in range(16))
    im = Image.fromarray(lvl, mode="P")
    im.putpalette(pal)
    im.save(out / "green.png", optimize=True, transparency=alpha)
    left, top = T.c + c0 * T.a - x0, T.f + r0 * T.e - y0
    print(f"  green layer {lvl.shape[1]}x{lvl.shape[0]}px, {(out / 'green.png').stat().st_size / 1e6:.2f} MB")
    return {"file": "green.png", "x": left, "y": top - lvl.shape[0] * GREEN_RES,
            "w": lvl.shape[1] * GREEN_RES, "h": lvl.shape[0] * GREEN_RES}


if __name__ == "__main__":
    main(*sys.argv[1:5])
