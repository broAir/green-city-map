# Contributing to Green City Map

Thank you for helping. There are four ways to contribute:

1. [Add a city](#add-a-city)
2. [Improve a city's sources](#improve-a-citys-sources)
3. [Report a score that looks wrong](#report-a-score-that-looks-wrong)
4. [Change the code or the method](#change-the-code-or-the-method)

## Ground rules

- **Open data only.** Every source must be on the shareable list in [spec/input-layers.md](spec/input-layers.md#3-licence-policy) §3. Never use Google, Apple or Bing maps, Street View or satellite images, or other Google datasets, not even to check something. The one exception is district safety, which may use crowd and survey sources (Hoodmaps, Reddit, Numbeo) as derived, credited values; see [DATA_LICENSE.md](DATA_LICENSE.md#district-safety-the-one-exception).
- **Describe streets, not people.** Talk about what the map measures: noise, trees, tourists, shops on foot. Never describe an area by who lives there.
- **Be honest about safety.** Safety scores are estimates from sources that can be unreliable. They never change the map colours or the main score.
- **Nothing personal.** Don't put your home address, API keys or other private details in issues, pull requests or bundles.

## Add a city

The easiest way is to ask an AI agent, such as Claude Code:

> Install the Green City Map skill from github.com/broAir/green-city-map and map <your city>.

Or follow [skill/SKILL.md](skill/SKILL.md) yourself. The [README](README.md#run-it-yourself) has the commands.

When the city has passed `validate_city.py` and you have looked at the map:

1. Pack it: `.venv/bin/python -I skill/pipeline/package_city.py out/<city> share --credit "Your name"` (the credit is optional).
2. Send it to the website, once its address is announced in the README: `submit_city.py` asks you to approve the upload in your browser. See [docs/UPLOAD.md](docs/UPLOAD.md).
   - This is the only way a city reaches the map. Bundles attached to GitHub issues are never loaded, so please don't attach them. Until the address is announced, keep your bundle in `share/`.
3. After the upload, `submit_city.py` saves the website's receipt as `.submissions/<cc>-<city>-<date>-<sha256[:12]>.json` and prints the steps for a pull request. If you agree, open a pull request that adds only that file:
   - It must be one commit on top of the latest main. Don't edit the receipt, don't add any other file or commit, and don't merge main into the branch.
   - A GitHub check tests the receipt. The check and its checker come from the main branch, so changing them in the same pull request doesn't help.
   - The site owner checks your upload against the receipt, puts the city on the map, and then squash-merges the pull request.
   - The pull request is public and shows your GitHub name.
4. Also open a separate pull request with the city's `registry/<cc>/<city>/` folder (`sources.yaml`, and `candidates.yaml` if you researched local data), so the next person starts from your work.

Don't commit `data/`, `out/`, `share/` or `review/`: they are large and made by the scripts. Receipts are the only files that go in `.submissions/`.

You can also just ask for a city with the [Add a city form](https://github.com/broAir/green-city-map/issues/new?template=city-request.yml), without running anything.

## Improve a city's sources

Each city has a folder in [registry/](registry):

- `sources.yaml` is what the run used. `resolve_city.py` writes it.
- `candidates.yaml` lists better local data: official noise maps, tree registers, 3D buildings or LiDAR heights, public-transport timetables (GTFS). See [registry/pl/krakow/candidates.yaml](registry/pl/krakow/candidates.yaml) for the format.

To add or fix a candidate:

1. Follow the research steps in [spec/input-layers.md](spec/input-layers.md#4-how-the-skill-resolves-sources-for-a-new-city) §4, Step 3. Steps 4 to 6 there are planned, not built yet: nothing checks sources automatically, so a maintainer checks them by hand.
2. For every source, record the licence and its class (shareable, local-only or never), with a link to where the licence is stated. If you can't find a licence, the source is local-only.
3. Say whether you checked that the download works (`verified: true`) or only found it in a catalogue.
4. Open a pull request that changes only that city's folder.

Today the scores use the worldwide sources, so every city is scored the same way. Candidates are kept for future adapters and shown on the map.

## Report a score that looks wrong

You know your streets better than any satellite. If a street or area looks wrong, open a [Wrong score issue](https://github.com/broAir/green-city-map/issues/new?template=wrong-score.yml). Say which street, what the map says, and what it is really like. A street name is enough; please don't share your own address.

Reports like these are how the method gets better. They lead to changes in `skill/config/street_metrics.yaml` or the code, never to hand-edited scores.

## Change the code or the method

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Keep every threshold and weight in `skill/config/street_metrics.yaml`, not in the scripts.
3. If the method changes, update the matching file in [spec/](spec) in the same pull request.
4. Run Python with `.venv/bin/python -I`. Re-run at least one city with `skill/run_city.sh <cc>/<city>` and check that `validate_city.py` still passes.
   - Run the tests too: `python3 -I -m unittest discover -s skill/tests`. They need only the standard library.
   - Changes to `skill/pipeline/check_receipt.py`, `.github/` or `.claude-plugin/` go in their own pull request, never together with a receipt.
   - Never add files that Claude Code loads or runs, such as `bin/`, `hooks/`, `.mcp.json`, `settings.json`, `agents/` or `commands/` inside `skill/`, or anything new in `.claude/`. The "Protected paths" check refuses them.
5. Write docs and messages in plain English with short sentences.

## Licences of contributions

By contributing, you agree that:

- your contributions to the code, spec and docs are under the [PolyForm Noncommercial License 1.0.0](LICENSE.md), like the rest of the code;
- you also grant broAir a perpetual, worldwide, royalty-free licence to use, change and relicense your contribution under any terms, including commercial ones. You keep the copyright in your work;
- city data you contribute (outputs, bundles) is under the [Open Database License 1.0](DATA_LICENSE.md), like OpenStreetMap.

Only contribute work you have the right to share under these terms.
