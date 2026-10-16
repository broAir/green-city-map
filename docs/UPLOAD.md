# Sending a city to Green City Map

When the skill has mapped a city, `skill/pipeline/package_city.py` packs it into one file, `share/<cc>-<city>.gcm.zip`. This page explains how that file gets to the Green City Map website, why a person has to approve every upload, and how each upload is then recorded in public: a small receipt file, added to this repository by a pull request.

Most people never need this page: the AI agent runs `submit_city.py` and tells you what to click. Read on if you want to know what happens, or if you are writing your own client.

## The short version

```sh
.venv/bin/python -I skill/pipeline/package_city.py out/<city> share --credit "Your name or @handle"   # credit is optional
.venv/bin/python -I skill/pipeline/submit_city.py share/<cc>-<city>.gcm.zip --site https://<website address>
```

1. The script tells the website which city it wants to send.
2. If the website already has that city with the same data, the script says so and stops. Nothing is sent.
3. Otherwise it prints a link and a short code, such as `KQ7X-4PMD`.
4. You open the link, check the city name and size, and click "Yes, send it". You can add a name to be credited.
5. The script uploads the file once. The city waits in a review queue.
6. The website answers with a receipt. The script checks it and saves it as `.submissions/<cc>-<city>-<date>-<sha256[:12]>.json`.
7. If you agree, a pull request adds that one file to this repository. The site owner checks the upload against the receipt, puts the city on the map and merges the pull request.

You have 20 minutes to click. If you don't, nothing is sent; run the script again for a new code.

## The website address

The website is https://greencitymap.city, and `submit_city.py` sends there by default. To send to another copy of the site, such as a local test server, give its address:

- `--site https://<website address>` on the command line, or
- `GCM_SITE=https://<website address>` in your environment.

Don't guess an address: the script sends the bundle to whatever address you give it.

## What is in the bundle

`package_city.py` refuses to pack a city that failed `validate_city.py`. The bundle holds:

- the street scores and measurements (`streets.json`, `details.json`, `layers.json`);
- the basemap pictures (`base_<row>_<col>.png`, `green.png`) and `basemap.json`;
- the preview, street index and tour for the home page (`preview.webp`, `street_index.json`, `tour.json`);
- the sources, licences, quality report and summaries (`manifest.json`, `quality.json`, `summary.json`, `stats.json`);
- where they exist: districts, extra scores, district safety and a per-street table (`districts.json`, `extras.json`, `safety.json`, `api_points.json`);
- `bundle.json`: the city, country, data release, scoring version, the optional credit, and a sha256 checksum for every file.

It holds no file paths from your computer and nothing about you, except the credit name if you gave one.

## Why a person has to approve

Anyone can run the skill, so the upload address is open to the internet. These rules keep junk out:

| Risk | What stops it |
|---|---|
| Scripts uploading with no person behind them | A person must open the link and click "Yes, send it" within 20 minutes. No click, no upload key. TVs and command-line tools use the same pattern to sign in (the OAuth "device flow"). |
| Sending a city that is already there | Before anything is sent, the client declares the city, data release, boundary and scoring version. If the website already has exactly that, it answers `409 up_to_date` and nothing is sent. At upload time the bundle's own release and scoring version must match what was declared. |
| Huge or repeated uploads | Size and checksum are declared first, and the upload must match both exactly. Each approval allows one upload. A city can have at most 2 uploads open or waiting. The review queue has a size limit. |
| Changed or replayed uploads | The upload is signed with a one-time key that is handed out only after approval. The signature covers the time (within 5 minutes) and the file's checksum. A used approval can't be used again. |

Every bundle that passes still waits for a check by the site owner before it goes on the map.

### What the website keeps

- The bundle, and the credit name if you gave one.
- The receipt: the upload id and the facts listed under [The receipt](#the-receipt).
- The IP address that started the upload, for rate limits and to stop abuse.

## The protocol

All requests and answers are JSON, except the upload itself. Every answer has a `status`; refusals also have an `error` that a person can read.

```
client                                   website                                person
  |  POST /api/v1/contrib/start  ------->  up to date? already waiting?
  |  {city, country, bundle_sha256,        queue full? city busy?
  |   bytes, overture_release, ...}
  |  <-- 201 {user_code, device_code, verify_url}
  |  prints: "Open <verify_url>, code KQ7X-4PMD"  ------------------------------>  opens the link
  |                                        GET /api/v1/contrib/session/<code>      sees city, size, release
  |                                        POST /api/v1/contrib/approve  <-------  "Yes, send it" (+ optional credit)
  |  POST /api/v1/contrib/poll  (every 5 s)
  |  <-- 200 {upload_key, upload_url}   (handed out once)
  |  PUT /api/v1/contrib/upload  ------->  size, checksum, signature, bundle checks
  |  <-- 202 received + receipt            (waits for review)
  |  checks the receipt, saves .submissions/<cc>-<city>-<date>-<sha12>.json
  |  prints the pull request steps  ----------------------------------------------->  agrees (or not)
  |                                        pull request with the receipt  <------------  opens it
  |                                        GitHub check: one commit, one new receipt, rules pass
  |                                        owner: publish --pr N, then a pinned squash merge
```

### 1. Start: `POST /api/v1/contrib/start`

```json
{"city": "ljubljana", "name": "Ljubljana", "country": "SI",
 "bundle_sha256": "<64 lowercase hex>", "bytes": 3910189,
 "overture_release": "2026-09-23.1", "division_id": "<Overture division id>",
 "config_sha": "<scoring config sha>", "skill_version": "0.2.0", "pieces": 24401}
```

- Required: `city` (lowercase letters, digits and hyphens, as in the bundle), `country` (two letters), `bundle_sha256`, and `bytes` (the exact file size). The site takes bundles up to 20 MB by default; the biggest city so far, Warsaw, is 12.2 MB.
- `submit_city.py` takes the other fields from `bundle.json` and `manifest.json` in the bundle. The website uses `overture_release`, `division_id` and `config_sha` to tell whether the city is already up to date, and shows `name`, `pieces` and `skill_version` to the person who approves.

| Answer | Meaning |
|---|---|
| `201 waiting_for_approval` | Gives `user_code` (for the person, such as `KQ7X-4PMD`; no 0, O, 1 or I), `device_code` (secret, keep it to yourself), `verify_url`, `interval` (5 seconds), `expires_in` (1200 seconds) and `update` (true if this would replace a city already on the map). |
| `409 up_to_date` | The same release, boundary and scoring are already on the map. Nothing to send. |
| `409 duplicate` | This exact bundle is already waiting for review. |
| `409 refused` | The city already has 2 uploads open or waiting. Try again once they are reviewed. |
| `400` | A field is missing or wrong. |
| `413` | The bundle is bigger than the site takes for now (20 MB by default). Say so in an "Add a city" issue, without attaching the bundle. |
| `429` | Too many requests from your address, or you already have an upload waiting for review. |
| `503` | Uploads are closed for now, the review queue is full, or the site has taken all the uploads it can for today. |

### 2. The person approves

The person opens `verify_url`. The page reads `GET /api/v1/contrib/session/<user_code>`, which returns `name`, `country`, `city`, `pieces`, `bytes`, `release`, `skill_version`, `status` and `replaces_existing`.

The page then sends `POST /api/v1/contrib/approve` with `{"user_code": "...", "decision": "approve" | "cancel", "credit": "optional name"}`. The credit can be up to 80 characters. After approval the client has 30 minutes to upload. An expired or used code gets `410`.

### 3. Wait: `POST /api/v1/contrib/poll`

Send `{"device_code": "..."}` every `interval` seconds until `expires_in` runs out.

| Answer | Meaning |
|---|---|
| `202 waiting` | The person hasn't decided yet. |
| `200 approved` | Gives `upload_key` and `upload_url`. This happens exactly once. |
| `409` | The upload key was already handed out. |
| `410` | `status` says why: `expired`, `cancelled` or `used`. |

### 4. Upload: `PUT /api/v1/contrib/upload`

The body is the `.gcm.zip`, byte for byte as declared. Headers:

```
Content-Type:    application/zip
Content-Length:  <exactly the declared bytes>
X-GCM-Device:    <device_code>
X-GCM-Timestamp: <unix time in seconds>
X-GCM-Signature: hex(HMAC-SHA256(upload_key, "<timestamp>.<sha256 hex of the body>"))
```

In Python:

```python
import hashlib, hmac, time
sha = hashlib.sha256(body).hexdigest()
ts = str(int(time.time()))
sig = hmac.new(upload_key.encode(), f"{ts}.{sha}".encode(), hashlib.sha256).hexdigest()
```

| Answer | Meaning |
|---|---|
| `202 received` | The bundle is in the review queue. The answer holds `message` and `receipt` (see [The receipt](#the-receipt)). |
| `400` | The size differs from what was declared, the bundle fails a check (below), it is for a different city, or its data release or scoring version differs from what was declared. Nothing is stored. |
| `401` | The time is more than 5 minutes off (check the computer's clock), or the signature or checksum doesn't match. Nothing is stored. |
| `403` | The upload isn't approved, or the approval expired. |
| `409` | This approval was already used. |
| `411` / `413` | The `Content-Length` header is missing, or the body is bigger than the site takes. Nothing is read. |
| `429` | Too many uploads from your address. |
| `503` | The site is busy storing another city. Wait a minute and send the same upload again; nothing was stored. |

## The receipt

After a good upload the website answers with a receipt. The website writes it, not the client, and it has exactly these keys:

```json
{
  "bundle": "si-ljubljana.gcm.zip",
  "bytes": 3911245,
  "city": "ljubljana",
  "config_sha": "df1c4ea5464dd846",
  "country": "SI",
  "credit": "",
  "date": "2026-10-16",
  "format": "gcm-receipt-1",
  "name": "Ljubljana",
  "overture_release": "2026-09-23.1",
  "pieces": 24401,
  "sha256": "<64 lowercase hex>",
  "skill_version": "0.2.0",
  "upload": "<16-character upload id>"
}
```

| Key | What it is | Rule |
|---|---|---|
| `format` | The receipt format | Always `gcm-receipt-1` |
| `city` | The city id | Lowercase letters, digits and hyphens, at most 64, starting with a letter or digit |
| `country` | The country code | Two capital letters |
| `name` | The city name | Text |
| `date` | The day of the upload, in UTC | `YYYY-MM-DD`, a real calendar date |
| `bundle` | The bundle's file name | `<cc>-<city>.gcm.zip`, with the country code in lowercase |
| `sha256` | The bundle's checksum | 64 lowercase hex characters |
| `bytes` | The bundle's size | A whole number from 1 to 41,943,040 (40 MB) |
| `pieces` | Street pieces scored | A whole number from 1 to 5,000,000 |
| `overture_release` | The Overture data release | Like `2026-09-23.1`: a date, a dot and 1 to 3 digits |
| `config_sha` | The scoring settings' checksum | 16 lowercase hex characters |
| `skill_version` | The skill version | Like `0.2.0`: three numbers of 1 to 3 digits, with dots |
| `upload` | The website's id for this upload | 16 characters: letters, digits, `-` and `_` |
| `credit` | The name to credit | Text, may be empty |

Text fields hold 1 to 200 characters (`credit` may be empty), in Unicode NFC form. They may not hold:

- control characters, line or paragraph separators, or lone surrogates;
- format characters: zero-width characters, soft hyphens, the bidirectional controls that can make text display in another order, and the invisible "tag" characters (U+E0000 to U+E007F);
- private-use or unassigned characters;
- characters that look blank or carry hidden data: Hangul fillers, the blank braille pattern, the combining grapheme joiner and variation selectors.

Invisible characters can hide instructions that a person never sees but an AI agent still reads. Nothing else may be in a receipt.

### The receipt file

`submit_city.py` checks that the receipt's `sha256` and `bytes` match the file it just uploaded, and that the receipt passes every rule above. Then it saves the receipt in this repository as `.submissions/<cc>-<city>-<date>-<sha256[:12]>.json`, for example `.submissions/si-ljubljana-2026-10-16-8f4060037e94.json`. The file is pretty-printed JSON in UTF-8, with sorted keys and one newline at the end.

- It never overwrites a file. If the same receipt is already there, it says so. If a different file has that name, it stops.
- If the receipt fails a check, nothing is saved. The upload itself went through; tell the site owner in an issue.
- The file must stay as the website wrote it. Any change makes it fail the checks.

You can check a receipt yourself, with any Python 3:

```sh
python3 -I skill/pipeline/check_receipt.py .submissions/<file>.json
```

It also checks that the file is a plain file (not a link), sits directly in `.submissions/`, is at most 4,096 bytes of UTF-8 without a byte order mark, is plain JSON (no duplicate keys, no `NaN`), and has the right file name for its content.

### The pull request

The person is asked first. Only if they agree, the agent runs the steps that `submit_city.py` prints (it never runs git or gh itself). If the person has no fork yet, it first runs `gh repo fork broAir/green-city-map --remote=true`. Then:

```sh
git fetch https://github.com/broAir/green-city-map.git main
git switch -c submission/<cc>-<city>-<date> FETCH_HEAD
git add .submissions/<cc>-<city>-<date>-<sha12>.json
git commit -m "Add <name> (<date>)"
git push -u origin HEAD
gh pr create --repo broAir/green-city-map --title "Add <name> (<date>)" --body "Receipt for upload <sha12>, made with skill <version>."
```

The branch starts from the latest `main`, so the pull request is one commit that holds only the receipt. Keep it that way: don't add commits, and don't merge `main` into the branch. If something needs fixing, start a new branch the same way. `origin` must be the person's fork, as it is after `gh repo fork --remote=true`.

Without gh, upload the file through GitHub's web page: open the `.submissions` folder, click "Add file", then "Upload files", and start a pull request. GitHub makes a fork first if needed, and the upload is one commit.

The pull request is public. It shows your GitHub name, and the credit if you gave one.

### The GitHub check

Every pull request that touches `.submissions/` runs [.github/workflows/submission-check.yml](../.github/workflows/submission-check.yml). It fails unless the pull request:

- goes into `main`;
- is exactly one commit, not a merge commit, starting from a commit on `main`;
- adds exactly one new file, `.submissions/<name>.json`, as a plain file, and changes nothing else: no other file, no subfolder, no link, no edits or deletions.

Then it checks the receipt with the rules above.

- The workflow runs on `pull_request_target`, so GitHub always takes it from `main`. It also runs `check_receipt.py` from the current `main`, never the copy in the pull request. A pull request can't change what is checked, not even by editing the workflow.
- Nothing from the pull request is run. git lists its files, and the checker reads the receipt as data.
- It has read-only access and no secrets.
- If the repository variable `GCM_SITE` is set to the website's address, it also asks the website whether the upload is waiting for review or already published, for the same city. If the variable is not set, the check says so in a NOTE line.

A second check, [.github/workflows/protected-paths.yml](../.github/workflows/protected-paths.yml), runs on every pull request. It fails if the repository would hold files that Claude Code loads or runs: plugin hooks, `bin/`, MCP or LSP servers, monitors, settings, agents or commands, `.claude/` files other than the skill link, other symbolic links, or submodules.

The checks only help. The gate is the site owner's own check, below.

### The upload's status: `GET /api/v1/contrib/receipt/<sha256>`

Anyone can ask the website about an upload by its checksum (64 lowercase hex characters; anything else gets `404`):

```json
{"sha256": "<64 lowercase hex>", "status": "waiting", "city": "ljubljana"}
```

`status` is `waiting` (in the review queue), `published` (on the map), `rejected`, or `unknown` (the website has no such upload; `city` is then `null`). It reveals nothing else. Answers are never cached, and requests are rate-limited per IP address.

### What the site owner does

The site owner checks each pull request with the website's admin tool before merging:

- `verify <receipt.json | - | --pr N>` reads the receipt from a file, from standard input, or from pull request N on GitHub. A pull request must be one commit that adds exactly one `.submissions/*.json` file. It checks the receipt with the same rules, finds the waiting upload with that checksum, recomputes the checksum of the stored bundle, and compares its size, city, country, data release, scoring settings, skill version and upload id. It also runs the bundle checks again. It prints a plain pass or fail.
- `publish <same>` runs `verify`, then puts the city on the map. With `--pr N` it notes the commit it checked, and gives the merge command pinned to that commit:

  ```sh
  gh pr merge N --squash --match-head-commit <commit>
  ```

Merge with that command, never with the merge button. The contributor can push again after the check, and the button merges whatever is there at that moment. The pinned command fails if the pull request changed after the check. Then don't merge: look at what changed, and close the pull request if it holds anything but the receipt. A squash merge also keeps only the receipt: earlier commits, and the names on them, never reach `main`.

### Repository settings

The owner sets these once on GitHub:

- **Settings, General, Pull Requests**: allow squash merging only. Turn off merge commits and rebase merging.
- **Settings, Rules, Rulesets**: a ruleset for `main` that:
  - blocks force pushes and deletion;
  - requires a pull request with one approval, review from Code Owners ([.github/CODEOWNERS](../.github/CODEOWNERS)), and approval of the most recent reviewable push;
  - requires the "Check for plugin and agent files" check.

  Add the owner as a bypass actor, for their own pull requests only. A receipt pull request is then approved first (`gh pr review N --approve`) and merged with the pinned command. A push after the approval cancels it.
- **Settings, Actions, General**: require approval for all outside contributors before their workflows run.

The receipt check is not made required: it runs only for pull requests that touch `.submissions/`, and a required check that never runs blocks every other pull request.

## Checks every bundle must pass

The website reads the zip in memory and trusts nothing in it. It refuses a bundle when:

- the zip is over 40 MB, unpacks to more than 150 MB, or holds a file over 60 MB;
- `bundle.json` is missing, or isn't format `green-city-map-bundle` version 1;
- the city id or country code isn't valid;
- the files in the zip differ from the list in `bundle.json`;
- a required file is missing: `streets.json`, `details.json`, `layers.json`, `basemap.json`, `manifest.json`, `quality.json`, `preview.webp`, `street_index.json`, `tour.json`;
- a file has a name outside the known set: the JSON files listed under [What is in the bundle](#what-is-in-the-bundle), `base_<row>_<col>.png`, `green.png` and `preview.webp`;
- a file doesn't match its checksum, a JSON file doesn't parse, or an image isn't a real PNG or WebP;
- `quality.json` says the city did not pass its quality checks.

## Rate limits

Per IP address:

| Step | Limit |
|---|---|
| start | 10 per hour |
| approve | 20 per hour |
| view the approval page | 60 per hour |
| poll | 400 per hour |
| upload | 10 per hour |

The receipt status lookup is rate-limited per IP address too.

## What the script tells you

`submit_city.py` uses only the Python standard library. It ends in one of these ways:

| It prints | What it means |
|---|---|
| "Set the website address" | No address given. Use `--site` or `GCM_SITE`. |
| "The website address should look like ..." or "... must start with https://" | The address isn't a web address, or it isn't https. Give it as `https://...` (`http://` works only for `localhost`). |
| "No bundle at ..." or "... is not a city bundle" | Make the bundle first with `package_city.py`. |
| "Could not reach ..." | No connection, or nothing answers at that address. Check the address and your connection. |
| "The answer from ... doesn't look right" | The link or the upload address the site sent is not on the same site, or the code has the wrong form. Nothing is sent. Check that the address is https://greencitymap.city, or the copy of the site you meant. |
| A message, and nothing is sent | The city is already up to date, the same bundle is already waiting, or the city already has uploads waiting. |
| "The site refused: ..." | The start was refused; the reason follows. |
| "Not sent: expired." or "Not sent: cancelled." | Nobody approved in time, or the person cancelled. Run it again if you still want to send. |
| "Upload failed: ..." | The upload was refused; the reason follows. |
| "Thanks! ... is in the review queue." | The upload worked. A receipt line follows. |
| "Receipt saved: .submissions/..." | The receipt passed every check and was saved. The pull request steps follow; ask the person before opening one. |
| "The receipt was already saved" | The same receipt is already in `.submissions/`. Nothing changed. |
| "... already exists and is different" | A different file has that name. Nothing was overwritten; look at it first. |
| "The upload went through, but the receipt from the website ..." | The upload is in the queue, but the receipt failed a check, so nothing was saved. Don't open a pull request; tell the site owner. |
| "The website sent no receipt" | The upload went through, but there is nothing for a pull request. |
| "The code expired before it was approved." | 20 minutes passed. Run it again for a new code. |

Don't retry in a loop, and don't try to get round a refusal.

The script reads at most 64 KB of each answer from the website. Text from the website is printed only after control and invisible characters are removed.

## Bundles go only through the website

A city reaches the map only through the upload above, with a person's approval, the receipt and the owner's check. Bundles attached to GitHub issues are never loaded onto the map, so please don't attach them. You can still ask for a city, or point to better local data, with the [Add a city form](https://github.com/broAir/green-city-map/issues/new?template=city-request.yml). Don't commit bundles to the repository.
