"""Assemble the review artifact: index.html (template + metric config) and
data/<city>/ files copied from the pipeline output.

Usage: python -I build_review.py <street_metrics.yaml> <out_dir> <review_dir> <build_label> <city> [<city> ...] [--hosted]
City labels come from registry/*/<city>/sources.yaml (`name`).
--hosted builds the page for the Green City Map website: city data from /data/<city>/ and
review marks through the site's /api/marks; no data files are copied. Without it, the page is for
local viewing (for example `python -I -m http.server 8000 --bind 127.0.0.1 --directory review`),
where marks can't be saved.
"""
import json
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def city_label(city):
    for p in (ROOT / "registry").glob(f"*/{city}/sources.yaml"):
        return yaml.safe_load(p.read_text()).get("name", city)
    return city


def safe_json(obj):
    """JSON for inlining in a <script>: no way to close the tag early."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")


def main(metrics_path, out_dir, review_dir, build, *cities):
    hosted = "--hosted" in cities
    cities = [c for c in cities if c != "--hosted"]
    cfg = yaml.safe_load(Path(metrics_path).read_text(encoding="utf-8"))
    slim = {
        "version": cfg["version"], "metrics": cfg["metrics"], "sub_indices": cfg["sub_indices"],
        "must_have_slack": cfg["must_have_slack"],
        "walk_speed_m_per_min": cfg["walk_graph"]["walk_speed_m_per_min"],
        "amenities": {"reach_m": cfg["amenities"]["reach_m"],
                      "categories": [{"id": c["id"], "label": c["label"], "points": c["points"]}
                                     for c in cfg["amenities"]["categories"]]},
        "cities": [{"id": c, "label": city_label(c)} for c in cities],
    }
    if hosted:
        slim.update({"api": "/api", "dataBase": "/data/"})
    tpl = (Path(__file__).parent / "review.template.html").read_text(encoding="utf-8")
    if hosted:
        # The website serves these fonts itself (/fonts/map.css), so no visitor's browser asks Google for them.
        lines = tpl.split("\n")
        google = [i for i, line in enumerate(lines[:6]) if "fonts.googleapis.com" in line or "fonts.gstatic.com" in line]
        assert google, "review.template.html: the Google Fonts lines moved; update build_review.py"
        lines[google[0]:google[-1] + 1] = ['<link rel="stylesheet" href="/fonts/map.css">']
        tpl = "\n".join(lines)
    html = tpl.replace("__CONFIG_JSON__", safe_json(slim)).replace('"__BUILD__"', safe_json(build))
    review = Path(review_dir)
    review.mkdir(parents=True, exist_ok=True)
    # Both the website and the local `http.server` serve the page on its own, so it needs a document skeleton:
    # the doctype (standards mode) and the charset (http.server sends text/html with no charset, and some
    # browsers would then show "Kraków" as "KrakÃ³w").
    html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>'
            + html + "</body></html>")
    (review / "index.html").write_text(html, encoding="utf-8")
    if hosted:
        print(f"review: hosted index.html for {len(cities)} cities")
        return
    files = {}
    for c in cities:
        src, dst = Path(out_dir) / c, review / "data" / c
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True, exist_ok=True)
        bm = json.loads((src / "basemap.json").read_text())
        names = ["streets.json", "details.json", "layers.json", "basemap.json", "manifest.json"]
        names += [ch["file"] for ch in bm["chunks"]]
        if bm.get("green"):
            names.append(bm["green"]["file"])
        for n in names:
            shutil.copy2(src / n, dst / n)
            files[f"data/{c}/{n}"] = str(dst.relative_to(review) / n)     # relative: no local paths leak
    (review / "files.json").write_text(json.dumps(files, indent=1))
    total = sum((review / p).stat().st_size for p in files.values())
    print(f"review: index.html + {len(files)} data files, {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main(*sys.argv[1:])
