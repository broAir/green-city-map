# Green City Map: input layers and source discovery

Version 0.1 (draft, 2026-10-09). Companion file: [metrics.md](metrics.md).

This file defines the **abstract data each metric needs** and **how the skill finds that data for any city**. Every input layer has:
- a **contract**: the geometry, fields, units and minimum resolution it must provide
- a **global fallback**: what to use when nothing better exists
- a **ladder of better sources** to search for, with the quality tier each gives

## 1. Input layer catalogue

Tiers are defined in metrics.md §2: A is authoritative local data, B is regional or global at adequate resolution, C is a proxy or coarse source, and S is a street-imagery scan.

| ID | Layer | Contract | Used by | Global fallback (tier, licence) | Better sources to look for |
|---|---|---|---|---|---|
| I00 | City boundary and residential mask | Admin boundary polygon; residential flag per building or area | All (aggregation weights) | OSM admin boundary + OSM `building=residential/apartments/house` and `landuse=residential` (B, ODbL) | National admin boundaries; cadastral building use; census blocks |
| I01 | Building footprints | One polygon per building, ~1–2 m positional accuracy | M01–M05, M15, M16 | [Overture buildings](https://docs.overturemaps.org/guides/buildings), which merge OSM with open footprints from Esri Community Maps and machine-learned ones published by Microsoft and Google (B, ODbL; credits in DATA_LICENSE.md) | National cadastre or building register (in the EU, often published as INSPIRE "Buildings"); municipal footprint layers (A) |
| I02 | Building heights | Height above ground in m per building, or a ≤ 1 m normalised surface-height raster | M01–M03, M05 | Height ladder (§2) ending at OSM `building:levels` × 3 m (C) | National LiDAR or 3D city model (A) |
| I03 | Pedestrian network | Routable walk graph with intersections | M03, M12, M13 | OSM via OSMnx (B, ODbL) | Rarely improved locally; *validate connectivity* (share of nodes in the largest component) |
| I04 | Road and rail attributes | Centrelines with class, lanes, speed limit, surface; rail and tram lines | M16, M17, I10 model | OSM `highway=*`, `railway=*` (B, ODbL) | National road database with traffic volumes (AADT) |
| I05 | Canopy height | Raster ≤ 1–2 m, metres above ground | M04, M06, M02 | [Meta/WRI CHMv2](https://registry.opendata.aws/dataforgood-fb-forestsv2) 1 m (B, CC BY 4.0, no urban validation yet) → 10 m tree cover: ESA WorldCover tree class (C, CC BY 4.0) | LiDAR vegetation class or surface model masked to vegetation; municipal canopy maps (A). EU: HRL Tree Cover Density 10 m (B/C) |
| I06 | Tree points | Point per tree; optional height and crown size | M04 | OSM `natural=tree` (C, partial) | Municipal street-tree inventories (A). *Record their coverage share: they often list public trees only.* |
| I07 | Green and natural areas | Polygons with type and access (public, private, unknown) | M12, M13 | OSM `leisure=park/nature_reserve`, `landuse=forest/meadow`, `natural=wood/scrub/grassland/wetland/beach/water` + ESA WorldCover (C, ODbL / CC BY) | EU: [Urban Atlas 2021](https://library.land.copernicus.eu/products/Urban_Atlas_CLMS_UA2021_LULC_PUM_v1.html) with access field (B). Municipal green-space registers; national topographic maps (A) |
| I08 | Sealed surface | Raster ≤ 10 m, 0–100% sealed, or paving polygons | M07, M12 filter | ESA WorldCover built-up class, 10 m (C, CC BY 4.0) | EU: [Copernicus HRL Imperviousness](https://land.copernicus.eu/en/products/high-resolution-layer-imperviousness) 10 m (B). National large-scale topography with paving polygons (A) |
| I09 | Vegetation index | Summer NDVI raster, 10 m | M08, M12 filter | Sentinel-2 L2A (B, free under the Copernicus Sentinel terms). Needs no account via Copernicus Data Space or the AWS Earth Search STAC | — |
| I10 | Road and rail noise | Lden and Lnight; raster ≤ 10 m or 5 dB contour polygons | M15 | Modelled: [NoiseModelling](https://github.com/Universite-Gustave-Eiffel/NoiseModelling) (tool GPL-3) on I01, I02, I04 with default traffic flows, or a road-class × distance × shielding estimate (C) | Official strategic or municipal noise maps (A). The EU Environmental Noise Directive requires maps every 5 years for agglomerations over 100k people and for major roads, railways and airports. Validate against [NoiseCapture](https://noise-planet.org/noisecapture_data.html) crowd measurements (ODbL) |
| I11 | Traffic counts | Counts or AADT per counted segment | M17 (calibration) | None | Municipal counters, national road agency, Telraam citizen counters |
| I12 | Population | Residents on a grid ≤ 100 m or in census blocks | M13, M14, M18 | GHS-POP or WorldPop 100 m (B, CC BY 4.0); Kontur on H3 (B) | National census grid or blocks (A) |
| I13 | Short-term rentals | Listings or registered units with location and type | M18 | None. [Inside Airbnb](https://insideairbnb.com/get-the-data/) for selected cities (B): aggregates only, locations offset ≤ 150 m, and raw data must not be republished | Official short-term-rental registries (A) |
| I14 | Tourism attractors | Points with a popularity weight | M18 | OSM `tourism=*` + Wikipedia pageviews + distinct Wikimedia Commons uploaders of geotagged photos (C) | Official visitor counts or hotel-bed registers |
| I15 | Pedestrian counts | Counts per sensor and hour | M19 (calibration) | None | Municipal crowd-monitoring sensors |
| I16 | Street-level imagery | Images with location, heading, capture date; 360° preferred | M02 (variant), M09, M10, M19 | Mapillary (CC BY-SA 4.0, API token), Panoramax (federated, mostly CC BY-SA), KartaView (CC BY-SA) (S) | Municipal panorama programmes; **contributor capture** uploaded to Panoramax or Mapillary |
| I17 | Land surface temperature | Summer clear-sky scenes, 30 m | M11 | Landsat 8/9 Collection 2 Level-2 (B, public domain) | — |
| I18 | Terrain | Ground-elevation raster | M01, M05 (slopes); I02 derivation | Copernicus DEM GLO-30, 30 m (C) | National LiDAR ground-elevation model, ≤ 1 m (A) |
| I19 | Public transport | Stops with location and mode; lines serving each stop; departures per hour per line and stop | M20 | OSM `public_transport=*`, `highway=bus_stop`, `railway=tram_stop/station/subway_entrance` + route relations, from a city or region extract (Geofabrik, BBBike, openstreetmap.fr) or Overpass; frequency defaults per mode, OSM `interval=*` where tagged (C, ODbL) | Static GTFS feeds of the city operators, found via the [Mobility Database](https://mobilitydatabase.org) catalogue (A); check the licence of each feed |
| I20 | Everyday amenities | Points with a category: groceries, food and drink, pharmacy, doctor, school or childcare, culture and sport | M21 | OSM `shop=*`, `amenity=*`, `leisure=*` + Overture places with confidence ≥ 0.7 and deduplicated against OSM (B, ODbL / CDLA-Permissive) | Official registers: schools, pharmacies, licensed clinics (A) |

## 2. The building-height ladder (I02)

Heights drive the most distinctive metrics (M01, M03 and the derived "View from your windows"), and their availability varies the most between cities. The skill tries these sources in order and stops at the first one that covers ≥ 90% of the city's residential buildings.

| Order | Source | Tier | Notes |
|---|---|---|---|
| 1 | National or municipal 3D city model (LoD1 or better: CityGML, CityJSON, 3D Tiles) | A | Example: the Netherlands' 3DBAG |
| 2 | LiDAR point cloud or surface/ground-elevation models (DSM/DTM) → per-footprint `median(DSM − DTM)` | A | Needs ≥ ~4 points/m². Leaf-off flights are fine for buildings |
| 3 | Cadastral floor counts × 3 m (+ roof allowance) | B | Many cadastres store floors above ground |
| 4 | [EUBUCCO](https://docs.eubucco.com/latest/) (EU-27, UK, Norway, Switzerland) | B | ODbL; about 57% of heights are machine-learned estimates |
| 5 | Overture `height` / `num_floors` where present | B | Sparse in Europe |
| 6 | OSM `building:levels` × 3 m | C | Uneven tagging |
| 7 | **Imagery floor-counting scan** | S | A contributor task: count storeys on facade images. This is an objective, visible feature, the kind vision models handle reliably (inference; to be validated) |
| 8 | GHSL building height, 100 m (2018) | C | Cell-level only: cannot drive per-facade metrics, so M01's θ and M03 become cell estimates |

GlobalBuildingAtlas heights are the best global per-building heights, but they are **CC BY-NC**. They are therefore allowed only in local-only mode (§3).

## 3. Licence policy

The shared database is open data. Being a non-commercial project does **not** make non-commercial (NC) inputs safe: NC terms conflict with the share-alike licences (ODbL, CC BY-SA) the database must use anyway because of OSM and Mapillary inputs.

| Class | Licences or sources | What the skill may do |
|---|---|---|
| **Shareable** | Public domain, CC0, PDDL, CC BY (any version), ODbL, CC BY-SA, government open licences (e.g. OGL, Licence Ouverte / Etalab, DL-DE BY, NLOD) | Use and upload results to the shared database. Each output layer carries the licence its inputs require; layers are kept separate. |
| **Local-only** | CC BY-NC, "research and non-profit use only" (e.g. EEA END noise contours, GlobalBuildingAtlas heights), unclear or missing licence | Compute for the contributor's own private map. Results are tagged `shareable: false` and **never uploaded**. The skill should look for the same data from an original publisher with an open licence (for example national noise portals instead of the EEA copy). |
| **Never** | Google Maps, Street View or satellite; other Google datasets fetched on their own, such as Google Open Buildings or Dynamic World; Apple Maps; Bing; any source whose terms forbid scraping, analysis or derived datasets; anything behind a login or paywall that the contributor isn't licensed for | Not used at all, including for "just checking". The agent instructions forbid them. |

Overture Maps is shareable. Its buildings include some open footprints and heights that Microsoft and Google published under open licences, and its places include data from Microsoft. They reach the pipeline only inside Overture, and they are credited in [DATA_LICENSE.md](../DATA_LICENSE.md).

**Exception: district safety.** The district safety score (`skill/pipeline/safety.py`) may also use crowd and survey sources whose terms are not open: the Hoodmaps crowd map, Reddit posts and the Numbeo survey. For these sources:

- only derived values are kept: one value per district or city, vote or author counts, and the source's credit line;
- no tags, posts or other text from them goes into the outputs;
- these values are not offered under the ODbL; they keep their sources' own terms ([DATA_LICENSE.md](../DATA_LICENSE.md));
- each one carries an (i) note saying it can be unreliable, and none of them changes the map colours or the main score.

Code licence: PolyForm Noncommercial 1.0.0 ([LICENSE.md](../LICENSE.md)). City data: ODbL 1.0 ([DATA_LICENSE.md](../DATA_LICENSE.md)); each layer keeps the credits its sources require.

## 4. How the skill resolves sources for a new city

This is the skill's **research phase**. Today the skill does Steps 0 to 3: it writes what it finds to `registry/<cc>/<city>/candidates.yaml` and doesn't change the layers in `sources.yaml`. Steps 4 to 6 are planned, not built yet: there is no CI for sources (the only GitHub check is for upload receipts), no pinned container and no scan mode.

**Step 0: Registry check.**
- If `registry/<country>/<city>/sources.yaml` exists, load it.
- For each layer, re-check that the URL resolves and whether a newer vintage exists.
- Research only the layers that are missing, stale (older than the layer's refresh interval) or below their best-known tier.

**Step 1: Global baseline (deterministic, no research).**
- Resolve every input layer to its global fallback.
- This gives the city a complete tier B/C map on day one, and a reference to sanity-check better sources against.

**Step 2: Gap list.**
- For each layer, compare the current tier with the best tier plausible for this jurisdiction. For example, an EU country probably has a strategic noise map; a country with a national LiDAR programme has tier A heights.
- Queue the gaps, most valuable first: I02 heights, I10 noise, I05/I06 trees, then I07, I08, I12, I13 and the rest.

**Step 3: Research each gap (agentic).** For each queued layer:
1. **Establish the jurisdiction stack:** country, region or state, and municipality, plus their official languages.
2. **Search known catalogue types in order:**
   - the national mapping or cadastre agency
   - the national open-data portal
   - regional catalogues (e.g. data.europa.eu and the INSPIRE Geoportal in the EU)
   - the municipal open-data portal
   - thematic catalogues (e.g. OpenTopography for LiDAR)
   Many portals run on a few platforms with search APIs (CKAN, Socrata, ArcGIS Hub, OpenDataSoft), so the skill should detect the platform and query the API directly rather than scraping pages.
3. **Search the web with layer-specific keywords translated into the local language(s).** Maintain a keyword table per layer. Examples for I10:
   - "strategic noise map Lden"
   - "mapa estratégico de ruido"
   - "Lärmkarte"
   - "carte de bruit stratégique"
   - "geluidskaart"
   - "mapa de ruído"
   - "mappa acustica strategica"
4. **Evaluate each candidate against this checklist:**
   - **Coverage:** % of the city's residential buildings or area covered.
   - **Resolution and vintage:** do they meet the layer contract?
   - **Licence:** classify as shareable, local-only or never (§3). If the licence cannot be found, the source is local-only.
   - **Machine access:** direct download or API preferred. Free registration is acceptable and recorded. Scraping web pages is not.
   - **Schema mapping:** how its fields map to the layer contract.
   - **Sanity check against the global baseline:**
     - heights: median absolute difference vs Overture or OSM
     - noise: correlation with road class
     - trees: count vs canopy maxima
     Large disagreement is reported, not silently accepted.
5. **Decide:** adopt the candidate if it passes and improves the tier. Otherwise record it as "considered, rejected" with a reason, so the next contributor doesn't repeat the work.

**Step 4 (planned, not built yet): Write the manifest and submit it.**
- Write the manifest with full provenance.
- Open a pull request to the registry. CI validates the schema, checks that URLs resolve, checks licences against the shareable list, and re-runs the sanity checks. Maintainers review.

**Step 5 (planned, not built yet): Acquire and compute.** The deterministic pipeline is pinned by container digest and model and code hashes. It downloads the manifest's sources and computes every metric whose inputs exist.

**Step 6 (planned, not built yet): Optional scan mode.** The contributor claims H3 cells and runs the imagery metrics (M09, M10, M19 and floor-counting) on open images listed by the coordinator. Each cell is computed by 2–3 contributors and accepted on agreement.

## 5. City source manifest (schema sketch)

The values below are placeholders showing the shape. A real manifest is filled by the research phase.

```yaml
city: <slug>                 # e.g. barcelona
country: <ISO 3166-1 alpha-2>
languages: [<iso-639-1>, ...]
boundary: {source: <dataset>, id: <feature id>}
resolved_at: <date>
skill_version: <semver>
layers:
  I02_building_heights:
    tier: A
    source_name: <publisher + dataset name>
    url: <landing page>
    access: {method: download|api|wms|stac, endpoint: <url>, registration: false}
    licence: {id: CC-BY-4.0, class: shareable, attribution: "<required text>"}
    vintage: <year or date>
    coverage: 0.97            # share of residential buildings covered
    derivation: "median(DSM - DTM) per I01 footprint"
    checks: {median_abs_diff_vs_global_m: 1.6, n_compared: 5000}
    considered:
      - {source_name: GlobalBuildingAtlas heights, decision: local-only, reason: CC BY-NC}
      - {source_name: OSM building:levels, decision: rejected, reason: "coverage 0.18"}
  I10_noise:
    tier: C
    source_name: NoiseModelling on OSM, default flows
    status: gap               # research found nothing better; flagged for local contributors
    ...
```

The registry turns each city into reusable shared knowledge. In a city with good open data, the first contributor's research phase takes the longest, and everyone after them inherits the manifest. In a city with poor open data, the manifest documents the gaps, which tells contributors where scanning or capture campaigns would help most.

## 6. Open items

- Build the per-layer multilingual keyword tables (I02, I10, I05/I06 and I13 first).
- Decide the default validity window per layer (e.g. noise maps 5 years, footprints 1 year, imagery 3 years).
- Validate CHMv2 canopy against LiDAR in 3–5 cities with tier A data before relying on it as the global tree fallback.
- Validate imagery floor-counting against tier A heights.
- Confirm the licence-compatibility questions with a lawyer before the first public release: CC BY-SA vs ODbL in one database, and whether ML measurements from CC BY-SA imagery count as "adapted material".
