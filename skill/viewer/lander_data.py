"""Lander data for one or more cities, written next to each city's outputs:
  out/<city>/preview.webp       sharp light preview of the default overall score
  out/<city>/street_index.json  every named street (split into local stretches) with
                                position on the preview, score and plain-language traits
  out/<city>/tour.json          four real streets for the guided tour (best, touristy,
                                loud, car-dependent) and the preview size

Scores use the viewer's defaults (render_preview.scores): equal weights, Everyday
access as a must-have.

Usage: python -I lander_data.py <street_metrics.yaml> <out_dir> <city> [<city> ...] [--combined <dir>]
--combined also writes <dir>/streets_index.json, <dir>/tour.json and <dir>/<city>.webp for all cities together.
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import yaml
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))   # -I drops the script dir
import render_preview as rp  # noqa: E402

# trait bits, decoded by the lander (order = priority when picking two to show)
TRAITS = ["very_touristy", "loud", "need_car", "narrow_dark", "busy_visitors", "everything_on_foot",
          "leafy", "quiet", "good_transit", "open_views", "park_close"]
STRETCH_GAP_M = 300   # pieces of a same-named street further apart than this are separate stretches


def traits(sub, raw, need_all, tourism, nature_tiers, tcfg):
    t = {
        "very_touristy": tourism >= tcfg["very_touristy_per_km2"],
        "busy_visitors": tcfg["busy_per_km2"] <= tourism < tcfg["very_touristy_per_km2"],
        "loud": raw["noise_lden"] >= 66,
        "quiet": raw["noise_lden"] <= 52,
        "leafy": sub["green"] >= 68,
        "everything_on_foot": need_all,
        "need_car": sub["access"] <= 30,
        "good_transit": raw["transit_ai"] > 15,     # PTAL 4 or better, as on the card
        "narrow_dark": raw["theta"] >= 35,
        "open_views": sub["view"] >= 85,
        "park_close": nature_tiers >= 4,
    }
    return sum(1 << i for i, k in enumerate(TRAITS) if t[k])


def stretches(xy, gap):
    """Group points that chain together within gap metres."""
    n = len(xy)
    parent = np.arange(n)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    if n > 1:
        for a, b in cKDTree(xy).query_pairs(gap, output_type="ndarray"):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
    roots = np.array([find(a) for a in range(n)])
    return [np.where(roots == r)[0] for r in np.unique(roots)]


def city_data(cfg, src, lw):
    d = json.loads((src / "streets.json").read_text(encoding="utf-8"))
    p = d["pieces"]
    overall, subs, raw, need = rp.scores(p, cfg)
    reach = cfg["amenities"]["reach_m"]
    need_all = np.all(np.stack([np.nan_to_num(v, nan=1e9) <= reach for v in need.values()]), axis=0)
    ext = d["extent"]
    mids = np.array([rp.decode(g)[len(g) // 4] for g in p["geom"]])
    names = np.array(d["names"], dtype=object)[np.array(p["name"])]
    cls = np.array(d["classes"], dtype=object)[np.array(p["cls"])]
    by = {}
    for i, nm in enumerate(names):
        if nm:
            by.setdefault(nm, []).append(i)
    rows = []
    for nm, idx in by.items():
        idx = np.array(idx)
        for grp in stretches(mids[idx], STRETCH_GAP_M):
            g = idx[grp]
            ok = g[np.isfinite(overall[g])]
            if not len(ok):
                continue
            cen = mids[ok].mean(0)
            k = ok[np.argmin(((mids[ok] - cen) ** 2).sum(1))]          # a real point on the street
            med = lambda a: float(np.nanmedian(a[ok])) if np.isfinite(a[ok]).any() else np.nan
            sub = {s: med(subs[s]) for s in subs}
            rw = {m: med(raw[m]) for m in ("noise_lden", "transit_ai", "theta")}
            bits = traits(sub, {m: (v if np.isfinite(v) else 0) for m, v in rw.items()},
                          float(np.mean(need_all[ok])) >= 0.5, med(raw["tourism"]), med(raw["nature_tiers"]), cfg["tourism"])
            rows.append({"name": nm, "x": float(mids[k][0] / ext[2]), "y": float(1 - mids[k][1] / ext[3]),
                         "score": int(round(med(overall))), "n": int(len(ok)), "bits": bits, "sub": sub, "rw": rw,
                         "tour": med(raw["tourism"]), "cls": cls[k]})
    index = [[r["name"], round(r["x"] * 10000), round(r["y"] * 10000), r["score"], r["bits"]]
             for r in sorted(rows, key=lambda r: -r["n"])]

    # guided tour: four real, named streets that tell the city's story
    big = [r for r in rows if r["n"] >= 3 and 0.07 < r["x"] < 0.93 and 0.07 < r["y"] < 0.93] or rows
    has = lambda r, t: bool(r["bits"] & (1 << TRAITS.index(t)))
    pick = {}
    cand = [r for r in big if has(r, "everything_on_foot") and not has(r, "very_touristy") and not has(r, "loud")]
    top = [r for r in cand if r["score"] >= 82 and r["n"] >= 5]
    pick["best"] = max(top, key=lambda r: (r["n"], r["score"])) if top else max(cand or big, key=lambda r: (r["score"], r["n"]))
    pick["touristy"] = max(big, key=lambda r: (r["tour"], r["n"]))
    bridge = ("estakad", "most ", "bridge", "viaduct", "wiadukt", "tunel", "tunnel", "ponte", "brug", "міст", "largo")
    loud = [r for r in big if r["cls"] in ("primary", "secondary") and r["rw"]["noise_lden"] >= 67
            and not any(b in r["name"].lower() for b in bridge) and 0.08 < r["x"] < 0.92 and 0.08 < r["y"] < 0.92]
    pick["loud"] = max(loud, key=lambda r: r["n"]) if loud else max(big, key=lambda r: (r["rw"]["noise_lden"], r["n"]))
    car = [r for r in big if has(r, "need_car") and (has(r, "quiet") or has(r, "leafy"))]
    if car:
        pick["car"] = max(car, key=lambda r: (r["sub"]["green"] + r["sub"]["noise"], r["n"]))
    tour = {k: {"name": v["name"], "x": round(v["x"], 4), "y": round(v["y"], 4), "score": v["score"], "bits": v["bits"]}
            for k, v in pick.items()}
    write_api_points(cfg, d, src, mids, names, overall, subs, raw, need, need_all)
    info = render(rp, d, src, overall, src / "preview.webp", lw)
    tour["px"] = info[:2]
    ok = np.isfinite(overall)
    tr = raw["transit_ai"]
    tour["stats"] = {        # plain numbers for the city cards
        "pieces": int(ok.sum()), "median": int(round(float(np.nanmedian(overall)))),
        "share_70": round(float(np.mean(overall[ok] >= 70)), 3),
        "share_needs": round(float(np.mean(need_all[ok])), 3),
        "share_transit": round(float(np.mean(np.nan_to_num(tr[ok]) > 15)), 3),
        "share_quiet": round(float(np.mean(np.nan_to_num(raw["noise_lden"][ok], nan=99) <= 55)), 3),
        "share_car": round(float(np.mean(np.nan_to_num(subs["access"][ok], nan=100) <= 30)), 3),
    }
    (src / "street_index.json").write_text(json.dumps({"traits": TRAITS, "scale": 10000, "streets": index},
                                                      ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (src / "tour.json").write_text(json.dumps(tour, ensure_ascii=False), encoding="utf-8")
    return index, tour


def write_api_points(cfg, d, src, mids, names, overall, subs, raw, need, need_all):
    """One row per street piece, for street lookups by position on the website: position (WGS84), scores,
    traits, walking distances."""
    from pyproj import Transformer
    tr = Transformer.from_crs(d["crs"], "EPSG:4326", always_xy=True)
    lon, lat = tr.transform(mids[:, 0] + d["origin"][0], mids[:, 1] + d["origin"][1])
    f = lambda a: np.nan_to_num(a, nan=-1)
    bits = np.zeros(len(overall), int)
    conds = {
        "very_touristy": f(raw["tourism"]) >= cfg["tourism"]["very_touristy_per_km2"],
        "busy_visitors": (f(raw["tourism"]) >= cfg["tourism"]["busy_per_km2"]) & (f(raw["tourism"]) < cfg["tourism"]["very_touristy_per_km2"]),
        "loud": f(raw["noise_lden"]) >= 66, "quiet": (raw["noise_lden"] <= 52) & np.isfinite(raw["noise_lden"]),
        "leafy": f(subs["green"]) >= 68, "everything_on_foot": need_all,
        "need_car": (subs["access"] <= 30) & np.isfinite(subs["access"]), "good_transit": f(raw["transit_ai"]) > 15,
        "narrow_dark": f(raw["theta"]) >= 35, "open_views": f(subs["view"]) >= 85, "park_close": f(raw["nature_tiers"]) >= 4,
    }
    for i, t in enumerate(TRAITS):
        bits |= np.where(conds[t], 1 << i, 0)
    street_names = sorted({n for n in names if n})
    sid = {n: i for i, n in enumerate(street_names)}
    r = lambda a: np.where(np.isfinite(a), np.round(a), -1).astype(int)
    cats = [c["id"] for c in cfg["amenities"]["categories"]]
    cols = [np.round(np.asarray(lon) * 1e6).astype(int), np.round(np.asarray(lat) * 1e6).astype(int),
            np.array([sid.get(n, -1) if n else -1 for n in names]), r(overall)] + \
        [r(subs[k]) for k in ("view", "green", "nature", "noise", "crowds", "access")] + \
        [bits, r(raw["noise_lden"]), r(raw["transit_ai"] * 10)] + [r(np.minimum(need[c], 9999)) for c in cats]
    rows = np.column_stack(cols).tolist()
    fields = ["lon_e6", "lat_e6", "street", "overall", "view", "green", "nature", "noise", "crowds", "access",
              "bits", "noise_db", "transit_ai_x10"] + [f"walk_{c}_m" for c in cats]
    (src / "api_points.json").write_text(json.dumps({"fields": fields, "traits": TRAITS, "streets": street_names,
                                                     "rows": rows}, ensure_ascii=False, separators=(",", ":")),
                                         encoding="utf-8")


def main(metrics_path, out_dir, *args):
    args = list(args)
    combined = None
    if "--combined" in args:
        i = args.index("--combined")
        combined = Path(args[i + 1])
        del args[i:i + 2]
    cfg = yaml.safe_load(Path(metrics_path).read_text(encoding="utf-8"))
    rp.WIDTH, lw = 2400, 1.25
    index, tours = {}, {}
    for city in args:
        src = Path(out_dir) / city
        index[city], tours[city] = city_data(cfg, src, lw)
        print(f"{city}: {len(index[city])} street stretches, tour {[v['name'] for k, v in tours[city].items() if isinstance(v, dict) and 'name' in v]}")
    if combined:
        combined.mkdir(parents=True, exist_ok=True)
        for city in args:
            shutil.copy2(Path(out_dir) / city / "preview.webp", combined / f"{city}.webp")
        (combined / "streets_index.json").write_text(json.dumps({"traits": TRAITS, "scale": 10000, "cities": index},
                                                                ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        (combined / "tour.json").write_text(json.dumps(tours, ensure_ascii=False), encoding="utf-8")


def render(rp, d, src, overall, path, lw):
    from PIL import Image, ImageDraw
    ext = d["extent"]
    W = rp.WIDTH
    k = W / ext[2]
    H = int(round(ext[3] * k))
    SS = 2
    img = Image.new("RGB", (W * SS, H * SS), (255, 255, 255))
    bm = json.loads((src / "basemap.json").read_text())
    for ch in bm["chunks"]:
        im = Image.open(src / ch["file"])
        idx = np.array(im)
        rgba = np.zeros(idx.shape + (4,), np.uint8)
        for v, rgb in {1: (226, 239, 224), 2: (214, 230, 246), 3: (234, 235, 237)}.items():
            rgba[idx == v] = (*rgb, 255)
        tile = Image.fromarray(rgba, "RGBA").resize((max(1, int(ch["w"] * k * SS)), max(1, int(ch["h"] * k * SS))),
                                                    Image.BILINEAR)
        img.paste(tile, (int(ch["x"] * k * SS), int((ext[3] - ch["y"] - ch["h"]) * k * SS)), tile)
    dr = ImageDraw.Draw(img)
    to_px = lambda xy: [(x * k * SS, (ext[3] - y) * k * SS) for x, y in xy]
    for g in d["grey"]:
        dr.line(to_px(rp.decode(g)), fill=(208, 211, 207), width=SS)
    w = max(2, int(round(lw * SS)))
    for i in np.argsort(-np.nan_to_num(overall, nan=101)):
        s = overall[i]
        dr.line(to_px(rp.decode(d["pieces"]["geom"][i])), fill=(196, 198, 194) if not np.isfinite(s) else rp.color(s), width=w)
    img = img.resize((W, H), Image.LANCZOS)
    img.save(path, quality=82, method=6)
    return [W, H, path.stat().st_size]


if __name__ == "__main__":
    main(*sys.argv[1:])
