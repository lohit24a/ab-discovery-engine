"""YC company adapter tests: saved fictional records shaped like the real yc-oss data. No network."""

import argparse
import copy
import inspect
import sqlite3
import urllib.error

import pytest

from discovery import cli, database as db
from discovery.adapters import yc_companies as yc

NOW = "2026-10-20T12:00:00+00:00"
UPDATED = "2026-10-20T02:41:43.163Z"


def rec(yc_id=1, name="Acme AI", **over):
    base = {
        "id": yc_id, "name": name, "slug": name.lower().replace(" ", "-"), "former_names": [],
        "small_logo_thumb_url": "https://logos.example/a.png", "website": "https://www.acme.example/home",
        "all_locations": "San Francisco, CA, USA", "long_description": "Fictional long text.",
        "one_liner": "Fictional one-liner", "team_size": 12, "industry": "B2B",
        "subindustry": "B2B -> Sales", "launched_at": 1625175137, "tags": ["SaaS"], "tags_highlighted": [],
        "top_company": False, "isHiring": False, "nonprofit": False, "batch": "Summer 2021",
        "status": "Active", "industries": ["B2B"], "regions": ["United States of America", "America / Canada"],
        "stage": "Early", "app_video_public": False, "demo_day_video_public": False, "app_answers": None,
        "question_answers": False, "url": f"https://www.ycombinator.com/companies/{name.lower().replace(' ', '-')}",
        "api": "https://yc-oss.github.io/api/batches/summer-2021/x.json",
    }
    base.update(over)
    return base


def india(yc_id=2, name="Bharat Labs", **over):
    over.setdefault("all_locations", "Bengaluru, KA, India")
    over.setdefault("regions", ["India", "South Asia"])
    over.setdefault("website", "bharat.example")  # scheme-less, as in the real data
    return rec(yc_id, name, **over)


def make_fetch(records, updated=UPDATED, count=None, meta=None):
    meta = meta or {"last_updated": updated, "companies": {"all": {"count": len(records) if count is None else count}}}

    def fetch(url):
        if url == yc.META_URL:
            return copy.deepcopy(meta)
        if url == yc.ALL_URL:
            return copy.deepcopy(records)
        raise AssertionError(f"unexpected URL {url}")
    return fetch


def snapshot_of(records, **kw):
    return yc.fetch_snapshot(make_fetch(records, **kw), now=NOW)


def run_import(conn, records, at=NOW, **kw):
    snap = snapshot_of(records, **kw)
    analysis = yc.analyze(snap, now=NOW)
    return yc.import_companies(conn, snap, analysis, now=at), analysis


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "yc.db"
    db.initialize_database(path)
    return path


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    yield c
    c.close()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def company_for(conn, yc_id):
    row = conn.execute("""SELECT c.* FROM companies c JOIN sightings s ON s.company_id = c.company_id
                          WHERE s.source_id = ? AND s.external_id = ?""", (yc.SOURCE_ID, str(yc_id))).fetchone()
    return row


# 1 -------------------------------------------------------------- normalization
def test_normal_record_is_normalized_without_inventing_anything():
    out, problem = yc.normalize_record(rec(7, "Acme AI", former_names=["Old Acme"]))
    assert problem is None
    assert out["yc_id"] == 7 and out["name"] == "Acme AI" and out["aliases"] == ["Old Acme"]
    assert out["profile_url"] == "https://www.ycombinator.com/companies/acme-ai"
    y = out["yc"]
    assert y["website_domain_reported"] == "acme.example" and y["batch"] == "Summer 2021"
    assert y["is_hiring_reported"] is False and y["launched_on"] == "2021-07-01"
    assert "funding" not in str(y).lower()


# 2
def test_missing_website_and_description_stay_missing():
    out, _ = yc.normalize_record(rec(3, website=None, long_description=None, one_liner="", team_size=None,
                                     all_locations=""))
    y = out["yc"]
    assert y["website_reported"] is None and y["website_domain_reported"] is None
    assert y["long_description"] is None and y["one_liner"] is None and y["team_size"] is None
    assert out["location"] is None


# 3
def test_unexpected_optional_fields_and_types_are_tolerated():
    raw = rec(4, brand_new_field={"x": 1}, regions="Europe", tags=None, team_size="ten", isHiring="yes",
              launched_at="soon")
    out, problem = yc.normalize_record(raw)
    assert problem is None
    y = out["yc"]
    assert y["regions"] == [] and y["tags"] == [] and y["team_size"] is None
    assert y["is_hiring_reported"] is None and y["launched_on"] is None
    assert "brand_new_field" not in y


# 4
@pytest.mark.parametrize("bad_id", [None, "12", 0, -3, True, 1.5])
def test_missing_or_malformed_ids_are_reported_and_skipped(conn, bad_id):
    records = [rec(1)] + [rec(i + 10, f"Co {i}") for i in range(30)] + [rec(bad_id, "Broken Co")]
    result, analysis = run_import(conn, records)
    assert len(analysis.malformed) == 1 and "YC id" in analysis.malformed[0]
    assert count(conn, "companies") == 31 and result.created == 31
    assert conn.execute("SELECT outcome, complete FROM source_checks").fetchone()[:] == ("partial", 0)


# 5
def test_identical_duplicate_in_input_is_dropped_and_differing_one_is_a_conflict(conn):
    records = [rec(1), rec(1), rec(2, "Beta"), rec(2, "Beta Renamed")]
    records += [rec(i + 10, f"Co {i}") for i in range(100)]
    result, analysis = run_import(conn, records)
    assert analysis.duplicates_dropped == 1
    assert len(analysis.conflicts) == 1 and "YC id 2" in analysis.conflicts[0]
    assert company_for(conn, 1) is not None and company_for(conn, 2) is None  # conflict skipped, not guessed
    assert count(conn, "companies") == 101


# 6 / 18
def test_importing_the_same_dataset_twice_creates_no_duplicates(conn):
    records = [rec(1), india(2), rec(3, "Gamma", isHiring=True)]
    first, _ = run_import(conn, records, at="2026-10-20T12:00:00+00:00")
    ids_before = {r[0] for r in conn.execute("SELECT company_id FROM companies")}
    second, _ = run_import(conn, records, at="2026-10-21T12:00:00+00:00")
    assert (first.created, second.created, second.unchanged, second.refreshed) == (3, 0, 3, 0)
    assert {r[0] for r in conn.execute("SELECT company_id FROM companies")} == ids_before
    assert count(conn, "companies") == 3 and count(conn, "sightings") == 3
    s = conn.execute("SELECT * FROM sightings WHERE external_id = '1'").fetchone()
    assert s["first_seen_at"] == "2026-10-20T12:00:00+00:00" and s["last_seen_at"] == "2026-10-21T12:00:00+00:00"
    assert s["provider_observed_at"] == UPDATED and s["source_id"] == yc.SOURCE_ID
    assert count(conn, "runs") == 2 and count(conn, "source_checks") == 2


# 7
def test_changed_name_keeps_identity_and_records_alias(conn):
    run_import(conn, [rec(1, "Acme AI")])
    uuid_before = company_for(conn, 1)["company_id"]
    result, _ = run_import(conn, [rec(1, "Acme Intelligence", former_names=["Acme AI"])], at="2026-10-21T12:00:00+00:00")
    row = company_for(conn, 1)
    assert row["company_id"] == uuid_before and result.refreshed == 1 and result.created == 0
    assert row["name"] == "Acme Intelligence" and "Acme AI" in row["aliases"]
    assert count(conn, "companies") == 1


# 8
def test_shared_or_changed_websites_do_not_merge_or_become_verified_domains(conn):
    records = [rec(1, "One", website="https://ycombinator.com"), rec(2, "Two", website="http://www.ycombinator.com/x"),
               rec(3, "Same Site", website="https://same.example"), rec(4, "Same Site Too", website="https://same.example")]
    records += [rec(i + 10, f"Co {i}") for i in range(100)]
    _, analysis = run_import(conn, records)
    assert any("ycombinator.com" in w for w in analysis.warnings)
    assert count(conn, "companies") == 104
    assert conn.execute("SELECT COUNT(*) FROM companies WHERE domain IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM companies WHERE identity_status != 'provisional'").fetchone()[0] == 0
    run_import(conn, [rec(3, "Same Site", website="https://moved.example")], at="2026-10-21T12:00:00+00:00")
    assert "moved.example" in company_for(conn, 3)["facts"]


# 9
def test_not_hiring_company_is_imported_and_flag_creates_no_job_or_status_events(conn):
    records = [rec(1, isHiring=False), rec(2, "Hiring Co", isHiring=True), india(3, isHiring=False)]
    run_import(conn, records)
    assert count(conn, "companies") == 3
    assert count(conn, "jobs") == 0 and count(conn, "status_events") == 0
    assert '"is_hiring_reported": false' in company_for(conn, 3)["facts"]


# 10 / 11 / 12
@pytest.mark.parametrize("location,regions,expected", [
    ("Bengaluru, KA, India", ["India", "South Asia"], yc.INDIA_BASED),
    ("Gurugram, HR, India; Remote", ["India", "Remote"], yc.INDIA_BASED),
    ("San Francisco, CA, USA; Mumbai, MH, India", ["United States of America", "India"], yc.INDIA_BASED),
    ("", ["India"], yc.INDIA_BASED),
    ("Austin, TX, USA; Remote", ["United States of America", "Remote", "Partly Remote"], yc.UNVERIFIED),
    ("Remote", ["Remote", "Fully Remote"], yc.POTENTIALLY_RELEVANT),
    ("London, England, United Kingdom", ["United Kingdom", "Fully Remote"], yc.POTENTIALLY_RELEVANT),
    ("Remote", ["Remote"], yc.UNVERIFIED),
    ("Lahore, Punjab, Pakistan", ["Pakistan", "South Asia"], yc.NO_INDIA_EVIDENCE),
    ("Berlin, Germany; Paris, France", ["Germany", "France"], yc.NO_INDIA_EVIDENCE),
    ("Atlantis", ["Unspecified"], yc.UNVERIFIED),
    ("Somewhere odd; ???", [], yc.UNVERIFIED),
    ("", [], yc.UNVERIFIED),
    ("", ["Unspecified"], yc.UNVERIFIED),
])
def test_location_classification(location, regions, expected):
    assert yc.classify_location(location, regions)[0] == expected


def test_remote_is_never_treated_as_india_eligibility(conn):
    run_import(conn, [rec(1, all_locations="Remote", regions=["Remote", "Fully Remote"]), rec(2)])
    facts = company_for(conn, 1)["facts"]
    assert '"sheet_publish": false' in facts and "potentially_relevant" in facts


def test_no_india_evidence_label_is_descriptive_not_an_exclusion():
    cls, reason = yc.classify_location("San Francisco, CA, USA", ["United States of America"])
    assert cls == "no_india_evidence_yet"
    assert "USA" in reason and "not an eligibility restriction" in reason
    assert "incompatible" not in cls and "incompatible" not in reason.lower()
    assert not hasattr(yc, "EXPLICITLY_INCOMPATIBLE")


def test_no_india_evidence_is_reevaluated_and_never_permanent(conn):
    from tests.test_sheets_sync import FakeBackend, sync
    run_import(conn, [rec(1, all_locations="Austin, TX, USA", regions=["United States of America"])])
    assert '"class": "no_india_evidence_yet"' in company_for(conn, 1)["facts"]
    assert count(conn, "companies") == 1  # kept, never discarded
    backend = FakeBackend()
    sync(conn, backend)
    assert backend.data("Companies") == []
    # later YC data adds an Indian office: same company, now in the review subset
    cid = company_for(conn, 1)["company_id"]
    run_import(conn, [rec(1, all_locations="Austin, TX, USA; Bengaluru, KA, India",
                          regions=["United States of America", "India"])], at="2026-10-21T12:00:00+00:00")
    assert company_for(conn, 1)["company_id"] == cid and '"class": "india_based"' in company_for(conn, 1)["facts"]
    sync(conn, backend, now="2026-10-22T10:00:00+00:00")
    assert [r[0] for r in backend.data("Companies")] == [cid]


def test_preview_counts_stored_facts_that_a_new_apply_would_refresh(conn):
    run_import(conn, [rec(1), india(2)])
    conn.execute("""UPDATE companies SET facts = json_set(facts, '$.india_relevance.class', 'explicitly_incompatible')
                    WHERE json_extract(facts, '$.yc.yc_id') = 1""")
    snap = snapshot_of([rec(1), india(2)])
    pv = yc.preview(yc.analyze(snap, now=NOW), yc.existing_state(conn))
    assert pv == {"new": 0, "existing": 2, "existing_changed": 0, "would_refresh": 1}
    result, _ = run_import(conn, [rec(1), india(2)], at="2026-10-21T12:00:00+00:00")
    assert result.refreshed == 1 and count(conn, "companies") == 2
    assert '"class": "no_india_evidence_yet"' in company_for(conn, 1)["facts"]


# 13
def test_fetch_json_retries_transient_errors_then_gives_up():
    calls, sleeps = [], []

    class Resp:
        status = 200
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return self.body

    def flaky(req, timeout):
        calls.append(timeout)
        if len(calls) < 3:
            raise urllib.error.URLError("temporary")
        return Resp(b'{"ok": true}')

    assert yc.fetch_json("https://x.example/a.json", opener=flaky, sleep=sleeps.append) == {"ok": True}
    assert len(calls) == 3 and sleeps == [1, 2] and calls[0] == 60

    def dead(req, timeout):
        raise TimeoutError("timed out")

    with pytest.raises(yc.SourceError, match="after 3 attempts"):
        yc.fetch_json("https://x.example/a.json", opener=dead, sleep=lambda s: None)

    def not_found(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    with pytest.raises(yc.SourceError, match="404"):
        yc.fetch_json("https://x.example/a.json", opener=not_found, sleep=lambda s: None)

    with pytest.raises(yc.SourceError, match="not valid JSON"):
        yc.fetch_json("https://x.example/a.json", opener=lambda r, timeout: Resp(b"<html>"), sleep=lambda s: None)


def test_apply_records_a_failed_check_and_changes_no_companies(conn, db_path, monkeypatch, tmp_path):
    run_import(conn, [rec(1)])
    before = count(conn, "companies")
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv(db.DB_PATH_ENV, str(db_path))

    def broken(url):
        raise yc.SourceError("connection refused")

    code = cli.cmd_import_yc(argparse.Namespace(dry_run=False, apply=True), fetch=broken)
    assert code == 1
    check = conn.execute("SELECT outcome, complete, error_info FROM source_checks ORDER BY started_at DESC").fetchall()
    assert any(r["outcome"] == "failed" and not r["complete"] for r in check)
    assert count(conn, "companies") == before
    assert conn.execute("SELECT result FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()[0] == "failed"


# 14
def test_schema_change_is_detected_and_nothing_is_imported(conn):
    with pytest.raises(yc.SourceError, match="format"):
        snap = snapshot_of([{"company_id": i, "title": f"x{i}"} for i in range(50)])
        yc.analyze(snap, now=NOW)
    with pytest.raises(yc.SourceError, match="meta.json"):
        yc.fetch_snapshot(make_fetch([rec(1)], meta={"updated": "x"}), now=NOW)
    with pytest.raises(yc.SourceError, match="not a list"):
        yc.fetch_snapshot(lambda url: {"companies": []} if url == yc.ALL_URL else
                          {"last_updated": UPDATED, "companies": {"all": {"count": 1}}}, now=NOW)
    assert count(conn, "companies") == 0


# 15
def test_stale_or_incomplete_results_are_recorded_as_partial(conn):
    records = [rec(i, f"Co {i}") for i in range(1, 6)]
    run_import(conn, records, count=9)  # meta promised 9, got 5
    check = conn.execute("SELECT outcome, complete, error_info FROM source_checks").fetchone()
    assert (check["outcome"], check["complete"]) == ("partial", 0) and "count" in check["error_info"]
    assert count(conn, "companies") == 5
    stale = yc.analyze(snapshot_of(records, updated="2026-10-01T00:00:00.000Z"), now=NOW)
    assert stale.stale and not stale.clean


def test_snapshot_older_than_already_imported_data_is_refused(conn):
    run_import(conn, [rec(1)], updated="2026-10-20T02:41:43.163Z")
    snap = snapshot_of([rec(1, "Acme AI Rolled Back")], updated="2026-10-19T02:41:43.163Z")
    with pytest.raises(yc.SourceError, match="older than data already imported"):
        yc.import_companies(conn, snap, yc.analyze(snap, now=NOW), now=NOW)
    assert company_for(conn, 1)["name"] == "Acme AI"


# 16
def test_interrupted_import_rolls_back_the_open_batch_and_a_rerun_completes(conn, monkeypatch):
    monkeypatch.setattr(yc, "BATCH_SIZE", 2)
    records = [rec(i, f"Co {i}") for i in range(1, 8)]
    snap = snapshot_of(records)
    analysis = yc.analyze(snap, now=NOW)
    real = yc._import_one
    seen = []

    def dying(conn_, r, snap_, result, now):
        if len(seen) == 5:
            raise KeyboardInterrupt
        seen.append(r["yc_id"])
        real(conn_, r, snap_, result, now)

    monkeypatch.setattr(yc, "_import_one", dying)
    with pytest.raises(KeyboardInterrupt):
        yc.import_companies(conn, snap, analysis, now=NOW)
    assert count(conn, "companies") == 4  # two committed batches; the interrupted batch rolled back
    assert conn.execute("SELECT result FROM runs").fetchone()[0] == "interrupted"
    monkeypatch.setattr(yc, "_import_one", real)
    result = yc.import_companies(conn, snap, analysis, now=NOW)
    assert count(conn, "companies") == 7 and result.created == 3 and result.unchanged == 4
    assert count(conn, "sightings") == 7


# 17
def test_existing_verdicts_and_notes_survive_reimport_and_changes(conn):
    run_import(conn, [india(2)])
    cid = company_for(conn, 2)["company_id"]
    db.save_verdict(conn, "Yes", "great fit", company_id=cid)
    db.save_verdict(conn, notes="refined note", company_id=cid)
    db.save_verdict(conn, "No", company_id=cid)  # decision changed explicitly; note kept
    run_import(conn, [india(2, "Bharat Labs Renamed", one_liner="changed", isHiring=True, status="Acquired")],
               at="2026-10-21T12:00:00+00:00")
    run_import(conn, [india(2, "Bharat Labs Renamed", one_liner="changed", isHiring=True, status="Acquired")],
               at="2026-10-22T12:00:00+00:00")
    v = db.get_verdict(conn, company_id=cid)
    assert (v["decision"], v["notes"]) == ("No", "refined note")
    assert company_for(conn, 2)["company_id"] == cid and count(conn, "verdicts") == 1


# 19
def test_empty_or_failed_source_never_deletes_existing_companies(conn):
    run_import(conn, [rec(1), india(2)])
    for bad in ([], {"error": "x"}):
        with pytest.raises(yc.SourceError):
            yc.fetch_snapshot(lambda url, bad=bad: {"last_updated": UPDATED, "companies": {"all": {"count": 0}}}
                              if url == yc.META_URL else bad, now=NOW)
    assert count(conn, "companies") == 2
    # a later, smaller snapshot also removes nothing
    run_import(conn, [rec(1)], at="2026-10-21T12:00:00+00:00")
    assert count(conn, "companies") == 2


# 20
def test_yc_adapter_has_no_spreadsheet_code_and_live_stays_blocked():
    source = inspect.getsource(yc)
    assert "gspread" not in source and "sheets_sync" not in source
    from discovery import sheets_sync
    with pytest.raises(sheets_sync.SyncError, match="LIVE"):
        sheets_sync.open_backend("live")


def test_dry_run_changes_nothing(conn, db_path, monkeypatch, tmp_path, capsys):
    run_import(conn, [rec(1)])
    tables = ("companies", "sightings", "runs", "source_checks", "jobs", "status_events", "verdicts")
    before = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv(db.DB_PATH_ENV, str(db_path))
    fetch = make_fetch([rec(1), india(2), rec(3, "Gamma", all_locations="Remote", regions=["Fully Remote"])])
    code = cli.cmd_import_yc(argparse.Namespace(dry_run=True, apply=False), fetch=fetch)
    out = capsys.readouterr().out
    assert code == 0 and "DRY RUN" in out and "New companies: 2" in out and "india_based=1" in out
    assert "already imported: 1" in out
    assert {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables} == before


def test_dry_run_without_a_database_still_reports(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv(db.DB_PATH_ENV, str(tmp_path / "missing.db"))
    assert cli.cmd_import_yc(argparse.Namespace(dry_run=True, apply=False), fetch=make_fetch([rec(1)])) == 0
    assert "New companies: 1" in capsys.readouterr().out
    assert not (tmp_path / "missing.db").exists()


def test_apply_requires_an_initialized_database(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv(db.DB_PATH_ENV, str(tmp_path / "missing.db"))
    assert cli.cmd_import_yc(argparse.Namespace(dry_run=False, apply=True), fetch=make_fetch([rec(1)])) == 1
    assert not (tmp_path / "missing.db").exists()


def test_cli_requires_exactly_one_mode():
    with pytest.raises(SystemExit):
        cli.main(["import-yc"])
    with pytest.raises(SystemExit):
        cli.main(["import-yc", "--dry-run", "--apply"])


# Sheets publication subset -------------------------------------------------
def test_only_the_india_subset_is_published_to_the_sheet(conn):
    from tests.test_sheets_sync import FakeBackend, T3, sync
    run_import(conn, [india(2), rec(1), rec(3, "Remote Co", all_locations="Remote", regions=["Fully Remote"])])
    backend = FakeBackend()
    report = sync(conn, backend, now=T3)
    names = [r[1] for r in backend.data("Companies")]
    assert names == ["Bharat Labs"] and report.appended["Companies"] == 1
    row = backend.data("Companies")[0]
    header = backend.header("Companies")
    details = row[header.index("Details")]
    assert "YC: Summer 2021" in details and "site (YC-reported): bharat.example" in details
    assert row[header.index("Domain")] == ""  # source-reported website is not a verified domain
    assert "YC-reported hiring flag: false (unverified)" in row[header.index("Hiring Evidence")]
    # a non-subset company with a human note still reaches the sheet
    other = company_for(conn, 1)["company_id"]
    db.save_verdict(conn, None, "look into this", company_id=other)
    sync(conn, backend, now="2026-10-22T10:00:00+00:00")
    assert len(backend.data("Companies")) == 2
    again = sync(conn, backend, now="2026-10-23T10:00:00+00:00")
    assert again.appended["Companies"] == 0
