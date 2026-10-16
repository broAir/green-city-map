"""Resolve a city name to a ready-to-run source manifest (registry/<cc>/<slug>/sources.yaml).

Does the setup steps that used to be manual:
  1. finds the city's boundary in Overture divisions (prefers subtype locality, then
     localadmin, then county; the largest area wins when names repeat),
  2. picks the UTM zone of the boundary's centre as the working CRS,
  3. picks leaf-on months from the latitude (northern summer, southern summer, or the
     whole year near the equator),
  4. finds the smallest OpenStreetMap extract that covers the city: a BBBike city
     extract when one exists and its outline covers the city, otherwise the smallest
     Geofabrik region whose outline covers it.

Usage: python -I resolve_city.py "<city name>" <country code, e.g. SI> [--id <overture division id>] [--slug <folder name>] [--write]
Without --write it only prints what it found.
"""
import json
import math
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

import shapely
import yaml
from shapely.geometry import shape

RELEASE = "2026-09-23.1"
UA = {"User-Agent": "GreenCityMap/0.2 (open-source livability research)"}
ROOT = Path(__file__).resolve().parents[2]
SUBTYPE_RANK = {"locality": 0, "localadmin": 1, "county": 2, "region": 3}   # Ljubljana, for one, is a "region"


def slug(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "city"


def find_division(name, cc, div_id=None):
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2'; SET enable_progress_bar=false;")
    src = f"read_parquet('s3://overturemaps-us-west-2/release/{RELEASE}/theme=divisions/type=division_area/*', hive_partitioning=1)"
    if div_id:
        where, params = "id = ?", [div_id]
    else:
        where = ("country = ? AND subtype IN ('locality', 'localadmin', 'county', 'region') AND coalesce(class, 'land') <> 'maritime' "
                 "AND (names.primary = ? OR list_contains(map_values(names.common), ?))")
        params = [cc.upper(), name, name]
    rows = con.execute(f"SELECT id, names.primary, subtype, country, ST_AsWKB(geometry), names.common['en'] FROM {src} WHERE {where}",
                       params).fetchall()
    if not rows:
        raise SystemExit(f"No Overture division named {name!r} in {cc}. Try the local spelling, or pass --id.")
    en = lambda v: (v[0] if isinstance(v, list) and v else v) or None
    cands = [(r[0], r[1], r[2], r[3], shapely.from_wkb(bytes(r[4])), en(r[5])) for r in rows]
    cands.sort(key=lambda c: (SUBTYPE_RANK.get(c[2], 9), -c[4].area))
    for c in cands:
        print(f"  candidate {c[0]} {c[1]!r} {c[2]} area {c[4].area:.4f} deg²")
    return cands[0]


def utm_epsg(lon, lat):
    zone = int(math.floor((lon + 180) / 6)) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def leaf_on(lat):
    if abs(lat) < 15:
        return list(range(1, 13))
    return [6, 7, 8] if lat > 0 else [12, 1, 2]


def head_size(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return None


def parse_poly(text):
    """Osmosis .poly format -> shapely (Multi)Polygon."""
    rings, cur = [], None
    for line in text.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        if line == "END":
            if cur is not None:
                if len(cur) >= 3:
                    rings.append(cur)
                cur = None
            continue
        if cur is None:
            cur = []
            continue
        x, y = map(float, line.split()[:2])
        cur.append((x, y))
    polys = [shapely.Polygon(r) for r in rings]
    return shapely.union_all(polys) if polys else None


def bbbike_extract(names, need):
    for n in names:
        city = re.sub(r"[^A-Za-z]", "", unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode()).capitalize()
        if not city:
            continue
        base = f"https://download.bbbike.org/osm/bbbike/{city}/{city}"
        try:
            req = urllib.request.Request(base + ".poly", headers=UA)
            poly = parse_poly(urllib.request.urlopen(req, timeout=30).read().decode())
        except Exception:
            continue
        if poly is not None and poly.contains(need):
            return base + ".osm.pbf", "bbbike"
    return None


def geofabrik_extract(need):
    req = urllib.request.Request("https://download.geofabrik.de/index-v1.json", headers=UA)
    idx = json.loads(urllib.request.urlopen(req, timeout=120).read())
    best = None
    for f in idx["features"]:
        url = (f["properties"].get("urls") or {}).get("pbf")
        if not url or not f.get("geometry"):
            continue
        g = shape(f["geometry"])
        if g.contains(need) and (best is None or g.area < best[0]):
            best = (g.area, url)
    return (best[1], "geofabrik") if best else None


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    name, cc = argv[0], argv[1]
    div_id = argv[argv.index("--id") + 1] if "--id" in argv else None
    did, primary, subtype, country, geom, english = find_division(name, cc, div_id)
    c = geom.centroid
    xmin, ymin, xmax, ymax = geom.bounds
    need = geom.buffer(0.005).simplify(0.002)   # the city itself plus ~500 m, so stops just outside still count
    epsg = utm_epsg(c.x, c.y)
    zones = {utm_epsg(x, c.y) for x in (xmin, xmax)}
    ext = bbbike_extract([primary, name], need) or geofabrik_extract(need)
    size = head_size(ext[0]) if ext else None
    print(f"{primary} ({subtype}, {country}) id {did}")
    print(f"  centre {c.y:.4f}, {c.x:.4f}  CRS {epsg}" + ("" if len(zones) == 1 else f"  (city spans {sorted(zones)}; using the centre zone)"))
    print(f"  leaf-on months {leaf_on(c.y)}")
    print(f"  OSM extract {ext[0] if ext else 'none found: the pipeline will use Overpass'}"
          + (f"  ({size / 1e6:.0f} MB)" if size else ""))
    if size and size > 800e6:
        print("  warning: the extract is large; look for a smaller city or region extract before running")
    if "--write" not in argv:
        return
    # folder name: --slug, else the English or local name in plain letters (Дніпро -> dnipro via the English name)
    s = argv[argv.index("--slug") + 1] if "--slug" in argv else (slug(english or "") if english else "") or slug(primary)
    if s == "city":
        s = f"city-{did[:8]}"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", s):
        raise SystemExit(f"--slug must be lower-case letters, digits and dashes, got {s!r}")
    reg = ROOT / "registry" / country.lower() / s
    reg.mkdir(parents=True, exist_ok=True)
    tpl = yaml.safe_load((ROOT / "skill" / "config" / "sources.template.yaml").read_text())
    tpl.update({"city": s, "name": primary, "country": country, "crs": epsg, "leaf_on_months": leaf_on(c.y),
                "boundary": {"overture_division_id": did, "overture_subtype": subtype}})
    tpl["pins"]["overture_release"] = RELEASE
    tpl["resolved_at"] = __import__("datetime").date.today().isoformat()
    if ext:
        tpl["osm_extract"] = {"url": ext[0], "provider": ext[1], "licence": "ODbL-1.0"}
    out = reg / "sources.yaml"
    out.write_text("# City source manifest (schema: spec/input-layers.md §5), written by resolve_city.py.\n"
                   "# Every layer starts at its global baseline; the research phase lists local upgrades in candidates.yaml.\n"
                   + yaml.safe_dump(tpl, sort_keys=False, allow_unicode=True))
    print(f"wrote {out.relative_to(ROOT)}  ->  run: skill/run_city.sh {country.lower()}/{s}")


if __name__ == "__main__":
    main(sys.argv[1:])
