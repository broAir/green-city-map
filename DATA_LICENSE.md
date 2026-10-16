# Licences: code and city data

This repository holds two kinds of things under two licences.

## Code: PolyForm Noncommercial 1.0.0

The skill (`skill/`), the method (`spec/`), the docs (`docs/`) and the source lists in `registry/` are under the PolyForm Noncommercial License 1.0.0, see [LICENSE.md](LICENSE.md).

- You may use, change and share them for any non-commercial purpose.
- This is a source-available licence, not an OSI "open source" licence, because it does not allow commercial use.
- The copyright holder can license the code for commercial use separately.

## City data: Open Database License 1.0

The city data the skill makes is under the Open Database License 1.0 (ODbL), <https://opendatacommons.org/licenses/odbl/1-0/>. That covers:

- the pipeline outputs in `out/<city>/`: street scores and measurements, map layers, districts, extra scores, summaries, `manifest.json` and `quality.json`;
- the `.gcm.zip` bundles made by `skill/pipeline/package_city.py`.

The one exception is the part of district safety that comes from crowd and survey sources. See [District safety](#district-safety-the-one-exception) below.

Why the ODbL: the scores are built from OpenStreetMap and Overture Maps data, which are under the ODbL. The ODbL requires databases made from them to stay under the ODbL. So the city data **cannot** be limited to non-commercial use. Anyone may use it, including commercially, as long as they:

- credit the sources (below);
- offer any changed or derived database they use publicly under the ODbL too.

The README pictures in `docs/images/` are drawn from the city data and carry the same credits.

## Required attribution

Anything that shows the city data must show:

- © OpenStreetMap contributors (ODbL)
- © Overture Maps Foundation, overturemaps.org
- Contains modified Copernicus Sentinel data (year of the scenes)
- ESA WorldCover 10 m 2021 v200, © ESA WorldCover project, CC BY 4.0

Overture's buildings, streets (transportation), base and divisions are under the ODbL. Its places are under each provider's licence. Overture also asks for these credits, which apply to the parts we use:

- Buildings: Esri Community Maps contributors (CC BY 4.0); Global ML Building Footprints, licensed by Microsoft under the ODbL; Google Open Buildings (CC BY 4.0); USGS 3D Elevation Program.
- Streets: Data from TomTom.
- Base (land, land use and water): Data from the Daylight Map Distribution.
- Divisions (the city boundary): geoBoundaries (CC BY 4.0); Esri Community Maps contributors (CC BY 4.0); Land Information New Zealand (CC BY 4.0).
- Places (names and positions of shops, cafés and other everyday places):
  - Data from Meta. Available under CDLA Permissive 2.0.
  - Data from Microsoft. Available under CDLA Permissive 2.0.
  - Copyright 2024 Foursquare Labs, Inc. All rights reserved. Available under Apache 2.0. Foursquare's NOTICE: <https://opensource.foursquare.com/places-notice-txt/>. Overture changed the data to its own schema; Green City Map filtered it and grouped it into categories.
  - Data from AllThePlaces. Available under CC0 1.0.
  - Data from PinMeTo, Krick, RenderSEO, DAC and BrightQuery. Available under CDLA Permissive 2.0.

The pipeline doesn't yet record which provider each building or place came from, so every provider of these themes is credited. Overture's list is at <https://docs.overturemaps.org/attribution/>.

Each city's `manifest.json` lists the exact sources, releases and dates used, and the same credit lines.

## District safety: the one exception

A city's `safety.json` (and the safety part of `districts.json`) combines several sources into one safety score per district:

- our own street safety estimate (part of the city data, ODbL);
- police-recorded crime where it is open: for example Politie via CBS in the Netherlands, and data.police.uk in the United Kingdom ("Contains public sector information licensed under the Open Government Licence v3.0");
- Eurostat regional figures (© European Union, CC BY 4.0);
- the Hoodmaps crowd map, Reddit posts and the Numbeo survey.

The last three are crowd and survey sources whose terms are not open. For them:

- only derived values are kept: one value per district or city, vote or author counts, and a credit line;
- no tags, posts or other text from them is stored in the outputs;
- these values are **not** offered under the ODbL; they stay under their sources' own terms;
- each carries a note saying it can be unreliable, and none changes the map colours or the main score.

`safety.json` names each source it used, with its link and credit line. Keep those credits with the safety scores wherever you show them. If you need data that is fully under the ODbL, leave out the `hoodmaps`, `reddit` and `numbeo` parts of the safety scores.

## Local sources

Better local data found by the research step is used only when its licence class is "shareable" ([spec/input-layers.md](spec/input-layers.md) §3). Apart from the district safety values described above, data under non-commercial or unclear terms never goes into a shared bundle.

This is not legal advice. If you plan anything beyond personal, non-commercial use, check the licences yourself.
