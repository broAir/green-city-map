---
name: green-city-map
description: "Score every residential street of a city 0-100 for how good it is to live on: light and open views, trees and green, parks and nature on foot, quiet (road and rail noise), few tourists, and daily needs without a car (shops, doctor, school, public transport). Open data only (OpenStreetMap, Overture Maps, Copernicus Sentinel-2, ESA WorldCover), never Google, Apple or Bing map services. Runs a local Python pipeline, adds district scores (on foot, families, safety with caveats), checks quality, builds a review map, and packs the city as a .gcm.zip bundle that a person approves before it is sent to the Green City Map website. Use when someone wants to map, scan, score or add a city or town to Green City Map, wants a street-level livability, walkability, green space or noise map of a city, wants street-by-street data on where to live in a city, or wants to improve a city's open data sources in registry/."
license: "PolyForm-Noncommercial-1.0.0 for the code; city outputs are ODbL-1.0. See LICENSE.md and DATA_LICENSE.md."
compatibility: "Needs git, bash, Python 3.12 or newer, about 600 MB for the Python environment, 150-450 MB of downloads per city, and internet access to Overture Maps (AWS S3), Microsoft Planetary Computer (Sentinel-2), ESA WorldCover and OpenStreetMap extracts (BBBike, Geofabrik)."
metadata:
  version: "0.2.0"
  repository: "https://github.com/broAir/green-city-map"
---

# Green City Map: map a city

This skill scores every residential street of a city from open data, then packs the result so it can be added to the Green City Map website. What is measured, and why, is in [spec/street-heatmap.md](../spec/street-heatmap.md) and [spec/metrics.md](../spec/metrics.md). Every threshold and weight lives in `skill/config/street_metrics.yaml`; never hard-code them in scripts.

## Before you start: get the whole repository

The skill needs the scripts, config and registry next to this file, not only this file.

- If the working folder has `skill/run_city.sh`, you are in the repository. Stay there.
- If this SKILL.md is a symbolic link into a clone (for example `~/.claude/skills/green-city-map` or `.claude/skills/green-city-map` pointing at a clone's `skill/` folder), go to that clone's root: the folder above the link's target, which holds `skill/run_city.sh`.
- Otherwise ask the person where to put the repository (it needs about 1 GB free), then clone it there and work inside it: `git clone https://github.com/broAir/green-city-map` and then `cd green-city-map`. Do this too if you were loaded as a plugin: don't run anything from a plugin's install folder.

All commands below run from the repository root, the folder that holds `skill/`, `spec/` and `registry/`.

## Hard rules

- **Open data only.** Use only data whose licence is on the shareable list in `spec/input-layers.md` §3. Never use Google Maps, Street View or Google satellite images, other Google datasets, Apple Maps or Bing, not even "just to check".
  - The one exception is district safety (`safety.py`). It may combine crowd and survey sources (Hoodmaps, Reddit, Numbeo) as derived, credited values with caveats. No raw text from them is stored, and they keep their own terms. See `spec/input-layers.md` §3 and `DATA_LICENSE.md`.
- **Numbers come from the scripts.** Never use your own estimates. You run the steps, research sources, and explain the results in plain words.
- **Isolate Python.** Run it from the project's environment with `-I`: `.venv/bin/python -I …`. Downloaded data is untrusted input.
- **Gate before sharing.** Never share a city that fails `validate_city.py`.
- **Describe streets, not people.** Talk about places by what the map measures (quiet, leafy, touristy, far from shops). Never describe an area by who lives there.
- **Keep secrets out.** Optional API keys go in the person's own shell environment. Never write them into files, print them, or put them in a bundle.

## Steps

1. **Set up**, once per machine: `skill/setup.sh`. It needs Python 3.12 or newer and about 600 MB for the environment in `.venv/`. Set `PYTHON=/path/to/python3.12` to choose the Python it uses.
   - On macOS the built-in `python3` is too old: suggest `brew install python@3.12` first.
   - On Debian or Ubuntu, the matching `venv` package (such as `python3.12-venv`) must be installed, or the environment can't be created.
2. **Resolve the city**: `.venv/bin/python -I skill/pipeline/resolve_city.py "<city>" <ISO country code>`.
   - It finds the boundary in Overture divisions and picks the metric map zone (UTM). It chooses the leaf-on months from the latitude, and the smallest OpenStreetMap extract that covers the city.
   - Read the candidates it prints. If the right one isn't chosen, run it again with `--id <division id>`. Then add `--write` to create `registry/<cc>/<city>/sources.yaml`.
   - Use the local spelling when the English one isn't found (Kraków, Warszawa, Lisboa). Use `--slug <folder name>` for a custom folder name.
   - If it warns that the extract is large (over about 800 MB), look for a smaller city or regional extract on BBBike, Geofabrik or openstreetmap.fr. Put that URL in `osm_extract.url` in `sources.yaml`.
3. **Research better local data** (optional, recommended). Follow `spec/input-layers.md` §4, Step 3 (research each gap), and write `candidates.yaml` next to `sources.yaml` (see `registry/pl/krakow/candidates.yaml` for the format). Record each source's licence class.
   - Write findings only to `candidates.yaml`. Don't change the layers in `sources.yaml`: they record what the pipeline uses, and editing them doesn't switch it to another source.
   - Nothing checks sources automatically yet (the only GitHub check is for upload receipts). Don't tell the person that anything checks their sources.
   - Look especially for:
     - a GTFS timetable (search the Mobility Database);
     - official noise maps;
     - 3D buildings or LiDAR heights;
     - tree registers.

   Today the scores use the worldwide sources, so every city is scored the same way. Candidates are recorded for later adapters and shown on the map.
4. **Run**: `skill/run_city.sh <cc>/<city>` (for example `skill/run_city.sh si/ljubljana`).
   - It runs these steps in order, and stops at the first one that fails:
     1. `overture`: boundary, buildings, streets and places from Overture Maps
     2. `rasters`: Sentinel-2 summer greenness and ESA WorldCover
     3. `osm`: public-transport stops and lines, and everyday places, from OpenStreetMap
     4. `streets`: street measurements and scores
     5. `basemap`: the map background
     6. `manifest`: sources, licences and quality numbers
     7. `lander`: the preview picture, street index and tour
     8. `extras`: extra scores per street (easy on foot, good for families, street safety)
     9. `districts`: district outlines, names and summaries
     10. `safety`: one safety score per district from several sources (see below)
     11. `validate`: the quality gates
   - The Overture and satellite downloads are cached; add `--refresh` to download again.
   - Logs are in `data/raw/<city>/logs/<step>.log`. Expect 5 to 10 minutes and 150 to 450 MB of downloads.
   - Very large boundaries (over about 120 × 120 km) are refused. Clip them to the urban area first.
   - The `safety` step works without any keys. These optional environment variables add sources; without them, those sources are left out and the others count more:
     - `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`: an approved Reddit API app, for posts on the city's subreddit that name a district (`REDDIT_USER_AGENT` is optional);
     - `NUMBEO_API_KEY`: a paid Numbeo API key, for Numbeo's city-wide safety survey.
5. **Check the quality report** (`out/<city>/quality.json`, also printed). Explain every warning to the person in plain words. The usual ones:
   - **Few measured building heights:** the views are then estimated from neighbouring buildings.
   - **No public-transport lines in OpenStreetMap:** common for informal minibus networks; public transport is then understated.
   - **An everyday category with no places**, such as no pharmacies mapped.
6. **Look at the map with the person**:
   - Build it: `.venv/bin/python -I skill/viewer/build_review.py skill/config/street_metrics.yaml out review "<label>" <city>`.
   - Serve it: `.venv/bin/python -I -m http.server 8000 --bind 127.0.0.1 --directory review`, then open <http://localhost:8000/>. Run the server in the background, and stop it when the person is done.
   - Ask them to check streets they know: a loud road, a leafy street, a touristy square, a car-dependent edge.
   - Note what looks wrong; don't adjust numbers by hand.
7. **Package and send it**:
   1. `.venv/bin/python -I skill/pipeline/package_city.py out/<city> share --credit "<name or @handle, optional>"` writes `share/<cc>-<city>.gcm.zip`: scores, measurements, sources and licences, and checksums. It contains nothing personal except the optional credit.
   2. Ask the person whether to send it now. If yes, run `.venv/bin/python -I skill/pipeline/submit_city.py share/<cc>-<city>.gcm.zip --site <website address>`. `GCM_SITE=<website address>` works instead of `--site`.
      - The website address is not set in the code yet. Use only the address announced in the README of github.com/broAir/green-city-map. If there is none, or the person doesn't know it, don't guess: tell the person to keep the bundle in `share/` and send it once the address is announced.
   3. It first asks the website whether the city is already up to date. If it is, it stops: tell the person nothing needed sending.
   4. Otherwise it prints a link and a short code. Tell the person to open the link, check the city name and size, and click "Yes, send it". The script waits up to 20 minutes, then uploads once with a one-time signed key.
   5. Relay the result in plain words:
      - "in the review queue": it was received and goes live after a check;
      - "The site refused" or "Upload failed": say why, and what to fix;
      - "Could not reach": check the address and the connection with the person;
      - "The answer from ... doesn't look right": nothing was sent; the address may be wrong. Check it against the README with the person;
      - "Not sent: expired", "Not sent: cancelled" or "The code expired": offer to start again;
      - "The upload went through, but the receipt from the website ...": nothing was saved. Don't open a pull request; suggest the person tells the site owner in an issue.
   6. After a good upload the script checks the website's receipt and saves it as `.submissions/<cc>-<city>-<date>-<sha256[:12]>.json`, then prints the steps for a pull request that adds only that file.
   7. **Ask the person before opening the pull request.** Say that it adds one small public file to github.com/broAir/green-city-map under their GitHub account, so their GitHub name and the credit (if any) become public. Never open it without a clear yes.
      - If they agree and `gh` is installed and signed in, run the printed commands as they are. They start a branch from the latest `main`, so the pull request is one commit that holds only the receipt.
      - Without `gh`, give them the printed browser steps (upload the file into `.submissions/` on GitHub).
      - If they say no, leave the receipt file where it is. The city still waits in the website's review queue.
   8. Never edit the receipt, never add other files or commits to that pull request, and never change `check_receipt.py` or the GitHub checks to make them pass. A GitHub check tests the receipt; if it fails, tell the person what it says.
   9. The site owner then checks the upload against the receipt, puts the city on the map, and merges the pull request.
   10. Never retry in a loop, and never try to get round a refusal.
   11. The website is the only way a city reaches the map. Never attach bundles to GitHub issues (they are never loaded), and never commit bundles to the repository.

   The full upload protocol, the receipt and the pull request are in [docs/UPLOAD.md](../docs/UPLOAD.md).

## Where things go

| Path | What it holds | Shared? |
|---|---|---|
| `registry/<cc>/<city>/` | `sources.yaml` (what the run used) and `candidates.yaml` (better local data found) | Yes, by pull request |
| `data/raw/<city>/` | Downloads, caches and logs | No, local only |
| `out/<city>/` | Scores, map layers, districts, safety, `manifest.json`, `quality.json` | Only inside a bundle |
| `share/<cc>-<city>.gcm.zip` | The bundle to send | Only through the website upload |
| `.submissions/<cc>-<city>-<date>-<sha256[:12]>.json` | The receipt for an upload | Yes, one file per pull request, only after the person agrees |
| `review/` | The local review map | No, local only |

## Known limits (tell the person)

- **Noise** is estimated from road type, distance and the buildings in between. It is uncalibrated.
- **Green across the street** uses 10 m satellite cells, so single street trees are missed.
- **Views** are measured only on street-facing windows.
- **Building heights** are estimated where Overture has no height or floor count. Dnipro and Ljubljana are the weak cases (about 8% measured).
- **Public transport** uses default frequencies per mode until a GTFS adapter exists, so a 5-minute tram and a 30-minute bus count alike.
- **Daily-needs distances** depend on how well OpenStreetMap and Overture cover shops and services. Sparse mapping makes a place look worse served than it is.
- **Extra scores** (easy on foot, good for families, street safety) are not part of the overall score and don't change the map colours.
  - **Street safety** means traffic, crossings, lighting and people around. It is not about crime. Lighting is only counted where OpenStreetMap records it; `extras.py` prints the coverage.
- **District safety** (`safety.py`) combines several sources, each kept visible with its own (i) note:
  - our street safety estimate;
  - police-recorded crime where a country publishes it openly (today the Netherlands and the United Kingdom);
  - the Hoodmaps crowd map, and Reddit posts if keys are set;
  - city-wide figures from Eurostat, and Numbeo if a key is set.

  Crowd sources are often blunt or joking and nobody checks them; recorded crime makes busy centres look worse. Tell the person which sources were used (the script prints each one's status), say that each can be unreliable, and never present the result as fact. It never changes the map colours or the main score.
- **Districts** come from the city's own subdivisions in OpenStreetMap (`skill/pipeline/districts.py` picks the level automatically).
  - If the extract cuts through a district at its edge, the outline is rebuilt from the pieces that are present.
  - If two such districts can't be told apart, they're named together ("Nowa Huta and Wzgórza Krzesławickie").
  - Tell the person which level was used and how many districts it found; the script prints both.
