# Green City Map: metric specification

Version 0.1 (draft, 2026-10-09). Companion file: [input-layers.md](input-layers.md).

This file defines **what** Green City Map measures. Every formula works the same in any city. Where the data comes from is defined separately, so the same metric can run on Barcelona's sources and on Amsterdam's.

## 1. How the spec fits together

```
metric (this file)          input layer (input-layers.md)        city source manifest (registry)
---------------------       -----------------------------        -------------------------------
M01 facing distance  ───►   I01 building footprints     ───►    barcelona: <dataset X>, tier A
                            I02 building heights        ───►    barcelona: <dataset Y>, tier A
                                                                 nairobi:   <global fallback>, tier B
```

- **Metric:** a fixed, city-agnostic formula with a unit, a spatial level and bands. It names the abstract input layers it needs.
- **Input layer:** a data contract, such as "building heights, metres above ground, per building or ≤1 m raster". Each layer has a **global fallback** that works for any city, plus a list of better sources to look for.
- **City source manifest:** the record of which concrete dataset satisfies each input layer in one city. The skill's research phase produces it, and it is kept in a shared registry so the next contributor in that city reuses it.

## 2. Design rules

1. **Measure physical things; users set the weights.** No metric is a judgement of "quality" or "beauty". Vision-language models agree with people on visible objects but not on subjective appraisals ([Cart et al. 2025](https://arxiv.org/abs/2505.08163); [Mushkani 2025](https://arxiv.org/html/2509.14574v2)).
2. **Every core metric has a global fallback path.** Any city gets a value on day one. A quality tier says how far to trust it.
3. **Use published standards for thresholds** (WHO, EN 17037, Natural England, national building codes). Where none exist, show within-city percentiles and label them as non-comparable across cities.
4. **Deterministic code computes the numbers**, with pinned code, model and data versions. A language model may orchestrate, research and explain, but its free-form output is never a measurement.
5. **Compute at the finest natural unit and aggregate up.** That unit is a facade sample, building, street segment or image point. The join key is H3 resolution 12. The heatmap is shown at resolutions 9–10 and aggregated over residential floor area, so empty office blocks don't dominate.
6. **Missing data shows as "no data", never as zero.** The composite renormalises over available metrics and displays its coverage.

### Quality tiers (stored with every value)

| Tier | Meaning | Examples |
|---|---|---|
| **A** | Authoritative local data, measured | LiDAR-derived heights, official strategic noise map, municipal tree inventory |
| **B** | Regional or global data at adequate resolution | EU Copernicus 10 m layers, Meta/WRI 1 m canopy height, EUBUCCO/Overture heights |
| **C** | Proxy or coarse | OSM floor counts × 3 m, 100 m grids, noise estimated from road class |
| **S** | Street-imagery scan by contributors | Green View Index; confidence comes from image coverage and agreement between contributors |
| — | Not computable | Required input missing |

A metric's tier is the lowest tier among its required inputs.

## 3. Catalogue

The metrics are grouped into six sub-indices, one per pain point. Everyday access was added in v1 (2026-10-10): without it, quiet green outskirts outscored central streets that people actually choose to live on. **Core** metrics ship in v1. **Optional** ones are computed when inputs exist. **Experimental** ones are computed but kept out of the default composite.

| ID | Slug | Metric | Level | Required inputs | Status |
|---|---|---|---|---|---|
| **View and openness** | | | | | |
| M01 | `view.facing_distance` | Facing distance and obstruction angle | Facade → building | I01, I02 | Core |
| M02 | `view.sky_view_factor` | Sky view factor | Street point, facade | I02 | Optional |
| M03 | `view.canyon_ratio` | Street canyon height-to-width ratio (H/W) | Street segment | I01, I02, I03 | Core |
| M04 | `view.trees_in_view` | Trees visible from the facade | Facade → building | I01, I05 or I06 | Core |
| M05 | `view.winter_sun` | Direct sun on the facade in winter | Facade → building | I01, I02 | Optional |
| **Green and surface** | | | | | |
| M06 | `green.canopy_cover` | Neighbourhood tree canopy % | Building buffer, cell | I05 | Core |
| M07 | `green.sealed_share` | Sealed ("concrete") surface % | Building buffer, cell | I08 | Core |
| M08 | `green.ndvi` | Neighbourhood greenness (NDVI) | Building buffer, cell | I09 | Core |
| M09 | `green.street_gvi` | Street Green View Index | Image → segment | I16 | Core (scan) |
| M10 | `green.street_hardness` | Street soft/hard ratio and sky share | Image → segment | I16 | Core (scan) |
| M11 | `green.summer_heat` | Summer surface heat anomaly | Cell | I17 | Optional |
| **Nature and space** | | | | | |
| M12 | `nature.access_ladder` | Walking access to natural areas, by size | Building | I03, I07, I09 | Core |
| M13 | `nature.green_per_capita` | Accessible green space per 1,000 residents | Building | I03, I07, I12 | Optional |
| M14 | `space.resident_density` | Residents per hectare | Cell | I12 | Optional (user sets direction) |
| **Noise and traffic** | | | | | |
| M15 | `noise.facade_exposure` | Facade noise with quiet-side credit | Facade → building | I01, I10 | Core |
| M16 | `noise.major_road_rail` | Distance to major roads and railways | Facade → building | I01, I04 | Core |
| M17 | `traffic.load` | Traffic load near home | Building | I04 | Optional |
| **Crowds and tourism** | | | | | |
| M18 | `crowd.tourism_pressure` | Tourism pressure index | Cell | I14 | Core |
| M19 | `crowd.street_crowding` | Pedestrian crowding on the street | Image → segment | I16 | Experimental |
| **Everyday access** | | | | | |
| M20 | `access.transit` | Public-transport access (PTAL-lite) | Street piece, building | I03, I19 | Core |
| M21 | `access.daily_needs` | Daily needs within walking distance | Street piece, building | I03, I20 | Core |

**Derived headline: "View from your windows" (V).** V combines M01 (distance and obstruction), M04 (trees in view) and the facade sky share from M02, using user-adjustable weights. This is the per-building score that no existing product publishes.

## 4. Metric cards

Notation: `D` is distance in m and `θ` is elevation angle. `h_eye = 1.5 m + 3 m × floor` is eye height on a floor, counting the ground floor as 0. "Facade samples" are points every 2–3 m along each exterior wall. Walls that touch another footprint within 0.5 m are treated as **party walls** and excluded.

### M01 `view.facing_distance`: facing distance and obstruction angle (core)

- **Answers:** How far away is the building opposite my windows, and how much sky does it block?
- **Unit and level:** metres (D) and degrees (θ). Computed per facade sample, then summarised per building and per cell.
- **Method:** From each facade sample, cast a ray along the outward wall normal, capped at 50 m (the momepy convention). `D` is the distance to the first building hit, or "> 50 m" if there is none. `θ = atan((H_opp − h_eye) / D)`, computed for the ground floor and for a mid floor (`floor = ⌊levels / 2⌋`). Per building, report:
  - the median D
  - the best-facade D (one open side matters to residents)
  - the share of samples with D < 10 m
  - the median θ at each eye height
- **Inputs:** I01 footprints (required). I02 heights (required for θ; D alone works without them). I18 terrain (optional, for sloped cities).
- **Bands:**
  - D: < 6 m is below the EN 17037 minimum outside-view distance; 6–20 m is minimum; 20–50 m is medium; ≥ 50 m is high ([EN 17037 summary](https://arxiv.org/pdf/2109.11037)).
  - θ: ≤ 25° passes the BRE daylight screen ([Right of Light](https://www.right-of-light.co.uk/fact-sheets/fact-sheet-1-25-and-45-degree-rules-of-thumbfact-sheets/)); > 45° breaks the common 45° envelope.
  - The skill also records the city's own legal facade-separation rule, if it finds one, as context. Portugal's RGEU, for example, requires 10 m between facades with habitable-room windows.
- **Tier:** that of I02 for θ; that of I01 for D.
- **Pitfalls:**
  - Window positions are unknown, so every exterior wall is assumed to have windows.
  - Merged footprints hide courtyards.
  - Tree-lined facing distances are handled by M04, not here.

### M02 `view.sky_view_factor`: sky view factor (optional)

- **Answers:** How much open sky is visible from the street and from the facade?
- **Unit and level:** 0–1. Computed at street-centreline points every 10 m and at facade samples.
- **Method:** Sample the horizon elevation β in N ≥ 36 directions against building heights, then compute `SVF = (1/N) Σ cos²β`. Report two variants: buildings only, and buildings plus canopy from I05. An imagery alternative reprojects panoramas to a fisheye view and counts sky pixels; Hong Kong work found R² > 0.95 against fisheye field photos ([Gong et al.](https://www.cuhk.edu.hk/research/SED/publication/ICUC10_Abstract_Gong.pdf)). Raster tooling: UMEP.
- **Inputs:** I02 (required), I01, I05 (optional), I16 (optional imagery variant).
- **Bands:** No validated "cramped" cut-off exists, so this is shown as a within-city percentile.
- **Tier:** that of I02, or S for the imagery variant.

### M03 `view.canyon_ratio`: street canyon H/W (core)

- **Answers:** Is my street a narrow corridor between tall walls?
- **Unit and level:** a ratio, per street segment.
- **Method:** Take cross-sections every 10 m, 50 m long on each side, as in `momepy.street_profile`. For each section, W is the distance between the first building hits on the left and right; H is the mean height of those buildings. If one side has no hit, mark the section "open". The segment value is the median H/W. Pedestrian-only segments (OSM `highway=footway|pedestrian|steps`) are kept but flagged.
- **Inputs:** I01, I02, I03 (street centrelines).
- **Bands:** ≤ 0.5 open; 0.5–1 comfortable; 1–2 enclosed; > 2 cramped. Perceived enclosure rises and pleasantness falls from H/W 0.5 to 4 ([Yilmaz et al. 2025](https://www.sciencedirect.com/science/article/pii/S0360132325003336)), and 0.5–1 was best for stress recovery ([IBE 2025](https://doi.org/10.1177/1420326X251327543)). Visible trees and sky reduce perceived oppressiveness ([Asgarzadeh et al. 2014](https://www.sciencedirect.com/science/article/abs/pii/S0169204614001741)), so the composite gives M04 and M02 a credit against this metric.
- **Tier:** that of I02. **Evidence:** medium (small VR samples).

### M04 `view.trees_in_view`: trees visible from the facade (core)

- **Answers:** Will I see trees from my window?
- **Unit and level:** count of distinct trees, plus the share of view rays that hit canopy. Computed per facade, then per building.
- **Method:** From facade samples every 5 m, cast a ±60° fan of rays out to 30 m, stopped by buildings. Count the distinct trees hit, using I06 tree points or local maxima of ≥ 3 m in I05 canopy height. A building passes the "3 trees" test if any of its facades sees ≥ 3 ([Croeser et al. 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11576734/)). Later versions add floor-dependent rays that pass over lower buildings.
- **Inputs:** I01 (required); I05 or I06 (one required); I02 (optional).
- **Bands:** ≥ 3 trees is the "3" of the 3-30-300 rule. It is a communication target, not a dose-response threshold ([Konijnendijk](https://pmc.ncbi.nlm.nih.gov/articles/PMC9415244/)). Nature window views are associated with better health at r ≈ 0.15–0.25 ([Soga & Gaston 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12352305/)).
- **Tier:** A with a municipal tree inventory plus LiDAR canopy; B with Meta/WRI CHMv2; C with 10 m tree cover only.

### M05 `view.winter_sun`: direct sun on the facade (optional)

- **Answers:** Does direct sun reach my windows in winter?
- **Unit and level:** hours of direct sun per facade sample, at ground floor and mid floor, on 21 Dec (21 Jun in the southern hemisphere) and at the equinox.
- **Method:** Compute sun positions every 15 min for the city's latitude. Cast a ray toward the sun against building heights and terrain, and count unobstructed time while the sun is within 90° of the facade normal.
- **Inputs:** I01, I02 (required); I18 (optional).
- **Bands:** EN 17037's sunlight-exposure levels (1.5 h minimum, 3 h medium, 4 h high, on a date between 1 Feb and 21 Mar) are the reference. *These levels were not re-verified in this research round.*
- **Tier:** that of I02.

### M06 `green.canopy_cover`: neighbourhood canopy % (core)

- **Answers:** How leafy is the area around my home?
- **Unit and level:** % of land with canopy ≥ 3 m within a 300 m buffer of each residential building, and per cell.
- **Inputs:** I05.
- **Bands:** 30% is the "30" of 3-30-300; about 40% is cited for substantial cooling ([Konijnendijk](https://pmc.ncbi.nlm.nih.gov/articles/PMC9415244/)). The 30% figure rests mainly on Australian observational studies.
- **Tier:** A with LiDAR vegetation; B with CHMv2, which has no published urban validation yet; C with 10 m tree cover.

### M07 `green.sealed_share`: sealed surface % (core)

- **Answers:** How much of the ground around me is buildings, asphalt and paving?
- **Unit and level:** % sealed within a 300 m buffer, and per cell.
- **Inputs:** I08.
- **Bands:** None evidence-based; shown as absolute % plus a within-city percentile.
- **Tier:** that of I08.
- **Note:** Correlated with M06, M08 and M11; see the weight cap in §5.

### M08 `green.ndvi`: neighbourhood greenness (core)

- **Answers:** How green is the surroundings overall, including grass, gardens and courtyards that street views miss?
- **Unit and level:** mean summer NDVI within 500 m of each building, and per cell.
- **Method:** Cloud-masked median of Sentinel-2 L2A over the local leaf-on season, using 3 recent years.
- **Inputs:** I09 (available globally).
- **Bands:** +0.1 NDVI within 500 m is associated with about 4% lower premature mortality, the strongest health evidence of any greenery measure here ([Rojas-Rueda](https://repositori.upf.edu/items/225b7adc-3f7c-414b-8b4c-3fd5f94cc351)). Shown in absolute units plus a percentile.
- **Tier:** B everywhere.

### M09 `green.street_gvi`: street Green View Index (core, scan)

- **Answers:** How green does my street look at eye level?
- **Unit and level:** % vegetation pixels per image, then the length-weighted mean per street segment and per cell.
- **Method:**
  1. Reproject panoramas to a common virtual camera (for example 4 × 90° horizontal views at 0° pitch).
  2. Run a pinned semantic-segmentation model trained on Mapillary Vistas.
  3. Use leaf-on months only; the season comes from latitude and the hemisphere.
  4. Publish cell aggregates only. Re-photographing an unchanged street shifts scores by about two-thirds of a typical between-street difference ([Tan 2026](https://arxiv.org/abs/2609.00649)).
- **Inputs:** I16.
- **Bands:** Treepedia city reference values are Paris 8.8%, Amsterdam 20.6% and Singapore 29.3%. Indicative bands: < 10% low, 10–20% moderate, 20–30% good (inference, not a standard).
- **Tier:** S.
- **Note:** Correlates only weakly to moderately with satellite greenness (r ≈ 0–0.5). That is why it is a separate metric and not a substitute for M08.

### M10 `green.street_hardness`: soft/hard ratio and sky share (core, scan)

- **Answers:** Is my street mostly concrete and walls, or does it have soil, plants and sky?
- **Unit and level:** a ratio and a %, per image, then per segment and per cell.
- **Method:** Using the same segmentation as M09:
  - `soft/hard = (vegetation + terrain + water) ÷ (road + sidewalk + building + wall + fence + vehicles)`, with sky excluded.
  - Sky share is reported alongside.
- **Inputs:** I16.
- **Bands:** within-city percentile.
- **Tier:** S.

### M11 `green.summer_heat`: summer surface heat anomaly (optional)

- **Answers:** Is my area a heat island in summer?
- **Unit and level:** °C above the city's median land surface temperature, per cell.
- **Method:** Median of clear-sky summer Landsat 8/9 surface-temperature scenes over 3 years. The thermal band is ~100 m native, resampled to 30 m, so this is shown per cell only.
- **Inputs:** I17.
- **Tier:** B. **Note:** part of the correlated group in §5.

### M12 `nature.access_ladder`: walking access to natural areas (core)

- **Answers:** Can I walk out of my house to real, unbuilt nature, and how big is it?
- **Unit and level:** the number of tiers met (0–5) per building, plus the walking distance to the nearest qualifying area in each tier.
- **Method:**
  1. **Qualifying areas** are polygons from I07 that are natural or semi-natural: woods, scrub, grassland, wetland, beach, parks, or accessible shoreline. They must also have:
     - summer NDVI ≥ ~0.5 (I09; beaches and water excepted)
     - a sealed share ≤ 15–20% (I08)
     - footpaths inside or public access
  2. **Exclusions:** squares, verges, sports pitches and private land.
  3. **Distance** is walking-network distance (I03) from building entrances, or footprint centroids as a fallback, to area entrances, meaning the points where paths enter the area.
- **Inputs:** I03, I07, I09 (required); I08 (optional filter).
- **Bands:** the Natural England ladder ([AGS](https://designatedsites.naturalengland.org.uk/GreenInfrastructure/downloads/Accessible%20Greenspace%20Standard%20User%20Guide.pdf)): **2 ha within 300 m, 10 ha within 1 km, 20 ha within 2 km, 100 ha within 5 km, 500 ha within 10 km**. The WHO baseline is ≥ 0.5–1 ha within 300 m ([ISGlobal](https://isglobalranking.org/faq-items/what-are-the-who-guidelines-on-green-space/)). The 20 ha and 100 ha tiers capture "there's nowhere to walk that isn't city".
- **Tier:** that of I07; the naturalness filter is untested, so it is flagged.
- **Pitfalls:** Validate street-graph connectivity first. One Amsterdam study fell back to straight-line distance because the network was inconsistent.

### M13 `nature.green_per_capita`: green space per 1,000 residents (optional)

- **Answers:** Will the nearby parks be packed?
- **Unit and level:** hectares of qualifying area (as in M12) within a 10-minute walk, divided by residents in the same catchment × 1,000. Per building.
- **Inputs:** I03, I07, I12.
- **Bands:** ≥ 3 ha per 1,000 residents (Natural England capacity target).
- **Tier:** the lowest of I07 and I12.

### M14 `space.resident_density`: residents per hectare (optional; user sets the direction)

- **Answers:** How densely populated is it here?
- **Unit and level:** residents per ha, per cell.
- **Inputs:** I12.
- **Scoring:** This is a *preference* metric: some users want density, others want space. The user chooses which direction scores well, and it defaults to excluded.

### M15 `noise.facade_exposure`: facade noise with quiet-side credit (core)

- **Answers:** How loud is it at my windows, day and night, and is there a quiet side to sleep on?
- **Unit and level:** dB Lden and dB Lnight, per facade sample and then per building, converted to % highly annoyed.
- **Method:**
  1. Sample I10 2–3 m outside each facade every 5 m. Combine sources such as road and rail with `L = 10·log10(Σ 10^(Li/10))`.
  2. Building exposure is the loudest facade.
  3. `quiet_side = loudest − quietest facade`. If it is ≥ 10 dB, subtract 5 dB from the effective exposure ([de Kluizenaar](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3717735/)).
  4. Convert with the WHO annoyance curve `%HA = 78.9270 − 3.1162·Lden + 0.0342·Lden²` ([Guski et al.](https://pubmed.ncbi.nlm.nih.gov/29292769/)).
- **Inputs:** I01, I10.
- **Bands:** WHO recommends road traffic noise below **53 dB Lden and 45 dB Lnight** ([WHO](https://cdn.who.int/media/docs/default-source/who-compendium-on-health-and-environment/who_compendium_noise_01042022.pdf)). Above 60 dB is high and above 65 dB very high. At 53 dB about 10% of residents are highly annoyed; at 65 dB about 21%.
- **Tier:** that of I10.
- **Pitfalls:**
  - Official maps are computed at a single height (typically 4 m), so upper floors are approximate.
  - EU strategic maps stop at 55 dB. Below that, use the I10 fallback model rather than reading "no contour" as quiet.

### M16 `noise.major_road_rail`: distance to major roads and railways (core)

- **Answers:** Is there a big road or railway right outside?
- **Unit and level:** metres from each facade to the nearest motorway, trunk or primary road, railway or tramway, with a flag for whether a building blocks the line of sight. Per building.
- **Inputs:** I01, I04 (available globally from OSM).
- **Bands:** flag a facade with an unshielded major road within ~30 m. This rule is deliberately simple and globally available, and it complements M15 where noise maps are missing or stop at 55 dB.
- **Tier:** B (OSM road classes are reliable for major roads).

### M17 `traffic.load`: traffic load near home (optional)

- **Answers:** How much traffic passes near me? This is volume, not jams.
- **Unit and level:** a relative index per building.
- **Method:** Distance-weighted sum over nearby road segments of a capacity class (from highway class, lanes and speed) times a through-traffic score (network betweenness). Calibrate on I11 counts where they exist.
- **Inputs:** I04 (required); I11 (optional).
- **Bands:** within-city percentile only.
- **Tier:** C, or B when calibrated.
- **Note:** No open street-segment congestion data exists anywhere; Uber Movement has been decommissioned.

### M18 `crowd.tourism_pressure`: tourism pressure index (core)

- **Answers:** Is this a tourist hotspot where streets and housing are taken over by visitors?
- **Unit and level:** within-city percentile per cell.
- **Method:** Sum of standardised components, each smoothed over ≥ 300 m:
  - attraction gravity: OSM `tourism=*` attractions weighted by Wikipedia pageviews, decaying over ~400 m
  - visitor-photo density: distinct Wikimedia Commons uploaders per cell
  - hotel and hostel density
  - entire-home short-term rentals per 100 dwellings, when I13 exists
- **Inputs:** I14 (required); I13, I12 (optional).
- **Bands:** No dose-response evidence exists, so it is shown as a relative percentile only.
- **Tier:** C with I14 alone; B with an official short-term-rental registry.

### M19 `crowd.street_crowding`: pedestrian crowding (experimental)

- **Answers:** Are the sidewalks packed?
- **Unit and level:** people per m² of visible sidewalk, per image, then per segment.
- **Method:** Count detected people per image, divide by the sidewalk area segmented from the same image, and normalise by capture time.
- **Inputs:** I16 (required); I15 (optional, for calibration).
- **Bands:** Fruin level-of-service bands are a reference for flow comfort, not for perceived crowding. Kept out of the default composite because capture times are biased.
- **Tier:** S.

### M20 `access.transit`: public-transport access, PTAL-lite (core)

- **Answers:** Can I get around without a car from here, and how good is the service?
- **Unit and level:** access index (AI) and PTAL band (0, 1a … 6b). Per street piece; per building later.
- **Method:** After TfL's Public Transport Accessibility Level ([Assessing transport connectivity in London, 2015](https://content.tfl.gov.uk/connectivity-assessment-guide.pdf)).
  - For every stop within the walk catchment (640 m for bus and trolleybus, 960 m for tram, metro, rail and ferry), on the walk graph:
    - total access time `TAT = walk time + 0.5 × 60 / frequency + reliability allowance` (2 min bus, 0.75 min rail-based)
    - equivalent doorstep frequency `EDF = 30 / TAT`
  - Each line counts once, at its best stop.
  - Per mode: `AI_mode = max EDF + 0.5 × Σ other EDFs`; `AI = Σ AI_mode`.
- **Frequency:**
  - Tier A uses departures per hour from a static GTFS feed, weekday 07:00–19:00.
  - The global baseline (tier C) uses OSM route relations, with a default frequency per mode, overridden by the route's `interval=*` tag where present. Stops that no route lists count as one line.
- **Inputs:** I03 (required), I19 (required).
- **Bands:** PTAL band edges 2.5 / 5 / 10 / 15 / 20 / 25 / 40, scored 15 / 35 / 55 / 70 / 82 / 90 / 100. Also reported: UN SDG 11.2.1 "convenient access" (a stop within 500 m, or a rapid-transit station within 1 km).
- **Tier:** C with OSM lines and default frequencies; B with OSM `interval` tags on most lines; A with GTFS.
- **Pitfalls:** OSM route coverage varies. Informal minibus networks (marshrutky) are often mapped as stops only. Night lines and long-distance trains are excluded.

### M21 `access.daily_needs`: daily needs within walking distance (core)

- **Answers:** Can I buy food, have a coffee, see a doctor and take children to school on foot?
- **Unit and level:** walking metres to the nearest place in each of six categories, plus a 0–100 score. Per street piece.
- **Method:** Run a multi-source shortest path on the walk graph from every place in the category. The categories are:
  - groceries
  - cafés, bars and restaurants
  - pharmacy
  - doctor or clinic
  - school or kindergarten
  - library, cinema, gym or culture

  Each distance is scored on a piecewise-linear target band; the score is the mean over categories. The card also shows how many categories are within 800 m (a 10-minute walk).
- **Inputs:** I03 (required), I20 (required).
- **Bands:** design targets in the 15-minute-city family (Moreno et al. 2021), not a published standard. For example, groceries score 100 at ≤ 300 m, 75 at 600 m, 40 at 1 km and 0 at 2.4 km.
- **Tier:** B with OSM and Overture combined in well-mapped cities; C where both are sparse.
- **Pitfalls:**
  - Overture places include closed businesses, so only confidence ≥ 0.7 is used, and only where OSM has nothing of the same category within 40 m.
  - Opening hours and price level are ignored.

## 5. Scoring and the composite

1. **Per-metric score (0–100).** For metrics with standard bands (M01, M03, M04, M06, M12, M13, M15), map piecewise-linearly across the band edges. For the rest, use the within-city percentile, marked `relative`; these are never used for cross-city comparison.
2. **Sub-index scores** are weighted means of their metric scores. **The overall score** is a weighted mean of the sub-indices. Users set all weights; the defaults are equal.
3. **Must-haves.** The user can mark any sub-index as a must-have. The overall score then cannot exceed that sub-index's score by more than a slack (20 points by default). Everyday access is a must-have by default: a quiet, green street you can only leave by car is not a good place to live for most people.
4. **Correlated-group cap.** M06, M07, M08, M09 and M11 overlap strongly, so together they may carry at most 30% (configurable) of the overall weight. Otherwise "concrete" counts three times.
5. **Missing metrics.** Renormalise the weights over the metrics present and show coverage (for example "14 of 17 core metrics").
6. **Badges.** Every displayed value carries its tier (A/B/C/S), data vintage and source. All of these come from the city source manifest.
7. **Search.** Every building and H3 cell has a stable ID: Overture GERS where available, otherwise a footprint hash. Address search resolves to a building card showing all metric values, then to its cell.

## 6. Considered and left out (for now)

- **Air quality:** CAMS (~10 km) and EEA maps (~1 km) are too coarse to tell streets apart. A cell-level context layer can be added later.
- **Light pollution:** VIIRS (~500 m) is coarse, and the main sky-brightness atlas is non-commercial.
- **Amenities / 15-minute access:** left out in v0 as well covered by other tools. *Brought in for v1 as M20 and M21* after review: without them, car-dependent outskirts ranked above central streets.
- **"Beauty", "safety" and other perception scores:** vision-language models and Place Pulse-style models are unreliable here and transfer poorly across cultures (see design rule 1).
- **Per-window views from photos:** user-submitted window photos would be the gold standard for "View from your windows", but they raise privacy (GDPR) issues. This is a v2 candidate.
