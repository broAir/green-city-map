"""Everyday access (spec/street-heatmap.md §5): the shared walk graph, M20 transit
access (PTAL-lite) and M21 daily needs on foot.

Imported by compute_streets.py. All distances are walking distances on the
Overture segment graph, densified so that snapping error stays small.
"""
import json
import math
import re

import numpy as np
import shapely
from scipy import sparse
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree


# ---------------------------------------------------------------- walk graph

class WalkGraph:
    def __init__(self, xy, G):
        self.xy, self.G = xy, G
        self.tree = cKDTree(xy)

    def snap(self, pts, max_d=np.inf):
        """Nearest node per point; -1 where no node is within max_d."""
        d, i = self.tree.query(pts, distance_upper_bound=max_d)
        ok = np.isfinite(d)
        return np.where(ok, i, -1), np.where(ok, d, np.inf)

    def nearest(self, src_nodes, limit):
        """Multi-source shortest paths: distance to the nearest source and which source node it is."""
        src = np.unique(src_nodes[src_nodes >= 0])
        if len(src) == 0:
            n = len(self.xy)
            return np.full(n, np.inf), np.full(n, -1)
        dist, _, sources = dijkstra(self.G, directed=False, indices=src, min_only=True, limit=limit,
                                    return_predecessors=True)
        return dist, sources


def build_walk_graph(seg_geom, seg_connectors, walk, con_ids, con_xy, densify_m, tol_m=2.0):
    """Walk graph from Overture segments and their connectors.

    Connectors are placed on each segment by projecting their coordinates onto the
    segment's geometry, part by part, rather than trusting the stored `at` fractions:
    segments rebuilt from tile pieces can be multi-part with parts out of order, or
    clipped at the download edge. Connectors further than tol_m from a part are not on
    it. Long stretches get extra nodes every densify_m so snapping stays accurate."""
    cid = {c: i for i, c in enumerate(con_ids)}
    C = len(con_xy)
    ei, ej, ew = [], [], []
    new_part, new_dist = [], []
    nxt = C
    for i in np.where(walk)[0]:
        raw = seg_connectors[i]
        if not raw:
            continue
        cs = json.loads(raw) if isinstance(raw, str) else raw
        ids = np.unique([cid[c["connector_id"]] for c in cs if c.get("connector_id") in cid])
        if len(ids) < 2:
            continue
        g = seg_geom[i]
        parts = [q for q in getattr(g, "geoms", [g]) if q.geom_type == "LineString" and q.length > 0]
        pts = shapely.points(con_xy[ids])
        for part in parts:
            on = shapely.distance(part, pts) <= tol_m
            if on.sum() < 2:
                continue
            pos = shapely.line_locate_point(part, pts[on])
            order = np.argsort(pos)
            nodes, pos = ids[on][order], pos[order]
            for a in range(len(nodes) - 1):
                n1, n2, p1, p2 = nodes[a], nodes[a + 1], pos[a], pos[a + 1]
                if n1 == n2:
                    continue
                seglen = max(p2 - p1, 0.1)
                k = max(1, math.ceil(seglen / densify_m))
                chain = [n1]
                for j in range(1, k):
                    new_part.append(part)
                    new_dist.append(p1 + (p2 - p1) * j / k)
                    chain.append(nxt)
                    nxt += 1
                chain.append(n2)
                for u, v in zip(chain[:-1], chain[1:]):
                    ei.append(u)
                    ej.append(v)
                    ew.append(seglen / k)
    new_xy = (shapely.get_coordinates(shapely.line_interpolate_point(np.array(new_part, dtype=object), np.array(new_dist)))
              if new_part else np.zeros((0, 2)))
    all_xy = np.vstack([con_xy, new_xy])
    ei, ej, ew = np.array(ei), np.array(ej), np.array(ew)
    # parallel segments between the same two nodes: keep the shortest (COO->CSR would add them up)
    lo, hi = np.minimum(ei, ej), np.maximum(ei, ej)
    order = np.lexsort((ew, hi, lo))
    lo, hi, ew = lo[order], hi[order], ew[order]
    first = np.r_[True, (lo[1:] != lo[:-1]) | (hi[1:] != hi[:-1])]
    ei, ej, ew = lo[first], hi[first], ew[first]
    used = np.unique(np.r_[ei, ej])
    remap = np.full(len(all_xy), -1)
    remap[used] = np.arange(len(used))
    G = sparse.coo_matrix((ew, (remap[ei], remap[ej])), shape=(len(used), len(used))).tocsr()
    return WalkGraph(all_xy[used], G)


# ---------------------------------------------------------------- transit (M20)

def _interval_minutes(v):
    """OSM interval=* ("10", "00:10", "0:07:30") in minutes, or None."""
    if not v:
        return None
    v = v.split(";")[0].strip()
    try:
        if ":" in v:
            parts = [float(p) for p in v.split(":")]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            return parts[0] * 60 + parts[1] + parts[2] / 60
        return float(v)
    except ValueError:
        return None


def _mode_from_tags(t):
    if t.get("railway") == "subway_entrance":
        return "entrance"
    if t.get("station") == "subway" or t.get("subway") == "yes":
        return "subway"
    if t.get("station") == "light_rail" or t.get("light_rail") == "yes":
        return "light_rail"
    if t.get("railway") == "tram_stop" or t.get("tram") == "yes":
        return "tram"
    if t.get("trolleybus") == "yes":
        return "trolleybus"
    if t.get("highway") == "bus_stop" or t.get("bus") == "yes":
        return "bus"
    if t.get("railway") in ("station", "halt") or t.get("train") == "yes":
        return "train"
    if t.get("amenity") == "ferry_terminal" or t.get("ferry") == "yes":
        return "ferry"
    if t.get("public_transport") == "platform":
        return "bus"
    return None


def _clusters(xy, keys, radius):
    """Union-find of points within radius that share a key."""
    parent = np.arange(len(xy))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    if len(xy):
        for a, b in cKDTree(xy).query_pairs(radius, output_type="ndarray"):
            if keys[a] == keys[b]:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
    roots = np.array([find(a) for a in range(len(xy))], dtype=int)
    _, lab = np.unique(roots, return_inverse=True)
    return lab


def transit_access(osm, project, graph, pnode, psnap, cfg, log):
    tc, wg = cfg["transit"], cfg["walk_graph"]
    modes = tc["modes"]
    speed = wg["walk_speed_m_per_min"]
    ex_tags = tc["exclude_route_tags"]
    ex_ref = re.compile(tc["exclude_ref_regex"])
    ex_name = re.compile(tc.get("exclude_name_regex", r"(?!)"), re.IGNORECASE)

    # stops -------------------------------------------------------------------
    stops = [s for s in osm["stops"] if "lat" in s]
    skey = {(s["type"], s["id"]): k for k, s in enumerate(stops)}
    sxy = project(np.array([[s["lon"], s["lat"]] for s in stops]))
    sname = np.array([s["tags"].get("name", "") for s in stops], dtype=object)
    tag_mode = [_mode_from_tags(s["tags"]) for s in stops]

    # routes, deduplicated by (mode, ref or name) -----------------------------
    rkey, rmode, rfreq, rref, rtagged = {}, [], [], [], []
    stop_routes = [set() for _ in stops]
    for r in osm["routes"]:
        t = r["tags"]
        mode = t.get("route")
        if mode not in modes:
            continue
        if any(t.get(k) in v for k, v in ex_tags.items()):
            continue
        ref = (t.get("ref") or "").strip()
        if (ref and ex_ref.match(ref)) or ex_name.search(t.get("name") or ""):
            continue
        ident = (mode, ref or t.get("name") or str(r["id"]))
        if ident not in rkey:
            rkey[ident] = len(rmode)
            rmode.append(mode)
            rfreq.append(modes[mode]["default_per_hour"])
            rref.append(ref or (t.get("name") or "")[:24])
            rtagged.append(False)
        ri = rkey[ident]
        iv = _interval_minutes(t.get("interval")) if tc.get("use_osm_interval_tag") else None
        if iv and 1 <= iv <= 120:      # the most frequent tagged variant wins over the mode default
            rfreq[ri] = max(rfreq[ri], 60.0 / iv) if rtagged[ri] else 60.0 / iv
            rtagged[ri] = True
        for typ, ref_id, _role in r["members"]:
            k = skey.get((typ, ref_id))
            if k is not None:
                stop_routes[k].add(ri)
    rmode, rfreq = np.array(rmode, dtype=object), np.array(rfreq, dtype=float)
    routed = np.array([len(x) > 0 for x in stop_routes])
    log(f"transit: {len(stops)} stop features, {len(rmode)} routes, {routed.sum()} stops listed by a route")

    # stops no route lists (often the platform twin of a listed stop position): take the lines of the same
    # mode from listed stops within adopt_m; only where no listed stop of that mode is within walking reach
    # does the stop count as one line of its own
    rtree = cKDTree(sxy[routed]) if routed.any() else None
    ridx = np.where(routed)[0]
    rapid_modes = {m for m, v in modes.items() if v["group"] == "rapid"}
    family = {"bus": {"bus", "trolleybus", "minibus", "share_taxi"}, "trolleybus": {"trolleybus", "bus"},
              "tram": {"tram", "light_rail"}, "light_rail": {"light_rail", "tram", "subway"},
              "subway": {"subway", "light_rail"}, "train": {"train", "light_rail"}, "ferry": {"ferry"}}
    adopt_m = tc.get("unrouted_adopt_m", 150)
    for k in np.where(~routed)[0]:
        m = tag_mode[k]
        if m is None:
            continue
        if m == "entrance":
            if rtree is None:
                continue
            for j in rtree.query_ball_point(sxy[k], tc["entrance_adopt_m"]):
                stop_routes[k] |= {r for r in stop_routes[ridx[j]] if rmode[r] in rapid_modes}
            continue
        fam = family.get(m, {m})
        if rtree is not None:
            for j in rtree.query_ball_point(sxy[k], adopt_m):
                stop_routes[k] |= {r for r in stop_routes[ridx[j]] if rmode[r] in fam}
            if stop_routes[k]:
                continue
            reach = modes.get(m, {}).get("catchment_m", 640)
            if any(rmode[r] in fam for j in rtree.query_ball_point(sxy[k], reach) for r in stop_routes[ridx[j]]):
                continue        # a listed stop of this mode is in walking reach: no imaginary extra line
        if m in modes:
            ident = (m, "unrouted:" + (sname[k] or str(stops[k]["id"])))
            if ident not in rkey:
                rkey[ident] = len(rmode)
                rmode = np.append(rmode, m)
                rfreq = np.append(rfreq, modes[m]["default_per_hour"] * tc["unrouted_stop_routes"])
                rref.append("")
            stop_routes[k].add(rkey[ident])
    has = np.array([len(x) > 0 for x in stop_routes])
    stop_group = np.array(["rapid" if any(rmode[r] in rapid_modes for r in x) else "surface" for x in stop_routes],
                          dtype=object)
    is_entrance = np.array([m == "entrance" for m in tag_mode])

    # display clusters (entrances are access points, not symbols). Stations merge all their
    # platforms within the radius whatever their names ("Peron 1", "Peron 2"); surface stops
    # merge only with stops of the same name.
    disp = np.where(has & ~is_entrance)[0]
    keys = [("rapid",) if stop_group[k] == "rapid" else ("surface", sname[k]) for k in disp]
    lab = _clusters(sxy[disp], keys, tc["display_cluster_m"])
    ncl = lab.max() + 1 if len(lab) else 0
    cl_xy = np.zeros((ncl, 2))
    np.add.at(cl_xy, lab, sxy[disp])
    cl_xy /= np.maximum(np.bincount(lab, minlength=ncl), 1)[:, None]
    mode_list = list(modes)
    cl_modes = np.zeros(ncl, int)
    cl_routes = [set() for _ in range(ncl)]
    cl_names = [{} for _ in range(ncl)]
    for k, c in zip(disp, lab):
        for r in stop_routes[k]:
            cl_modes[c] |= 1 << mode_list.index(rmode[r])
            cl_routes[c].add(r)
        if sname[k]:
            t = stops[k]["tags"]
            station = t.get("railway") in ("station", "halt") or t.get("public_transport") == "station"
            cl_names[c][sname[k]] = cl_names[c].get(sname[k], 0) + (100 if station else 1)
    cl_name = [max(d, key=d.get) if d else "" for d in cl_names]
    cl_group = np.array(["rapid" if any(rmode[r] in rapid_modes for r in rs) else "surface" for rs in cl_routes])
    stop_cluster = np.full(len(stops), -1)
    stop_cluster[disp] = lab
    if len(disp):
        ctree = cKDTree(cl_xy)
        for k in np.where(is_entrance & has)[0]:
            cand = ctree.query_ball_point(sxy[k], tc["entrance_adopt_m"])
            cand = [c for c in cand if cl_group[c] == "rapid"]
            if cand:
                stop_cluster[k] = min(cand, key=lambda c: np.hypot(*(cl_xy[c] - sxy[k])))

    # snap stops; merge route sets per graph node -----------------------------------
    snode, ssnap = graph.snap(sxy, tc["rapid_snap_max_m"])
    far = (ssnap > wg["snap_max_m"]) & (stop_group != "rapid")
    snode[far], ssnap[far] = -1, np.inf
    use = np.where(has & (snode >= 0))[0]
    node_routes = {}
    node_snap = {}
    for k in use:
        n = snode[k]
        node_routes.setdefault(n, set()).update(stop_routes[k])
        node_snap[n] = min(node_snap.get(n, np.inf), ssnap[k])
    nodes = np.array(sorted(node_routes), dtype=int)
    if not len(nodes):
        log("transit: WARNING no public-transport stops found; transit access is left as no data")
        nan = np.full(len(pnode), np.nan)
        return {"ai": nan, "nroutes": nan, "near": {g: (nan, np.full(len(pnode), -1)) for g in ("surface", "rapid")},
                "stops": {"x": [], "y": [], "name": [], "group": [], "modes": [], "mode_list": list(modes),
                          "routes": [], "n_routes": []}, "stats": {"stop_features": len(stops), "routes": 0}}
    nsnap = np.array([node_snap[n] for n in nodes])
    nr_ptr = np.zeros(len(nodes) + 1, int)
    nr_ptr[1:] = np.cumsum([len(node_routes[n]) for n in nodes])
    nr_idx = np.array([r for n in nodes for r in sorted(node_routes[n])], dtype=int)

    # node-to-piece walking distances within the largest catchment -------------------
    max_c = max(v["catchment_m"] for v in modes.values())
    up, pinv = np.unique(pnode, return_inverse=True)
    pairs_s, pairs_p, pairs_d = [], [], []
    chunk = 24
    for c0 in range(0, len(nodes), chunk):
        idx = nodes[c0:c0 + chunk]
        D = dijkstra(graph.G, directed=False, indices=idx, limit=max_c)
        sub = D[:, up]
        si, pj = np.nonzero(np.isfinite(sub))
        pairs_s.append(si + c0)
        pairs_p.append(pj)
        pairs_d.append(sub[si, pj] + nsnap[si + c0])
    ps, pp, pd = (np.concatenate(a) if a else np.zeros(0) for a in (pairs_s, pairs_p, pairs_d))
    ps, pp = ps.astype(int), pp.astype(int)
    log(f"transit: {len(nodes)} stop nodes, {len(ps)} node-to-street pairs")

    # expand pairs by the routes at each stop node; keep each route's best stop per street node
    cnt = nr_ptr[ps + 1] - nr_ptr[ps]
    rep = np.repeat(np.arange(len(ps)), cnt)
    offs = np.arange(len(rep)) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    rr = nr_idx[nr_ptr[ps[rep]] + offs]
    p_of, d_of = pp[rep], pd[rep]
    catch = np.array([modes[m]["catchment_m"] for m in rmode])
    rel = np.array([modes[m]["reliability_min"] for m in rmode])
    ok = d_of <= catch[rr]
    p_of, d_of, rr = p_of[ok], d_of[ok], rr[ok]
    tat = d_of / speed + 0.5 * 60.0 / rfreq[rr] + rel[rr]
    edf = 30.0 / tat
    order = np.lexsort((-edf, rr, p_of))
    p_of, rr, edf = p_of[order], rr[order], edf[order]
    first = np.r_[True, (p_of[1:] != p_of[:-1]) | (rr[1:] != rr[:-1])]
    p_of, rr, edf = p_of[first], rr[first], edf[first]
    # PTAL: per mode, best route counts in full, the others at half weight
    mcode = np.array([mode_list.index(m) for m in rmode])[rr]
    order = np.lexsort((-edf, mcode, p_of))
    p_of, mcode, edf = p_of[order], mcode[order], edf[order]
    head = np.r_[True, (p_of[1:] != p_of[:-1]) | (mcode[1:] != mcode[:-1])]
    contrib = np.where(head, edf, 0.5 * edf)
    ai_node = np.bincount(p_of, weights=contrib, minlength=len(up))
    nroutes_node = np.bincount(p_of, minlength=len(up))
    ai = ai_node[pinv]
    nroutes = nroutes_node[pinv]
    ai[psnap > wg["snap_max_m"]] = np.nan

    # nearest stop per group, for the card and the map ------------------------------------
    near = {}
    node_cluster = {}
    for k in use:
        node_cluster.setdefault((snode[k], stop_group[k]), stop_cluster[k])
    for grp in ("surface", "rapid"):
        src = np.array([n for n in nodes if (n, grp) in node_cluster], dtype=int)
        dist, sources = graph.nearest(src, 5000)
        sidx = sources[pnode]
        d = dist[pnode] + psnap + np.array([node_snap.get(x, 0.0) if x >= 0 else 0.0 for x in sidx])
        cl = np.array([node_cluster.get((s, grp), -1) if s >= 0 else -1 for s in sidx])
        near[grp] = (np.where(np.isfinite(d), d, np.inf), cl)
    stops_out = {
        "x": cl_xy[:, 0], "y": cl_xy[:, 1], "name": cl_name, "group": cl_group.tolist(),
        "modes": cl_modes.tolist(), "mode_list": mode_list,
        "routes": [sorted({(rmode[r], rref[r]) for r in rs if rref[r]}) for rs in cl_routes],
        "n_routes": [len(rs) for rs in cl_routes],
    }
    stats = {"stop_features": len(stops), "routes": int(sum(1 for r in rref if r)), "pseudo_routes_unlisted_stops": int(sum(1 for r in rref if not r)),
             "stop_symbols": int(ncl),
             "share_stops_routed": round(float(routed[has | routed].mean()) if (has | routed).any() else 0, 3),
             "routes_with_interval_tag": int(sum(1 for r in osm["routes"] if r["tags"].get("interval")))}
    return {"ai": ai, "nroutes": nroutes, "near": near, "stops": stops_out, "stats": stats}


# ---------------------------------------------------------------- daily needs (M21)

def amenity_access(osm, places, project, graph, pnode, psnap, cfg, log):
    ac, wg = cfg["amenities"], cfg["walk_graph"]
    cats = ac["categories"]
    pts, pcat, pname, psrc = [], [], [], []
    # OSM: a feature can count in several categories (a bakery-café)
    els = [e for e in osm["elements"] if "lat" in e]
    oxy = project(np.array([[e["lon"], e["lat"]] for e in els])) if els else np.zeros((0, 2))
    for k, e in enumerate(els):
        t = e["tags"]
        if t.get("access") in ("private", "no"):
            continue
        for ci, c in enumerate(cats):
            if any(t.get(key) in vals for key, vals in c["osm"].items()):
                pts.append(oxy[k])
                pcat.append(ci)
                pname.append(t.get("name", ""))
                psrc.append(0)
    n_osm = len(pts)
    # Overture places, high confidence only, unless OSM already has the same category within dedupe_m
    conf = np.array([c if c is not None else 0 for c in places["confidence"]], dtype=float)
    keep = np.where(conf >= ac["overture_min_confidence"])[0]
    ov_cat = []
    lookup = {}
    for ci, c in enumerate(cats):
        for name in c["overture"]:
            lookup.setdefault(name, ci)
    for k in keep:
        tx = places["taxonomy"][k]
        try:
            tx = json.loads(tx) if isinstance(tx, str) else (tx or {})
        except ValueError:
            tx = {}
        chain = [tx.get("primary"), places["basic_category"][k]]   # own category only, not ancestors
        ci = next((lookup[c] for c in chain if c in lookup), None)
        if ci is not None:
            ov_cat.append((k, ci))
    if ov_cat:
        ok_idx = np.array([k for k, _ in ov_cat])
        ov_ci = np.array([ci for _, ci in ov_cat])
        ov_xy = project(shapely.get_coordinates(places["geom"][ok_idx]))
        osm_xy = np.array(pts) if pts else np.zeros((0, 2))
        osm_ci = np.array(pcat)
        for ci in range(len(cats)):
            sel = np.where(ov_ci == ci)[0]
            if not len(sel):
                continue
            o = osm_xy[osm_ci == ci] if len(osm_ci) else np.zeros((0, 2))
            if len(o):
                d, _ = cKDTree(o).query(ov_xy[sel], distance_upper_bound=ac["dedupe_m"])
                sel = sel[~np.isfinite(d)]
            for j in sel:
                pts.append(ov_xy[j])
                pcat.append(ci)
                pname.append(places["name"][ok_idx[j]] or "")
                psrc.append(1)
    pts = np.array(pts) if pts else np.zeros((0, 2))
    pcat, psrc = np.array(pcat, dtype=int), np.array(psrc, dtype=int)
    node, _ = graph.snap(pts, wg["snap_max_m"])
    log(f"amenities: {n_osm} from OSM + {len(pts) - n_osm} from Overture; "
        f"by category {np.bincount(pcat, minlength=len(cats)).tolist()}")
    dist, near = {}, {}
    for ci, c in enumerate(cats):
        sel = np.where((pcat == ci) & (node >= 0))[0]
        if not len(sel):     # nothing mapped in this category: unknown, not "none within reach"
            log(f"amenities: WARNING no {c['id']} places found; the category is left out of daily needs")
            dist[c["id"]] = np.full(len(pnode), np.nan)
            near[c["id"]] = np.full(len(pnode), -1)
            continue
        limit = c["points"][-1][0] + 1
        d, sources = graph.nearest(node[sel], limit)
        node_poi = {}
        for j in sel:
            node_poi.setdefault(node[j], j)
        dd = d[pnode] + psnap
        dist[c["id"]] = np.where(np.isfinite(dd), dd, np.inf)
        near[c["id"]] = np.array([node_poi.get(s, -1) if s >= 0 else -1 for s in sources[pnode]])
    pois = {"x": pts[:, 0] if len(pts) else [], "y": pts[:, 1] if len(pts) else [], "cat": pcat, "name": pname,
            "src": psrc}
    stats = {"osm": int(n_osm), "overture_added": int(len(pts) - n_osm),
             "by_category": {c["id"]: int((pcat == ci).sum()) for ci, c in enumerate(cats)}}
    return {"dist": dist, "near": near, "pois": pois, "stats": stats}
