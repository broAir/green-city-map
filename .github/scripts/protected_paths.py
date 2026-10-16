"""Refuse files that Claude Code would load or run from this repository.

The plugin in .claude-plugin/marketplace.json is the skill/ folder. In a plugin folder, Claude Code
loads some files with no manifest entry: bin/ (put on the Bash tool's PATH), hooks/hooks.json,
.mcp.json, .lsp.json, monitors/, settings.json, agents/, commands/, and others. In a clone opened in
Claude Code, .claude/ and .mcp.json at the root do the same. None of them belong in this repository,
so a commit that adds one is refused, whoever makes it.

The GitHub check "Protected paths" (.github/workflows/protected-paths.yml) runs this on every pull
request, with the copy of this file from the default branch.

Usage:
  git ls-tree -r -z --full-tree <commit> > tree.bin
  python3 -I .github/scripts/protected_paths.py tree.bin

Exit code 0 when nothing is refused, 1 otherwise. Standard library only; never runs git.
"""
import hashlib
import json
import sys

MAX_LISTING = 64 * 1024 * 1024
PLUGIN_ROOTS = ("skill/", "")      # the plugin folder, and the repository root in case the plugin ever moves back
# default component locations under a plugin root (code.claude.com/docs/en/plugins-reference, "Standard layout")
COMPONENTS = ("bin", "hooks", "commands", "agents", "skills", "output-styles", "workflows", "themes", "monitors",
              "settings.json", ".mcp.json", ".lsp.json", ".claude-plugin", ".claude")
ALLOWED = {".claude-plugin/marketplace.json", ".claude/skills/green-city-map"}
# the one symbolic link in the repository: .claude/skills/green-city-map -> ../../skill
LINK, LINK_TARGET = ".claude/skills/green-city-map", b"../../skill"
LINK_BLOB = hashlib.sha1(b"blob %d\x00" % len(LINK_TARGET) + LINK_TARGET).hexdigest()


def show(value, limit=120):
    """A path from the commit, quoted on one line (never starts a workflow command)."""
    s = json.dumps(value, ensure_ascii=True)
    return s if len(s) <= limit else s[:limit - 4] + '..."'


def protected(path):
    """Why path may not be in the repository, or None."""
    if path in ALLOWED:
        return None
    for root in PLUGIN_ROOTS:
        if not path.startswith(root):
            continue
        rest = path[len(root):]
        for name in COMPONENTS:
            if rest == name or rest.startswith(name + "/"):
                where = "the plugin folder skill/" if root else "the repository root"
                return f"{name} in {where} is loaded or run by Claude Code"
    if path == ".gitmodules":
        return "submodules are not allowed"
    return None


def check(listing):
    """Problems in `git ls-tree -r -z --full-tree` output (an empty list when all is fine)."""
    problems = []
    entries = [e for e in listing.split(b"\x00") if e]
    for raw in entries:
        try:
            meta, path = raw.split(b"\t", 1)
            mode, kind, obj = meta.decode("ascii").split(" ")
            path = path.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            problems.append(f"an entry can't be read: {show(raw.decode('utf-8', 'replace'))}")
            continue
        why = protected(path)
        if why:
            problems.append(f"{show(path)}: {why}")
        elif mode == "160000" or kind == "commit":
            problems.append(f"{show(path)}: submodules are not allowed")
        elif mode == "120000":
            if path != LINK:
                problems.append(f"{show(path)}: symbolic links are not allowed")
            elif obj != LINK_BLOB:
                problems.append(f"{show(path)}: this link must point to ../../skill")
        elif kind != "blob" or mode not in ("100644", "100755"):
            problems.append(f"{show(path)}: unexpected entry type {show(mode + ' ' + kind, 40)}")
    if not entries:
        problems.append("the commit has no files")
    return problems


def main(argv):
    if len(argv) != 1:
        print("protected_paths: give one file with the output of git ls-tree -r -z --full-tree <commit>.")
        return 1
    try:
        with open(argv[0], "rb") as f:
            listing = f.read(MAX_LISTING + 1)
    except OSError:
        print(f"protected_paths: can't read {show(argv[0])}.")
        return 1
    if len(listing) > MAX_LISTING:
        print("FAIL: the commit has too many files to check.")
        return 1
    problems = check(listing)
    if problems:
        print("FAIL: the commit adds files that Claude Code would load or run. They don't belong in this repository.")
        for p in problems[:50]:
            print(f"  - {p}")
        if len(problems) > 50:
            print(f"  ... and {len(problems) - 50} more")
        return 1
    print("OK: no plugin, hook, agent or settings files, no links other than .claude/skills/green-city-map, no submodules.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
