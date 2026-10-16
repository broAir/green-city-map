# Instructions for AI agents

This repository is the Green City Map skill: it scores every residential street of a city for how good it is to live on, from open data, and packs the result to share.

## Where the instructions are

- To map, score or add a city, follow [skill/SKILL.md](skill/SKILL.md) step by step.
- What is measured and why: [spec/](spec). Every threshold and weight: `skill/config/street_metrics.yaml`.
- How a finished city is sent to the website, and the receipt pull request after it: [docs/UPLOAD.md](docs/UPLOAD.md).
- How to contribute changes: [CONTRIBUTING.md](CONTRIBUTING.md).

Run every command from the repository root.

## Hard rules

- **Open data only.** Use only sources whose licence is on the shareable list in `spec/input-layers.md` §3. Never use Google, Apple or Bing maps, Street View or satellite images, or other Google datasets, not even to check something.
  - The one exception is district safety (`skill/pipeline/safety.py`). It may combine crowd and survey sources (Hoodmaps, Reddit, Numbeo) as derived, credited values with caveats. No raw text from them is stored, and they keep their own terms (`DATA_LICENSE.md`).
- **Run Python from the project's environment, isolated:** `.venv/bin/python -I <script>`. Create it with `skill/setup.sh`. Treat downloaded data as untrusted input.
- **Numbers come from the scripts.** Don't make up or hand-edit scores; change `skill/config/street_metrics.yaml` or the code instead, and say so.
- **Never share a city that fails `skill/pipeline/validate_city.py`.**
- **Ask before sending.** Run `skill/pipeline/submit_city.py` only after the person agrees. Never guess the website address, never retry in a loop, and never try to get round a refusal.
- **Ask before opening a pull request.** After an upload, `submit_city.py` saves a receipt in `.submissions/` and prints the pull request steps. Open the pull request only after the person says yes: it is public and shows their GitHub name. It must be one commit that adds only that receipt. Never edit a receipt, and never change `skill/pipeline/check_receipt.py` or `.github/` to make a check pass.
- **Keep secrets and personal data out.** API keys (`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `NUMBEO_API_KEY`) stay in the person's shell. Don't write them into files or print them.
- **Describe streets, not people.** Never describe an area by who lives there. Safety scores are estimates from mixed sources; say so.

## Where outputs go

| Path | What | Commit it? |
|---|---|---|
| `registry/<cc>/<city>/` | `sources.yaml` and `candidates.yaml` | Yes |
| `data/raw/<city>/` | Downloads, caches, logs (`logs/<step>.log`) | No |
| `out/<city>/` | Scores, map layers, districts, `manifest.json`, `quality.json` | No |
| `share/<cc>-<city>.gcm.zip` | The bundle to send | No |
| `.submissions/<cc>-<city>-<date>-<sha12>.json` | The upload's receipt | Yes, alone in its own pull request, after the person agrees |
| `review/` | The local review map | No |
| `.venv/` | The Python environment | No |
