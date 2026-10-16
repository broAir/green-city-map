"""Quality gates for a city's run. Prints each check, writes quality.json next to the
outputs, and exits non-zero when a hard gate fails, so a broken city is never shared.

Hard gates (fail): outputs present and readable, pieces scored, satellite coverage
of land >= 90 %, every metric array the right length.
Soft gates (warn): satellite coverage < 95 %, measured building heights < 30 %,
no public-transport lines, an amenity category with no places.

Usage: python -I validate_city.py <out_dir>
"""
import json
import sys
from pathlib import Path

REQUIRED = ["streets.json", "details.json", "layers.json", "basemap.json", "manifest.json"]


def main(out_dir):
    out = Path(out_dir)
    checks, fail = [], False

    def check(name, ok, detail, hard=True):
        nonlocal fail
        level = "ok" if ok else ("FAIL" if hard else "warn")
        fail |= (not ok and hard)
        checks.append({"check": name, "result": level, "detail": detail})
        print(f"  [{level:4}] {name}: {detail}")

    missing = [f for f in REQUIRED if not (out / f).exists()]
    check("outputs present", not missing, "all files written" if not missing else f"missing {missing}")
    if missing:
        sys.exit(1)
    st = json.loads((out / "streets.json").read_text(encoding="utf-8"))
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    q = man.get("quality", {})
    n = len(st["pieces"]["geom"])
    check("pieces scored", n > 0, f"{n:,} street pieces")
    bad = [k for k, v in st["pieces"].items() if len(v) != n]
    check("metric arrays aligned", not bad, "every array has one value per piece" if not bad else f"wrong length: {bad}")
    cov = q.get("ndvi_land_coverage")
    check("satellite coverage", cov is not None and cov >= 0.90, f"{cov:.1%} of land has a summer greenness value" if cov is not None else "unknown")
    if cov is not None and 0.90 <= cov < 0.95:
        check("satellite coverage (target 95 %)", False, f"{cov:.1%}: some greenness values are missing", hard=False)
    h = q.get("measured_height_share")
    check("measured building heights", h is not None and h >= 0.30,
          f"{h:.0%} of buildings have a measured height or floor count; the rest are estimated" if h is not None else "unknown",
          hard=False)
    check("public transport lines", bool(q.get("transit_routes")), f"{q.get('transit_routes') or 0} lines found in OpenStreetMap", hard=False)
    empty = [k for k, v in (q.get("amenities") or {}).items() if not v]
    check("everyday amenities", not empty, "every category has places" if not empty else f"no places for {empty}", hard=False)
    (out / "quality.json").write_text(json.dumps({"passed": not fail, "checks": checks}, indent=1), encoding="utf-8")
    if fail:
        print("Quality gates failed: fix the inputs before sharing this city.")
        sys.exit(1)
    print("Quality gates passed.")


if __name__ == "__main__":
    main(sys.argv[1])
