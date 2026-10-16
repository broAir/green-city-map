"""Districts and neighbourhoods: outlines, names, and a plain summary of each from the street scores.

Works for any city from its OpenStreetMap extract. Cities subdivide themselves differently (Kraków has
18 dzielnice, Amsterdam stadsdelen and wijken, Lisbon freguesias), so instead of a fixed admin level
we look at every candidate level and pick:
  - "districts": the coarsest level that splits the city into 4-45 parts and covers most of it;
  - "neighbourhoods": a finer level with more parts, if the city has one.
Candidates are administrative boundaries (admin_level 7-11) and place polygons (suburb, quarter,
neighbourhood...). Place points (suburb, quarter, neighbourhood) give extra name labels.

Each district gets the same summary the street card uses: median score, shares of streets that are
quiet, leafy, touristy, loud or car-dependent, typical walking minutes to daily needs, counts of shops
and stops, its main problem, and its best streets.

Writes out/<city>/districts.json (local metres, same frame as streets.json).
Usage: python -I districts.py <street_metrics.yaml> <out_dir> <raw_dir> <city> [<city> ...]
"""
import json
import sys
from pathlib import Path

import numpy as np
import osmium
import shapely
import yaml
from pyproj import Transformer
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, polylabel, unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "viewer"))
import render_preview as rp  # noqa: E402  (shared scoring)

PLACE_AREAS = ("borough", "suburb", "city_district", "quarter", "neighbourhood")
PLACE_LABELS = ("suburb", "quarter", "neighbourhood", "borough", "city_district")
SIMPLIFY_M = 6


def decode(arr):
    return np.cumsum(np.array(arr, float).reshape(-1, 2), axis=0)


def city_shape(d):
    poly = None
    for ring in d["boundary"]:
        xy = decode(ring)
        if len(xy) < 4:
            continue
        p = Polygon(xy).buffer(0)
        poly = p if poly is None else poly.symmetric_difference(p)   # even-odd, like the map draws it
    return poly


LANG = {"UA": "uk", "PL": "pl", "PT": "pt", "BR": "pt", "NL": "nl", "BE": "nl", "SI": "sl", "DE": "de", "AT": "de", "CH": "de",
        "FR": "fr", "ES": "es", "MX": "es", "AR": "es", "CO": "es", "CL": "es", "PE": "es", "IT": "it", "CZ": "cs", "SK": "sk",
        "HU": "hu", "RO": "ro", "BG": "bg", "GR": "el", "TR": "tr", "RU": "ru", "LT": "lt", "LV": "lv", "EE": "et", "FI": "fi",
        "SE": "sv", "NO": "no", "DK": "da", "HR": "hr", "RS": "sr", "JP": "ja", "KR": "ko", "CN": "zh", "GB": "en", "US": "en",
        "IE": "en", "AU": "en", "CA": "en", "NZ": "en", "IN": "en"}


def best_name(tags, lang=None):
    """The name tag, unless it's a lone generic word ('район') and a language tag spells it out ('Центральний Район').
    The country's own language wins over others."""
    name = tags.get("name")
    if name and " " not in name.strip():
        spelled = [(tg.k[5:], tg.v) for tg in tags
                   if tg.k.startswith("name:") and len(tg.k) == 7 and name.lower() in tg.v.lower() and len(tg.v.split()) > 1]
        if spelled:
            return next((v for k, v in spelled if k == lang), spelled[0][1])
    return name


def group_of(tags):
    if tags.get("boundary") == "administrative" and tags.get("admin_level", "").isdigit():
        lv = int(tags["admin_level"])
        return f"admin {lv}" if 7 <= lv <= 11 else None
    return f"place {tags['place']}" if tags.get("place") in PLACE_AREAS else None


def read_osm(pbf, bbox_ll, lang=None):
    """Administrative and place polygons, and place points, inside the city's lon/lat box. Relations
    whose outline can't be closed (extracts are often cut through a district at the edge) come back
    as 'broken' with the boundary lines that are present, for repair()."""
    x0, y0, x1, y1 = bbox_ll
    tf = osmium.filter.TagFilter(("boundary", "administrative"), *[("place", v) for v in PLACE_AREAS])
    wkb = osmium.geom.WKBFactory()
    rels = {}
    for o in osmium.FileProcessor(str(pbf), osmium.osm.RELATION):
        g = group_of(o.tags)
        if g and o.tags.get("name"):
            rels[o.id] = {"group": g, "name": best_name(o.tags, lang), "name_en": o.tags.get("name:en"),
                          "ways": [m.ref for m in o.members if m.type == "w" and m.role in ("outer", "")]}
    wanted = {w for r in rels.values() for w in r["ways"]}
    way_ll = {}
    for o in osmium.FileProcessor(str(pbf)).with_locations().with_filter(osmium.filter.IdFilter(wanted)):
        if o.is_way() and o.id in wanted:
            try:
                way_ll[o.id] = [(n.lon, n.lat) for n in o.nodes]
            except osmium.InvalidLocationError:
                continue
    areas, points, built = [], [], set()
    for o in osmium.FileProcessor(str(pbf)).with_areas(tf).with_filter(osmium.filter.KeyFilter("boundary", "place")):
        tags = o.tags
        name = best_name(tags, lang)
        if not name:
            continue
        if o.is_area():
            group = group_of(tags)
            if not group:
                continue
            if not o.from_way():
                built.add(o.orig_id())
            try:
                g = shapely.from_wkb(bytes.fromhex(wkb.create_multipolygon(o)))
            except Exception:          # broken multipolygons happen in OSM; skip them
                continue
            bx0, by0, bx1, by1 = g.bounds
            if bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1:
                continue
            areas.append({"group": group, "name": name, "name_en": tags.get("name:en"), "geom": g})
        elif o.is_node() and tags.get("place") in PLACE_LABELS:
            lon, lat = o.location.lon, o.location.lat
            if x0 <= lon <= x1 and y0 <= lat <= y1:
                points.append({"kind": tags["place"], "name": name, "name_en": tags.get("name:en"), "lon": lon, "lat": lat})
    broken = []
    for rid, r in rels.items():
        if rid in built:
            continue
        lines = [LineString(way_ll[w]) for w in r["ways"] if w in way_ll and len(way_ll[w]) >= 2]
        if not lines:
            continue
        ml = shapely.MultiLineString(lines)
        bx0, by0, bx1, by1 = ml.bounds
        if bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1:
            continue
        broken.append({"group": r["group"], "name": r["name"], "name_en": r["name_en"], "lines": ml,
                       "missing": len(r["ways"]) - len(lines)})
    return areas, points, broken


def repair(items, broken, city, group):
    """Close broken districts with what's left of the city: split the uncovered part along their
    surviving boundary lines and give each piece to the district whose lines run along most of it."""
    if not broken:
        return []
    covered = unary_union([it["geom"] for it in items]) if items else Polygon()
    rest = city.difference(covered).buffer(0)
    if rest.area < 500_000:
        return []
    faces = [f for f in polygonize(unary_union([rest.boundary, *[b["lines"] for b in broken]]))
             if f.area > 50_000 and rest.contains(f.representative_point())]
    got = {}
    for f in faces:
        edge = f.boundary
        along = sorted(((edge.intersection(b["lines"].buffer(3)).length, b) for b in broken), key=lambda t: -t[0])
        claim = [b for L, b in along if L > 0.15 * edge.length]
        if not claim:
            continue
        # when the line between two broken districts is missing too, they can't be told apart: name both
        name = " and ".join(b["name"] for b in claim[:2])
        got.setdefault(name, (claim[0], []))[1].append(f)
    return [{"group": group, "name": n, "name_en": None if " and " in n else b["name_en"],
             "geom": unary_union(fs).intersection(city), "repaired": True} for n, (b, fs) in got.items()]


def choose_levels(areas, broken, city):
    """Group candidates by level, clip to the city, repair broken ones, and pick a districts level and a finer one."""
    A = city.area
    groups = {}
    for a in areas:
        inside = a["geom"].intersection(city)
        if inside.is_empty or inside.area < 0.6 * a["geom"].area or inside.area > 0.9 * A or inside.area < 50_000:
            continue
        groups.setdefault(a["group"], []).append({**a, "geom": inside})
    for g in {b["group"] for b in broken}:
        if g in groups:
            groups[g] += repair(groups[g], [b for b in broken if b["group"] == g and b["name"] not in {i["name"] for i in groups[g]}], city, g)
    stats = []
    for g, items in groups.items():
        # the same name twice at one level (split polygons) is one district
        merged = {}
        for it in items:
            merged.setdefault(it["name"], []).append(it)
        items = [{**v[0], "geom": unary_union([x["geom"] for x in v])} for v in merged.values()]
        cover = unary_union([it["geom"] for it in items]).area / A
        stats.append({"group": g, "items": items, "n": len(items), "cover": cover})
    rank_kind = lambda s: 0 if s["group"].startswith("admin") else 1
    ok = [s for s in stats if 4 <= s["n"] <= 45 and s["cover"] >= 0.5]
    districts = min(ok, key=lambda s: (-round(s["cover"], 1), rank_kind(s), abs(s["n"] - 15))) if ok else None
    finer = [s for s in stats if s is not districts and s["cover"] >= 0.4 and s["n"] <= 400
             and (districts is None or s["n"] > districts["n"] * 1.5)]
    hoods = min(finer, key=lambda s: (-round(s["cover"], 1), rank_kind(s), s["n"])) if finer else None
    return districts, hoods, stats


def extra_medians(extras, inside):
    """District medians of the extra scores (extras.py): easy on foot, good for families, street safety."""
    if not extras:
        return None
    n = len(inside)
    S = np.array(extras["scores"], float).reshape(len(extras["fields"]), n)
    S[S < 0] = np.nan
    out = {k: (int(round(float(np.nanmedian(S[j][inside])))) if np.isfinite(S[j][inside]).any() else None) for j, k in enumerate(extras["fields"])}
    lit = np.array(extras["parts"]["lit_pct"], float)[inside]
    out["lit_known"] = round(float((lit >= 0).mean()), 2)
    return out


def summarise(cfg, d, layers, details, poly_items, extras=None):
    p = d["pieces"]
    overall, subs, raw, need = rp.scores(p, cfg)
    mids = np.array([decode(g)[len(g) // 4] for g in p["geom"]])
    names = np.array(d["names"], dtype=object)[np.array(p["name"])]
    reach, speed = cfg["amenities"]["reach_m"], cfg["walk_graph"]["walk_speed_m_per_min"]
    tcfg = cfg["tourism"]
    need_all = np.all(np.stack([np.nan_to_num(v, nan=1e9) <= reach for v in need.values()]), axis=0)
    stop_d = np.array(details.get("stop_s_d", [-9999] * len(overall)), float) if details else np.full(len(overall), np.nan)
    stop_d[stop_d == -9999] = np.nan
    stop_d *= 10
    traits = {
        "touristy": raw["tourism"] >= tcfg["very_touristy_per_km2"], "busy": raw["tourism"] >= tcfg["busy_per_km2"],
        "loud": raw["noise_lden"] >= 66, "quiet": raw["noise_lden"] <= 52, "leafy": subs["green"] >= 68,
        "on_foot": need_all, "transit": raw["transit_ai"] > 15, "car": subs["access"] <= 30, "dark": raw["theta"] >= 35,
    }
    pois = layers.get("pois") if layers else None
    cats = [c["id"] for c in layers["categories"]] if layers else []
    stops = layers.get("stops") if layers else None
    out = []
    for it in poly_items:
        g = it["geom"]
        inside = shapely.contains_xy(g, mids[:, 0], mids[:, 1]) & np.isfinite(overall)
        n = int(inside.sum())
        if n < 15:
            continue
        sc = overall[inside]
        med = lambda a: float(np.nanmedian(a[inside])) if np.isfinite(a[inside]).any() else None
        sub = {k: med(v) for k, v in subs.items()}
        problems = {"crowds": sub["crowds"], "noise": sub["noise"], "access": sub["access"],
                    "bare": min(x for x in (sub["view"], sub["green"], sub["nature"]) if x is not None)}
        score = float(np.median(sc))
        main = min((k for k in problems if problems[k] is not None), key=lambda k: problems[k]) if score < 65 else None
        walk = {c: (round(med(need[c]) / speed, 1) if med(need[c]) is not None else None) for c in need}
        walk["stop"] = round(med(stop_d) / speed, 1) if med(stop_d) is not None else None
        # named stretches: median score per street name inside the district
        by = {}
        for i in np.where(inside)[0]:
            if names[i]:
                by.setdefault(names[i], []).append(overall[i])
        streets = sorted(((nm, float(np.median(v)), len(v)) for nm, v in by.items() if len(v) >= 3), key=lambda t: -t[1])
        counts = {}
        if pois:
            pin = shapely.contains_xy(g, np.array(pois["x"], float), np.array(pois["y"], float))
            cat = np.array(pois["cat"])
            counts = {c: int((pin & (cat == k)).sum()) for k, c in enumerate(cats)}
        if stops:
            counts["stops"] = int(shapely.contains_xy(g, np.array(stops["x"], float), np.array(stops["y"], float)).sum())
        part = max(g.geoms, key=lambda q: q.area) if g.geom_type == "MultiPolygon" else g
        lab = polylabel(part, tolerance=10)
        simple = g.simplify(SIMPLIFY_M, preserve_topology=True)
        rings = []
        for q in (simple.geoms if simple.geom_type == "MultiPolygon" else [simple]):
            for r in [q.exterior, *q.interiors]:
                xy = np.round(np.array(r.coords)).astype(int)
                rings.append(np.concatenate([xy[:1], np.diff(xy, axis=0)]).ravel().tolist())
        out.append({
            "name": it["name"], "name_en": it.get("name_en"), "repaired": it.get("repaired", False), "rings": rings, "label": [round(lab.x), round(lab.y)],
            "area_km2": round(g.area / 1e6, 2), "pieces": n, "score": round(score),
            "share_good": round(float(np.mean(sc >= 65)), 2), "share_tough": round(float(np.mean(sc < 35)), 2),
            "sub": {k: (round(v) if v is not None else None) for k, v in sub.items()}, "main_problem": main,
            "traits": {k: round(float(np.mean(v[inside])), 2) for k, v in traits.items()},
            "walk_min": walk, "counts": counts,
            "extra": extra_medians(extras, inside),
            "best": [[s[0], round(s[1])] for s in streets[:3]], "worst": [[s[0], round(s[1])] for s in streets[-2:][::-1]] if len(streets) > 4 else [],
        })
    return out


def main(metrics_path, out_dir, raw_dir, *cities):
    cfg = yaml.safe_load(Path(metrics_path).read_text(encoding="utf-8"))
    for city in cities:
        src = Path(out_dir) / city
        d = json.loads((src / "streets.json").read_text(encoding="utf-8"))
        layers = json.loads((src / "layers.json").read_text(encoding="utf-8")) if (src / "layers.json").exists() else None
        details = json.loads((src / "details.json").read_text(encoding="utf-8")) if (src / "details.json").exists() else None
        extras = json.loads((src / "extras.json").read_text(encoding="utf-8")) if (src / "extras.json").exists() else None
        city_poly = city_shape(d)
        ox, oy = d["origin"]
        to_local = Transformer.from_crs("EPSG:4326", d["crs"], always_xy=True)
        to_ll = Transformer.from_crs(d["crs"], "EPSG:4326", always_xy=True)
        x0, y0, x1, y1 = city_poly.bounds
        lons, lats = to_ll.transform([x0 + ox, x1 + ox, x0 + ox, x1 + ox], [y0 + oy, y0 + oy, y1 + oy, y1 + oy])
        areas, points, broken = read_osm(Path(raw_dir) / city / "osm" / "extract.osm.pbf", (min(lons), min(lats), max(lons), max(lats)),
                                         LANG.get(d.get("country", "").upper()))
        def loc(g):
            def f(c):
                x, y = to_local.transform(c[:, 0], c[:, 1])
                return np.column_stack([np.asarray(x) - ox, np.asarray(y) - oy])
            return shapely.transform(g, f)
        for a in areas:
            a["geom"] = loc(a["geom"]).buffer(0)
        for b in broken:
            b["lines"] = loc(b["lines"])
        dist, hoods, stats = choose_levels(areas, broken, city_poly)
        levels = []
        for key, s in (("districts", dist), ("neighbourhoods", hoods)):
            if s:
                items = summarise(cfg, d, layers, details, s["items"], extras)
                levels.append({"id": key, "source": s["group"], "items": items})
        labels = []
        for pt in points:
            x, y = to_local.transform(pt["lon"], pt["lat"])
            x, y = x - ox, y - oy
            if city_poly.contains(shapely.Point(x, y)):
                labels.append({"name": pt["name"], "name_en": pt["name_en"], "kind": pt["kind"], "x": round(x), "y": round(y)})
        out = {"city": city, "levels": levels, "labels": labels,
               "candidates": [{"group": s["group"], "n": s["n"], "cover": round(s["cover"], 2)} for s in sorted(stats, key=lambda s: s["group"])]}
        (src / "districts.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        desc = ", ".join(f"{lv['id']}: {len(lv['items'])} from {lv['source']}" for lv in levels) or "no subdivisions found"
        print(f"{city}: {desc}; {len(labels)} place labels; candidates {out['candidates']}")


if __name__ == "__main__":
    if len(sys.argv) < 5:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
