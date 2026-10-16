"""Adapter: Overture Maps (global baseline, ODbL / CDLA) via the release PMTiles.

Reads the vector tiles covering one city from Overture's CDN
(tiles.overturemaps.org), which is far faster than scanning the GeoParquet
release when bandwidth to the S3 bucket is poor. Features cut at tile edges
are clipped to their tile and merged back by Overture id.

The boundary is still read once from the GeoParquet divisions theme (one
small filtered query).

Writes Parquet files with WKB geometry in EPSG:4326:
  boundary, buildings, segments, connectors, land_use, land, water, places

Usage: python -I fetch_overture.py <sources.yaml> <out_dir>
"""
import gzip
import json
import math
import re
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mapbox_vector_tile
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
import yaml
from pmtiles.tile import deserialize_directory, deserialize_header, find_tile, zxy_to_tileid
from shapely.geometry import box, shape

PAD_DEG = 0.02
TILES = "https://tiles.overturemaps.org/{rel}/{theme}.pmtiles"
CATALOG = "https://stac.overturemaps.org/catalog.json"
RELEASE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.\d+$")
ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
FAR_PART_KM = 20      # boundary parts this far from the main part (islands, exclaves) are left out
WORKERS = 24

# theme -> [(layer, out_name, kept attributes, geometry kind)]
LAYERS = {
    "buildings": [("building", "buildings",
                   ["id", "height", "num_floors", "min_height", "is_underground", "class", "subtype",
                    "@height_source"], "polygon")],
    "transportation": [("segment", "segments",
                        ["id", "subtype", "class", "subclass", "@name", "connectors", "road_flags",
                         "rail_flags", "level_rules"], "line"),
                       ("connector", "connectors", ["id"], "point")],
    "base": [("land_use", "land_use", ["id", "subtype", "class", "@name", "surface"], "polygon"),
             ("land", "land", ["id", "subtype", "class", "@name", "surface"], "polygon"),
             ("water", "water", ["id", "subtype", "class", "@name"], "polygon")],
    "places": [("place", "places", ["id", "basic_category", "taxonomy", "confidence", "@name"], "point")],
}


class RemotePMTiles:
    """Minimal PMTiles v3 reader over HTTP range requests, with directory caching."""

    def __init__(self, url):
        self.url = url
        self.header = deserialize_header(self._get(0, 127))
        h = self.header
        self.root = deserialize_directory(self._get(h["root_offset"], h["root_length"]))
        self.leaves = {}

    def _get(self, offset, length):
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={offset}-{offset + length - 1}"})
        for attempt in range(5):
            try:
                return urllib.request.urlopen(req, timeout=120).read()
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 * (attempt + 1))

    def get(self, z, x, y):
        tid = zxy_to_tileid(z, x, y)
        h = self.header
        d = self.root
        for _ in range(4):
            e = find_tile(d, tid)
            if e is None:
                return None
            if e.run_length > 0:
                return self._get(h["tile_data_offset"] + e.offset, e.length)
            key = h["leaf_directory_offset"] + e.offset
            if key not in self.leaves:
                self.leaves[key] = deserialize_directory(self._get(key, e.length))
            d = self.leaves[key]
        return None


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def tile_to_lonlat_fn(z, x, y, extent):
    n = 2 ** z

    def f(coords):
        px, py = coords[:, 0], coords[:, 1]
        lon = (x + px / extent) / n * 360 - 180
        lat = (180 / math.pi) * np.arctan(np.sinh(math.pi * (1 - 2 * (y + py / extent) / n)))
        return np.column_stack([lon, lat])
    return f


def tile_bounds(z, x, y):
    n = 2 ** z
    lon0, lon1 = x / n * 360 - 180, (x + 1) / n * 360 - 180
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat0 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return box(lon0, lat0, lon1, lat1)


def decode_tile(raw, z, x, y, wanted):
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    tile = mapbox_vector_tile.decode(data, default_options={"y_coord_down": True})
    out = {}
    tb = tile_bounds(z, x, y)
    for layer, (keep, kind) in wanted.items():
        if layer not in tile:
            continue
        lyr = tile[layer]
        conv = tile_to_lonlat_fn(z, x, y, lyr.get("extent", 4096))
        feats = []
        for f in lyr["features"]:
            try:
                g = shapely.transform(shape(f["geometry"]), conv)
                if kind != "point":
                    g = g.intersection(tb)  # clip the tippecanoe buffer so pieces abut exactly
                    if g.is_empty:
                        continue
            except Exception:
                continue
            props = {k: f["properties"].get(k) for k in keep}
            feats.append((props, g))
        out[layer] = feats
    return out


def merge(feats, kind):
    """Merge tile pieces by Overture id."""
    by_id = defaultdict(list)
    props = {}
    for p, g in feats:
        fid = p.get("id")
        by_id[fid].append(g)
        props.setdefault(fid, p)
    rows = []
    for fid, geoms in by_id.items():
        if kind == "point":
            g = geoms[0]
        elif len(geoms) == 1:
            g = geoms[0]
        elif kind == "polygon":
            g = shapely.union_all([shapely.make_valid(x) for x in geoms], grid_size=1e-7)
        else:
            g = shapely.line_merge(shapely.union_all(geoms))
        if kind == "polygon":
            g = shapely.make_valid(g)
            polys = [p for p in getattr(g, "geoms", [g]) if p.geom_type in ("Polygon", "MultiPolygon")]
            if not polys:
                continue
            g = shapely.union_all(polys)
        rows.append((props[fid], g))
    return rows


def to_parquet(rows, keep, path):
    cols = {k.lstrip("@"): [] for k in keep}
    wkb = []
    for p, g in rows:
        for k in keep:
            v = p.get(k)
            cols[k.lstrip("@")].append(json.dumps(v) if isinstance(v, (list, dict)) else v)
        wkb.append(shapely.to_wkb(g))
    table = pa.table({**{k: pa.array(v) for k, v in cols.items()}, "wkb": pa.array(wkb, pa.binary())})
    pq.write_table(table, path, compression="zstd")


def resolve_release(pinned):
    """Use the pinned Overture release while it is still served, else the latest one.
    Overture keeps only the last few releases online."""
    def served(rel):
        try:
            # the CDN refuses HEAD, so ask for the first bytes of the archive
            req = urllib.request.Request(TILES.format(rel=rel, theme="divisions"), headers={"Range": "bytes=0-15"})
            return urllib.request.urlopen(req, timeout=30).read(7) == b"PMTiles"
        except Exception:
            return False
    if pinned and RELEASE_RE.match(pinned) and served(pinned):
        return pinned
    with urllib.request.urlopen(CATALOG, timeout=60) as r:
        latest = json.load(r)["latest"]
    if not RELEASE_RE.match(latest):
        raise SystemExit(f"Unexpected Overture release name {latest!r}")
    print(f"  Overture release {pinned} is no longer served; using {latest}", flush=True)
    return latest


def fetch_boundary(rel, div_id, out):
    import duckdb
    if not ID_RE.match(div_id or ""):
        raise SystemExit(f"boundary.overture_division_id {div_id!r} is not an Overture id")
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2'; "
                "SET enable_progress_bar=false;")
    rows = con.execute(f"""
        SELECT id, names.primary, ST_AsWKB(geometry)
        FROM read_parquet('s3://overturemaps-us-west-2/release/{rel}/theme=divisions/type=division_area/*',
                          hive_partitioning=1)
        WHERE (id = ? OR division_id = ?) AND coalesce(class, 'land') <> 'maritime'""", [div_id, div_id]).fetchall()
    if not rows:
        raise SystemExit(f"No Overture division area with id {div_id} in release {rel}. "
                         "Re-run resolve_city.py to look the city up again.")
    g = shapely.union_all([shapely.from_wkb(bytes(r[2])) for r in rows])
    g, dropped = main_parts(g)
    if dropped:
        print(f"  boundary: left out {dropped} part(s) more than {FAR_PART_KM} km from the city", flush=True)
    pq.write_table(pa.table({"id": [div_id], "name": [rows[0][1]], "release": [rel],
                             "wkb": pa.array([shapely.to_wkb(g)], pa.binary())}),
                   out / "boundary.parquet")
    return g


def main_parts(g):
    """Keep the part with the largest area and the parts near it (drops far islands and exclaves,
    which would otherwise blow up the bounding box)."""
    parts = list(getattr(g, "geoms", [g]))
    if len(parts) == 1:
        return g, 0
    parts.sort(key=lambda p: -p.area)
    lat = parts[0].centroid.y
    deg = FAR_PART_KM / 111.0
    near = shapely.buffer(parts[0], deg / max(math.cos(math.radians(lat)), 0.2))
    keep = [p for p in parts if p.intersects(near)]
    return shapely.union_all(keep), len(parts) - len(keep)


def main(sources_path, out_dir):
    cfg = yaml.safe_load(Path(sources_path).read_text())
    rel = resolve_release(cfg["pins"].get("overture_release"))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    div_id = cfg["boundary"]["overture_division_id"]
    bpath = out / "boundary.parquet"
    cached = pq.read_table(bpath) if bpath.exists() else None
    if cached is not None and cached["id"][0].as_py() == div_id:
        g = shapely.from_wkb(cached["wkb"][0].as_py())
    else:
        g = fetch_boundary(rel, div_id, out)
    xmin, ymin, xmax, ymax = g.bounds
    bbox = [xmin - PAD_DEG, ymin - PAD_DEG, xmax + PAD_DEG, ymax + PAD_DEG]
    print(f"{cfg['city']}: bbox {bbox} ({time.time() - t0:.0f}s)", flush=True)

    for theme, layers in LAYERS.items():
        t = time.time()
        src = RemotePMTiles(TILES.format(rel=rel, theme=theme))
        z = src.header["max_zoom"]
        x0, y1 = lonlat_to_tile(bbox[0], bbox[1], z)
        x1, y0 = lonlat_to_tile(bbox[2], bbox[3], z)
        tiles = [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
        wanted = {lyr: (keep, kind) for lyr, _, keep, kind in layers}

        def work(zxy):
            raw = src.get(*zxy)
            return decode_tile(raw, *zxy, wanted) if raw else {}

        collected = defaultdict(list)
        with ThreadPoolExecutor(WORKERS) as ex:
            for res in ex.map(work, tiles):
                for lyr, feats in res.items():
                    collected[lyr].extend(feats)
        for lyr, name, keep, kind in layers:
            rows = merge(collected[lyr], kind)
            to_parquet(rows, keep, out / f"{name}.parquet")
            print(f"  {name}: {len(rows)} features from {len(tiles)} z{z} tiles", flush=True)
        print(f"  [{theme} {time.time() - t:.0f}s]", flush=True)

    (out / "meta.json").write_text(json.dumps({"overture_release": rel, "bbox_padded": bbox,
                                                "division_id": div_id, "via": "pmtiles",
                                                "fetched": time.strftime("%Y-%m-%d")}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
