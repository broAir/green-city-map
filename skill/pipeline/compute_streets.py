"""Compute the v0 street-heatmap metrics for one city.

Implements spec/street-heatmap.md, parameterised by skill/config/street_metrics.yaml.
Inputs come from the adapters (fetch_overture.py, fetch_rasters.py).

Usage: python -I compute_streets.py <sources.yaml> <street_metrics.yaml> <raw_dir> <out_dir>
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import shapely
import yaml
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import Affine
from scipy import sparse
from scipy.sparse.csgraph import dijkstra
from scipy.ndimage import gaussian_filter
from scipy.signal import fftconvolve
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir; access.py lives here
from access import amenity_access, build_walk_graph, transit_access  # noqa: E402

T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


# ---------------------------------------------------------------- loading

def load(path, cols=None):
    t = pq.read_table(path)
    d = {c: t[c].to_pylist() for c in (cols or t.column_names) if c != "wkb"}
    d["geom"] = shapely.from_wkb(np.array(t["wkb"].to_pylist(), dtype=object))
    return d


def project(geoms, tr):
    return shapely.transform(geoms, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))


def flag_share(raw, flag):
    """Share of a segment (0..1) covered by a road/rail flag; `between` limits it to part of the segment."""
    if not raw:
        return 0.0
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return 0.0
    share = 0.0
    for it in items or []:
        if flag in (it.get("values") or []):
            b = it.get("between")
            share += 1.0 if not b else max(0.0, min(1.0, b[1]) - max(0.0, b[0]))
    return min(share, 1.0)


def flags_contain(raw, flag):
    if not raw:
        return False
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return False
    return any(flag in (it.get("values") or []) for it in items)


# ---------------------------------------------------------------- heights

def building_heights(b, cfg):
    n = len(b["geom"])
    h = np.full(n, np.nan)
    tier = np.full(n, "", dtype=object)
    src = np.full(n, "", dtype=object)
    ladder = cfg["heights"]
    height = np.array([x if x is not None else np.nan for x in b["height"]], dtype=float)
    floors = np.array([x if x is not None else np.nan for x in b["num_floors"]], dtype=float)
    ok = np.isfinite(height) & (height >= 2)
    h[ok], tier[ok], src[ok] = height[ok], "B", "height"
    step = ladder[1]
    ok2 = ~ok & np.isfinite(floors) & (floors >= 1)
    h[ok2] = floors[ok2] * step["floor_height_m"] + step["roof_m"]
    tier[ok2], src[ok2] = "B", "num_floors"

    cent = shapely.get_coordinates(shapely.centroid(b["geom"]))
    area = shapely.area(b["geom"])
    known = np.isfinite(h)
    nb = ladder[2]
    if known.sum() >= nb["min_known"]:
        tree = cKDTree(cent[known])
        kh = h[known]
        unk = np.where(~known)[0]
        dist, idx = tree.query(cent[unk], k=25, distance_upper_bound=nb["radius_m"])
        valid = np.isfinite(dist)
        cnt = valid.sum(1)
        safe = np.where(valid, idx, 0)
        vals = np.where(valid, kh[safe], np.nan)
        med = np.nanmedian(np.where(cnt[:, None] >= nb["min_known"], vals, np.nan), axis=1) if len(unk) else []
        good = np.isfinite(med)
        h[unk[good]] = med[good]
        tier[unk[good]], src[unk[good]] = "C", "neighbour_median"

    breaks = ladder[3]["size_breaks_m2"]
    cls = np.digitize(area, breaks)
    for c in range(len(breaks) + 1):
        sel = (cls == c)
        ref = h[sel & (src != "")]
        fill = np.nanmedian(ref) if len(ref) else (4.0 + 3.0 * c)
        m = sel & ~np.isfinite(h)
        h[m] = fill
        tier[m], src[m] = "C", "size_class_median"
    return h, tier, src


# ---------------------------------------------------------------- pieces

def split_line(line, max_len):
    L = line.length
    n = max(1, math.ceil(L / max_len))
    if n == 1:
        return [line]
    return [shapely.ops.substring(line, L * i / n, L * (i + 1) / n) for i in range(n)]


def make_pieces(segs, cfg):
    pc = cfg["piece"]
    out_geom, out_seg = [], []
    for i in np.where(segs["scored"])[0]:
        g = segs["geom"][i]
        for part in getattr(g, "geoms", [g]):
            if part.geom_type != "LineString" or part.length < 1:
                continue
            for p in split_line(part, pc["max_length_m"]):
                out_geom.append(p)
                out_seg.append(i)
    return np.array(out_geom, dtype=object), np.array(out_seg)


def cross_sections(pieces, spacing):
    pts, dirs, owner = [], [], []
    for k, g in enumerate(pieces):
        L = g.length
        n = max(1, int(L // spacing))
        ts = (np.arange(n) + 0.5) * (L / n)
        a = shapely.line_interpolate_point(g, np.clip(ts - 1, 0, L))
        b = shapely.line_interpolate_point(g, np.clip(ts + 1, 0, L))
        p = shapely.line_interpolate_point(g, ts)
        ca, cb = shapely.get_coordinates(a), shapely.get_coordinates(b)
        d = cb - ca
        nrm = np.linalg.norm(d, axis=1, keepdims=True)
        d = np.where(nrm > 0, d / np.maximum(nrm, 1e-9), [1.0, 0.0])
        pts.append(shapely.get_coordinates(p))
        dirs.append(d)
        owner.append(np.full(len(ts), k))
    return np.vstack(pts), np.vstack(dirs), np.concatenate(owner)


def first_hits(origins, normals, ray_max, btree, bgeom, bheight, min_d=1.0):
    """Distance and height of the first building hit along each ray."""
    ends = origins + normals * ray_max
    rays = shapely.linestrings(np.stack([origins, ends], axis=1))
    ri, bi = btree.query(rays, predicate="intersects")
    dist = np.full(len(origins), np.nan)
    hgt = np.full(len(origins), np.nan)
    if len(ri) == 0:
        return dist, hgt
    inter = shapely.intersection(rays[ri], bgeom[bi])
    d = shapely.distance(shapely.points(origins[ri]), inter)
    ok = np.isfinite(d) & (d >= min_d)
    ri, bi, d = ri[ok], bi[ok], d[ok]
    order = np.lexsort((d, ri))
    ri, bi, d = ri[order], bi[order], d[order]
    first = np.r_[True, ri[1:] != ri[:-1]]
    dist[ri[first]] = d[first]
    hgt[ri[first]] = bheight[bi[first]]
    return dist, hgt


# ---------------------------------------------------------------- rasters

class Grid:
    def __init__(self, npz):
        self.ndvi = npz["ndvi"].astype(np.float32)
        self.ndvi[(self.ndvi < -1) | (self.ndvi > 1)] = np.nan     # a few noisy pixels from older runs
        self.wc = npz["worldcover"]
        self.T = Affine(*npz["transform"])
        self.inv = ~self.T
        self.h, self.w = self.wc.shape

    def index(self, xy):
        c, r = self.inv * (xy[:, 0], xy[:, 1])
        r, c = np.floor(r).astype(int), np.floor(c).astype(int)
        ok = (r >= 0) & (r < self.h) & (c >= 0) & (c < self.w)
        return np.where(ok, r, 0), np.where(ok, c, 0), ok

    def sample(self, arr, xy, fill=np.nan):
        r, c, ok = self.index(xy)
        v = arr[r, c].astype(float)
        v[~ok] = fill
        return v


def disk(radius_px):
    y, x = np.ogrid[-radius_px:radius_px + 1, -radius_px:radius_px + 1]
    return (x * x + y * y <= radius_px * radius_px).astype(np.float32)


def focal_mean(values, valid, radius_px):
    k = disk(radius_px)
    num = fftconvolve(np.where(valid, values, 0).astype(np.float32), k, mode="same")
    den = fftconvolve(valid.astype(np.float32), k, mode="same")
    with np.errstate(all="ignore"):
        return np.where(den > 1, num / den, np.nan)


# ---------------------------------------------------------------- main

def main(sources_path, metrics_path, raw_dir, out_dir):
    src_cfg = yaml.safe_load(Path(sources_path).read_text())
    cfg = yaml.safe_load(Path(metrics_path).read_text())
    raw, out = Path(raw_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ov = raw / "overture"
    crs = src_cfg["crs"]
    tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    stats = {}

    boundary = project(load(ov / "boundary.parquet")["geom"], tr)[0]
    shapely.prepare(boundary)

    # buildings -------------------------------------------------------------
    b = load(ov / "buildings.parquet")
    b["geom"] = project(b["geom"], tr)
    keep = np.array([u is not True for u in b["is_underground"]]) & (shapely.area(b["geom"]) >= 12)
    b = {k: (np.array(v, dtype=object)[keep] if k != "geom" else v[keep]) for k, v in b.items()}
    bh, btier, bsrc = building_heights(b, cfg)
    bgeom = b["geom"]
    btree = shapely.STRtree(bgeom)
    inside_b = shapely.contains_xy(boundary, *shapely.get_coordinates(shapely.centroid(bgeom)).T)
    u, c = np.unique(bsrc[inside_b], return_counts=True)
    stats["buildings"] = {"count_in_city": int(inside_b.sum()),
                          "height_source_share": {k: round(v / inside_b.sum(), 3) for k, v in zip(u, c)},
                          "median_height_m": round(float(np.median(bh[inside_b])), 1)}
    log(f"buildings {len(bgeom)} (in city {inside_b.sum()}), height sources {stats['buildings']['height_source_share']}")

    # segments --------------------------------------------------------------
    s = load(ov / "segments.parquet")
    s["geom"] = project(s["geom"], tr)
    n = len(s["geom"])
    sub = np.array(s["subtype"], dtype=object)
    cls = np.array([c or "" for c in s["class"]], dtype=object)
    # a segment counts as a tunnel when at least half of it is underground
    tunnel = np.array([max(flag_share(s["road_flags"][i], "is_tunnel"), flag_share(s["rail_flags"][i], "is_tunnel")) >= 0.5
                       for i in range(n)])
    pc = cfg["piece"]
    subcls = np.array([c or "" for c in s["subclass"]], dtype=object)
    excluded_flag = np.array([any(flags_contain(s["road_flags"][i], f) for f in pc.get("exclude_road_flags", []))
                              for i in range(n)])
    s["scored"] = (sub == "road") & np.isin(cls, pc["scored_classes"]) & ~tunnel & ~excluded_flag & \
        ~np.isin(subcls, pc.get("exclude_subclasses", []))
    s["cls"], s["sub"], s["tunnel"] = cls, sub, tunnel

    pieces, pseg = make_pieces(s, cfg)
    mid = shapely.line_interpolate_point(pieces, 0.5, normalized=True)
    midxy = shapely.get_coordinates(mid)
    in_city = shapely.contains_xy(boundary, midxy[:, 0], midxy[:, 1])
    pieces, pseg, midxy = pieces[in_city], pseg[in_city], midxy[in_city]
    log(f"segments {n}, scored pieces in city {len(pieces)}")

    # cross-sections ----------------------------------------------------------
    cs = cfg["cross_section"]
    P, D, owner = cross_sections(pieces, cs["spacing_m"])
    nl = np.column_stack([-D[:, 1], D[:, 0]])
    dL, hL = first_hits(P, nl, cs["ray_max_m"], btree, bgeom, bh)
    dR, hR = first_hits(P, -nl, cs["ray_max_m"], btree, bgeom, bh)
    log(f"cross-sections {len(P)}")

    both = np.isfinite(dL) & np.isfinite(dR)
    one = np.isfinite(dL) ^ np.isfinite(dR)
    W = np.where(both, dL + dR, np.inf)
    eye = cs["eye_height_m"]
    with np.errstate(all="ignore"):
        hw = np.where(both, (hL + hR) / 2 / W,
                      np.where(one, np.minimum(np.fmax(hL, hR) / (np.fmin(dL, dR) + cs["ray_max_m"]), cs["open_hw_cap"]),
                               np.nan))
        theta = np.where(both, np.degrees(np.maximum(np.arctan((hR - eye) / W), np.arctan((hL - eye) / W))),
                         np.where(one, 0.0, np.nan))
    theta = np.where(np.isfinite(theta), np.maximum(theta, 0), theta)
    near = np.fmin(dL, dR)
    frontage_sec = np.isfinite(near) & (near <= cs["frontage_max_m"])

    # footways only count as lanes between buildings
    pcls = s["cls"][pseg]
    fw_lim = pc["footway_needs_both_sides_within_m"]
    lane_sec = both & (dL <= fw_lim) & (dR <= fw_lim)

    # view samples across the street --------------------------------------------
    grid = Grid(np.load(raw / "rasters" / "rasters.npz", allow_pickle=False))
    rc = cfg["rasters"]
    tree_mask = np.isin(grid.wc, rc["worldcover_tree_classes"]).astype(np.float32)
    step = cs["view_sample_step_m"]
    lim_l = np.where(np.isfinite(dL), dL, 30.0)
    lim_r = np.where(np.isfinite(dR), dR, 30.0)
    offs = np.arange(-cs["ray_max_m"], cs["ray_max_m"] + step, step)
    S = P[:, None, :] + offs[None, :, None] * nl[:, None, :]
    inwin = (offs[None, :] <= lim_l[:, None]) & (-offs[None, :] <= lim_r[:, None])
    flat = S.reshape(-1, 2)
    nv = grid.sample(grid.ndvi, flat).reshape(S.shape[:2])
    tv = grid.sample(tree_mask, flat).reshape(S.shape[:2])
    known = (tv > 0) | np.isfinite(nv)          # missing NDVI is unknown, not "not green"
    tv = np.where((tv > 0) | (np.nan_to_num(nv) >= cs["green_view_ndvi_min"]), 1.0, 0.0)
    with np.errstate(all="ignore"):
        kwin = inwin & known
        trees_sec = np.where(kwin.sum(1) > 0, np.sum(np.where(kwin, tv, 0), 1) / np.maximum(kwin.sum(1), 1), np.nan)
        nmask = inwin & np.isfinite(nv)
        ndvi_sec = np.where(nmask.sum(1) > 0, np.nansum(np.where(nmask, nv, 0), 1) / np.maximum(nmask.sum(1), 1), np.nan)

    # aggregate sections -> pieces --------------------------------------------------
    npieces = len(pieces)
    order = np.argsort(owner, kind="stable")
    bounds = np.searchsorted(owner[order], np.arange(npieces + 1))

    def per_piece(vals, mask, how="median"):
        res = np.full(npieces, np.nan)
        for k in range(npieces):
            idx = order[bounds[k]:bounds[k + 1]]
            idx = idx[mask[idx]]
            if len(idx):
                v = vals[idx]
                res[k] = np.median(v) if how == "median" else np.mean(v)
        return res

    frontage = np.zeros(npieces, bool)
    lane = np.zeros(npieces, bool)
    for k in range(npieces):
        idx = order[bounds[k]:bounds[k + 1]]
        frontage[k] = frontage_sec[idx].any()
        lane[k] = lane_sec[idx].mean() >= 0.5 if len(idx) else False
    is_fw = np.isin(pcls, pc["lane_classes"])
    scored = frontage & (~is_fw | lane)
    m = frontage_sec
    open_share = per_piece(np.isinf(W).astype(float), m, "mean")
    P_W = per_piece(W, m & np.isfinite(W))
    P_W[open_share > 0.5] = np.inf             # open only when most sections have nothing opposite
    P_hw = per_piece(hw, m)
    P_theta = per_piece(theta, m)
    P_trees = per_piece(trees_sec, m & np.isfinite(trees_sec), "mean")
    P_ndvi_c = per_piece(ndvi_sec, m & np.isfinite(ndvi_sec), "mean")
    P_facade_d = per_piece(near, m)
    # a piece is "open" when most of its cross-sections have nothing opposite; keep its
    # obstruction angle and canyon ratio consistent with that
    open_piece = np.isinf(P_W)
    P_theta[open_piece] = 0.0
    P_hw[open_piece] = np.fmin(P_hw[open_piece], cs["open_hw_cap"])
    log(f"pieces scored {scored.sum()} / {npieces}")
    if not scored.any():
        raise SystemExit("No street piece has a building within reach: is the boundary right, and does "
                         "Overture have buildings here?")

    # area rasters ---------------------------------------------------------------
    water = grid.wc == rc["worldcover_water_class"]
    land = (grid.wc > 0) & ~water
    px = rc["grid_m"]
    canopy = focal_mean(tree_mask, land, int(rc["focal_radii_m"]["canopy_300"] / px)) * 100
    builtup = grid.wc == rc["worldcover_builtup_class"]
    sealed_mask = builtup & (np.nan_to_num(grid.ndvi, nan=0) < rc["sealed_ndvi_max"])
    sealed_known = land & (~builtup | np.isfinite(grid.ndvi))   # built-up cells without NDVI are unknown
    sealed = focal_mean(sealed_mask.astype(np.float32), sealed_known, int(rc["focal_radii_m"]["sealed_300"] / px)) * 100
    nd_ok = land & np.isfinite(grid.ndvi)
    ndvi500 = focal_mean(np.nan_to_num(grid.ndvi), nd_ok, int(rc["focal_radii_m"]["ndvi_500"] / px))
    P_canopy = grid.sample(canopy, midxy)
    P_sealed = grid.sample(sealed, midxy)
    P_ndvi500 = grid.sample(ndvi500, midxy)
    log("area rasters done")

    # nature ladder ------------------------------------------------------------------
    nat = cfg["nature"]
    cand = []
    for name, classes in (("land_use", nat["land_use_classes"]), ("land", nat["land_classes"])):
        d = load(ov / f"{name}.parquet")
        cl = np.array([c or "" for c in d["class"]], dtype=object)
        sel = np.isin(cl, classes) & ~np.isin(cl, nat["exclude_classes"])
        g = project(d["geom"][sel], tr)
        cand.append((g, cl[sel]))
    cgeom = np.concatenate([c[0] for c in cand])
    ccls = np.concatenate([c[1] for c in cand])
    beach = shapely.union_all(cgeom[np.isin(ccls, ["beach", "sand"])]) if np.isin(ccls, ["beach", "sand"]).any() else None
    gap = nat["merge_gap_m"] / 2
    merged = shapely.union_all(shapely.buffer(shapely.make_valid(cgeom), gap)).buffer(-gap)
    areas = np.array([p for p in getattr(merged, "geoms", [merged]) if p.area > 0], dtype=object)
    area_ha = shapely.area(areas) / 1e4
    areas, area_ha = areas[area_ha >= nat["tiers"][0]["min_ha"]], area_ha[area_ha >= nat["tiers"][0]["min_ha"]]
    lab = rasterize([(g, i + 1) for i, g in enumerate(areas)], out_shape=grid.wc.shape, transform=grid.T,
                    fill=0, dtype="int32")
    okn = (lab > 0) & np.isfinite(grid.ndvi)
    sums = np.bincount(lab[okn], weights=grid.ndvi[okn], minlength=len(areas) + 1)
    cnts = np.bincount(lab[okn], minlength=len(areas) + 1)
    mean_ndvi = np.where(cnts > 0, sums / np.maximum(cnts, 1), 0)[1:]
    is_beach = np.zeros(len(areas), bool)
    if beach is not None:
        is_beach = shapely.area(shapely.intersection(areas, beach)) > 0.5 * shapely.area(areas)
    qual = (mean_ndvi >= nat["min_mean_ndvi"]) | is_beach
    areas, area_ha = areas[qual], area_ha[qual]
    stats["nature_areas"] = {"qualifying": int(len(areas)), "total_ha": round(float(area_ha.sum()), 1),
                             "by_tier": {f">={t['min_ha']}ha": int((area_ha >= t["min_ha"]).sum()) for t in nat["tiers"]}}
    log(f"nature areas qualifying {len(areas)} ({area_ha.sum():.0f} ha)")

    con = load(ov / "connectors.parquet")
    cxy = shapely.get_coordinates(project(con["geom"], tr))
    walk = (s["sub"] == "road") & ~np.isin(s["cls"], nat["walk_exclude_classes"])
    graph = build_walk_graph(s["geom"], s["connectors"], walk, con["id"], cxy, cfg["walk_graph"]["densify_m"])
    piece_node, snap_d = graph.snap(midxy)
    log(f"walk graph {len(graph.xy)} nodes, {graph.G.nnz} edges")
    atree = shapely.STRtree(areas)
    ni, ai = atree.query(shapely.points(graph.xy), predicate="dwithin", distance=nat["entrance_snap_m"])
    tiers_met = np.zeros(len(midxy), int)
    nature_d = {}
    for t in nat["tiers"]:
        srcs = ni[area_ha[ai] >= t["min_ha"]]
        dist, _ = graph.nearest(srcs, max(t["max_walk_m"], 3000) + 1)
        d = dist[piece_node] + snap_d
        nature_d[t["min_ha"]] = d
        tiers_met += (d <= t["max_walk_m"]).astype(int)
    log("nature ladder done")

    # noise proxy ----------------------------------------------------------------------
    nz = cfg["noise_proxy"]
    lref = np.full(n, np.nan)
    for c, v in nz["road_l_ref"].items():
        lref[(s["sub"] == "road") & (s["cls"] == c)] = v
    for c, v in nz["rail_l_ref"].items():
        lref[(s["sub"] == "rail") & (s["cls"] == c)] = v
    lref[s["tunnel"]] = np.nan
    own_l = lref[pseg]
    fd = np.maximum(np.where(np.isfinite(P_facade_d), P_facade_d, 10.0), nz["facade_min_distance_m"])
    L_own = own_l - 10 * np.log10(fd / 10)
    src_idx = np.where(np.isfinite(lref) & (lref >= nz["neighbour_min_l_ref"]))[0]
    stree = shapely.STRtree(s["geom"][src_idx])
    pi, si = stree.query(shapely.points(midxy), predicate="dwithin", distance=nz["neighbour_radius_m"])
    sidx = src_idx[si]
    names = np.array([x or "" for x in s["name"]], dtype=object)
    ident = np.where(names != "", names, np.array([f"seg{i}" for i in range(n)], dtype=object))   # road identity
    keepm = (sidx != pseg[pi]) & (ident[sidx] != ident[pseg[pi]])   # the piece's own road is counted once, as L_own
    pi, sidx = pi[keepm], sidx[keepm]
    dist = shapely.distance(shapely.points(midxy[pi]), s["geom"][sidx])
    dist = np.maximum(dist, fd[pi])                 # a window is no closer to a road than its own facade
    key = np.where(names[sidx] != "", names[sidx], s["cls"][sidx])
    # one contribution per (piece, road identity): the nearest segment of that road
    order2 = np.lexsort((dist, key, pi))
    pi, sidx, dist, key = pi[order2], sidx[order2], dist[order2], key[order2]
    firstk = np.r_[True, (pi[1:] != pi[:-1]) | (key[1:] != key[:-1])]
    pi, sidx, dist = pi[firstk], sidx[firstk], np.maximum(dist[firstk], 3.0)
    lines = shapely.shortest_line(shapely.points(midxy[pi]), s["geom"][sidx])
    li, _ = btree.query(lines, predicate="crosses")
    shielded = np.zeros(len(pi), bool)
    shielded[np.unique(li)] = True
    Li = lref[sidx] - 10 * np.log10(dist / 10) - np.where(shielded, nz["shielding_db"], 0)
    energy = np.where(np.isfinite(L_own), 10 ** (L_own / 10), 0)
    np.add.at(energy, pi, 10 ** (Li / 10))
    P_noise = np.where(energy > 0, 10 * np.log10(np.maximum(energy, 1e-9)), np.nan)
    major = np.isin(s["cls"], ["motorway", "trunk", "primary"]) & (s["sub"] == "road") | \
        ((s["sub"] == "rail") & np.isin(s["cls"], ["standard_gauge", "narrow_gauge", "light_rail"]))
    major &= ~s["tunnel"]
    if major.any():
        mtree = shapely.STRtree(s["geom"][major])
        _, md = mtree.query_nearest(shapely.points(midxy), return_distance=True, all_matches=False)
        P_major = np.asarray(md, dtype=float)
    else:                                           # small towns: no major road or railway at all
        P_major = np.full(len(midxy), 9999.0)
    P_major[np.isin(pcls, ["primary"])] = 0.0
    log("noise proxy done")

    # tourism -------------------------------------------------------------------------
    pl = load(ov / "places.parquet")
    pats = cfg["tourism"]["place_category_patterns"]
    def own_category(bc, tx):
        try:
            prim = (json.loads(tx) if isinstance(tx, str) else (tx or {})).get("primary") or ""
        except ValueError:
            prim = ""
        return f"{bc or ''} {prim}".lower()
    # match the place's own category only: an ancestor like "cultural_and_historic" would pull in every church
    text = [own_category(bc, tx) for bc, tx in zip(pl["basic_category"], pl["taxonomy"])]
    conf = np.array([c if c is not None else 0 for c in pl["confidence"]], dtype=float)
    tsel = np.array([any(p in t for p in pats) for t in text]) & (conf >= 0.5)
    txy = shapely.get_coordinates(project(pl["geom"][tsel], tr))
    r, c, ok = grid.index(txy)
    dens = np.zeros(grid.wc.shape, np.float32)
    np.add.at(dens, (r[ok], c[ok]), 1.0)
    dens = gaussian_filter(dens, cfg["tourism"]["kernel_sigma_m"] / px) * 1e6 / (px * px)
    P_tour = grid.sample(dens, midxy)
    stats["tourism_places"] = int(tsel.sum())
    log(f"tourism places {tsel.sum()}")

    # everyday access ---------------------------------------------------------------------
    def lonlat(a):
        a = np.asarray(a, dtype=float).reshape(-1, 2)
        return np.column_stack(tr.transform(a[:, 0], a[:, 1])) if len(a) else np.zeros((0, 2))
    osm_dir = raw / "osm"
    tra = transit_access(json.loads((osm_dir / "transit.json").read_text()), lonlat, graph, piece_node, snap_d,
                         cfg, log)
    ame = amenity_access(json.loads((osm_dir / "amenities.json").read_text()), pl, lonlat, graph, piece_node,
                         snap_d, cfg, log)
    stats["transit"] = tra["stats"]
    stats["amenities"] = ame["stats"]
    log("access done")

    # export ----------------------------------------------------------------------------
    sc = scored
    metrics = {
        "W": np.where(np.isinf(P_W), -1, P_W),            # -1 = open (no facing building within 60 m)
        "theta": P_theta, "hw": P_hw, "green_across": P_trees,
        "canopy_300": P_canopy, "sealed_300": P_sealed, "ndvi_500": P_ndvi500, "ndvi_corridor": P_ndvi_c,
        "nature_tiers": tiers_met.astype(float),
        "nature_d2": nature_d[2], "nature_d20": nature_d[20], "nature_d100": nature_d[100],
        "noise_lden": P_noise, "major_road_m": P_major, "tourism_density": P_tour,
    }

    x0, y0 = math.floor(boundary.bounds[0]), math.floor(boundary.bounds[1])

    def enc(g, tol=0.0):
        """Delta-encoded integer metre coordinates relative to the city origin."""
        xy = np.round(shapely.get_coordinates(g.simplify(tol) if tol else g) - [x0, y0]).astype(int)
        d = np.vstack([xy[:1], np.diff(xy, axis=0)])
        return d.ravel().tolist()

    sc_idx = np.where(sc)[0]
    seg_names = names[pseg]
    uniq_names, name_idx = np.unique(seg_names[sc_idx], return_inverse=True)
    classes = sorted(set(pcls[sc_idx]))
    cls_idx = np.array([classes.index(c) for c in pcls[sc_idx]])

    # unscored pieces: merged back per original segment, simplified, grey context.
    # Footpaths and service ways without frontage are left out: they are most of the
    # volume (park paths, yards) and add little to reading the map.
    uns = np.where(~sc & ~np.isin(pcls, list(pc["lane_classes"]) + ["service"]))[0]
    grey = []
    if len(uns):
        by_seg = {}
        for k in uns:
            by_seg.setdefault(pseg[k], []).append(pieces[k])
        for parts in by_seg.values():
            g = shapely.line_merge(shapely.union_all(parts)) if len(parts) > 1 else parts[0]
            for part in getattr(g, "geoms", [g]):
                if part.length > 15:
                    grey.append(enc(part, 3))

    # context: motorways, trunks and rail outside tunnels, near the city
    ctx = (np.isin(s["cls"], ["motorway", "trunk"]) & (s["sub"] == "road") | (s["sub"] == "rail")) & ~s["tunnel"]
    ctx_geoms = shapely.intersection(s["geom"][ctx], boundary.buffer(500))
    ctx_lines = []
    for g, kind in zip(ctx_geoms, np.where(s["sub"][ctx] == "rail", 1, 0)):
        for part in getattr(g, "geoms", [g]):
            if part.geom_type == "LineString" and part.length > 5:
                ctx_lines.append([int(kind)] + enc(part, 2))

    def q(v, scale):
        v = v[sc_idx]
        return np.where(np.isfinite(v), np.round(v * scale), -9999).astype(int).tolist()

    payload = {
        "city": src_cfg["city"], "name": src_cfg["name"], "country": src_cfg["country"], "crs": crs,
        "origin": [x0, y0],
        "boundary": [enc(p.exterior, 10) for p in getattr(boundary, "geoms", [boundary])],
        "extent": [0, 0, math.ceil(boundary.bounds[2] - x0), math.ceil(boundary.bounds[3] - y0)],
        "stats": {**stats, "pieces": int(npieces), "pieces_scored": int(sc.sum()),
                  "cross_sections": int(len(P))},
        "layers": src_cfg["layers"],
        "classes": classes, "names": uniq_names.tolist(),
        "pieces": {
            "geom": [enc(pieces[k]) for k in sc_idx],
            "cls": cls_idx.tolist(), "name": name_idx.tolist(),
            # W in decimetres; open streets (nothing opposite within ray_max) are the sentinel -1
            "W": np.where(metrics["W"][sc_idx] == -1, -1, q(metrics["W"], 10)).astype(int).tolist(),
            "theta": q(metrics["theta"], 1),
            "hw": q(metrics["hw"], 100),
            "green_across": q(metrics["green_across"], 100),
            "canopy_300": q(metrics["canopy_300"], 1), "sealed_300": q(metrics["sealed_300"], 1),
            "ndvi_500": q(metrics["ndvi_500"], 100), "ndvi_corridor": q(metrics["ndvi_corridor"], 100),
            "nature_tiers": q(metrics["nature_tiers"], 1),
            "noise_lden": q(metrics["noise_lden"], 1), "major_road_m": q(np.minimum(metrics["major_road_m"], 9999), 1),
            "tourism": q(P_tour, 1),                      # places per km2, smoothed; ranked in the viewer
            "transit_ai": q(tra["ai"], 10),
            **{f"need_{c}": q(np.minimum(ame["dist"][c], 9999), 0.1) for c in ame["dist"]},   # 10 m units
        },
        "grey": grey,
        "context": ctx_lines,
    }
    (out / "streets.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    # per-piece details for the card, loaded by the viewer after the map is drawn
    def dl(a):
        """Delta-encode an int list (neighbouring pieces often share a nearest stop or shop)."""
        a = np.asarray(a, dtype=int)
        return np.diff(np.r_[0, a]).tolist()
    details = {
        "nature_d2": q(np.minimum(metrics["nature_d2"], 99999), 0.1),
        "nature_d20": q(np.minimum(metrics["nature_d20"], 99999), 0.1),
        "nature_d100": q(np.minimum(metrics["nature_d100"], 99999), 0.1),
        "transit_routes": q(tra["nroutes"].astype(float), 1),
        **{f"stop_{g[0]}_d": q(np.minimum(tra["near"][g][0], 9999), 0.1) for g in ("surface", "rapid")},
        **{f"stop_{g[0]}_i": dl(tra["near"][g][1][sc_idx]) for g in ("surface", "rapid")},
        **{f"need_{c}_i": dl(ame["near"][c][sc_idx]) for c in ame["near"]},
        "units": "distances in 10 m; *_i delta-encoded indices into layers.json stops / pois",
    }
    (out / "details.json").write_text(json.dumps(details, separators=(",", ":")), encoding="utf-8")

    # point layers for the map: stop symbols and amenities, local integer metres
    st, po = tra["stops"], ame["pois"]
    lnames = sorted(set(st["name"]) | set(po["name"]))
    lid = {n: i for i, n in enumerate(lnames)}
    layers = {
        "names": lnames,
        "stops": {"x": np.round(np.asarray(st["x"]) - x0).astype(int).tolist(),
                  "y": np.round(np.asarray(st["y"]) - y0).astype(int).tolist(),
                  "name": [lid[n] for n in st["name"]], "rapid": [int(g == "rapid") for g in st["group"]],
                  "modes": st["modes"], "mode_list": st["mode_list"], "n_routes": st["n_routes"],
                  "routes": [[[st["mode_list"].index(m), r] for m, r in rs] for rs in st["routes"]]},
        "pois": {"x": np.round(np.asarray(po["x"]) - x0).astype(int).tolist(),
                 "y": np.round(np.asarray(po["y"]) - y0).astype(int).tolist(),
                 "cat": np.asarray(po["cat"]).tolist(), "name": [lid[n] for n in po["name"]],
                 "src": np.asarray(po["src"]).tolist()},
        "categories": [{"id": c["id"], "label": c["label"]} for c in cfg["amenities"]["categories"]],
    }
    (out / "layers.json").write_text(json.dumps(layers, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    (out / "stats.json").write_text(json.dumps(payload["stats"], indent=1))

    # quick sanity summary of scored pieces
    summ = {}
    for k in ("W", "theta", "hw", "green_across", "canopy_300", "sealed_300", "ndvi_500", "ndvi_corridor",
              "nature_tiers", "noise_lden", "major_road_m"):
        v = metrics[k][sc]
        v = v[np.isfinite(v) & (v >= 0)] if k == "W" else v[np.isfinite(v)]
        summ[k] = [round(float(x), 2) for x in np.percentile(v, [10, 50, 90])] if len(v) else None
    summ["W_open_share"] = round(float((metrics["W"][sc] == -1).mean()), 3)
    v = tra["ai"][sc]
    v = v[np.isfinite(v)]
    summ["transit_ai"] = [round(float(x), 2) for x in np.percentile(v, [10, 50, 90])] if len(v) else None
    for c in cfg["amenities"]["categories"]:
        v = ame["dist"][c["id"]][sc]
        v = v[np.isfinite(v)] if np.isfinite(v).any() else v[:0]
        summ[f"need_{c['id']}_m"] = [round(float(x)) for x in np.percentile(np.minimum(v, 9999), [10, 50, 90])] if len(v) else None
    (out / "summary.json").write_text(json.dumps(summ, indent=1))
    log(f"summary p10/p50/p90: {json.dumps(summ)}")
    log(f"wrote {out / 'streets.json'} ({(out / 'streets.json').stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    import shapely.ops  # noqa: F401  (substring)
    main(*sys.argv[1:5])
