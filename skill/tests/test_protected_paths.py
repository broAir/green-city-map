"""Tests for .github/scripts/protected_paths.py (the "Protected paths" GitHub check). Standard library only.

Run from the repository root:  python3 -I -m unittest discover -s skill/tests -v
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("protected_paths", ROOT / ".github" / "scripts" / "protected_paths.py")
pp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pp)

BLOB = "0" * 40


def entry(path, mode="100644", kind="blob", obj=BLOB):
    return f"{mode} {kind} {obj}\t{path}".encode() + b"\x00"


def listing(*entries):
    return b"".join(entries)


GOOD = (entry("README.md"), entry("skill/SKILL.md"), entry("skill/run_city.sh", "100755"),
        entry("skill/pipeline/check_receipt.py"), entry(".claude-plugin/marketplace.json"),
        entry(".github/workflows/submission-check.yml"), entry(".submissions/README.md"),
        entry(".claude/skills/green-city-map", "120000", obj=pp.LINK_BLOB))


class Paths(unittest.TestCase):
    def test_the_repository_layout_passes(self):
        self.assertEqual(pp.check(listing(*GOOD)), [])

    def test_link_blob_is_the_real_one(self):
        # git hash-object of a symbolic link is the hash of its target text
        self.assertEqual(pp.LINK_BLOB, hashlib.sha1(b"blob 11\x00../../skill").hexdigest())
        link = ROOT / ".claude" / "skills" / "green-city-map"
        if link.is_symlink():
            self.assertEqual(str(link.readlink()).encode(), pp.LINK_TARGET)

    def test_plugin_components_are_refused(self):
        for path in ("skill/bin/gh", "skill/bin/python3", "skill/hooks/hooks.json", "skill/.mcp.json", "skill/.lsp.json",
                     "skill/monitors/monitors.json", "skill/settings.json", "skill/agents/a.md", "skill/commands/c.md",
                     "skill/skills/x/SKILL.md", "skill/output-styles/s.md", "skill/workflows/w.js", "skill/themes/t.json",
                     "skill/.claude-plugin/plugin.json", "skill/.claude/settings.json",
                     "bin/gh", "hooks/hooks.json", ".mcp.json", ".lsp.json", "monitors/monitors.json", "settings.json",
                     "agents/a.md", "commands/c.md", "workflows/w.js",
                     ".claude/settings.json", ".claude/settings.local.json", ".claude/hooks/x.sh", ".claude/agents/a.md",
                     ".claude/skills/other/SKILL.md", ".claude-plugin/plugin.json", ".gitmodules"):
            with self.subTest(path=path):
                problems = pp.check(listing(*GOOD, entry(path)))
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(json.dumps(path), problems[0])

    def test_similar_names_are_fine(self):
        for path in ("skill/pipeline/bin.py", "skill/binaries.txt", "skill/viewer/hooks.js", "docs/settings.json.md",
                     "skill/config/settings.yaml", "registry/pl/krakow/agents.yaml", ".github/workflows/x.yml",
                     "skill/tests/test_hooks.py", "spec/commands.md"):
            with self.subTest(path=path):
                self.assertEqual(pp.check(listing(*GOOD, entry(path))), [])

    def test_links_and_submodules(self):
        self.assertTrue(pp.check(listing(*GOOD, entry("skill/passwd", "120000"))))
        self.assertTrue(pp.check(listing(*GOOD, entry("docs/x", "120000"))))
        self.assertTrue(pp.check(listing(*GOOD, entry("skill/vendor", "160000", "commit"))))
        retarget = [e for e in GOOD if b".claude/skills" not in e] + [entry(".claude/skills/green-city-map", "120000", obj="1" * 40)]
        self.assertIn("must point to ../../skill", " ".join(pp.check(listing(*retarget))))
        as_file = [e for e in GOOD if b".claude/skills" not in e] + [entry(".claude/skills/green-city-map")]
        self.assertEqual(pp.check(listing(*as_file)), [])     # a plain file there is harmless (it isn't a skill)

    def test_odd_entries(self):
        self.assertIn("can't be read", " ".join(pp.check(listing(*GOOD, b"garbage\x00"))))
        self.assertIn("unexpected entry", " ".join(pp.check(listing(*GOOD, entry("x", "040000", "tree")))))
        self.assertIn("no files", " ".join(pp.check(b"")))
        weird = "skill/bin/‮gh\n::error::x"
        problems = pp.check(listing(*GOOD, entry(weird)))
        self.assertEqual(len(problems), 1)
        self.assertNotIn("\n", problems[0])
        self.assertNotIn("‮", problems[0])

    def test_main(self):
        with tempfile.TemporaryDirectory() as d:
            ok, bad = Path(d) / "ok.bin", Path(d) / "bad.bin"
            ok.write_bytes(listing(*GOOD))
            bad.write_bytes(listing(*GOOD, entry("skill/hooks/hooks.json")))
            for path, want in ((ok, 0), (bad, 1), (Path(d) / "missing.bin", 1)):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = pp.main([str(path)])
                with self.subTest(path=path.name):
                    self.assertEqual(code, want, out.getvalue())
                    for line in out.getvalue().splitlines():
                        self.assertFalse(line.startswith("::"), line)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pp.main([]), 1)


if __name__ == "__main__":
    unittest.main()
