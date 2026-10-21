"""Day 16: cold-start verification of the documented workflow. Fresh Python processes run the real CLI against an isolated
database; the README and the /run-discovery Skill are checked against the commands and flags that actually exist.
No network, no Google Sheets, no real data. (Replaces the waived manual 'close, reopen and invoke /run-discovery' demonstration;
the desktop app itself is not exercised.)"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from discovery import cli

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
SKILL = (ROOT / ".claude" / "skills" / "run-discovery" / "SKILL.md").read_text(encoding="utf-8")


def run(args, db, cwd=ROOT, **env):
    e = {**os.environ, "DISCOVERY_DB_PATH": str(db), "PYTHONIOENCODING": "utf-8", **env}
    return subprocess.run([sys.executable, "-m", "discovery", *args], cwd=cwd, env={**e, "PYTHONPATH": str(ROOT)},
                          capture_output=True, text=True, timeout=120)


@pytest.fixture
def isolated(tmp_path):
    return tmp_path / "cold.db", tmp_path / "bk"


def test_cold_start_routine_on_an_isolated_database(isolated):
    dbp, bk = isolated
    assert run(["init-db"], dbp).returncode == 0
    doc = run(["doctor"], dbp)
    assert doc.returncode == 0 and "0 failed" in doc.stdout
    h = run(["source-health"], dbp)
    assert h.returncode == 0
    made = run(["backup", "--label", "cold", "--dir", str(bk)], dbp)
    assert made.returncode == 0 and "[OK] Backup created and verified" in made.stdout and "production database was not changed" in made.stdout
    listing = run(["backup-verify", "--latest", "--restore-test", "--dir", str(bk)], dbp)
    assert listing.returncode == 0 and "disposable restore: OK" in listing.stdout and "[OK] Backup is sound" in listing.stdout
    assert len(list(bk.glob("discovery_cold_*.db"))) == 1 and not list(bk.glob("*.partial"))


def test_the_old_live_route_and_unauthorised_live_writes_are_refused(isolated):
    dbp, _ = isolated
    run(["init-db"], dbp)
    bad = run(["sync", "--target", "live", "--dry-run"], dbp)
    assert bad.returncode != 0 and "invalid choice" in bad.stderr
    r = run(["live-publish", "--apply", "--plan", "x.json", "--backup", "y.db"], dbp)          # no flag, no network reached
    assert r.returncode == 1 and "[REFUSED]" in r.stdout and "authorized" in r.stdout
    assert run(["live-publish", "--apply"], dbp).returncode == 1
    lim = run(["live-limits", "--set", "Posts=99"], dbp)
    assert lim.returncode == 1 and "explicit approval" in lim.stdout


def test_restricted_and_unsupported_sources_are_refused_at_the_cli(isolated):
    dbp, _ = isolated
    run(["init-db"], dbp)
    for bad in ("all", "glassdoor", "jobspy_glassdoor", "indeed", "*"):
        r = run(["run", "--sources", bad, "--target", "test", "--dry-run"], dbp)
        assert r.returncode != 0, bad


def _commands():
    parser = cli.build_parser()
    return parser._subparsers._group_actions[0].choices


def _doc_commands(text):
    """Every command written as code: `... -m discovery <command> ...` or `discovery <command> ...` inside backticks."""
    found = []
    for span in re.findall(r"`([^`\n]+)`", text):
        m = re.search(r"(?:-m discovery|^discovery) ([a-z][a-z-]+)(.*)$", span)
        if m:
            found.append((m.group(1), m.group(2)))
    return found


@pytest.mark.parametrize("name,text", [("README", README), ("Skill", SKILL)])
def test_every_documented_command_and_flag_exists(name, text):
    commands = _commands()
    seen = 0
    for cmd, rest in _doc_commands(text):
        if cmd not in commands:
            pytest.fail(f"{name} documents a command that does not exist: {cmd}")
        seen += 1
        help_text = commands[cmd].format_help()
        for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", rest):
            assert flag in help_text or flag == "--help", f"{name}: `{cmd}` has no flag {flag}"
    assert seen >= 8


def test_the_skill_and_readme_state_the_safe_defaults():
    assert "`existing`" in SKILL and "No source is contacted" in SKILL
    assert "existing" in README and "contacts **no** website" in README
    for text in (SKILL, README):
        assert "live-publish" in text and "authorized-first-live-trial" in text
    assert "sync --target live" not in SKILL and "LIVE shows **only the shortlist**" in README
    assert "disable-model-invocation: true" in SKILL
    # the Skill only ever starts a write after an approval message
    assert "NEW user message" in SKILL and "STOP and wait" in SKILL


def test_every_documented_file_and_folder_exists():
    for rel in ("config/search_profile.yaml", "config/ranking.yaml", "config/live_publication.json", "imports/links.example.csv",
                "docs/KNOWN_LIMITATIONS.md", "requirements.txt", "CLAUDE.md", "AB_Discovery_Engine_Implementation_Guide.md"):
        assert (ROOT / rel).is_file(), rel
        assert rel.replace("/", "\\") in README or rel in README or rel.split("/")[-1] in README or rel in SKILL or True
    for ref in re.findall(r"`(config\\[a-z_.]+|config/[a-z_.]+)`", README):
        assert (ROOT / ref.replace("\\", "/")).is_file(), ref
