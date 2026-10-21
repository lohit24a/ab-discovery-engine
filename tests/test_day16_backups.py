"""Day 16: the user-facing backup command, its verification and the disposable restore test."""
import hashlib
import sqlite3
import time

import pytest

from discovery import backups, cli, database as db


@pytest.fixture
def prod(tmp_path):
    path = tmp_path / "discovery.db"
    db.initialize_database(path)
    c = db.connect(path)
    db.register_source(c, "yc", "YC Directory", "yc")
    db.observe_company(c, source_id="yc", source_url="https://yc.example/a", name="Acme AI", external_id="a", domain="acme.example", now="2026-10-20T10:00:00+00:00")
    c.close()
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_backup_is_created_verified_and_leaves_production_untouched(prod, tmp_path):
    before = sha(prod)
    made = backups.create_backup(prod, tmp_path / "bk", "unit")
    assert made.name.startswith("discovery_unit_") and made.suffix == ".db" and made.parent == tmp_path / "bk"
    assert backups.manifest_path(made).is_file()
    ok, lines = backups.verify_backup(made, restore_test=True)
    assert ok and any("disposable restore: OK" in x for x in lines)
    assert sha(prod) == before                                            # production bytes unchanged


def test_a_backup_never_overwrites_an_existing_one(prod, tmp_path):
    fixed = time.localtime(1_800_000_000)
    a = backups.create_backup(prod, tmp_path / "bk", "same", now=fixed)
    first = sha(a)
    b = backups.create_backup(prod, tmp_path / "bk", "same", now=fixed)
    assert a != b and sha(a) == first and b.name.endswith("-2.db")


def test_backup_works_while_the_database_is_open_for_writing(prod, tmp_path):
    writer = db.connect(prod)
    db.register_source(writer, "ats", "Fake ATS", "ats_feed")
    made = backups.create_backup(prod, tmp_path / "bk", "live")
    writer.close()
    ok, _ = backups.verify_backup(made, restore_test=True)
    assert ok


def test_a_corrupt_source_is_refused_and_leaves_no_backup_file(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a database" * 100)
    with pytest.raises(backups.BackupError):
        backups.create_backup(bad, tmp_path / "bk", "bad")
    assert not list((tmp_path / "bk").glob("discovery_*"))


def test_missing_database_and_bad_label_are_refused(tmp_path, prod):
    with pytest.raises(backups.BackupError):
        backups.create_backup(tmp_path / "none.db", tmp_path / "bk")
    for label in ("../evil", "a b", "", "x" * 41):
        with pytest.raises(backups.BackupError):
            backups.create_backup(prod, tmp_path / "bk", label)


def test_verify_detects_a_damaged_backup(prod, tmp_path):
    made = backups.create_backup(prod, tmp_path / "bk", "dmg")
    con = sqlite3.connect(made)
    con.execute("DELETE FROM companies")
    con.commit()
    con.close()
    ok, lines = backups.verify_backup(made, restore_test=True)
    assert not ok and any("MISMATCH" in x for x in lines)


def test_restore_test_compares_every_table_with_the_source(prod, tmp_path):
    made = backups.create_backup(prod, tmp_path / "bk", "cmp")
    manifest = __import__("json").loads(backups.manifest_path(made).read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"file:{prod.as_posix()}?mode=ro", uri=True)
    assert manifest["tables"] == backups.table_digests(conn) and manifest["tables"]["companies"]["rows"] == 1
    conn.close()


def test_cli_backup_commands(prod, tmp_path, capsys):
    ns = type("A", (), {"label": "cli", "db": str(prod), "dir": str(tmp_path / "bk")})()
    assert cli.cmd_backup(ns) == 0
    out = capsys.readouterr().out
    assert "[OK] Backup created and verified" in out and "production database was not changed" in out
    v = type("A", (), {"path": None, "latest": True, "restore_test": True, "dir": str(tmp_path / "bk")})()
    assert cli.cmd_backup_verify(v) == 0 and "[OK] Backup is sound" in capsys.readouterr().out
    empty = type("A", (), {"path": None, "latest": True, "restore_test": False, "dir": str(tmp_path / "none")})()
    assert cli.cmd_backup_verify(empty) == 1
    bad = type("A", (), {"label": "x y", "db": str(prod), "dir": str(tmp_path / "bk")})()
    assert cli.cmd_backup(bad) == 1 and "[FAIL]" in capsys.readouterr().out


def test_live_limits_come_from_the_operator_file_and_never_default_larger(tmp_path):
    from discovery import live_publish as lp
    assert lp.load_limits() == lp.DEFAULT_LIMITS                           # the shipped file holds the approved first-trial numbers
    (tmp_path / "config").mkdir()
    assert lp.load_limits(tmp_path) == lp.DEFAULT_LIMITS                   # missing file
    (tmp_path / "config" / "live_publication.json").write_text("{not json", encoding="utf-8")
    assert lp.load_limits(tmp_path) == lp.DEFAULT_LIMITS                   # malformed file
    (tmp_path / "config" / "live_publication.json").write_text('{"max_rows": {"Shortlist": 25, "Posts": 40, "Companies": 40, "Runs": 5}}', encoding="utf-8")
    assert lp.load_limits(tmp_path)["Posts"] == 40                         # an approved change is just a number in the file


def _fake_plan(held=(24, 19, 23, 1), add=(0, 16, 13, 1), rows=25):
    tabs = {t: {"data_rows": n} for t, n in zip(("Shortlist", "Posts", "Companies", "Runs"), held)}
    return {"inspection": {"tabs": tabs}, "counts": {"appended": dict(zip(("Shortlist", "Posts", "Companies", "Runs"), add))},
            "shortlist_rows": [0] * rows, "session": "abcdef123456", "mode": "new", "violations": []}


def _limits_root(tmp_path):
    import shutil
    from discovery import live_publish as lp
    (tmp_path / "config").mkdir()
    shutil.copy(lp.db.PROJECT_ROOT / "config" / "live_publication.json", tmp_path / "config" / "live_publication.json")
    return tmp_path


def test_limits_preview_reports_overflow_without_changing_anything(tmp_path, monkeypatch, capsys):
    from discovery import live_publish as lp
    root = _limits_root(tmp_path)
    monkeypatch.setattr(lp, "make_plan", lambda *a, **k: _fake_plan())
    info = lp.limits_preview(root, new_session=True)
    assert info["rows_after_next_publication"] == {"Shortlist": 25, "Posts": 35, "Companies": 36, "Runs": 2}
    assert info["fits"] == {"Shortlist": True, "Posts": False, "Companies": False, "Runs": True}
    assert lp.load_limits(root) == lp.DEFAULT_LIMITS


def test_changing_limits_needs_the_approval_flag_and_never_goes_below_held_rows(tmp_path, monkeypatch, capsys):
    from discovery import cli, live_publish as lp
    root = _limits_root(tmp_path)
    monkeypatch.setattr(lp, "make_plan", lambda *a, **k: _fake_plan())
    ns = type("A", (), {"next_session": False, "set": ["Posts=45"], "operator_approved": False, "note": ""})()
    assert cli.cmd_live_limits(ns) == 1 and "explicit approval" in capsys.readouterr().out
    with pytest.raises(lp.PublishRefused):
        lp.set_limits({"Posts": 10}, root)                                  # below the 19 rows LIVE holds: no making capacity by deleting
    with pytest.raises(lp.PublishRefused):
        lp.set_limits({"Bogus": 99}, root)
    old, new = lp.set_limits({"Posts": 45, "Companies": 50}, root, note="operator approved")
    assert new["Posts"] == 45 and old["Posts"] == 30 and lp.load_limits(root)["Companies"] == 50
    doc = __import__("json").loads((root / "config" / "live_publication.json").read_text(encoding="utf-8"))
    assert doc["changes"][0]["from"]["Posts"] == 30 and doc["changes"][0]["note"] == "operator approved"
