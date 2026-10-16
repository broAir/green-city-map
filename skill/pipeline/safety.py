"""Safety: one score per district from every source we can reach, each kept visible with its own note.

Sources (all optional; a missing one is left out and the others re-weighted):
  street    our street-safety estimate (extras.py): traffic, crossings, lighting, people around
  recorded  police-recorded crime per district where a country publishes it openly
            (Netherlands: politie via CBS per buurt; United Kingdom: data.police.uk)
  hoodmaps  the Hoodmaps crowd map: its "crime" areas and its top voted tags about danger or safety.
            Only vote counts per district are kept; the tag text itself is never copied into the outputs.
  reddit    posts on the city's subreddit that name a district (needs REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET)
  city      whole-city context: Eurostat police-recorded offences for the NUTS 3 region, and the Numbeo
            safety index (needs NUMBEO_API_KEY)
Crowd sources count in proportion to their evidence (votes or distinct authors). Weights, word lists and the
(i) notes shown on the site are in street_metrics.yaml under `safety`.

Downloads are cached in data/raw/<city>/safety/ (shared files in data/raw/_shared/); --refresh downloads again.
Adds `safety` to every district in out/<city>/districts.json and writes out/<city>/safety.json.
Run after districts.py.
Usage: python -I safety.py <street_metrics.yaml> <out_dir> <raw_dir> <city> [<city> ...] [--refresh]
"""
import json
import math
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
import requests
import shapely
import yaml
from pyproj import Transformer
from shapely.geometry import Point, Polygon, shape

UA = "GreenCityMap/0.2 (open-data livability map)"
REFRESH = False


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")


def get_json(url, cache=None, params=None, headers=None, max_age_days=7):
    if cache and cache.exists() and not REFRESH and time.time() - cache.stat().st_mtime < max_age_days * 86400:
        return json.loads(cache.read_text(encoding="utf-8"))
    r = requests.get(url, params=params, headers={"User-Agent": UA, **(headers or {})}, timeout=90)
    r.raise_for_status()
    data = r.json()
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def band(points, x):
    xs, ys = zip(*points)
    return float(np.interp(x, xs, ys))


# ------------------------------------------------------------------ the city and its districts
class City:
    def __init__(self, cfg, out_dir, raw_dir, city):
        self.cfg, self.id = cfg, city
        self.src = Path(out_dir) / city
        self.raw = Path(raw_dir) / city / "safety"
        self.shared = Path(raw_dir) / "_shared"
        s = json.loads((self.src / "streets.json").read_text(encoding="utf-8"))
        self.name, self.country = s["name"], s.get("country", "").upper()
        self.ox, self.oy = s["origin"]
        self.to_local = Transformer.from_crs("EPSG:4326", s["crs"], always_xy=True)
        self.to_ll = Transformer.from_crs(s["crs"], "EPSG:4326", always_xy=True)
        e = s["extent"]
        self.centre = self.ll((e[0] + e[2]) / 2, (e[1] + e[3]) / 2)
        self.bbox_ll = (*self.ll(e[0], e[1]), *self.ll(e[2], e[3]))
        self.dist = json.loads((self.src / "districts.json").read_text(encoding="utf-8"))
        lv = next((l for l in self.dist["levels"] if l["id"] == "districts"), None)
        self.items = lv["items"] if lv else []
        self.polys = [self.polygon(it["rings"]) for it in self.items]

    def ll(self, x, y):
        lon, lat = self.to_ll.transform(x + self.ox, y + self.oy)
        return float(lon), float(lat)

    def local(self, lon, lat):
        x, y = self.to_local.transform(lon, lat)
        return float(x) - self.ox, float(y) - self.oy

    @staticmethod
    def polygon(rings):
        poly = None
        for r in rings:
            xy = np.cumsum(np.array(r, float).reshape(-1, 2), axis=0)
            if len(xy) < 4:
                continue
            p = Polygon(xy).buffer(0)
            poly = p if poly is None else poly.symmetric_difference(p)
        return poly

    def district_of(self, lon, lat):
        x, y = self.local(lon, lat)
        pt = Point(x, y)
        for k, p in enumerate(self.polys):
            if p is not None and p.contains(pt):
                return k
        return None


def km(a, b):
    (lon1, lat1), (lon2, lat2) = a, b
    return 6371 * math.hypot(math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2)), math.radians(lat2 - lat1))


# ------------------------------------------------------------------ crowd text: danger or safe?
class Words:
    def __init__(self, crowd):
        pat = lambda ws: re.compile("|".join(r"(?<!\w)" + re.escape(fold(w)) for w in ws))
        self.danger, self.safe = pat(crowd["danger"]), pat(crowd["safe"])

    def classify(self, text):
        t = fold(text)
        if self.danger.search(t):
            return -1
        if self.safe.search(t):
            return 1
        return 0


def crowd_value(pos, neg, flags, cc, neutral):
    """Neutral, moved by the balance of safe vs danger evidence; confidence grows with the evidence."""
    n = pos + neg
    value = neutral + cc["swing"] * (pos - neg) / (n + cc["prior"]) - cc["crime_flag_penalty"] * flags
    conf = min(1.0, (n + 10 * flags) / (n + 10 * flags + cc["prior"])) if n + flags else 0.0
    return max(0.0, min(100.0, value)), conf


# ------------------------------------------------------------------ sources
def src_hoodmaps(C, words):
    cc, neutral = C.cfg["safety"]["crowd"], C.cfg["safety"]["neutral"]
    index = get_json("https://hoodmaps.com/api", C.shared / "hoodmaps_index.json")
    names = {fold(C.name), fold(C.id)}
    exact = [e for e in index["cities"] if fold(e["city"]) in names or e["slug"] in names]
    near = [e for e in index["cities"] if any(fold(e["city"])[:4] == n[:4] for n in names)]
    chosen = None
    for e in exact + [e for e in near if e not in exact]:
        data = get_json(e["api"], C.raw / f"hoodmaps_{e['slug']}.json")
        if km((data["at"][1], data["at"][0]), C.centre) <= 30:
            chosen, city = e, data
            break
    if not chosen:
        return None, {"status": "no Hoodmaps page for this city"}
    tags = get_json(chosen["api"] + "/tags", C.raw / f"hoodmaps_{chosen['slug']}_tags.json")
    per = [{"pos": 0, "neg": 0, "flags": 0} for _ in C.items]
    for t in tags["tags"]:
        k = C.district_of(t["longitude"], t["latitude"])
        if k is None:
            continue
        sign = words.classify(t["tag"])
        if sign:
            per[k]["pos" if sign > 0 else "neg"] += max(1, t["votes"])
    for c in city.get("crime", []):
        k = C.district_of(c["at"][1], c["at"][0])
        if k is not None:
            per[k]["flags"] = 1
    out = {}
    for k, p in enumerate(per):
        value, conf = crowd_value(p["pos"], p["neg"], p["flags"], cc, neutral)
        if conf > 0:
            out[k] = {"value": round(value), "confidence": round(conf, 2), "n": p["pos"] + p["neg"],
                      # counts only: crowd tags often describe people, not places, and carry no licence to republish
                      "note": ("on Hoodmaps' crime list; " if p["flags"] else "") + f"{p['neg']} votes on danger tags, {p['pos']} on safe tags"}
    return out, {"status": "ok", "url": city.get("source"), "attribution": city.get("attribution"), "updated": city.get("updated")}


def src_recorded_nl(C):
    """Dutch police-recorded crime per buurt (CBS table 47018NED), summed into our districts, per resident."""
    if C.country != "NL":
        return None, None
    b = C.bbox_ll
    feats, url = [], "https://api.pdok.nl/cbs/wijken-en-buurten-2024/ogc/v1/collections/buurten/items"
    params = {"f": "json", "limit": 1000, "bbox": f"{b[0]},{b[1]},{b[2]},{b[3]}"}
    page = 0
    while url:
        data = get_json(url, C.raw / f"pdok_buurten_{page}.json", params=params if page == 0 else None, max_age_days=60)
        feats += data["features"]
        url = next((l["href"] for l in data.get("links", []) if l.get("rel") == "next"), None)
        page += 1
    buurt = {}
    for f in feats:
        p = f["properties"]
        if p.get("water") == "JA":
            continue
        c = shape(f["geometry"]).representative_point()
        k = C.district_of(c.x, c.y)
        if k is not None:
            buurt[p["buurtcode"]] = (k, max(0, p.get("aantal_inwoners") or 0), p["gemeentecode"])
    if not buurt:
        return None, {"status": "no buurten found"}
    gm = max({g for _, _, g in buurt.values()}, key=lambda g: sum(1 for v in buurt.values() if v[2] == g))
    info = get_json("https://dataderden.cbs.nl/ODataApi/odata/47018NED/Perioden", C.raw / "cbs_periods.json", max_age_days=30)
    years = sorted(p["Key"] for p in info["value"] if p["Key"].endswith("JJ00"))
    year = years[-1]
    url = "https://dataderden.cbs.nl/ODataApi/odata/47018NED/TypedDataSet"   # this API has no paging; one city fits in one call
    q = f"substringof('BU{gm[2:]}',WijkenEnBuurten) and Perioden eq '{year}' and SoortMisdrijf eq '0.0.0 '"
    rows = get_json(url, C.raw / f"cbs_crime_{year}.json", params={"$filter": q}, max_age_days=30)["value"]
    crimes, people = np.zeros(len(C.items)), np.zeros(len(C.items))
    for r in rows:
        v = buurt.get(r["WijkenEnBuurten"].strip())
        if v and r["GeregistreerdeMisdrijven_1"] is not None:
            crimes[v[0]] += r["GeregistreerdeMisdrijven_1"]
            people[v[0]] += v[1]
    city_rate = crimes.sum() / max(people.sum(), 1)
    bands = C.cfg["safety"]["recorded"]["ratio_bands"]
    out = {}
    for k in range(len(C.items)):
        if people[k] < 500:
            continue
        ratio = (crimes[k] / people[k]) / city_rate
        out[k] = {"value": round(band(bands, ratio)), "confidence": 1.0, "n": int(crimes[k]),
                  "note": f"{int(crimes[k]):,} crimes in {year[:4]}, {1000 * crimes[k] / people[k]:.0f} per 1,000 residents (city {1000 * city_rate:.0f})"}
    return out, {"status": "ok", "url": "https://dataderden.cbs.nl/ODataApi/odata/47018NED", "attribution": "Politie, via CBS (47018NED)", "updated": year[:4]}


def src_recorded_uk(C):
    """data.police.uk: street-level crimes for the latest month, per district polygon (12 months would be 12 calls each)."""
    if C.country not in ("GB", "UK"):
        return None, None
    last = get_json("https://data.police.uk/api/crime-last-updated", C.raw / "policeuk_last.json", max_age_days=7)["date"][:7]
    crimes, area = np.zeros(len(C.items)), np.zeros(len(C.items))
    for k, poly in enumerate(C.polys):
        if poly is None:
            continue
        hull = poly.convex_hull.simplify(50)
        pts = ":".join(f"{lat:.5f},{lon:.5f}" for lon, lat in (C.ll(x, y) for x, y in list(hull.exterior.coords)[:-1]))
        data = get_json("https://data.police.uk/api/crimes-street/all-crime", C.raw / f"policeuk_{last}_{k}.json", params={"poly": pts, "date": last}, max_age_days=30)
        crimes[k] = sum(1 for c in data if poly.contains(Point(*C.local(float(c["location"]["longitude"]), float(c["location"]["latitude"])))))
        area[k] = poly.area / 1e6
        time.sleep(0.1)
    rate = crimes / np.maximum(area, 0.01)
    city = crimes.sum() / max(area.sum(), 0.01)
    bands = C.cfg["safety"]["recorded"]["ratio_bands"]
    out = {k: {"value": round(band(bands, rate[k] / city)), "confidence": 1.0, "n": int(crimes[k]),
               "note": f"{int(crimes[k])} crimes in {last}, per km² vs the city"} for k in range(len(C.items)) if area[k] > 0}
    return out, {"status": "ok", "url": "https://data.police.uk", "attribution": "Contains public sector information licensed under the Open Government Licence v3.0", "updated": last}


def src_reddit(C, words):
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        return None, {"status": "not set up: add REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET (an approved Reddit API app)"}
    ua = os.environ.get("REDDIT_USER_AGENT", UA)
    tok = requests.post("https://www.reddit.com/api/v1/access_token", auth=(cid, secret), data={"grant_type": "client_credentials"},
                        headers={"User-Agent": ua}, timeout=30).json()["access_token"]
    H = {"Authorization": f"bearer {tok}", "User-Agent": ua}
    subs = get_json("https://oauth.reddit.com/subreddits/search", C.raw / "reddit_subs.json", params={"q": C.name, "limit": 10}, headers=H)
    cand = [s["data"] for s in subs["data"]["children"] if fold(s["data"]["display_name"])[:4] in {fold(C.name)[:4], fold(C.id)[:4]}]
    if not cand:
        return None, {"status": f"no subreddit found for {C.name}"}
    sub = max(cand, key=lambda s: s.get("subscribers") or 0)["display_name"]
    cc, neutral = C.cfg["safety"]["crowd"], C.cfg["safety"]["neutral"]
    out = {}
    for k, it in enumerate(C.items):
        name = it.get("short") or it["name"]
        data = get_json(f"https://oauth.reddit.com/r/{sub}/search", C.raw / "reddit" / f"{fold(name)[:40]}.json",
                        params={"q": f'"{name}"', "restrict_sr": 1, "limit": 100, "sort": "relevance", "t": "all"}, headers=H, max_age_days=30)
        time.sleep(0.7)   # 100 requests a minute
        pos, neg, authors = set(), set(), set()
        for p in data["data"]["children"]:
            d = p["data"]
            text = f"{d.get('title', '')}. {d.get('selftext', '')}"
            for sentence in re.split(r"(?<=[.!?\n])\s+", text):
                if fold(name) in fold(sentence):
                    s = words.classify(sentence)
                    if s:
                        (pos if s > 0 else neg).add(d.get("author"))
            authors.add(d.get("author"))
        if len(pos | neg) < cc["min_authors"]:
            continue
        value, conf = crowd_value(len(pos), len(neg), 0, cc, neutral)
        out[k] = {"value": round(value), "confidence": round(conf, 2), "n": len(pos | neg),
                  "note": f"r/{sub}: {len(neg)} people wrote about danger, {len(pos)} about it being safe"}
    return out, {"status": "ok", "url": f"https://www.reddit.com/r/{sub}", "updated": time.strftime("%Y-%m-%d")}


def jsonstat_rows(d):
    dims = d["id"]
    sizes = d["size"]
    cats = [sorted(d["dimension"][k]["category"]["index"].items(), key=lambda kv: kv[1]) for k in dims]
    rows = []
    for flat, v in d["value"].items():
        i = int(flat)
        idx = []
        for s in reversed(sizes):
            idx.append(i % s)
            i //= s
        idx.reverse()
        rows.append({k: cats[j][idx[j]][0] for j, k in enumerate(dims)} | {"value": v})
    return rows


def src_city(C):
    """Whole-city context: Eurostat police-recorded offences for the city's NUTS 3 region vs European regions; Numbeo if set up."""
    parts, meta = [], {}
    bands = C.cfg["safety"]["city"]["ratio_bands"]
    try:
        nuts = get_json("https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_20M_2021_4326_LEVL_3.geojson",
                        C.shared / "nuts3_2021.geojson", max_age_days=365)
        pt = Point(*C.centre)
        code = next((f["properties"]["NUTS_ID"] for f in nuts["features"] if shape(f["geometry"]).contains(pt)), None)
        if code:
            offences = C.cfg["safety"]["city"]["eurostat_offences"]
            d = get_json("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/crim_gen_reg",
                         C.shared / "eurostat_crim_gen_reg.json", params={"unit": "P_HTHAB", "iccs": offences, "lang": "en", "sinceTimePeriod": 2019},
                         max_age_days=60)
            rows = [r for r in jsonstat_rows(d) if len(r["geo"]) == 5]
            mine = [r for r in rows if r["geo"] == code]
            years = sorted({r["time"] for r in mine}, reverse=True)
            for y in years:
                got = {r["iccs"]: r["value"] for r in mine if r["time"] == y}
                if len(got) < len(offences) - 1:
                    continue
                total = sum(got.values())
                others = {}
                for r in rows:
                    if r["time"] == y and r["iccs"] in got:
                        others.setdefault(r["geo"], {})[r["iccs"]] = r["value"]
                peer = [sum(v.values()) for v in others.values() if set(v) == set(got)]
                ratio = total / float(np.median(peer))
                label = d["dimension"]["geo"]["category"]["label"].get(code, code)
                parts.append({"id": "eurostat", "value": round(band(bands, ratio)),
                              "note": f"{label}: {total:,.0f} assaults, robberies, burglaries and thefts per 100,000 people in {y}, {ratio:.1f}× the European regional median"})
                meta["eurostat"] = {"status": "ok", "updated": y, "url": "https://ec.europa.eu/eurostat/databrowser/view/crim_gen_reg/default/table"}
                break
        if "eurostat" not in meta:
            meta["eurostat"] = {"status": "no Eurostat region data for this city"}
    except requests.RequestException as e:
        meta["eurostat"] = {"status": f"Eurostat unreachable: {e.__class__.__name__}"}
    key = os.environ.get("NUMBEO_API_KEY")
    if key:
        d = get_json("https://www.numbeo.com/api/city_crime", C.raw / "numbeo.json", params={"api_key": key, "query": C.name}, max_age_days=30)
        if d.get("safety_index") is not None:
            parts.append({"id": "numbeo", "value": round(d["safety_index"]),
                          "note": f"Numbeo safety index {d['safety_index']:.0f} ({d.get('contributors', '?')} contributors)"})
            meta["numbeo"] = {"status": "ok", "url": "https://www.numbeo.com/crime/"}
    else:
        meta["numbeo"] = {"status": "not set up: add NUMBEO_API_KEY (a paid Numbeo API key)"}
    return parts, meta


# ------------------------------------------------------------------ combine
def city_safety(cfg, out_dir, raw_dir, city):
    C = City(cfg, out_dir, raw_dir, city)
    if not C.items:
        print(f"{city}: no districts, nothing to do")
        return
    S = cfg["safety"]
    words = Words(S["crowd"])
    sources = {}
    per = {k: {} for k in range(len(C.items))}
    for k, it in enumerate(C.items):   # street safety from extras.py, already a district median
        v = (it.get("extra") or {}).get("safety")
        if v is not None:
            per[k]["street"] = {"value": v, "confidence": 1.0, "note": "median of the district's streets"}
    sources["street"] = {"status": "ok"}
    for sid, fn in (("recorded", lambda: src_recorded_nl(C)), ("recorded", lambda: src_recorded_uk(C)),
                    ("hoodmaps", lambda: src_hoodmaps(C, words)), ("reddit", lambda: src_reddit(C, words))):
        try:
            vals, meta = fn()
        except (requests.RequestException, KeyError, ValueError) as e:
            vals, meta = None, {"status": f"failed: {e.__class__.__name__}: {str(e)[:120]}"}
        if meta is None:
            continue
        sources[sid] = {**sources.get(sid, {}), **meta}
        for k, v in (vals or {}).items():
            per[k][sid] = v
    city_parts, city_meta = src_city(C)
    sources.update(city_meta)
    if city_parts:
        cv = float(np.mean([p["value"] for p in city_parts]))
        for k in per:
            per[k]["city"] = {"value": round(cv), "confidence": 1.0, "note": "; ".join(p["note"] for p in city_parts), "from": [p["id"] for p in city_parts]}
    W = S["weights"]
    for k, it in enumerate(C.items):
        parts = per[k]
        num = den = 0.0
        for sid, p in parts.items():
            w = W[sid] * p.get("confidence", 1.0)
            num += w * p["value"]
            den += w
        it["safety"] = {"score": round(num / den) if den else None,
                        "parts": [{"id": sid, **p, "weight": round(W[sid] * p.get("confidence", 1.0) / den, 2)} for sid, p in parts.items()] if den else []}
    C.dist["safety_sources"] = {sid: {**S["sources"].get(sid, {}), **meta} for sid, meta in sources.items()}
    for sid in ("eurostat", "numbeo"):
        if sid in C.dist["safety_sources"]:
            C.dist["safety_sources"][sid] = {**S["sources"][sid], **sources[sid]}
    (C.src / "districts.json").write_text(json.dumps(C.dist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (C.src / "safety.json").write_text(json.dumps({"city": city, "sources": C.dist["safety_sources"],
                                                    "districts": [{"name": it["name"], **it["safety"]} for it in C.items]}, ensure_ascii=False, indent=1), encoding="utf-8")
    used = ", ".join(f"{sid}: {m.get('status')}" for sid, m in C.dist["safety_sources"].items())
    print(f"{city}: {used}")


def main(metrics_path, out_dir, raw_dir, *cities):
    global REFRESH
    REFRESH = "--refresh" in cities
    cfg = yaml.safe_load(Path(metrics_path).read_text(encoding="utf-8"))
    for city in (c for c in cities if c != "--refresh"):
        city_safety(cfg, out_dir, raw_dir, city)


if __name__ == "__main__":
    if len(sys.argv) < 5:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
