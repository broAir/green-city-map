"""Pack a validated city into one shareable bundle: share/<cc>-<city>.gcm.zip.

The bundle holds the city's outputs (street scores, details, map layers, basemap,
lander preview and search index, manifest with sources and licences, quality report)
and bundle.json with a sha256 per file. It contains no absolute paths and nothing
about the person who ran it, except an optional credit name.

Usage: python -I package_city.py <out_dir/city> <share_dir> [--credit "Name or @handle"]
"""
import datetime as dt
import hashlib
import json
import sys
import zipfile
from pathlib import Path

FORMAT = "green-city-map-bundle"
VERSION = 1
FIXED = ["streets.json", "details.json", "layers.json", "basemap.json", "manifest.json", "quality.json",
         "summary.json", "stats.json", "preview.webp", "street_index.json", "tour.json"]
OPTIONAL = ["api_points.json",   # one row per street piece, for street lookups by position on the website
            "districts.json",    # district outlines, names and summaries (skill/pipeline/districts.py)
            "extras.json",       # easy on foot, good for families, street safety per piece (skill/pipeline/extras.py)
            "safety.json"]       # combined district safety and its sources (skill/pipeline/safety.py)


def main(city_dir, share_dir, *rest):
    city_dir, share = Path(city_dir), Path(share_dir)
    credit = rest[rest.index("--credit") + 1][:80] if "--credit" in rest else None
    quality = json.loads((city_dir / "quality.json").read_text(encoding="utf-8")) if (city_dir / "quality.json").exists() else None
    if not quality or not quality.get("passed"):
        raise SystemExit("Run validate_city.py first; only cities that pass the quality gates can be shared.")
    man = json.loads((city_dir / "manifest.json").read_text(encoding="utf-8"))
    bm = json.loads((city_dir / "basemap.json").read_text(encoding="utf-8"))
    names = FIXED + [c["file"] for c in bm["chunks"]] + ([bm["green"]["file"]] if bm.get("green") else [])
    names += [n for n in OPTIONAL if (city_dir / n).exists()]
    missing = [n for n in names if not (city_dir / n).exists()]
    if missing:
        raise SystemExit(f"Missing outputs: {missing}")
    files = {}
    for n in names:
        b = (city_dir / n).read_bytes()
        files[n] = {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
    bundle = {"format": FORMAT, "version": VERSION, "city": man["city"], "name": man["name"], "country": man["country"],
              "skill_version": man.get("skill_version"), "config": man.get("config"),
              "overture_release": man.get("overture_release"), "output_licence": man.get("output_licence"),
              "attribution": man.get("attribution"), "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "credit": credit, "files": files}
    share.mkdir(parents=True, exist_ok=True)
    path = share / f"{man['country'].lower()}-{man['city']}.gcm.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("bundle.json", json.dumps(bundle, indent=1, ensure_ascii=False))
        for n in names:
            z.write(city_dir / n, n)
    print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB, {len(names)} files)")


if __name__ == "__main__":
    main(*sys.argv[1:])
