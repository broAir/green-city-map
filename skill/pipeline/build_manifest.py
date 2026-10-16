"""Summarise a city's run into manifest.json: sources actually used (with release,
dates and licences), required attributions, quality numbers, the research-phase
candidates, and the skill and config versions that produced it.

Usage: python -I build_manifest.py <registry_city_dir> <raw_dir> <out_dir>
"""
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

# Overture's own credit lines for the themes we read (buildings, transportation, base, divisions, places),
# as listed on https://docs.overturemaps.org/attribution/ . We don't yet keep each feature's provider, so every
# provider of those themes is credited. Base: only land, land use and water are read (no bathymetry or land cover).
OVERTURE_CREDITS = [
    "Overture buildings and divisions: Esri Community Maps contributors. Available under CC BY 4.0.",
    "Overture buildings: Global ML Building Footprints. Licensed by Microsoft under the Open Database License.",
    "Overture buildings: Google Open Buildings. Available under CC BY 4.0.",
    "Overture buildings: USGS 3D Elevation Program Digital Elevation Program.",
    "Overture transportation: Data from TomTom.",
    "Overture base: Data from the Daylight Map Distribution.",
    "Overture divisions: geoBoundaries. Available under CC BY 4.0.",
    "Overture divisions: Land Information New Zealand (LINZ). Available under CC BY 4.0.",
    "Overture places: Data from Meta. Available under CDLA Permissive 2.0.",
    "Overture places: Data from Microsoft. Available under CDLA Permissive 2.0.",
    "Overture places: Copyright 2024 Foursquare Labs, Inc. All rights reserved. Available under Apache 2.0. "
    "Transformed to the Overture schema by Overture, then filtered and grouped into categories by Green City Map. "
    "Foursquare NOTICE: https://opensource.foursquare.com/places-notice-txt/",
    "Overture places: Data from AllThePlaces. Available under CC0 1.0.",
    "Overture places: Data from PinMeTo, Krick, RenderSEO, DAC and BrightQuery. Available under CDLA Permissive 2.0.",
]

# licence and attribution of every global source the pipeline reads
SOURCES = {
    "overture": {"name": "Overture Maps Foundation",
                 "licence": "ODbL-1.0 (buildings, transportation, base, divisions); places per provider: CDLA-Permissive-2.0 "
                            "(Meta, Microsoft and others), Apache-2.0 (Foursquare), CC0-1.0 (AllThePlaces)",
                 "attribution": "© OpenStreetMap contributors, © Overture Maps Foundation (overturemaps.org)",
                 "credits": OVERTURE_CREDITS},
    "osm": {"name": "OpenStreetMap", "licence": "ODbL-1.0", "attribution": "© OpenStreetMap contributors"},
    "sentinel2": {"name": "Copernicus Sentinel-2 L2A (via Microsoft Planetary Computer)", "licence": "Copernicus open data terms",
                  "attribution": "Contains modified Copernicus Sentinel data"},
    "worldcover": {"name": "ESA WorldCover 10 m 2021 v200", "licence": "CC-BY-4.0",
                   "attribution": "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium"},
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def main(reg_dir, raw_dir, out_dir):
    reg, raw, out = Path(reg_dir), Path(raw_dir), Path(out_dir)
    src = yaml.safe_load((reg / "sources.yaml").read_text(encoding="utf-8"))
    cand_path = reg / src.get("candidates_file", "candidates.yaml")
    cand = yaml.safe_load(cand_path.read_text(encoding="utf-8")) if cand_path.exists() else {}
    rmeta = json.loads((raw / "rasters" / "rasters_meta.json").read_text(encoding="utf-8"))
    if rmeta.get("ndvi_land_coverage") is None:          # rasters fetched before coverage was recorded
        import numpy as np
        z = np.load(raw / "rasters" / "rasters.npz", allow_pickle=False)
        land = (z["worldcover"] > 0) & (z["worldcover"] != 80)
        nd = z["ndvi"]
        rmeta["ndvi_land_coverage"] = round(float((np.isfinite(nd) & (np.abs(nd) <= 1))[land].mean()), 4) if land.any() else 0.0
    ometa = json.loads((raw / "overture" / "meta.json").read_text(encoding="utf-8"))
    stats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    osm = {}
    for name in ("transit.json", "amenities.json"):
        p = raw / "osm" / name
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            osm = {"via": d.get("via", "overpass"), "fetched": d.get("fetched")}
            break

    found = {}
    for layer, entry in (cand.get("layers") or {}).items():
        entry = entry or {}
        best = entry.get("best_candidate") or entry.get("best_available") or {}
        lic = best.get("licence") or {}
        found[layer] = {
            "status": entry.get("status"),
            "tier": best.get("tier"),
            "source": best.get("source_name") or best.get("name"),
            "licence": lic.get("id") if isinstance(lic, dict) else lic,
            "licence_class": lic.get("class") if isinstance(lic, dict) else None,
            "verified": best.get("verified"),
        }
    scenes = rmeta.get("sentinel2_scenes", [])
    dates = sorted(s["date"] for s in scenes)
    hs = stats.get("buildings", {}).get("height_source_share", {})
    cfg_path = ROOT / "skill" / "config" / "street_metrics.yaml"
    manifest = {
        "city": src["city"], "name": src["name"], "country": src["country"], "crs": src["crs"],
        "skill_version": src.get("skill_version"),
        "config": {"version": yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["version"], "sha": sha(cfg_path)},
        "overture_release": ometa.get("overture_release"),
        "division_id": ometa.get("division_id"),
        "layers_in_use": src["layers"],
        "sources_used": {
            "overture": {**SOURCES["overture"], "release": ometa.get("overture_release"), "fetched": ometa.get("fetched")},
            "osm": {**SOURCES["osm"], **osm},
            "sentinel2": {**SOURCES["sentinel2"], "seasons": rmeta.get("sentinel2_seasons"),
                          "scenes": [s["id"] for s in scenes]},
            "worldcover": SOURCES["worldcover"],
        },
        "attribution": [v["attribution"] for v in SOURCES.values()] + OVERTURE_CREDITS,
        "output_licence": "ODbL-1.0",
        "quality": {
            "ndvi_land_coverage": rmeta.get("ndvi_land_coverage"),
            "measured_height_share": round(hs.get("height", 0) + hs.get("num_floors", 0), 3),
            "pieces_scored": stats.get("pieces_scored"),
            "transit_routes": (stats.get("transit") or {}).get("routes"),
            "amenities": (stats.get("amenities") or {}).get("by_category"),
        },
        "local_candidates": found,
        "sentinel2": {"scenes": len(scenes), "first": dates[0] if dates else None, "last": dates[-1] if dates else None},
        "stats": stats,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    print(f"{src['city']}: manifest with {len(found)} candidate layers, {len(scenes)} S2 scenes")


if __name__ == "__main__":
    main(*sys.argv[1:4])
