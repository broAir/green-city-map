"""Check upload receipts: the small JSON files in .submissions/ (see docs/UPLOAD.md).

When a city bundle is uploaded, the website answers with a receipt. submit_city.py saves it as
.submissions/<cc>-<city>-<date>-<sha256[:12]>.json, and a pull request adds that one file here.
This script checks such files. The GitHub check runs it, and anyone can run it by hand.

Usage:
  python3 -I skill/pipeline/check_receipt.py FILE...
  python3 -I skill/pipeline/check_receipt.py --changed CHANGES FILE
  add --site https://<website address> to either form to ask the website about each upload

  FILE      a receipt. It must be a plain file directly inside a folder named .submissions.
  CHANGES   the output of: git diff --name-status --no-renames <merge base> <head>
            The change set must be exactly one added file, .submissions/<receipt name>.json,
            and FILE must be that file.
  --site    asks <site>/api/v1/contrib/receipt/<sha256>. The upload must be "waiting" or
            "published" there, for the same city. https only, except for localhost.

Exit code 0 when every check passes, 1 otherwise. Standard library only; never runs git.
The same rules are used by the website's owner tools, so a receipt that passes here passes there.
"""
import datetime as dt
import json
import os
import re
import stat
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FORMAT = "gcm-receipt-1"
FOLDER = ".submissions"
MAX_FILE_BYTES = 4096                 # a real receipt is about 500 bytes
MAX_BUNDLE_BYTES = 40 * 1024 * 1024   # 41943040, the website's upload limit
MAX_PIECES = 5_000_000
MAX_TEXT = 200                        # characters, for every text field
MAX_DEPTH = 8                         # receipts are flat; anything deeper is refused before parsing
MAX_CHANGES_BYTES = 1024 * 1024
MAX_SITE_ANSWER = 4096
SITE_TIMEOUT = 10

# every key, with its type; nothing more and nothing less
KEYS = {"format": str, "city": str, "country": str, "name": str, "date": str, "bundle": str, "sha256": str,
        "bytes": int, "pieces": int, "overture_release": str, "config_sha": str, "skill_version": str,
        "upload": str, "credit": str}
MAY_BE_EMPTY = {"credit"}

CITY_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
COUNTRY_RE = re.compile(r"[A-Z]{2}")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
UPLOAD_RE = re.compile(r"[A-Za-z0-9_-]{16}")          # secrets.token_urlsafe(12)
NAME_RE = re.compile(r"[a-z]{2}-[a-z0-9][a-z0-9-]{0,63}-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{12}\.json")
RELEASE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]{1,3}")    # an Overture release, such as 2026-09-23.1
CONFIG_SHA_RE = re.compile(r"[0-9a-f]{16}")                          # build_manifest.py: sha256 of the config, first 16
VERSION_RE = re.compile(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}")         # the skill version, such as 0.2.0
# Text may hold letters, marks, digits, punctuation, symbols and spaces, in Unicode NFC form. Never:
# - control characters (Cc), format characters (Cf: zero-width characters, bidirectional controls, the
#   invisible "tag" characters U+E0000-E007F, soft hyphens), lone surrogates (Cs), private-use (Co) and
#   unassigned (Cn) characters, and line or paragraph separators (Zl, Zp);
# - characters that look blank or carry hidden data although they count as letters, marks or symbols:
#   Hangul fillers, the blank braille pattern, the combining grapheme joiner and variation selectors.
# Invisible characters can hide text that a person never sees but an AI agent still reads.
BAD_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
INVISIBLE = ({"\u034f", "\u115f", "\u1160", "\u17b4", "\u17b5", "\u180f", "\u2800", "\u3164", "\uffa0"}
             | {chr(c) for c in range(0x180B, 0x180E)} | {chr(c) for c in range(0xFE00, 0xFE10)}
             | {chr(c) for c in range(0xE0100, 0xE01F0)})
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def show(value, limit=80):
    """Untrusted text, quoted on one line, for messages (never starts a log line, never spans two)."""
    s = json.dumps(value if isinstance(value, str) else str(value), ensure_ascii=True)
    return s if len(s) <= limit else s[:limit - 4] + '..."'


def bad_char(s):
    """The first character of s that text may not hold, or None."""
    return next((c for c in s if c in INVISIBLE or unicodedata.category(c) in BAD_CATEGORIES), None)


def receipt_name(r):
    """The file name a receipt must have: <cc>-<city>-<date>-<sha256[:12]>.json."""
    return f"{r['country'].lower()}-{r['city']}-{r['date']}-{r['sha256'][:12]}.json"


def serialize(r):
    """The exact bytes submit_city.py writes: pretty JSON, sorted keys, UTF-8, one trailing newline."""
    return (json.dumps(r, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


# ------------------------------------------------------------------ the receipt itself
def validate(r):
    """Errors in a parsed receipt (an empty list when it is fine)."""
    if not isinstance(r, dict):
        return ["the receipt must be a JSON object"]
    errors = []
    missing = [k for k in KEYS if k not in r]
    extra = sorted(k for k in r if k not in KEYS)
    if missing:
        errors.append("missing keys: " + ", ".join(missing))
    if extra:
        errors.append("keys that don't belong in a receipt: " + ", ".join(show(k, 40) for k in extra[:10]))
    for key, kind in KEYS.items():
        if key not in r:
            continue
        v = r[key]
        if kind is int:
            if type(v) is not int:   # bool is a subclass of int; true/false is not a number here
                errors.append(f"{key} must be a whole number")
        elif not isinstance(v, str):
            errors.append(f"{key} must be text")
        elif len(v) > MAX_TEXT:
            errors.append(f"{key} is longer than {MAX_TEXT} characters")
        elif bad_char(v) is not None:
            errors.append(f"{key} holds a character that is not allowed (U+{ord(bad_char(v)):04X}: "
                          "control, invisible, private-use or unassigned characters are refused)")
        elif not unicodedata.is_normalized("NFC", v):
            errors.append(f"{key} is not in Unicode NFC form")
        elif not v.strip() and key not in MAY_BE_EMPTY:
            errors.append(f"{key} is empty")
    if errors:
        return errors
    if r["format"] != FORMAT:
        errors.append(f"format must be {FORMAT!r}")
    if not CITY_RE.fullmatch(r["city"]):
        errors.append("city must be lowercase letters, digits and hyphens (at most 64), starting with a letter or digit")
    if not COUNTRY_RE.fullmatch(r["country"]):
        errors.append("country must be two capital letters, such as SI")
    if not DATE_RE.fullmatch(r["date"]):
        errors.append("date must look like 2026-10-15")
    else:
        try:
            dt.date.fromisoformat(r["date"])
        except ValueError:
            errors.append("date is not a real calendar date")
    if not SHA_RE.fullmatch(r["sha256"]):
        errors.append("sha256 must be 64 lowercase hex characters")
    if not 1 <= r["bytes"] <= MAX_BUNDLE_BYTES:
        errors.append(f"bytes must be between 1 and {MAX_BUNDLE_BYTES}")
    if not 1 <= r["pieces"] <= MAX_PIECES:
        errors.append(f"pieces must be between 1 and {MAX_PIECES}")
    if not UPLOAD_RE.fullmatch(r["upload"]):
        errors.append("upload must be the 16-character upload id from the website")
    if not RELEASE_RE.fullmatch(r["overture_release"]):
        errors.append("overture_release must look like 2026-09-23.1")
    if not CONFIG_SHA_RE.fullmatch(r["config_sha"]):
        errors.append("config_sha must be 16 lowercase hex characters")
    if not VERSION_RE.fullmatch(r["skill_version"]):
        errors.append("skill_version must look like 0.2.0")
    if CITY_RE.fullmatch(r["city"]) and COUNTRY_RE.fullmatch(r["country"]):
        want = f"{r['country'].lower()}-{r['city']}.gcm.zip"
        if r["bundle"] != want:
            errors.append(f"bundle must be {want!r}")
    return errors


def depth_ok(text, limit=MAX_DEPTH):
    """True when [ and { (outside strings) never nest deeper than limit. Runs before json.loads."""
    depth, in_str, esc = 0, False, False
    for c in text:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "[{":
            depth += 1
            if depth > limit:
                return False
        elif c in "]}":
            depth -= 1
    return True


def _no_duplicates(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"the key {show(k, 40)} appears twice")
        seen[k] = v
    return seen


def _no_constant(name):
    raise ValueError(f"{name} is not allowed in a receipt")


def parse(raw):
    """(receipt, errors) from the bytes of a receipt file. Strict: UTF-8 without a BOM, plain JSON."""
    if len(raw) > MAX_FILE_BYTES:
        return None, [f"the file is over {MAX_FILE_BYTES} bytes"]
    if raw.startswith(b"\xef\xbb\xbf"):
        return None, ["the file starts with a byte order mark; save it as UTF-8 without one"]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, ["the file is not UTF-8 text"]
    if "\x00" in text:
        return None, ["the file holds a NUL character"]
    if not depth_ok(text):
        return None, [f"the JSON is nested deeper than {MAX_DEPTH} levels"]
    try:
        r = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except RecursionError:
        return None, ["the JSON is nested too deeply"]
    except ValueError as e:     # also json.JSONDecodeError
        return None, [f"not valid JSON: {show(str(e), 120)}"]
    return r, validate(r)


def check_bytes(raw, file_name):
    """(receipt or None, errors) for the bytes of one receipt saved under file_name (no folder)."""
    r, errors = parse(raw)
    if not errors and file_name != receipt_name(r):
        errors.append(f"the file name must be {receipt_name(r)!r}, not {show(file_name)}")
    return (None, errors) if errors else (r, [])


def read_receipt(path):
    """(bytes, errors) for a receipt path, refusing symbolic links, folders, ../ and big files."""
    p = Path(path)
    if ".." in p.parts:
        return None, ["the path must not contain .."]
    if p.parent.name != FOLDER:
        return None, [f"a receipt must sit directly in the {FOLDER} folder"]
    try:
        parent = os.lstat(p.parent)
        st = os.lstat(p)
    except OSError:
        return None, ["no such file"]
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        return None, [f"{FOLDER} must be a real folder, not a link"]
    if stat.S_ISLNK(st.st_mode):
        return None, ["the receipt is a symbolic link; it must be a plain file"]
    if not stat.S_ISREG(st.st_mode):
        return None, ["the receipt must be a plain file"]
    if st.st_size > MAX_FILE_BYTES:
        return None, [f"the file is over {MAX_FILE_BYTES} bytes"]
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(p, flags)
    except OSError:
        return None, ["the file can't be opened"]
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return None, ["the receipt must be a plain file"]
        raw = f.read(MAX_FILE_BYTES + 1)
    return raw, []


def check_file(path):
    """(receipt or None, errors) for one receipt file."""
    raw, errors = read_receipt(path)
    if errors:
        return None, errors
    return check_bytes(raw, Path(path).name)


# ------------------------------------------------------------------ the pull request's change set
VERBS = {"A": "adds", "M": "changes", "D": "deletes", "T": "changes the type of", "R": "renames", "C": "copies"}


def check_changes(text):
    """(added path or None, errors) for `git diff --name-status --no-renames` output."""
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        return None, ["the pull request changes no files"]
    if len(lines) > 1:
        errors = [f"the pull request must change exactly one file; it changes {len(lines)}"]
        for line in lines[:20]:
            status, *paths = line.split("\t")
            verb = VERBS.get(status[:1])
            errors.append(f"it {verb} {show(paths[0])}" if verb and paths else f"unexpected line {show(line, 120)}")
        if len(lines) > 20:
            errors.append(f"... and {len(lines) - 20} more")
        return None, errors
    status, *paths = lines[0].split("\t")
    verb = VERBS.get(status[:1])
    if status != "A" and verb and paths:
        return None, [f"the pull request {verb} {show(paths[0])}; it may only add one new receipt"]
    if status != "A" or len(paths) != 1:
        return None, [f"not a line of git diff --name-status --no-renames output: {show(lines[0], 120)}"]
    path = paths[0]
    if path.startswith('"'):
        return None, [f"the file name has unusual characters: {show(path)}"]
    if not path.startswith(FOLDER + "/") or path.count("/") != 1:
        return None, [f"the new file must sit directly in {FOLDER}/, not {show(path)}"]
    if not NAME_RE.fullmatch(path[len(FOLDER) + 1:]):
        return None, [f"the new file's name must look like {FOLDER}/si-ljubljana-2026-10-15-0123456789ab.json, not {show(path)}"]
    return path, []


def read_changes(path):
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_CHANGES_BYTES + 1)
    except OSError:
        return None, [f"can't read the change list {show(str(path))}"]
    if len(raw) > MAX_CHANGES_BYTES:
        return None, ["the change list is too long; the pull request changes too many files"]
    try:
        return raw.decode("utf-8"), []
    except UnicodeDecodeError:
        return None, ["the change list is not UTF-8 text"]


# ------------------------------------------------------------------ the website
class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirects are not followed", headers, fp)


def site_base(site):
    """The checked base address, or raise ValueError saying what is wrong."""
    u = urllib.parse.urlsplit(site.strip())
    host = (u.hostname or "").lower()
    if u.scheme not in ("https", "http") or not host:
        raise ValueError("the website address should look like https://example.org")
    if u.scheme == "http" and host not in LOCAL_HOSTS:
        raise ValueError("the website address must start with https:// (http:// only for localhost)")
    if u.username or u.password or u.query or u.fragment:
        raise ValueError("the website address must not hold a user name, ? or #")
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path.rstrip("/"), "", ""))


def check_site(site, r, opener=None):
    """Errors from asking the website about this receipt's upload."""
    try:
        base = site_base(site)
    except ValueError as e:
        return [str(e)]
    url = f"{base}/api/v1/contrib/receipt/{r['sha256']}"
    opener = opener or urllib.request.build_opener(_NoRedirects)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "GreenCityMap-receipt-check/1"})
    try:
        with opener.open(req, timeout=SITE_TIMEOUT) as resp:
            raw = resp.read(MAX_SITE_ANSWER + 1)
    except urllib.error.HTTPError as e:
        return [f"the website answered HTTP {e.code} for this upload"]
    except (OSError, ValueError) as e:
        return [f"could not reach the website: {show(str(getattr(e, 'reason', e)), 120)}"]
    if len(raw) > MAX_SITE_ANSWER:
        return ["the website's answer is too long"]
    try:
        a = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return ["the website's answer is not JSON"]
    if not isinstance(a, dict):
        return ["the website's answer is not a JSON object"]
    if a.get("sha256") not in (None, r["sha256"]):
        return ["the website answered about a different upload"]
    status = a.get("status")
    if status not in ("waiting", "published"):
        why = {"rejected": "the upload was rejected", "unknown": "the website doesn't know this upload"}
        return [why.get(status, f"the website says the upload is {show(status, 40)}") if isinstance(status, str) else "the website's answer has no status"]
    if a.get("city") != r["city"]:
        return [f"the website has this upload for a different city ({show(a.get('city'), 40)})"]
    return []


# ------------------------------------------------------------------ command line
def parse_args(argv):
    files, changed, site, i = [], None, None, 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            files += argv[i + 1:]
            break
        if a in ("-h", "--help"):
            print(__doc__.strip())
            raise SystemExit(0)
        if a in ("--changed", "--site"):
            if i + 1 >= len(argv):
                raise ValueError(f"{a} needs a value")
            if a == "--changed":
                changed = argv[i + 1]
            else:
                site = argv[i + 1]
            i += 2
            continue
        if a.startswith("--changed=") or a.startswith("--site="):
            k, v = a.split("=", 1)
            changed, site = (v, site) if k == "--changed" else (changed, v)
        elif a.startswith("-"):
            raise ValueError(f"unknown option {show(a, 40)}")
        else:
            files.append(a)
        i += 1
    if not files:
        raise ValueError("give at least one receipt file")
    if changed is not None and len(files) != 1:
        raise ValueError("with --changed, give exactly one receipt file")
    return files, changed, site


def main(argv):
    try:
        files, changed, site = parse_args(argv)
        if site is not None:
            site_base(site)
    except ValueError as e:
        print(f"check_receipt: {e}. Run with --help for how to use it.")
        return 1
    ok = True
    if changed is not None:
        text, errors = read_changes(changed)
        added = None
        if not errors:
            added, errors = check_changes(text)
        if not errors and Path(files[0]).name != Path(added).name:
            errors = [f"the receipt to check is not the file the pull request adds ({show(added)})"]
        if errors:
            ok = False
            print("FAIL: the pull request must add exactly one receipt and change nothing else.")
            for e in errors:
                print(f"  - {e}")
        else:
            print(f"OK: the pull request adds one receipt, {show(added)}, and changes nothing else.")
    for f in files:
        r, errors = check_file(f)
        if not errors and site is not None:
            errors = check_site(site, r)
        if errors:
            ok = False
            print(f"FAIL: receipt {show(f)}")
            for e in errors:
                print(f"  - {e}")
        else:
            where = ", and the website has it" if site is not None else ""
            print(f"OK: receipt {show(f)}: {show(r['name'], 60)}, {r['date']}, upload {r['sha256'][:12]}{where}.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
