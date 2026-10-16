"""Adapter: OpenStreetMap (global baseline, ODbL), from a city extract or the Overpass API.

Two routes to the same output:
  - `osm_extract.url` in sources.yaml: download a city or region .osm.pbf
    (Geofabrik, BBBike, openstreetmap.fr) once and read it locally with
    pyosmium. Preferred: one download, no load on shared servers.
  - otherwise the Overpass API, rotating over endpoints with backoff.

Writes, for the padded city bbox:
  transit.json    public-transport stops, stations, metro entrances and route relations (I19)
  amenities.json  everyday amenities in the categories of street_metrics.yaml `amenities` (I20)

Public Overpass servers are often busy, so every query rotates over several
endpoints with backoff. Responses with a runtime-error remark are partial and
are retried, never used.

Usage: python -I fetch_osm.py <sources.yaml> <street_metrics.yaml> <overture_dir> <out_dir>
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

EXTRACT_HOSTS = ("download.geofabrik.de", "download.bbbike.org", "download.openstreetmap.fr")
STOP_TAGS = {"public_transport": {"platform", "stop_position", "station"}, "highway": {"bus_stop"},
             "railway": {"tram_stop", "station", "halt", "stop", "subway_entrance", "platform"},
             "amenity": {"ferry_terminal"}}

ENDPOINTS = ["https://overpass-api.de/api/interpreter",
             "https://lz4.overpass-api.de/api/interpreter",
             "https://z.overpass-api.de/api/interpreter",
             "https://overpass.openstreetmap.ru/api/interpreter"]
UA = "GreenCityMap/0.2 (open-source livability research)"
KEEP_TAGS = {"name", "ref", "route", "network", "operator", "service", "by_night", "interval", "public_transport",
             "highway", "railway", "station", "subway", "tram", "bus", "trolleybus", "light_rail", "train",
             "ferry", "amenity", "shop", "leisure", "healthcare", "access"}


def overpass(query, label):
    data = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(12):
        url = ENDPOINTS[attempt % len(ENDPOINTS)]
        t = time.time()
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            raw = urllib.request.urlopen(req, timeout=400).read()
            res = json.loads(raw)
            remark = res.get("remark") or ""
            if "error" in remark.lower():
                raise RuntimeError(remark[:200])
            print(f"  {label}: {len(res['elements'])} elements from {url.split('/')[2]} "
                  f"({len(raw) / 1e6:.1f} MB, {time.time() - t:.0f}s)", flush=True)
            return res["elements"]
        except Exception as e:  # busy server, timeout, partial result
            print(f"  {label}: {url.split('/')[2]} failed ({str(e)[:80]}), retrying", flush=True)
            time.sleep(min(60, 5 * (attempt + 1)))
    raise RuntimeError(f"Overpass unavailable for {label}")


def slim(el):
    out = {"type": el["type"][0], "id": el["id"]}
    if "lat" in el:
        out["lat"], out["lon"] = el["lat"], el["lon"]
    elif "center" in el:
        out["lat"], out["lon"] = el["center"]["lat"], el["center"]["lon"]
    out["tags"] = {k: v for k, v in (el.get("tags") or {}).items() if k in KEEP_TAGS}
    if el["type"] == "relation":
        out["members"] = [[m["type"][0], m["ref"], m.get("role", "")] for m in el.get("members", [])
                          if m["type"] in ("node", "way")]
    return out


def is_stop(t, kind):
    if kind == "w":   # platforms and stations mapped as areas
        return t.get("public_transport") in ("platform", "station") or t.get("railway") in ("platform", "station")
    return any(t.get(k) in v for k, v in STOP_TAGS.items() if k != "railway" or t.get(k) != "platform")


def from_extract(pbf, bbox, cfg):
    """Read stops, route relations and amenities inside bbox from a local .osm.pbf."""
    import osmium
    w, s, e, n = bbox
    modes = set(cfg["transit"]["modes"])
    am_pairs = {(k, v) for c in cfg["amenities"]["categories"] for k, vals in c["osm"].items() for v in vals}
    pairs = {(k, v) for k, vals in STOP_TAGS.items() for v in vals} | am_pairs | {("type", "route")}
    am_keys = {k for k, _ in am_pairs}
    stops, routes, amen = [], [], []
    fp = osmium.FileProcessor(str(pbf)).with_locations().with_filter(osmium.filter.TagFilter(*sorted(pairs)))
    for o in fp:
        t = {k: v for k, v in o.tags}
        kind = "n" if o.is_node() else "w" if o.is_way() else "r"
        if kind == "r":
            if t.get("type") == "route" and t.get("route") in modes:
                routes.append({"type": "r", "id": o.id, "tags": {k: v for k, v in t.items() if k in KEEP_TAGS},
                               "members": [[m.type, m.ref, m.role] for m in o.members if m.type in ("n", "w")]})
            continue
        if kind == "n":
            if not o.location.valid():
                continue
            lon, lat = o.location.lon, o.location.lat
        else:
            locs = [(nd.lon, nd.lat) for nd in o.nodes if nd.location.valid()]
            if not locs:
                continue
            lon, lat = sum(x for x, _ in locs) / len(locs), sum(y for _, y in locs) / len(locs)
        if not (w <= lon <= e and s <= lat <= n):
            continue
        el = {"type": kind, "id": o.id, "lat": lat, "lon": lon, "tags": {k: v for k, v in t.items() if k in KEEP_TAGS}}
        if is_stop(t, kind):
            stops.append(el)
        if any((k, t.get(k)) in am_pairs for k in am_keys):
            amen.append(el)
    return stops, routes, amen


def download_extract(url, out):
    """Download a .osm.pbf extract once. The file is reused only if it came from the same URL
    and finished downloading (written to .part, size checked, then renamed)."""
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" or parts.hostname not in EXTRACT_HOSTS:
        raise SystemExit(f"osm_extract.url must be https from one of {EXTRACT_HOSTS}, got {url}")
    pbf, stamp = out / "extract.osm.pbf", out / "extract.url"
    if pbf.exists() and stamp.exists() and stamp.read_text().strip() == url:
        return pbf
    part = out / "extract.osm.pbf.part"
    for attempt in range(4):
        try:
            print(f"downloading {url}", flush=True)
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=600) as r, open(part, "wb") as f:
                expected = int(r.headers.get("Content-Length") or 0)
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            got = part.stat().st_size
            if expected and got != expected:
                raise IOError(f"incomplete download: {got} of {expected} bytes")
            part.rename(pbf)
            stamp.write_text(url)
            return pbf
        except Exception as e:
            print(f"  download failed ({e}), retrying", flush=True)
            time.sleep(10 * (attempt + 1))
    raise SystemExit(f"Could not download {url}")


def main(sources_path, metrics_path, overture_dir, out_dir):
    cfg = yaml.safe_load(Path(metrics_path).read_text())
    src = yaml.safe_load(Path(sources_path).read_text())
    meta = json.loads((Path(overture_dir) / "meta.json").read_text())
    w, s, e, n = meta["bbox_padded"]
    bb = f"{s:.5f},{w:.5f},{n:.5f},{e:.5f}"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    city = src["city"]
    ext = src.get("osm_extract")
    if ext:
        pbf = download_extract(ext["url"], out)
        t = time.time()
        stops, routes, am = from_extract(pbf, (w, s, e, n), cfg)
        print(f"{city}: extract {pbf.stat().st_size / 1e6:.0f} MB -> {len(stops)} stops, {len(routes)} routes, "
              f"{len(am)} amenities ({time.time() - t:.0f}s)", flush=True)
        stamp = {"bbox": [w, s, e, n], "fetched": time.strftime("%Y-%m-%d"), "via": ext["url"]}
        (out / "transit.json").write_text(json.dumps({**stamp, "stops": stops, "routes": routes}))
        (out / "amenities.json").write_text(json.dumps({**stamp, "elements": am}))
        return
    print(f"{city}: Overpass bbox {bb}", flush=True)

    modes = "|".join(cfg["transit"]["modes"])
    q_stops = f"""[out:json][timeout:300][maxsize:1073741824];
(
  node["public_transport"~"^(platform|stop_position|station)$"]({bb});
  node["highway"="bus_stop"]({bb});
  node["railway"~"^(tram_stop|station|halt|stop|subway_entrance)$"]({bb});
  node["amenity"="ferry_terminal"]({bb});
  way["public_transport"~"^(platform|station)$"]({bb});
  way["railway"~"^(platform|station)$"]({bb});
);
out tags center qt;"""
    q_routes = f"""[out:json][timeout:300][maxsize:1073741824];
rel["type"="route"]["route"~"^({modes})$"]({bb});
out body qt;"""
    stops = [slim(x) for x in overpass(q_stops, "transit stops")]
    routes = [slim(x) for x in overpass(q_routes, "route relations")]
    (out / "transit.json").write_text(json.dumps({"bbox": [w, s, e, n], "stops": stops, "routes": routes,
                                                  "fetched": time.strftime("%Y-%m-%d")}))

    clauses = []
    for cat in cfg["amenities"]["categories"]:
        for key, vals in cat["osm"].items():
            rx = "|".join(vals)
            clauses.append(f'  nwr["{key}"~"^({rx})$"]({bb});')
    q_am = "[out:json][timeout:300][maxsize:1073741824];\n(\n" + "\n".join(sorted(set(clauses))) + \
        "\n);\nout tags center qt;"
    am = [slim(x) for x in overpass(q_am, "amenities")]
    (out / "amenities.json").write_text(json.dumps({"bbox": [w, s, e, n], "elements": am,
                                                    "fetched": time.strftime("%Y-%m-%d")}))


if __name__ == "__main__":
    main(*sys.argv[1:5])
