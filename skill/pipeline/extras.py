"""Extra scores per street piece: easy on foot, good for families, and street safety (traffic and lighting).

These are for ranking districts ("best areas for families") and for the data; they are not part of the
overall score and not drawn on the map. All weights and bands live in street_metrics.yaml under `extras`.
"Street safety" means traffic danger, crossings, lighting and people around. We have no crime data and do
not guess it.

Inputs: out/<city>/streets.json and layers.json (scores, distances, places) and the city's OpenStreetMap
extract (speed limits, lighting, junctions, crossings, street lamps, playgrounds). Lighting is used only where
OpenStreetMap actually records it; elsewhere it is "unknown" and left out of the score.

Writes out/<city>/extras.json. Run before districts.py, which adds district medians.
Usage: python -I extras.py <street_metrics.yaml> <out_dir> <raw_dir> <city> [<city> ...]
"""
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import osmium
import yaml
from pyproj import Transformer
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "viewer"))
import render_preview as rp  # noqa: E402  (shared scoring)

LIT = {"yes": 1, "24/7": 1, "automatic": 1, "limited": 1, "interval": 1, "sunset-sunrise": 1, "dusk-dawn": 1, "no": 0, "disused": 0}
SAMPLE_M = 20


def parse_speed(tags):
    """km/h from maxspeed-style tags, or None. Handles '50', '30 mph', 'walk', 'PL:zone30', 'DE:urban', 'zone:maxspeed=PL:30'."""
    for key in ("maxspeed", "zone:maxspeed", "maxspeed:type", "source:maxspeed"):
        v = tags.get(key)
        if not v:
            continue
        v = v.strip().lower()
        if v in ("walk", "living_street") or v.endswith(":living_street"):
            return 10.0 if v == "walk" else 20.0
        m = re.search(r"(\d+(?:\.\d+)?)\s*(mph)?", v)
        if m:
            kmh = float(m.group(1)) * (1.609 if m.group(2) else 1.0)
            if 3 <= kmh <= 140:
                return kmh
        if v.endswith(":urban"):
            return 50.0
        if v.endswith(":rural"):
            return 90.0
    return None


def read_osm(pbf, bbox_ll, ecfg):
    x0, y0, x1, y1 = bbox_ll
    inside = lambda lon, lat: x0 <= lon <= x1 and y0 <= lat <= y1
    walkable, roads, streets = set(ecfg["walkable_highways"]), set(ecfg["road_classes"]), set(ecfg["junction_highways"])
    deg, junctions, crossings, lamps, playgrounds, ways = {}, [], [], [], [], []
    for o in osmium.FileProcessor(str(pbf)).with_locations().with_filter(osmium.filter.KeyFilter("highway", "leisure")):
        t = o.tags
        if o.is_node():
            if not o.location.valid():
                continue
            lon, lat = o.location.lon, o.location.lat
            if not inside(lon, lat):
                continue
            hw = t.get("highway")
            if hw == "crossing":
                crossings.append((lon, lat))
            elif hw == "street_lamp":
                lamps.append((lon, lat))
            if t.get("leisure") == "playground":
                playgrounds.append((lon, lat))
            continue
        if not o.is_way():
            continue
        try:
            nodes = [(n.ref, n.lon, n.lat) for n in o.nodes]
        except osmium.InvalidLocationError:
            continue
        if not nodes or not any(inside(lon, lat) for _, lon, lat in nodes[:: max(1, len(nodes) // 4)] + nodes[-1:]):
            continue
        if t.get("leisure") == "playground":
            playgrounds.append((float(np.mean([n[1] for n in nodes])), float(np.mean([n[2] for n in nodes]))))
        hw = t.get("highway")
        if not hw:
            continue
        if hw in streets:
            last = len(nodes) - 1
            for k, (ref, lon, lat) in enumerate(nodes):
                w = 1 if k in (0, last) else 2
                before = deg.get(ref, 0)
                deg[ref] = before + w
                if before < 3 <= before + w:
                    junctions.append((lon, lat))
        if hw in walkable or hw in roads:
            ways.append((np.array([[lon, lat] for _, lon, lat in nodes]), parse_speed(t), LIT.get(t.get("lit", ""), -1)))
    return junctions, crossings, lamps, playgrounds, ways


def wmean(parts, weights):
    num = np.zeros_like(next(iter(parts.values())), dtype=float)
    den = np.zeros_like(num)
    for k, w in weights.items():
        v = parts[k]
        ok = np.isfinite(v)
        num[ok] += w * v[ok]
        den[ok] += w
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def city_extras(cfg, out_dir, raw_dir, city):
    src = Path(out_dir) / city
    ecfg = cfg["extras"]
    d = json.loads((src / "streets.json").read_text(encoding="utf-8"))
    layers = json.loads((src / "layers.json").read_text(encoding="utf-8")) if (src / "layers.json").exists() else None
    p = d["pieces"]
    overall, subs, raw, need = rp.scores(p, cfg)
    n = len(overall)
    mids = np.array([rp.decode(g)[len(g) // 4] for g in p["geom"]])
    cls = np.array(d["classes"], dtype=object)[np.array(p["cls"])]
    metric = {m["id"]: m for m in cfg["metrics"]}
    band = lambda pts, x: np.where(np.isfinite(x), rp.band(pts, np.nan_to_num(x, nan=0)), np.nan)
    cats = {c["id"]: c for c in cfg["amenities"]["categories"]}
    need_scores = np.stack([band(cats[c]["points"], need[c]) for c in need])
    with np.errstate(all="ignore"):
        daily = np.nanmean(need_scores, axis=0)
    school = band(cats["school"]["points"], need["school"])
    transit = band(metric["transit_ai"]["scoring"]["points"], raw["transit_ai"])
    major = band(metric["major_road_m"]["scoring"]["points"], raw["major_road_m"])

    # OpenStreetMap details, in the city's metric frame
    ox, oy = d["origin"]
    to_local = Transformer.from_crs("EPSG:4326", d["crs"], always_xy=True)
    to_ll = Transformer.from_crs(d["crs"], "EPSG:4326", always_xy=True)
    ext = d["extent"]
    lons, lats = to_ll.transform([ext[0] + ox - 1000, ext[2] + ox + 1000], [ext[1] + oy - 1000, ext[3] + oy + 1000])
    junctions, crossings, lamps, playgrounds, ways = read_osm(Path(raw_dir) / city / "osm" / "extract.osm.pbf",
                                                              (min(lons), min(lats), max(lons), max(lats)), ecfg)

    def local(pts):
        if not len(pts):
            return np.zeros((0, 2))
        a = np.asarray(pts, float)
        x, y = to_local.transform(a[:, 0], a[:, 1])
        return np.column_stack([np.asarray(x) - ox, np.asarray(y) - oy])

    sx, sy, sspeed, slit = [], [], [], []
    for ll, speed, lit in ways:
        xy = local(ll)
        seg = np.hypot(*np.diff(xy, axis=0).T) if len(xy) > 1 else np.zeros(0)
        cum = np.concatenate([[0], np.cumsum(seg)])
        at = np.arange(0, cum[-1] + 1e-6, SAMPLE_M) if cum[-1] > 0 else np.array([0.0])
        sx.append(np.interp(at, cum, xy[:, 0])); sy.append(np.interp(at, cum, xy[:, 1]))
        sspeed.append(np.full(len(at), np.nan if speed is None else speed)); slit.append(np.full(len(at), lit))
    S_xy = np.column_stack([np.concatenate(sx), np.concatenate(sy)]) if sx else np.zeros((0, 2))
    S_speed, S_lit = (np.concatenate(sspeed), np.concatenate(slit)) if sx else (np.zeros(0), np.zeros(0))

    def counts(points, r):
        if not len(points):
            return np.zeros(n)
        return cKDTree(points).query_ball_point(mids, r=r, return_length=True).astype(float)

    # speed: the nearest road with a speed limit tag, else the street's own type
    defaults = ecfg["default_speed_kmh"]
    speed = np.array([defaults.get(c, defaults["other"]) for c in cls], float)
    tagged = np.isfinite(S_speed)
    speed_tagged = np.zeros(n, bool)
    if tagged.any():
        dist, idx = cKDTree(S_xy[tagged]).query(mids, distance_upper_bound=ecfg["speed_match_m"])
        hitm = np.isfinite(dist)
        speed[hitm] = S_speed[tagged][idx[hitm]]
        speed_tagged = hitm
    # lighting: share of tagged road length that is lit, or a street lamp close by; unknown otherwise
    allc = counts(S_xy, ecfg["lit_radius_m"])
    tagc = counts(S_xy[S_lit >= 0], ecfg["lit_radius_m"])
    yesc = counts(S_xy[S_lit == 1], ecfg["lit_radius_m"])
    lampc = counts(local(lamps), ecfg["lamp_radius_m"])
    with np.errstate(invalid="ignore", divide="ignore"):
        lit = np.where(tagc / np.maximum(allc, 1) >= ecfg["lit_min_coverage"], 100 * yesc / np.maximum(tagc, 1), np.nan)
    lit = np.where(lampc > 0, np.fmax(lit, 100.0), lit)
    junc = counts(local(junctions), ecfg["junction_radius_m"]) / (math.pi * (ecfg["junction_radius_m"] / 1000) ** 2)
    cross = counts(local(crossings), ecfg["crossing_radius_m"])
    eyes_n = counts(np.column_stack([layers["pois"]["x"], layers["pois"]["y"]]) if layers else np.zeros((0, 2)), ecfg["eyes_radius_m"])
    play_pts = local(playgrounds)
    play_m = cKDTree(play_pts).query(mids)[0] * ecfg["detour"] if len(play_pts) else np.full(n, np.inf)

    B = ecfg["bands"]
    calm = band(B["speed_kmh"], speed)
    traffic = wmean({"calm": calm, "major": major, "cross": band(B["crossings"], cross)}, {"calm": 0.5, "major": 0.3, "cross": 0.2})
    parts = {
        "daily_needs": daily, "junctions": band(B["junctions_km2"], junc), "calm_street": calm, "transit": transit,
        "school": school, "playground": band(B["playground_m"], np.where(np.isfinite(play_m), play_m, 1e6)),
        "nature": subs["nature"], "noise": subs["noise"], "crowds": subs["crowds"], "traffic": traffic,
        "lighting": lit, "eyes": band(B["eyes"], eyes_n),
    }
    scores = {k: wmean(parts, w) for k, w in ecfg["scores"].items()}
    for k, caps in (ecfg.get("caps") or {}).items():          # deal-breakers, as in the overall score
        for part, slack in caps.items():
            cap = parts[part] + slack
            scores[k] = np.where(np.isfinite(cap), np.fmin(scores[k], cap), scores[k])
    scored = np.isfinite(overall)
    for v in scores.values():
        v[~scored] = np.nan
    fmt = lambda a: [int(round(x)) if np.isfinite(x) else -1 for x in a]
    out = {
        "fields": list(scores), "labels": {"walk": "Easy on foot", "family": "Good for families", "safety": "Safe streets (traffic and lighting)"},
        "note": "Not part of the overall score. 'safety' is street safety: traffic, crossings, lighting and people around; not crime.",
        "scores": [x for k in scores for x in fmt(scores[k])],   # field-major: all walk, then all family, then all safety
        "parts": {"speed_kmh": fmt(speed), "lit_pct": fmt(lit), "playground_m": fmt(np.minimum(play_m, 9999)), "junctions_km2": fmt(junc)},
        "coverage": {"speed_tagged": round(float(speed_tagged[scored].mean()), 2), "lit_known": round(float(np.isfinite(lit[scored]).mean()), 2)},
    }
    (src / "extras.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    med = {k: int(np.nanmedian(v[scored])) for k, v in scores.items()}
    print(f"{city}: medians {med}; speed limit tagged near {out['coverage']['speed_tagged']:.0%} of pieces, "
          f"lighting known for {out['coverage']['lit_known']:.0%}; {len(playgrounds)} playgrounds, {len(junctions)} junctions")


def main(metrics_path, out_dir, raw_dir, *cities):
    cfg = yaml.safe_load(Path(metrics_path).read_text(encoding="utf-8"))
    for city in cities:
        city_extras(cfg, out_dir, raw_dir, city)


if __name__ == "__main__":
    if len(sys.argv) < 5:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
