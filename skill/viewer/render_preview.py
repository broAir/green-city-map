"""Render a light preview image of a city's default overall score (equal weights,
Everyday access as a must-have), plus a few headline numbers, for the lander
and for city thumbnails.

Scoring mirrors the viewer (review.template.html): band, percentile and
category scores per metric, sub-index means, weighted mean, must-have cap.

Usage: python -I render_preview.py <street_metrics.yaml> <out_dir> <dest_dir> <city> [<city> ...]
Writes <dest_dir>/<city>.webp and prints one JSON line of headline numbers per city.
"""
import json
import sys
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw

SCALE = {"W": 10, "theta": 1, "hw": 100, "green_across": 100, "canopy_300": 1, "sealed_300": 1, "ndvi_500": 100,
         "ndvi_corridor": 100, "nature_tiers": 1, "noise_lden": 1, "major_road_m": 1, "tourism": 1, "transit_ai": 10}
STOPS = [(0, (140, 16, 39)), (25, (217, 58, 43)), (45, (242, 140, 53)), (58, (245, 200, 66)), (72, (108, 195, 193)), (86, (58, 138, 211)), (100, (29, 76, 155))]   # no grey: buildings are grey
BASE = {1: (233, 242, 230), 2: (222, 234, 246), 3: (236, 237, 239)}   # green, water, buildings on white
WIDTH = 1400
SS = 2   # supersampling


def band(points, x):
    xs = np.array([p[0] for p in points], float)
    ys = np.array([p[1] for p in points], float)
    return np.interp(x, xs, ys)


def pct(v, higher_better):
    """Within-city percentile; tied values share their average rank (many streets have zero tourism)."""
    from scipy.stats import rankdata
    out = np.full(len(v), np.nan)
    ok = np.isfinite(v)
    r = rankdata(v[ok], method="average") - 1
    p = 100.0 * r / max(ok.sum() - 1, 1)
    out[ok] = p if higher_better else 100 - p
    return out


def scores(p, cfg):
    raw = {}
    for k, s in SCALE.items():
        a = np.array(p[k], float)
        a[a == -9999] = np.nan
        raw[k] = a / s
    cats = cfg["amenities"]["categories"]
    need = {}
    for c in cats:
        d = np.array(p["need_" + c["id"]], float)
        d[d == -9999] = np.nan
        need[c["id"]] = d * 10
    m_scores = {}
    for m in cfg["metrics"]:
        sc = m["scoring"]
        if sc["type"] == "percentile":
            m_scores[m["id"]] = pct(raw[m["id"]], sc["higher_is_better"])
        elif sc["type"] == "categories":
            m_scores[m["id"]] = np.nanmean(np.stack([band(c["points"], need[c["id"]]) for c in cats]), axis=0)
        else:
            x = raw[m["id"]]
            s = band(sc["points"], np.nan_to_num(x, nan=0))
            s[~np.isfinite(x)] = np.nan
            if m["id"] == "W":
                s[np.array(p["W"]) == -1] = sc["open_value_score"]
            m_scores[m["id"]] = s
    subs = {}
    for si in cfg["sub_indices"]:
        arr = np.stack([m_scores[m["id"]] for m in cfg["metrics"] if m["sub_index"] == si["id"]])
        subs[si["id"]] = np.nanmean(arr, axis=0)
    w = effective_weights(cfg["sub_indices"], {si["id"]: si["default_weight"] for si in cfg["sub_indices"]})
    S = np.stack([subs[si["id"]] for si in cfg["sub_indices"]])
    ok = np.isfinite(S) & (w[:, None] > 0)
    den = (ok * w[:, None]).sum(0)
    with np.errstate(all="ignore"):
        overall = np.where(den > 0, np.nansum(np.where(ok, S * w[:, None], 0), 0) / den, np.nan)
    for si in cfg["sub_indices"]:
        if si.get("must_have"):
            cap = subs[si["id"]] + si.get("slack", cfg["must_have_slack"])
            overall = np.where(np.isfinite(cap), np.minimum(overall, cap), overall)
    return overall, subs, raw, need


def effective_weights(sub_indices, weights):
    """Normalised weights with each max_share cap enforced; the excess goes to the uncapped
    groups in proportion, repeated until nothing is over its cap (same rule as the viewer)."""
    w = np.array([max(0.0, float(weights.get(si["id"], 0))) for si in sub_indices])
    if w.sum() <= 0:
        return w
    w = w / w.sum()
    cap = np.array([si.get("max_share") or 1.0 for si in sub_indices])
    for _ in range(len(w)):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = (w[over] - cap[over]).sum()
        w[over] = cap[over]
        free = (~over) & (w > 0) & (w < cap)
        if not free.any():
            break
        w[free] += excess * w[free] / w[free].sum()
    return w


def color(s):
    s = float(np.clip(s, 0, 100))
    k = next((j for j in range(1, len(STOPS)) if s <= STOPS[j][0]), len(STOPS) - 1)
    (x0, a), (x1, b) = STOPS[k - 1], STOPS[k]
    f = (s - x0) / (x1 - x0)
    return tuple(int(round(a[i] + (b[i] - a[i]) * f)) for i in range(3))


def decode(arr):
    xy = np.cumsum(np.array(arr, float).reshape(-1, 2), axis=0)
    return xy


def main(metrics_path, out_dir, dest, *cities):
    cfg = yaml.safe_load(Path(metrics_path).read_text())
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for city in cities:
        src = Path(out_dir) / city
        d = json.loads((src / "streets.json").read_text())
        p = d["pieces"]
        overall, subs, raw, need = scores(p, cfg)
        ext = d["extent"]
        k = WIDTH / ext[2]
        W, H = WIDTH, int(round(ext[3] * k))
        img = Image.new("RGB", (W * SS, H * SS), (255, 255, 255))
        # faint basemap: palette chunks recoloured for a white page
        bm = json.loads((src / "basemap.json").read_text())
        for ch in bm["chunks"]:
            im = Image.open(src / ch["file"])
            idx = np.array(im)
            rgba = np.zeros(idx.shape + (4,), np.uint8)
            for v, rgb in BASE.items():
                rgba[idx == v] = (*rgb, 255)
            tile = Image.fromarray(rgba, "RGBA").resize((max(1, int(ch["w"] * k * SS)), max(1, int(ch["h"] * k * SS))),
                                                        Image.BILINEAR)
            img.paste(tile, (int(ch["x"] * k * SS), int((ext[3] - ch["y"] - ch["h"]) * k * SS)), tile)
        dr = ImageDraw.Draw(img)
        to_px = lambda xy: [(x * k * SS, (ext[3] - y) * k * SS) for x, y in xy]
        for g in d["grey"]:
            dr.line(to_px(decode(g)), fill=(205, 208, 204), width=max(1, SS))
        order = np.argsort(-np.nan_to_num(overall, nan=101))   # good first, bad drawn on top
        lw = int(round(1.3 * SS))
        for i in order:
            s = overall[i]
            dr.line(to_px(decode(p["geom"][i])), fill=(190, 192, 188) if not np.isfinite(s) else color(s), width=lw)
        img = img.resize((W, H), Image.LANCZOS)
        img.save(dest / f"{city}.webp", quality=84, method=6)
        ai = raw["transit_ai"]
        reach = cfg["amenities"]["reach_m"]
        all_needs = np.all(np.stack([np.nan_to_num(v, nan=1e9) <= reach for v in need.values()]), axis=0)
        hs = d["stats"]["buildings"]["height_source_share"]
        print(json.dumps({
            "city": city, "name": d["name"], "country": d["country"],
            "streets_scored": int(len(overall)),
            "share_good": round(float(np.mean(overall[np.isfinite(overall)] >= 70)), 3),
            "median_overall": round(float(np.nanmedian(overall))),
            "share_all_needs_10min": round(float(all_needs.mean()), 3),
            "share_ptal_4plus": round(float(np.mean(np.nan_to_num(ai) > 15)), 3),
            "median_noise_db": round(float(np.nanmedian(raw["noise_lden"]))),
            "real_heights": round(float(hs.get("height", 0) + hs.get("num_floors", 0)), 2),
            "image": f"{city}.webp", "px": [W, H], "bytes": (dest / f"{city}.webp").stat().st_size,
        }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
