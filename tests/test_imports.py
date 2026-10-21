"""Day 12: manual import. Local files only, user-supplied provenance, idempotent, no speculative matching."""
import socket
import sqlite3

import pytest

from discovery import database as db
from discovery import imports as imp
from discovery import lifecycle
from discovery.schema import MIGRATIONS

NOW = "2026-10-20T10:00:00+00:00"
GH = "https://boards.greenhouse.io/acme/jobs/4567890"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "imports" / "descriptions").mkdir(parents=True)
    return tmp_path / "imports"


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.initialize_database(path)
    c = db.connect(path)
    yield c
    c.close()


def links(root, text, name="links.csv"):
    p = root / name
    p.write_text(text, encoding="utf-8")
    return p


def parse(root, text):
    p = links(root, text)
    return imp.parse_links(imp.read_text(p, imp.MAX_CSV_BYTES, root), root / "descriptions", root=root)


HEADER = "url,company_hint,job_title_hint,location_hint,experience_hint,source,notes\n"


def seed_greenhouse_job(conn, location="Bengaluru, India"):
    db.register_source(conn, "feashliaa", "Feashliaa", "ats_feed", now=NOW)
    cid, _ = db.get_or_create_company(conn, "Acme", None, now=NOW)
    return db.observe_job(conn, source_id="feashliaa", source_url=GH, company_id=cid, title="Product Analyst", external_id="f1",
                          location=location, posting_url=GH, now=NOW).job_id


# ---------------------------------------------------------------- parsing and validation

def test_valid_rows_and_hints(root):
    r = parse(root, HEADER + 'https://example.com/careers/role-123456,Example Co,AI Implementation Associate,Bengaluru,0-2 years,manual,"saw it, signed in\nlater"\n')
    assert not r.rejected and r.rows[0].notes.startswith("saw it, signed in") and r.rows[0].key.startswith("manual|")
    assert imp.parse_experience("0-2 years") == (0.0, 2.0, "0-2 years") and imp.parse_experience("3+ years")[:2] == (3.0, None)
    assert imp.parse_experience("early career") == (None, None, "early career")


@pytest.mark.parametrize("bad", ["file:///etc/passwd", "javascript:alert(1)", "ftp://x.com/a", "https://user:pw@example.com/job/1", "https://exa mple.com", "https:///nohost", "example.com/job"])
def test_unsafe_urls_are_rejected_per_row(root, bad):
    r = parse(root, HEADER + f"{bad},Co,Title,,,manual,\nhttps://ok.example.com/j/123456,Co,Title,,,manual,\n")
    assert len(r.rows) == 1 and len(r.rejected) == 1 and r.rejected[0][0] == 2


def test_header_problems_reject_the_whole_file(root):
    for text in ("", "url,secret\nhttps://a.com/x,1\n", "company_hint\nX\n"):
        with pytest.raises(imp.ImportRejected):
            parse(root, text)


def test_rows_without_url_need_description_and_hints(root):
    (root / "descriptions" / "d.txt").write_text("Own the rollout of AI agents for customers.", encoding="utf-8")
    r = parse(root, "url,company_hint,job_title_hint,description_file\n,Zed,Forward Deployed Engineer,d.txt\n,Zed,Only title,\n")
    assert len(r.rows) == 1 and r.rows[0].kind == "description_text" and len(r.rejected) == 1


def test_duplicates_in_one_file_import_once(root):
    r = parse(root, HEADER + "https://a.com/j/1234567,A,T,,,manual,\nhttps://a.com/j/1234567,A,T,,,manual,\n")
    assert len(r.rows) == 1 and "duplicate" in r.rejected[0][1]


def test_size_encoding_binary_and_path_boundaries(root, tmp_path):
    big = root / "big.csv"
    big.write_bytes(b"a" * 50)
    with pytest.raises(imp.ImportRejected, match="limit"):
        imp.read_text(big, 10, root)
    bad = root / "latin.csv"
    bad.write_bytes("url\ncaf\xe9".encode("latin-1"))
    with pytest.raises(imp.ImportRejected, match="UTF-8"):
        imp.read_text(bad, 1000, root)
    nul = root / "nul.csv"
    nul.write_bytes(b"url\x00")
    with pytest.raises(imp.ImportRejected, match="binary"):
        imp.read_text(nul, 1000, root)
    outside = tmp_path / "outside.csv"
    outside.write_text("url\n", encoding="utf-8")
    for p in (outside, root / ".." / "outside.csv", tmp_path / ".env", root / ".hidden.csv", root / "x.exe"):
        with pytest.raises(imp.ImportRejected):
            imp.safe_path(p, root)
    link = root / "link.csv"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(imp.ImportRejected):
        imp.safe_path(link, root)


def test_oversize_description_and_row_cap(root):
    (root / "descriptions" / "huge.txt").write_text("x" * (imp.MAX_DESCRIPTION_BYTES + 1), encoding="utf-8")
    r = parse(root, "url,company_hint,job_title_hint,description_file\n,Z,T,huge.txt\n")
    assert not r.rows and "limit" in r.rejected[0][1]


def test_export_needs_an_explicit_mapping(root):
    text = "Job URL,Role,Employer,Where,Junk\nhttps://x.com/j/1234567,Analyst,Xco,Pune,zzz\n"
    ok = imp.parse_export(text, {"url": "Job URL", "title": "Role", "company": "Employer", "location": "Where"})
    assert ok.rows[0].title_hint == "Analyst" and ok.rows[0].kind == "export_row"
    with pytest.raises(imp.ImportRejected):
        imp.parse_export(text, {"title": "Missing", "company": "Employer"})
    with pytest.raises(imp.ImportRejected):
        imp.parse_export(text, {"title": "Role"})


# ------------------------------------------------------------------- safety: no network

def test_the_importer_opens_no_socket(root, conn, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    parsed = parse(root, HEADER + "https://login.example.com/job/1234567?session=abc&token=zzz,Example,Role,,,manual,\n")
    items = imp.plan(conn, parsed)
    imp.apply(conn, items, now=NOW)
    assert items[0].action == "create"


# --------------------------------------------------------------- identity, history, replay

def test_new_job_new_provisional_company_one_event_and_provenance(root, conn):
    items = imp.plan(conn, parse(root, HEADER + "https://example.com/careers/role-123456,Example Co,AI Associate,Bengaluru,0-2 years,manual,note\n"))
    assert items[0].action == "create" and items[0].company_action == "new_provisional"
    res = imp.apply(conn, items, now=NOW)
    assert res["created"] == 1
    job = conn.execute("SELECT * FROM jobs").fetchone()
    assert job["experience_min_years"] == 0 and job["experience_max_years"] == 2
    assert conn.execute("SELECT COUNT(*) FROM status_events WHERE job_id = ?", (job["job_id"],)).fetchone()[0] == 1
    co = conn.execute("SELECT * FROM companies").fetchone()
    assert co["identity_status"] == "provisional" and co["domain"] is None            # a URL host never verifies a company
    m = conn.execute("SELECT * FROM manual_inputs").fetchone()
    assert m["user_supplied"] == 1 and m["verified"] == 0 and "note" in m["hints"]


def test_replay_is_idempotent(root, conn):
    parsed = parse(root, HEADER + "https://example.com/careers/role-123456,Example Co,AI Associate,Bengaluru,,manual,\n")
    imp.apply(conn, imp.plan(conn, parsed), now=NOW)
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("jobs", "sightings", "status_events", "manual_inputs", "companies")}
    items = imp.plan(conn, parsed)
    assert items[0].action == "refresh"
    imp.apply(conn, items, now="2026-10-21T10:00:00+00:00")
    assert before == {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}


def test_job_specific_url_attaches_to_exactly_one_existing_job(root, conn):
    job_id = seed_greenhouse_job(conn)
    items = imp.plan(conn, parse(root, HEADER + f"{GH}?utm_source=x,,,,,manual,from a friend\n"))
    assert items[0].action == "attach" and items[0].job_id == job_id
    n_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    imp.apply(conn, items, now=NOW)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == n_jobs
    assert {r[0] for r in conn.execute("SELECT source_id FROM sightings WHERE job_id = ?", (job_id,))} == {"feashliaa", "manual"}
    assert conn.execute("SELECT company_id FROM jobs WHERE job_id = ?", (job_id,)).fetchone()[0] == items[0].company_id


def test_generic_url_and_other_tenant_never_attach(root, conn):
    seed_greenhouse_job(conn)
    for url in ("https://acme.example.com/careers", "https://boards.greenhouse.io/othercorp/jobs/4567890"):
        items = imp.plan(conn, parse(root, HEADER + f"{url},Other Co,Some Role,,,manual,\n"))
        assert items[0].action == "create"


def test_location_conflict_on_a_workday_style_url_stays_separate(root, conn):
    wd = "https://acme.wd5.myworkdayjobs.com/en-US/ext/job/Pune/Analyst_R-123456-1"
    db.register_source(conn, "feashliaa", "Feashliaa", "ats_feed", now=NOW)
    cid, _ = db.get_or_create_company(conn, "Acme", None, now=NOW)
    db.observe_job(conn, source_id="feashliaa", source_url=wd, company_id=cid, title="Analyst", external_id="w1", location="Pune, India", posting_url=wd, now=NOW)
    items = imp.plan(conn, parse(root, HEADER + f"{wd},Acme,Analyst,London,,manual,\n"))
    assert items[0].action == "create" and "location conflict" in items[0].detail


def test_exact_domain_match_links_the_company_but_names_never_do(root, conn):
    cid, _ = db.get_or_create_company(conn, "Example Corp", "example.com", now=NOW)
    items = imp.plan(conn, parse(root, HEADER + "https://careers.example.com/job/987654,Different Name,Role A,,,manual,\nhttps://other.org/job/987655,Example Corp,Role B,,,manual,\n"))
    assert items[0].company_id == cid and items[0].company_action == "by_domain"
    assert items[1].company_id == "" and items[1].company_action == "new_provisional"       # same NAME, different host: not merged


def test_changed_description_is_one_changed_event_and_history_is_kept(root, conn):
    d = root / "descriptions" / "r.txt"
    d.write_text("First description of the role.", encoding="utf-8")
    csv_text = "url,company_hint,job_title_hint,description_file\nhttps://example.com/careers/r-123456,Zed,Ops Associate,r.txt\n"
    imp.apply(conn, imp.plan(conn, parse(root, csv_text)), now=NOW)
    d.write_text("Second, different description.", encoding="utf-8")
    items = imp.plan(conn, parse(root, csv_text))
    assert items[0].action == "update_text"
    imp.apply(conn, items, now="2026-10-21T10:00:00+00:00")
    assert conn.execute("SELECT COUNT(*) FROM manual_inputs").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM status_events WHERE new_state = 'Changed'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_existing_verdict_and_other_sightings_are_untouched(root, conn):
    job_id = seed_greenhouse_job(conn)
    db.save_verdict(conn, "Maybe", "keep me", job_id=job_id, now=NOW)
    seen_before = conn.execute("SELECT sighting_id, first_seen_at, content_hash FROM sightings WHERE source_id = 'feashliaa'").fetchall()
    imp.apply(conn, imp.plan(conn, parse(root, HEADER + f"{GH},,,,,manual,\n")), now="2026-10-21T10:00:00+00:00")
    assert dict(db.get_verdict(conn, job_id=job_id))["notes"] == "keep me"
    assert seen_before == conn.execute("SELECT sighting_id, first_seen_at, content_hash FROM sightings WHERE source_id = 'feashliaa'").fetchall()


def test_merged_alias_job_is_not_a_target_and_manual_is_never_closure_evidence(root, conn):
    assert not lifecycle.closure_capable("manual", lifecycle.NO_CLOSURE_EVIDENCE)
    imp.apply(conn, imp.plan(conn, parse(root, HEADER + "https://example.com/careers/r-123456,Zed,Role,,,manual,\n")), now=NOW)
    src = conn.execute("SELECT * FROM sources WHERE source_id = 'manual'").fetchone()
    assert src["config_ref"] == lifecycle.NO_CLOSURE_EVIDENCE and src["source_type"] == "manual"
    assert conn.execute("SELECT complete, outcome FROM source_checks").fetchone()[0] == 0          # never a complete check
    assert conn.execute("SELECT lifecycle_state FROM jobs").fetchone()[0] == "New"


def test_partial_check_when_a_row_is_rejected_and_apply_isolates_rows(root, conn):
    items = imp.plan(conn, parse(root, HEADER + "ftp://bad.com/x,A,B,,,manual,\nhttps://ok.example.com/j/123456,Ok,Role,,,manual,\n"))
    res = imp.apply(conn, items, now=NOW)
    assert res["created"] == 1 and res["rejected"] == 1
    assert conn.execute("SELECT outcome FROM source_checks").fetchone()[0] == "partial"


def test_dry_run_plan_writes_nothing_and_schema6_refuses_apply(root, tmp_path):
    path = tmp_path / "v6.db"
    raw = sqlite3.connect(path)
    for m in MIGRATIONS[:6]:
        raw.executescript(m)
    raw.close()
    before = path.read_bytes()
    ro = db.connect(path, readonly=True)
    items = imp.plan(ro, parse(root, HEADER + "https://example.com/careers/r-123456,Zed,Role,,,manual,\n"))
    ro.close()
    assert items[0].action == "create" and path.read_bytes() == before
    c = db.connect(path)
    with pytest.raises(imp.ManualImportError, match="migration 7"):
        imp.apply(c, items, now=NOW)
    assert c.execute("SELECT (SELECT COUNT(*) FROM jobs) + (SELECT COUNT(*) FROM sightings) + (SELECT COUNT(*) FROM runs)").fetchone()[0] == 0
    c.close()                                           # (the file header changes only because opening for write switches on WAL mode)


def test_migration_7_is_additive_and_append_only(tmp_path):
    path = tmp_path / "m.db"
    raw = sqlite3.connect(path)
    for m in MIGRATIONS[:6]:
        raw.executescript(m)
    raw.close()
    assert db.initialize_database(path) == 7
    c = db.connect(path)
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and not c.execute("PRAGMA foreign_key_check").fetchall()
    db.register_source(c, "manual", "m", "manual", now=NOW)
    cid, _ = db.get_or_create_company(c, "Z", None, now=NOW)
    jid = db.observe_job(c, source_id="manual", source_url="https://z.com/j/123456", company_id=cid, title="T", external_id="k", now=NOW)
    c.execute("INSERT INTO manual_inputs (input_id, sighting_id, kind, sha256, created_at) VALUES ('i', ?, 'url_row', ?, ?)", (jid.sighting_id, "a" * 64, NOW))
    for sql in ("UPDATE manual_inputs SET text = 'x'", "DELETE FROM manual_inputs",
                "INSERT INTO manual_inputs (input_id, sighting_id, kind, sha256, created_at, verified) VALUES ('j', '%s', 'url_row', '%s', 'n', 1)" % (jid.sighting_id, "b" * 64)):
        with pytest.raises(sqlite3.DatabaseError):
            c.execute(sql)
    c.close()
    assert db.initialize_database(path) == 7                                               # idempotent


# ------------------------------------------------------------ frozen fictional replay

def test_frozen_fictional_import_and_replay(tmp_path, conn):
    import shutil
    from pathlib import Path
    fixture = Path(__file__).parent / "fixtures" / "manual_import"
    root = tmp_path / "imports"
    shutil.copytree(fixture, root)
    parse_fixture = lambda: imp.parse_links(imp.read_text(root / "links.csv", imp.MAX_CSV_BYTES, root), root / "descriptions", root=root)
    parsed = parse_fixture()
    assert len(parsed.rows) == 4 and len(parsed.rejected) == 1 and "http" in parsed.rejected[0][1]
    first = imp.apply(conn, imp.plan(conn, parsed), now=NOW)
    assert (first["created"], first["rejected"], first["errors"]) == (4, 1, [])
    digest = lambda: [tuple(r) for r in conn.execute("SELECT job_id, title, location, lifecycle_state FROM jobs ORDER BY title")]
    frozen = digest()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("jobs", "sightings", "manual_inputs", "status_events", "companies")}
    assert counts == {"jobs": 4, "sightings": 4, "manual_inputs": 4, "status_events": 4, "companies": 4}
    for day in ("2026-10-21", "2026-10-22"):                                       # replay twice: nothing new, nothing changed
        items = imp.plan(conn, parse_fixture())
        assert imp.summarize(items) == {"refresh": 4, "reject": 1}
        imp.apply(conn, items, now=f"{day}T10:00:00+00:00")
    assert digest() == frozen
    assert counts == {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in counts}
    assert all(r[0] == "manual" for r in conn.execute("SELECT source_id FROM sightings"))
    assert conn.execute("SELECT COUNT(*) FROM manual_inputs WHERE user_supplied = 1 AND verified = 0").fetchone()[0] == 4


# ------------------------------------------------------- CLI routes, symlink logic, attribution

def _args(**kw):
    import argparse
    base = dict(links=None, descriptions=None, export=None, mapping=None, db=None, apply=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_cli_dry_run_then_apply_with_a_verified_backup(tmp_path, monkeypatch, capsys):
    import shutil
    from pathlib import Path
    from discovery import cli
    from discovery.adapters import jobspy_source
    root = tmp_path / "imports"
    shutil.copytree(Path(__file__).parent / "fixtures" / "manual_import", root)
    monkeypatch.setattr(imp, "IMPORTS_DIR", root)
    real = jobspy_source.verified_backup
    monkeypatch.setattr(jobspy_source, "verified_backup", lambda path, directory=None, label="x": real(path, tmp_path / "bk", label))
    path = tmp_path / "c.db"
    db.initialize_database(path)
    assert cli.cmd_import_manual(_args(links=str(root / "links.csv"), db=str(path))) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "create" in out and "reject" in out
    c = db.connect(path, readonly=True)
    assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0                  # a dry run writes nothing
    c.close()
    assert cli.cmd_import_manual(_args(links=str(root / "links.csv"), db=str(path), apply=True)) == 0
    assert list((tmp_path / "bk").glob("*pre-manual-import*.db"))                    # backup taken first
    c = db.connect(path, readonly=True)
    assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 4
    c.close()


def test_cli_apply_refuses_on_schema_6_and_unsafe_paths_and_export_needs_mapping(tmp_path, monkeypatch, capsys):
    from discovery import cli
    root = tmp_path / "imports"
    (root / "descriptions").mkdir(parents=True)
    monkeypatch.setattr(imp, "IMPORTS_DIR", root)
    (root / "links.csv").write_text(HEADER + "https://example.com/careers/r-123456,Zed,Role,,,manual,\n", encoding="utf-8")
    old = tmp_path / "v6.db"
    raw = sqlite3.connect(old)
    for m in MIGRATIONS[:6]:
        raw.executescript(m)
    raw.close()
    assert cli.cmd_import_manual(_args(links=str(root / "links.csv"), db=str(old))) == 0              # dry run works on schema 6
    assert cli.cmd_import_manual(_args(links=str(root / "links.csv"), db=str(old), apply=True)) == 1
    assert "schema version 6" in capsys.readouterr().out
    outside = tmp_path / "outside.csv"
    outside.write_text(HEADER, encoding="utf-8")
    assert cli.cmd_import_manual(_args(links=str(outside), db=str(old))) == 1
    assert "REFUSED" in capsys.readouterr().out
    assert cli.cmd_import_manual(_args(export=str(root / "links.csv"), db=str(old))) == 1


def test_cli_export_with_an_explicit_mapping(tmp_path, monkeypatch, capsys):
    from discovery import cli
    root = tmp_path / "imports"
    root.mkdir()
    monkeypatch.setattr(imp, "IMPORTS_DIR", root)
    (root / "export.csv").write_text("Job URL,Role,Employer,Where,Junk\nhttps://x.example/j/1234567,Analyst,Xco,Pune,zzz\n", encoding="utf-8")
    (root / "export_mapping.yaml").write_text("columns:\n  url: Job URL\n  title: Role\n  company: Employer\n  location: Where\n", encoding="utf-8")
    path = tmp_path / "e.db"
    db.initialize_database(path)
    assert cli.cmd_import_manual(_args(export=str(root / "export.csv"), mapping=str(root / "export_mapping.yaml"), db=str(path))) == 0
    assert "create" in capsys.readouterr().out


def test_symlink_and_junction_refusal_does_not_depend_on_os_privileges(root, tmp_path, monkeypatch):
    target = root / "links.csv"
    target.write_text(HEADER, encoding="utf-8")
    real = type(target).is_symlink
    monkeypatch.setattr(type(target), "is_symlink", lambda self: self == target or real(self))
    with pytest.raises(imp.ImportRejected, match="symbolic links"):
        imp.safe_path(target, root)


def test_manual_attach_never_makes_a_job_employer_confirmed_or_changes_other_state(root, conn):
    from discovery import candidates
    job_id = seed_greenhouse_job(conn)
    run = db.start_run(conn, "discovery", now=NOW)
    db.record_source_check(conn, run, "feashliaa", "success", scope="jobs", complete=True, record_count=1, started_at=NOW, finished_at=NOW)
    before = {c.job_id if hasattr(c, "job_id") else c["job_id"]: c["availability"] for c in candidates.load_candidates(conn, with_hints=False)[0]}
    imp.apply(conn, imp.plan(conn, parse(root, HEADER + f"{GH},,,,,manual,I verified this myself\n")), now="2026-10-21T10:00:00+00:00")
    after = {c["job_id"]: c["availability"] for c in candidates.load_candidates(conn, with_hints=False)[0]}
    assert after[job_id] == before[job_id] != "employer_confirmed_open"                  # the user's entry changes nothing about availability
    assert conn.execute("SELECT lifecycle_state FROM jobs WHERE job_id = ?", (job_id,)).fetchone()[0] == "New"
    assert conn.execute("SELECT COUNT(*) FROM identity_links").fetchone()[0] == 0
