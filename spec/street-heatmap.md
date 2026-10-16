# Green City Map: from metrics to a street heatmap

Version 0.2 (draft, 2026-10-10; v1 adds everyday access and the layers view). Builds on [metrics.md](metrics.md) and [input-layers.md](input-layers.md). The machine-readable form is `skill/config/street_metrics.yaml`.

This file defines how the metric spec becomes the thing people look at: **every residential street coloured by how good it is to live on**. It also defines the **v1 subset**, the part of the spec that runs today in any city from global data alone, and the **layers view**, which shows the evidence behind the scores.

## 1. The display unit: a street piece

- **Street piece.** An Overture transportation segment, split into pieces of at most 60 m. Short pieces keep the heatmap sharp at junctions and along long avenues.
- **Scored streets.** Road classes `primary`, `secondary`, `tertiary`, `residential`, `living_street`, `unclassified`, `pedestrian` and `service`, plus `footway` and `steps` where they are lanes between buildings (old-town lanes and stairways are often tagged this way).
  - `service` stays in because in post-Soviet microdistricts the roads between apartment blocks are tagged `service`; leaving them out would score those neighbourhoods only from their noisy avenues.
  - **Excluded:** tunnels; `service` subclasses `driveway`, `parking_aisle` and `drive_through`; separately mapped sidewalks and crossings; motorways and trunks.
  - **Context only:** motorways, trunks and railways are drawn but not scored. They are noise sources, not addresses.
- **Frontage rule.** A piece is scored only if at least one cross-section (§2) hits a building within 40 m on at least one side. Streets through parks, fields and industrial yards with no buildings are drawn grey.

## 2. The cross-section: how one street becomes numbers

Every 15 m along a piece (at least one per piece), the pipeline casts a **cross-section** perpendicular to the street. It sends one ray left and one right, each up to 60 m.

```
          building L                 street                 building R
   ████████████████|<---- dL ---->|   ·   |<---- dR ---->|████████████████
                   ^ facade                              ^ facade
     W = dL + dR (facade-to-facade)  ·  H = mean(hL, hR)  ·  view samples every 2.5 m
```

From each cross-section:

| Value | Definition | Spec metric |
|---|---|---|
| `W` | Facade-to-facade distance; "open" if either side has no hit within 60 m | M01 (street-facing facades) |
| `H/W` | Mean height of the two hit buildings ÷ W; one-sided streets are treated as open (H/W capped at 0.3) | M03 |
| `θ` | Obstruction angle seen from a first-floor window (eye height 4.5 m) looking across: `atan((H_opp − 4.5) / W)`, taking the worse of the two sides | M01 |
| `green_across` | Share of view samples between the two facades that are green: WorldCover tree class OR summer NDVI ≥ 0.5 (10 m tree classes alone miss most street trees) | M04 (street-facing proxy) |
| `ndvi_corridor` | Mean summer NDVI of the same samples (street trees, front gardens, verges) | M08 / M09 proxy |

The piece's value is the **median over its cross-sections**. That is robust to a single gap between buildings or a side alley.

**What this covers and what it misses.** A street-facing window looks across the street, which is exactly what the cross-section measures. A courtyard-facing window does not. Courtyard views need the per-facade version of M01 and M04 from metrics.md; that is v1 work, shown in the building cards rather than the street heatmap.

## 3. Area metrics: sampled at the piece midpoint

Some metrics describe the neighbourhood, not the street. They are computed once per city on the 10 m grid as focal (moving-window) means, then read at each piece's midpoint.

| Value | Definition | Spec metric |
|---|---|---|
| `canopy_300` | % tree canopy within 300 m | M06 |
| `sealed_300` | % sealed surface within 300 m | M07 |
| `ndvi_500` | Mean summer NDVI within 500 m, water excluded | M08 |
| `tourism` | Gaussian-smoothed density (σ = 300 m) of tourism places, in places per km² (absolute, so a city with few visitors has no touristy streets) | M18 |

## 4. Network metrics: walking distance from the piece

All network metrics share one **walk graph**: Overture segments (excluding motorways and trunks) joined at their connectors, with extra nodes every 50 m along long segments so that snapping a piece, a stop or a shop to the graph is never more than ~25 m off. Pieces, stops and amenities further than 150 m from any walkable segment are dropped.

`nature_ladder` (M12):
1. Qualifying natural areas are found and their entrances are snapped to graph nodes.
2. A multi-source shortest-path run is done per size tier.
3. Each piece reads the distance at its nearest node, plus the straight-line offset to that node.
4. The result is the number of tiers met (0–5) and the walking distance to the nearest area in each tier.

### Everyday access (v1)

**`transit_ai` (M20, PTAL-lite).**
- **Stops:** OSM platforms, stop positions, bus and tram stops, stations and metro entrances.
- **Lines:** OSM route relations, deduplicated by mode and line number, so both directions count once. Night lines and long-distance trains are dropped.
- **Stops no route lists:** a stop takes the lines of a listed stop with the same name within 80 m; otherwise it counts as one line of its own mode. Metro entrances take the lines of the station within 250 m.
- **Distances:** stop-to-street walking distances come from a limited Dijkstra run per stop node, out to the 960 m catchment.
- **Access index:** each line counts once, at its best stop: EDF per line, best line per mode in full, the others at half weight (metrics.md M20).
- **Frequencies** are defaults per mode (bus 6/h, tram 7.5/h, metro 15/h, rail 3/h). An OSM `interval=*` tag overrides them. A GTFS adapter (tier A) replaces them; the research phase found open feeds for Amsterdam, Lisbon (partly), Kraków (local-only licence) and Warsaw.

**`daily_needs` (M21).** One multi-source shortest-path run per category from every amenity in it. The piece keeps:
- the walking distance per category;
- the nearest place's index, so the card can name it and the map can draw a line to it.

The score is the mean of the six category band scores.

**Where the OSM data comes from.** A city or region `.osm.pbf` extract named in `sources.yaml` (`osm_extract.url`), read locally with pyosmium; the Overpass API otherwise. Public Overpass servers were overloaded on the test day (repeated 504s on route queries), while the five extracts downloaded and parsed in under 10 s each. Extracts are the recommended default.

## 5. Noise: the v0 proxy

No global noise map exists, so v0 uses the road-class proxy from input layer I10's fallback. Every value is labelled tier C.

- **Emission.** Each road or rail segment has a reference level `L_ref`: dB Lden at 10 m from its centreline, by class (see `street_metrics.yaml`). These values are **uncalibrated design guesses**. Calibrating them against the official noise maps in Amsterdam, Lisbon and Kraków is the first validation job.
- **The piece's own street.** `L_own = L_ref − 10·log10(d / 10)`, where `d = max(W/2, 3 m)`. That is the facade-line distance, with line-source attenuation of 3 dB per doubling of distance.
- **Other major roads and rail within 300 m** (tertiary and above, rail, tram):
  - `L_i = L_ref − 10·log10(d_i / 10) − S_i`
  - `S_i` = 15 dB if a building footprint blocks the straight line from the piece to the nearest point of that road, otherwise 0.
- **Total:** `L = 10·log10(Σ 10^(L_i/10))`. Scored against the WHO bands in metrics.md (M15).
- **`major_road_m`** (M16) is reported alongside: the distance to the nearest motorway, trunk, primary road or railway.

## 6. Scoring and colour

1. **Per-value score from 0 (bad) to 100 (good).**
   - Values with standard bands (`W`, `θ`, `H/W`, `canopy_300`, `nature_ladder`, `noise_lden`): piecewise-linear across the band edges defined in the YAML.
   - Values without standard bands (`sealed_300`, `ndvi_500`, `ndvi_corridor`, `green_across`): within-city percentile, flagged `relative`.
   - `tourism` uses fixed bands (since 2026-10-11): 0 places/km² → 100, 10 → 90, 25 → 75, 60 → 40, 120 → 10, 180+ → 0. For reference, Kraków Old Town is at 200–400 and Dnipro never goes above 40.
2. **Sub-indices**, as in metrics.md §3:
   - *View and openness*: `W`, `θ`, `H/W`, `green_across`
   - *Green and surface*: `canopy_300`, `sealed_300`, `ndvi_500`, `ndvi_corridor`
   - *Nature and space*: `nature_ladder`
   - *Noise and traffic*: `noise_lden`, `major_road_m`
   - *Crowds and tourism*: `tourism`
   - *Everyday access*: `transit_ai`, `daily_needs`
3. **Composite** is the weighted mean of the sub-indices, with weights set by the viewer and equal by default. The correlated-group cap from metrics.md §5 applies: the green and surface group carries at most 30% of the overall weight.
   - **Must-haves (deal-breakers):** any sub-index can be marked a must-have, and the composite is then capped at that sub-index's score plus a slack. There are three by default:
     - **Everyday access**, slack 20. This fixes the v0 finding that quiet, green, car-dependent outskirts outranked lively central streets.
     - **Tourism**, slack 10. A plain average let Kraków Old Town score 62, because quiet, transit and shops made up for the crowds; with the cap it scores 10.
     - **Noise**, slack 35, mild because noise is a road-type estimate. Zakopiańska, Kraków, a main road, dropped from 70 to 39.
   - Averaging six sub-scores pulls most streets toward the middle of the scale, which is why the colour scale must have no grey midpoint. The caps also spread the scores.
4. **Colour.** A diverging scale from the worst to the best score, applied identically in every city. Only band-scored values compare across cities; percentile-scored values are labelled "relative to this city".
5. **Every piece keeps its raw values and tiers**, so a click shows "W = 7.8 m, θ = 52°, noise ≈ 66 dB (tier C, road-class proxy)" rather than only a colour.

## 7. The layers view: the evidence, stacked

The score view answers "how good is this street for me?". The layers view answers "why?". It shows the raw factors as layers the reader can tick on and off. Each layer owns one mark type, so any combination stays readable:

| Layer | Mark | Encoding | Data |
|---|---|---|---|
| Greenery | Area wash under everything | Green opacity = summer greenness (WorldCover trees, or NDVI ramped 0.3 → 0.85), 20 m pixels | I05, I09 |
| Noise | Soft glow along roads and rail | Warm hue and glow width step up at 55 / 60 / 65 / 70 dB; glow width is in metres, so it grows when zooming in | I10 proxy |
| Sky blocked | The street line itself | Line weight and darkness step up with the obstruction angle θ (open, 10°, 25°, 35°, 45°+) | M01 |
| Crowds | People figures beside the street | Figure count per 100 m of street = tourism density / 140 places per km², capped at 10 | M18 / I14 |
| Public transport | Stop symbols | Ringed circle with a letter for metro, rail and ferry; square for tram; dot for bus. Station names when zoomed in | I19 |
| Everyday amenities | Small shaped markers | One hue; shape = category (circle food and drink, square groceries, plus pharmacy, diamond doctor, triangle school, star culture and sport); filter chips per category | I20 |

- **Colours.** The three point layers (transit, people, amenities) pass the colour-vision-deficiency separation check against each other. The two field layers (greenery and noise) rely on mark type, not hue: a wash versus a glow hugging roads.
- **Clicking a street** in either view opens its card and draws dashed lines to its nearest stops and nearest amenity of each category.

## 8. The v1 subset (runs anywhere, global data only)

| Spec metric | v0 street value | v0 source | Tier | Status in v0 |
|---|---|---|---|---|
| M01 facing distance | `W`, `θ` | Overture buildings + height ladder | B/C | **In** |
| M03 canyon ratio | `H/W` | Same | B/C | **In** |
| M04 trees in view | `green_across` (street-facing only) | WorldCover tree class + Sentinel-2 NDVI, 10 m | C | **In** (coarse) |
| M06 canopy cover | `canopy_300` | WorldCover tree class | C | **In** |
| M07 sealed share | `sealed_300` | WorldCover built-up ∩ NDVI < 0.3 | C | **In** |
| M08 greenness | `ndvi_500`, `ndvi_corridor` | Sentinel-2 L2A, summer 2025–26 median | B | **In** |
| M12 nature ladder | `nature_ladder` | Overture land use / land + NDVI filter + walk graph | C | **In** |
| M15 / M16 noise | `noise_lden`, `major_road_m` | Road-class proxy | C | **In** |
| M18 tourism | `tourism` | Overture places, tourism categories | C | **In** |
| M20 transit access | `transit_ai` (PTAL-lite) | OSM stops + route relations, default frequencies | C | **In** (v1) |
| M21 daily needs | `daily_needs` + six walking distances | OSM + Overture places (confidence ≥ 0.7) | B/C | **In** (v1) |
| M02 sky view, M05 winter sun | — | Need per-facade geometry | — | v1 |
| M09 / M10 / M19 imagery | — | Contributor scan mode | S | v1 |
| M11 heat, M13 / M14 population | — | Landsat; population grid | — | v1 (optional) |
| M17 traffic load | — | Folded into the noise proxy in v0 | — | v1 |

### The global height ladder in v0

For each building, take the first value available:
1. Overture `height`
2. Overture `num_floors × 3 m + 1 m`
3. The median height of known buildings within 150 m, if there are at least 5 of them (tier C, "imputed")
4. A city-wide median by footprint-size class (tier C)

The share of buildings at each step is reported per city. That share is the main quality signal for M01 and M03.

## 9. What the reviewer should check

The v0 map is a test of whether the **generic** path produces the truth a resident knows. The useful review questions are:
- **Streets that feel cramped:** are they red on `W`, `θ` and `H/W`?
- **Leafy streets:** are they green on `green_across` and `ndvi_corridor`? WorldCover's 10 m trees will miss single street trees.
- **Loud streets:** are they flagged on noise?
- **"I could walk to real nature from here":** does the nature ladder agree?
- **Wrong spots:** where is the map wrong, and which input caused it?
- **Car-dependent streets (v1):** do outskirts you'd need a car to live in now score low on Everyday access, with the overall score capped?
- **Transit you know:** is a street next to a frequent tram scored higher than one with a single bus? Missing informal minibus lines show up as low PTAL.

## 10. Changes in v1.1 (2026-10-10, after a code review)

A three-part review of the pipeline found rules that biased 3–30% of street pieces. They now work as follows:

- **Open streets.** A piece is "open" only when more than half of its cross-sections have nothing opposite within 60 m. Before, the median of an even number of sections could be infinite. Open pieces get θ = 0 and H/W at the open cap.
- **Tunnels.** A segment is a tunnel only when its `is_tunnel` flag covers at least half of it (`between`). Short underpasses no longer drop whole streets.
- **Walk graph.** Each junction is placed by projecting it onto the segment geometry, part by part, instead of using the stored `at` fraction. Segments rebuilt from tiles can be multi-part and out of order.
- **Noise.** The piece's own road (same name, or the same unnamed segment) counts once, at the facade distance. Other roads are measured from no closer than the facade. Before, the next segment of the same avenue was added again (+1 to +8 dB).
- **Transit.** A stop that no OSM line lists takes the same-mode lines of listed stops within 150 m. It becomes an extra line only if no listed stop of that mode is within walking reach. Night lines are dropped by name as well as by number.
- **Amenities and tourism.** Overture places are matched on their own category only, never on ancestors in the taxonomy. Before, physiotherapists counted as doctors and churches as tourism.
- **Missing data.** Missing NDVI is unknown, not "not green" and not "sealed". A city with no places in a category, or no transit stops, gets "no data" there rather than zero.
- **Quality gates.** `validate_city.py` fails a city with under 90% satellite coverage of land, or with misaligned outputs. It warns under 30% measured building heights, on no transit lines, and on empty amenity categories. Only cities that pass can be packaged (`package_city.py`).
- **Release and seasons.** The Overture release is resolved at run time when the pinned one is gone. Leaf-on seasons that cross New Year (southern hemisphere) and all-year windows (tropics) are handled.

## 9. Extra scores: easy on foot, good for families, safe streets (2026-10-11)

These three answer the words people add to "best area to live in {city}": walkable, families, safe. They are computed per street piece by `skill/pipeline/extras.py` and summarised per district by `districts.py`.

They are **not** part of the overall score and are not coloured on the map. They are used to rank districts ("Best areas for families") and they ship in the data (`extras.json`). Weights and bands are in `street_metrics.yaml` under `extras`.

| Score | Parts (weight) | Data |
|---|---|---|
| Easy on foot | daily needs on foot (0.40) · street-junction density within 400 m (0.20) · speed of the street itself (0.25) · public transport (0.15) | existing metrics; OSM junctions of street-class ways; OSM `maxspeed`, else a default per road type |
| Good for families | school or kindergarten on foot (0.25) · playground within ~1.3× straight-line distance (0.20) · parks (0.15) · quiet (0.15) · traffic (0.15) · few tourists (0.10); capped at the tourism sub-score + 30 and noise + 40 | OSM `leisure=playground`; existing sub-indices |
| Safe streets (traffic and lighting) | traffic (0.55: own-street speed, distance to a major road, crossings within 150 m) · lighting (0.25) · people around: places within 100 m (0.20) | OSM `maxspeed`, `highway=crossing`, `lit`, `highway=street_lamp`; places from the amenity layer |

Notes:
- **Safe streets is not crime.** We have no crime data and do not guess it. A score inferred from the built environment would be guesswork and would stigmatise poorer areas.
- **Lighting** counts only where OSM records it: at least 30% of nearby road length is tagged, or a street lamp is mapped within 40 m. Elsewhere it is unknown and the weights renormalise. Coverage in the six test cities is 19–50% of street pieces.
- **Sidewalks** are left out: they're tagged on only 2–52% of roads.
- **Hills** are not counted yet. Lisbon on foot is overrated; a slope part from an open DEM (Copernicus GLO-30) is the next step.

Sanity check, with the user validating by eye:
- Families, worst: Kraków Stare Miasto 35, Lisbon Santa Maria Maior 30, Amsterdam Centrum 39.
- Families, best: Mistrzejowice 94, Ajuda 90, Amsterdam Noord 90.
- On foot, best: the old towns (about 90). Lowest: outer districts (Wawer 56, Nowa Huta 49) and Amsterdam's port (Westpoort 30).

