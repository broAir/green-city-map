"""Send a packaged city to the Green City Map website (protocol in docs/UPLOAD.md).

  1. Tell the site what we want to send (city, data release, checksum, size). If the city is
     already on the map with the same data, nothing is sent.
  2. Print a short code and a link. The person opens the link and clicks "Yes, send it".
     This proves a real person stands behind every upload.
  3. Wait for that approval, then upload the bundle once, signed with the one-time key
     the site hands out after approval (HMAC-SHA256 over the time and the bundle's sha256).
  4. Check the receipt the site sends back (same checksum and size as the file, and the rules
     in check_receipt.py), save it as .submissions/<cc>-<city>-<date>-<sha256[:12]>.json in this
     repository, and print the steps for a pull request with that one file. The person is asked
     before any pull request is opened. This script never runs git or gh itself.

The website's answers are treated as untrusted: each is read up to 64 KB, text from it is printed
only after control and invisible characters are removed, and the approval link and the upload
address must be on the same site as --site.

Standard library only.
Usage: python -I submit_city.py share/<cc>-<city>.gcm.zip [--site https://example.org]
The site can also come from the GCM_SITE environment variable. There is no default site:
you always say where the city goes.
"""
import hashlib
import hmac
import importlib.util
import json
import os
import re
import shlex
import stat
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_SITE = "https://[YOUR-HEROKU-APP].herokuapp.com"
UA = "GreenCityMap-skill/0.2"
REPO = "broAir/green-city-map"
ROOT = Path(__file__).resolve().parents[2]      # the repository root: receipts go to ROOT/.submissions/
MAX_ANSWER = 64 * 1024                          # bytes; a real answer is well under 2 KB
MAX_SHOWN = 300                                 # characters of website text shown to the person
USER_CODE_RE = re.compile(r"[A-Z2-9]{4}-[A-Z2-9]{4}")
DEVICE_CODE_RE = re.compile(r"[A-Za-z0-9_-]{16,128}")
MIN_INTERVAL, MAX_INTERVAL, MAX_WAIT = 1, 60, 3600   # seconds


def load_checker():
    """check_receipt.py from this folder (python -I doesn't put the script's folder on sys.path)."""
    spec = importlib.util.spec_from_file_location("gcm_check_receipt", Path(__file__).resolve().with_name("check_receipt.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A Green City Map site never redirects these requests, so a redirect is answered as an error."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirects)


def clean(value, limit=MAX_SHOWN):
    """Text from the website, safe to print: control, invisible, private-use and unassigned characters
    (terminal escape sequences included) are dropped, line breaks become spaces, and it is cut short."""
    s = value if isinstance(value, str) else ("" if value is None else str(value))
    s = "".join(" " if c in "\t\n\r" else c for c in s[:limit * 4]
                if c in "\t\n\r" or unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"))
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit - 3] + "..."


def call(method, url, payload=None, headers=None, body=None):
    data = body if body is not None else (json.dumps(payload).encode() if payload is not None else None)
    h = {"User-Agent": UA, **({"Content-Type": "application/json"} if payload is not None else {}), **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with _OPENER.open(req, timeout=300) as r:
            status, raw = r.status, r.read(MAX_ANSWER + 1)
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read(MAX_ANSWER + 1)
        e.close()
    except OSError as e:            # no connection, unknown host, timeout
        raise SystemExit(f"Could not reach {url}: {clean(getattr(e, 'reason', e))}")
    if len(raw) > MAX_ANSWER:
        return status, {"error": f"HTTP {status}, and the answer is too long to be from a Green City Map site"}
    try:
        answer = json.loads(raw or b"{}")
    except (ValueError, RecursionError):
        answer = None
    if not isinstance(answer, dict):
        return status, {"error": f"HTTP {status}, and the answer is not from a Green City Map site"}
    return status, answer


def origin(url):
    """(scheme, host, port) of a web address, or None when it isn't a plain http(s) address."""
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port or {"http": 80, "https": 443}.get(u.scheme)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        return None
    return u.scheme, u.hostname.lower(), port


def same_site(url, site):
    """True when url is a plain address on the same site (scheme, host and port) as --site."""
    return isinstance(url, str) and len(url) <= 2000 and url.isprintable() and " " not in url \
        and origin(url) is not None and origin(url) == origin(site)


def number(value, default, low, high):
    """A number from the website, kept between low and high."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return default
    return min(max(value, low), high)


def parse_args(argv):
    """The bundle path and the site address, or a message saying what to fix."""
    usage = "Usage: python -I submit_city.py share/<cc>-<city>.gcm.zip [--site https://...]"
    if "-h" in argv or "--help" in argv:
        print(__doc__.strip())
        raise SystemExit(0)
    paths, site, i = [], None, 0
    while i < len(argv):
        a = argv[i]
        if a == "--site":
            if i + 1 >= len(argv):
                raise SystemExit(f"--site needs an address. {usage}")
            site, i = argv[i + 1], i + 2
        elif a.startswith("--site="):
            site, i = a.split("=", 1)[1], i + 1
        elif a.startswith("-"):
            raise SystemExit(f"Unknown option {a}. {usage}")
        else:
            paths.append(a)
            i += 1
    if len(paths) != 1:
        raise SystemExit(f"Give one bundle to send. {usage}")
    site = (site or os.environ.get("GCM_SITE") or DEFAULT_SITE).strip().rstrip("/")
    if "[YOUR-" in site:
        raise SystemExit("Set the website address: --site https://... or GCM_SITE=https://...\n"
                         "The address will be announced in the README at github.com/broAir/green-city-map. "
                         "Until then, keep the bundle in share/ and send it once the address is out. "
                         "Bundles attached to GitHub issues are never put on the map.")
    if not re.match(r"^https?://[^/\s]+(/\S*)?$", site) or origin(site) is None:
        raise SystemExit(f"The website address should look like https://example.org (got {site!r}).")
    if origin(site)[0] == "http" and origin(site)[1] not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit(f"The website address must start with https:// (http:// only for localhost; got {site!r}).")
    return Path(paths[0]), site


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    path, site = parse_args(argv)
    if not path.is_file():
        raise SystemExit(f"No bundle at {path}. Make one with: python -I skill/pipeline/package_city.py out/<city> share")
    blob = path.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    try:
        with zipfile.ZipFile(path) as z:
            b = json.loads(z.read("bundle.json"))
            man = json.loads(z.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError):
        raise SystemExit(f"{path} is not a city bundle. Make one with skill/pipeline/package_city.py.")
    declare = {"city": b["city"], "name": b["name"], "country": b["country"], "bundle_sha256": sha, "bytes": len(blob),
               "overture_release": b.get("overture_release"), "division_id": man.get("division_id"),
               "config_sha": (b.get("config") or {}).get("sha"), "skill_version": b.get("skill_version"),
               "pieces": (man.get("quality") or {}).get("pieces_scored")}
    status, r = call("POST", f"{site}/api/v1/contrib/start", declare)
    if status == 409:
        print(clean(r.get("message") or r.get("error")) or "Nothing to send: the website already has this city or this bundle.")
        return 0
    if status != 201:
        raise SystemExit(f"The site refused: {clean(r.get('error')) or status}")
    verify_url, user_code, device = r.get("verify_url"), r.get("user_code"), r.get("device_code")
    if not same_site(verify_url, site) or not isinstance(user_code, str) or not USER_CODE_RE.fullmatch(user_code) \
            or not isinstance(device, str) or not DEVICE_CODE_RE.fullmatch(device):
        raise SystemExit(f"The answer from {site} doesn't look right (the link must be on the same site, with a code "
                         "like KQ7X-4PMD). Nothing was sent. Check the website address.")
    print(f"\nTo send {b['name']}, open this link and check the code:\n\n    {verify_url}\n\n    code: {user_code}\n")
    print("Waiting for you to approve it in the browser…", flush=True)
    deadline = time.time() + number(r.get("expires_in"), 1200, 1, MAX_WAIT)
    interval = number(r.get("interval"), 5, MIN_INTERVAL, MAX_INTERVAL)
    while time.time() < deadline:
        time.sleep(interval)
        status, p = call("POST", f"{site}/api/v1/contrib/poll", {"device_code": device})
        if status == 202:
            continue
        if status != 200:
            raise SystemExit(f"Not sent: {clean(p.get('status') or p.get('error')) or status}.")
        key, upload_url = p.get("upload_key"), p.get("upload_url")
        if not isinstance(key, str) or not 16 <= len(key) <= 256 or not same_site(upload_url, site):
            raise SystemExit(f"The approval answer from {site} doesn't look right (the upload address must be on "
                             "the same site). Nothing was sent.")
        ts = str(int(time.time()))
        sig = hmac.new(key.encode(), f"{ts}.{sha}".encode(), hashlib.sha256).hexdigest()
        status, u = call("PUT", upload_url, body=blob,
                         headers={"Content-Type": "application/zip", "X-GCM-Device": device,
                                  "X-GCM-Timestamp": ts, "X-GCM-Signature": sig})
        if status != 202:
            raise SystemExit(f"Upload failed: {clean(u.get('error')) or status}")
        print(clean(u.get("message")) or "Sent.")
        if u.get("receipt") is None:
            print("The website sent no receipt, so there is no pull request to open. The upload itself went through.")
            return 0
        rel = save_receipt(u["receipt"], sha, len(blob), b)
        print_next_steps(u["receipt"], rel)
        return 0
    raise SystemExit("The code expired before it was approved. Run this again for a new one.")


def save_receipt(receipt, sha, size, bundle, root=None):
    """Check the website's receipt against the file we sent, then write it to .submissions/.
    Returns the path relative to the repository root. Never overwrites a different file."""
    root = Path(root or ROOT)
    chk = load_checker()
    stop = ("The upload went through, but the receipt from the website {}. Nothing was saved. "
            "Don't open a pull request; tell the site owner (open an issue on github.com/" + REPO + ").")
    errors = chk.validate(receipt)
    if errors:
        raise SystemExit(stop.format("failed a check: " + "; ".join(errors)))
    if not hmac.compare_digest(receipt["sha256"], sha) or receipt["bytes"] != size:
        raise SystemExit(stop.format("is for a different file (checksum or size differ)"))
    if receipt["city"] != bundle.get("city") or receipt["country"] != str(bundle.get("country") or "").upper():
        raise SystemExit(stop.format("is for a different city"))
    data, name = chk.serialize(receipt), chk.receipt_name(receipt)
    _, errors = chk.check_bytes(data, name)
    if errors:
        raise SystemExit(stop.format("failed a check: " + "; ".join(errors)))
    folder = root / chk.FOLDER
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise SystemExit(f"{folder} must be a real folder. Nothing was saved.")
    folder.mkdir(exist_ok=True)
    path, rel = folder / name, f"{chk.FOLDER}/{name}"
    try:   # O_EXCL: never replace a file, and never write through a symbolic link
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    except FileExistsError:
        st = os.lstat(path)
        if stat.S_ISREG(st.st_mode) and path.read_bytes() == data:
            print(f"The receipt was already saved: {rel}")
            return rel
        raise SystemExit(f"{rel} already exists and is different. Nothing was overwritten. Look at it before going on.")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    print(f"Receipt saved: {rel}")
    return rel


def sh(s):
    """A shell word: double quotes when that is safe, else single quotes."""
    return f'"{s}"' if re.fullmatch(r'[^"$`\\!]*', s) else shlex.quote(s)


def print_next_steps(r, rel):
    title = f"Add {r['name']} ({r['date']})"
    branch = f"submission/{r['country'].lower()}-{r['city']}-{r['date']}"
    body = f"Receipt for upload {r['sha256'][:12]}, made with skill {r['skill_version']}."
    print(f"""
Last step: a pull request that adds this one receipt to github.com/{REPO}.
Anyone can then see what was uploaded. The site owner checks the upload against the receipt
before {r['name']} goes on the map.

Ask the person first. Open the pull request only if they say yes.

With the GitHub command line (gh), from the repository root ({ROOT}).
If the person has no fork of {REPO} yet, first run:

    gh repo fork {REPO} --remote=true

Then:

    git fetch https://github.com/{REPO}.git main
    git switch -c {branch} FETCH_HEAD
    git add {rel}
    git commit -m {sh(title)}
    git push -u origin HEAD
    gh pr create --repo {REPO} --title {sh(title)} --body {sh(body)}

The branch starts from the latest main, so the pull request is one commit that holds only
the receipt. Keep it that way: add no other commits or files, and don't merge main into it.
"origin" must be the person's fork. After gh repo fork --remote=true it is; otherwise use
the name of the fork's remote (git remote -v lists them).

Without gh: on github.com/{REPO}, open the {Path(rel).parent} folder, click "Add file",
then "Upload files", and add {rel}. GitHub then offers to start a pull request
(it makes a fork first if needed). Title it {sh(title)}, and add no other file.""")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
