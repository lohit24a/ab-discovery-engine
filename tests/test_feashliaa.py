"""Feashliaa feed adapter tests with local fixtures shaped like the verified live schema. No network."""

import argparse
import gzip
import inspect
import json
import sqlite3
import tracemalloc

import pytest

from discovery import cli, database as db
from discovery.adapters import feashliaa as fz

GEN = "2026-10-19T15:48:40.506836Z"
NOW = "2026-10-20T12:00:00+00:00"
FRESH = "2026-10-19T15:05:44.517280Z"
OLD = "2026-10-05T09:00:00.000000Z"
FIRST = "2026-09-11T12:24:28.636336Z"
PF = fz.Prefilter(["analyst", "associate", "ai", "operations"])


def row(i=1, ats="Greenhouse", company="acme", title="Business Analyst", location="Bengaluru, Karnataka, India",
        scraped=FRESH, first=FIRST, **over):
    base = {"url": f"https://job-boards.greenhouse.io/{company}/jobs/{1000 + i}", "company": company, "ats": ats,
            "scraped_at": scraped, "skill_level": "mid", "location": location, "is_recruiter": False, "title": title,
            "salary": None, "first_seen": first, "remote": False}
    base.update(over)
    return base


class Feed:
    """Callable standing in for fz.fetch_bytes."""

    def __init__(self, chunks, generated=GEN, total=None):
        self.names = [f"jobs_chunk_{i}.json.gz" for i in range(len(chunks))]
        self.files = {n: gzip.compress(json.dumps(rows).encode()) for n, rows in zip(self.names, chunks)}
        self.total = sum(len(c) for c in chunks) if total is None else total
        self.generated = generated
        self.set_manifest()
        self.requests, self.fail = [], {}

    def set_manifest(self, **over):
        manifest = {"chunks": self.names, "totalJobs": self.total, "last_updated": self.generated}
        manifest.update(over)
        self.files["jobs_manifest.json"] = json.dumps({k: v for k, v in manifest.items() if v is not ...}).encode()

    def __call__(self, url, **kw):
        name = url.split("?")[0].rsplit("/", 1)[-1]
        self.requests.append(name)
        if name in self.fail:
            raise self.fail[name]
        if name not in self.files:
            raise fz.SourceError(f"{url}: HTTP 404")
        return self.files[name]


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "fz.db"
    db.initialize_database(path)
    c = db.connect(path)
    yield c
    c.close()


def tmp_of(conn):
    from pathlib import Path
    return Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent


def run(conn, feed, apply=True, now=NOW, **kw):
    return fz.execute(conn, apply=apply, cache_dir=tmp_of(conn) / "cache", fetch=feed, prefilter=PF, now=now, **kw)


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def job_row(conn, title=None):
    q = "SELECT * FROM jobs" + (" WHERE title = ?" if title else "")
    return conn.execute(q, (title,) if title else ()).fetchone()


def events(conn, job_id):
    return [r[0] for r in conn.execute("SELECT new_state FROM status_events WHERE job_id = ? ORDER BY rowid", (job_id,))]


# 1-3 manifest ----------------------------------------------------------------
def test_valid_manifest():
    m = fz.fetch_manifest(Feed([[row()]]))
    assert m.chunks == ["jobs_chunk_0.json.gz"] and m.total_jobs == 1 and m.generated_at == GEN
    assert m.snapshot_id == fz.fetch_manifest(Feed([[row()]])).snapshot_id


def test_stale_manifest_is_refused_and_recorded_without_importing(conn):
    report = run(conn, Feed([[row()]]), now="2026-10-25T12:00:00+00:00")
    assert report.status == "refused" and "stale" in report.reasons[0]
    assert count(conn, "jobs") == 0 and count(conn, "companies") == 0
    check = conn.execute("SELECT outcome, complete, error_info FROM source_checks").fetchone()
    assert (check["outcome"], check["complete"]) == ("partial", 0) and "stale" in check["error_info"]


@pytest.mark.parametrize("override", [
    {"totalJobs": ...}, {"chunks": ...}, {"last_updated": ...}, {"last_updated": "not a date"},
    {"chunks": []}, {"chunks": ["evil/../x.json.gz"]}, {"chunks": ["jobs_chunk_0.json.gz"] * 2}, {"totalJobs": 0},
])
def test_manifest_with_missing_or_bad_fields_fails(conn, override):
    feed = Feed([[row()]])
    feed.set_manifest(**override)
    with pytest.raises(fz.SourceError):
        fz.fetch_manifest(feed)
    report = run(conn, feed)
    assert report.status == "failed" and count(conn, "jobs") == 0
    assert conn.execute("SELECT outcome FROM source_checks").fetchone()[0] == "failed"


# 4-6 corruption --------------------------------------------------------------
def test_corrupted_manifest_json():
    feed = Feed([[row()]])
    feed.files["jobs_manifest.json"] = b"{not json"
    with pytest.raises(fz.SourceError, match="not valid JSON"):
        fz.fetch_manifest(feed)


def test_corrupted_chunk_json_and_gzip_block_the_import(conn):
    bad_json = Feed([[row()], [row(2)]])
    bad_json.files[bad_json.names[1]] = gzip.compress(b"{broken")
    assert run(conn, bad_json).status == "refused"
    bad_gzip = Feed([[row()], [row(2)]])
    bad_gzip.files[bad_gzip.names[1]] = b"this is not gzip"
    report = run(conn, bad_gzip)
    assert report.status == "refused" and "corrupt" in report.reasons[0]
    not_a_list = Feed([[row()]])
    not_a_list.files[not_a_list.names[0]] = gzip.compress(b'{"jobs": []}')
    assert run(conn, not_a_list).status == "refused"
    assert count(conn, "jobs") == 0 and count(conn, "companies") == 0


def test_missing_chunk_blocks_the_import_and_is_recorded_partial(conn):
    feed = Feed([[row()], [row(2)], [row(3)]])
    feed.fail[feed.names[1]] = fz.SourceError("HTTP 404")
    report = run(conn, feed)
    assert report.status == "refused" and report.metrics.chunks_ok == 2 and report.metrics.chunks_total == 3
    assert count(conn, "jobs") == 0
    check = conn.execute("SELECT outcome, complete FROM source_checks").fetchone()
    assert (check["outcome"], check["complete"]) == ("partial", 0)


def test_row_count_mismatch_with_manifest_blocks_the_import(conn):
    assert run(conn, Feed([[row()], [row(2)]], total=99)).status == "refused"
    assert count(conn, "jobs") == 0


def test_fetch_bytes_retries_then_fails_cleanly():
    calls, sleeps = [], []

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return b"ok"

    def flaky(req, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("slow")
        return Resp()

    assert fz.fetch_bytes("https://x.example/a", opener=flaky, sleep=sleeps.append) == b"ok" and sleeps == [1, 2]
    with pytest.raises(fz.SourceError, match="after 3 attempts"):
        fz.fetch_bytes("https://x.example/a", opener=lambda r, timeout: (_ for _ in ()).throw(TimeoutError()),
                       sleep=lambda s: None)


# 7 interruption ----------------------------------------------------------------
def test_interrupted_download_resumes_from_cache(conn):
    feed = Feed([[row(1)], [row(2)], [row(3)]])
    feed.fail[feed.names[2]] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run(conn, feed)
    del feed.fail[feed.names[2]]
    feed.requests.clear()
    report = run(conn, feed)
    assert report.status == "ok" and report.counts.jobs_created == 3
    assert feed.names[0] not in feed.requests and feed.names[1] not in feed.requests  # served from cache


def test_interrupted_import_rolls_back_the_open_batch_and_rerun_is_clean(conn, monkeypatch):
    monkeypatch.setattr(fz, "BATCH_SIZE", 2)
    feed = Feed([[row(i, company=f"co{i}") for i in range(1, 8)]])
    real = db.observe_job
    state = {"n": 0}

    def dying(*a, **k):
        state["n"] += 1
        if state["n"] == 5:
            raise KeyboardInterrupt
        return real(*a, **k)

    monkeypatch.setattr(fz.db, "observe_job", dying)
    with pytest.raises(KeyboardInterrupt):
        run(conn, feed)
    assert count(conn, "jobs") == 4
    assert conn.execute("SELECT result FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()[0] == "interrupted"
    assert conn.execute("SELECT COUNT(*) FROM source_checks WHERE outcome = 'success'").fetchone()[0] == 0
    monkeypatch.setattr(fz.db, "observe_job", real)
    report = run(conn, feed)
    assert count(conn, "jobs") == 7 and count(conn, "companies") == 7 and report.counts.jobs_unchanged == 4


# 8-13 duplicates, replay, freshness ----------------------------------------------
def test_duplicate_rows_in_one_snapshot_make_one_job(conn):
    report = run(conn, Feed([[row(1), row(1)], [row(1, scraped=OLD)]]))
    assert report.metrics.duplicates == 2 and count(conn, "jobs") == 1 and count(conn, "sightings") == 2  # job+board


def test_replaying_the_same_snapshot_changes_nothing(conn):
    feed = Feed([[row(1), row(2, company="beta")]])
    first = run(conn, feed)
    tables = ("companies", "jobs", "sightings", "status_events", "verdicts")
    before = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}
    second = run(conn, feed, now="2026-10-20T13:00:00+00:00")
    assert first.status == "ok" and second.status == "already_processed"
    assert {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables} == before
    assert conn.execute("SELECT outcome FROM source_checks ORDER BY rowid DESC LIMIT 1").fetchone()[0] == "skipped"


def test_new_snapshot_with_unchanged_job_only_refreshes_provider_and_local_last_seen(conn):
    run(conn, Feed([[row(1)]]), now="2026-10-20T12:00:00+00:00")
    jid = job_row(conn)["job_id"]
    second = Feed([[row(1, scraped="2026-10-20T10:00:00.000000Z")]], generated="2026-10-20T10:30:00.000000Z")
    report = run(conn, second, now="2026-10-20T12:30:00+00:00")
    assert report.counts.jobs_created == 0 and report.counts.jobs_unchanged == 1
    assert count(conn, "jobs") == 1 and events(conn, jid) == ["New"]
    s = conn.execute("SELECT * FROM sightings WHERE job_id IS NOT NULL").fetchone()
    assert s["first_seen_at"] == "2026-10-20T12:00:00+00:00" and s["last_seen_at"] == "2026-10-20T12:30:00+00:00"
    assert s["provider_observed_at"] == "2026-10-20T10:00:00.000000Z" and s["snapshot_at"] == "2026-10-20T10:30:00.000000Z"


def test_provider_retained_row_is_labelled_unknown_not_open(conn):
    run(conn, Feed([[row(1, scraped=OLD)]]))
    j = job_row(conn)
    assert j["lifecycle_state"] == "Unknown" and events(conn, j["job_id"]) == ["New", "Unknown"]
    assert conn.execute("SELECT provider_freshness FROM sightings WHERE job_id IS NOT NULL").fetchone()[0] == "provider_retained"


def test_row_from_the_latest_collection_is_marked_latest_but_not_activated(conn):
    run(conn, Feed([[row(1, scraped=FRESH)]]))
    j = job_row(conn)
    assert j["lifecycle_state"] == "New" and events(conn, j["job_id"]) == ["New"]  # never Active from this feed
    assert conn.execute("SELECT provider_freshness FROM sightings WHERE job_id IS NOT NULL").fetchone()[0] == "latest_collection"


def test_missing_or_future_scraped_at_is_unknown_freshness():
    gen = fz.parse_iso(GEN)
    assert fz.classify_freshness(None, gen) == "unknown" and fz.classify_freshness("garbage", gen) == "unknown"
    assert fz.classify_freshness("2026-11-01T00:00:00Z", gen) == "unknown"
    assert fz.classify_freshness(FRESH, gen) == "latest_collection" and fz.classify_freshness(OLD, gen) == "provider_retained"


def test_provider_first_seen_never_replaces_our_first_seen_or_posting_date(conn):
    run(conn, Feed([[row(1, first="2025-01-02T03:04:05.000000Z")]]))
    j = job_row(conn)
    s = conn.execute("SELECT * FROM sightings WHERE job_id IS NOT NULL").fetchone()
    assert s["provider_first_seen_at"] == "2025-01-02T03:04:05.000000Z"
    assert s["first_seen_at"] == NOW and j["first_seen_at"] == NOW
    assert j["posted_at"] is None  # the provider's first-seen is not an employer posting date


# 14-18 geography and prefilter ----------------------------------------------------
@pytest.mark.parametrize("location,expected", [
    ("Bengaluru, Karnataka, India", fz.BENGALURU), ("Bangalore", fz.BENGALURU), ("BANGALORE, KA", fz.BENGALURU),
    ("Remote - Bengaluru", fz.BENGALURU),
    ("Mumbai, Maharashtra, India", fz.INDIA_EXPLICIT), ("India", fz.INDIA_EXPLICIT), ("Remote, India", fz.INDIA_EXPLICIT),
    ("Bhubaneswar, Odisha", fz.INDIA_INFERRED), ("Pune", fz.INDIA_INFERRED), ("Hyderabad, Telangana", fz.INDIA_INFERRED),
    ("Hyderabad, Pakistan", fz.OTHER), ("Indiana, USA", fz.OTHER), ("Pune, USA", fz.OTHER),
    ("Remote", fz.REMOTE_UNVERIFIED), ("US, Remote", fz.REMOTE_UNVERIFIED), ("Anywhere", fz.REMOTE_UNVERIFIED),
    ("Remote - North America", fz.REMOTE_UNVERIFIED),
    ("San Francisco, CA", fz.OTHER), (None, fz.OTHER), ("", fz.OTHER),
])
def test_geography_classification(location, expected):
    assert fz.classify_geo(location)[0] == expected


def test_remote_flag_alone_is_unverified_never_india_eligible():
    assert fz.classify_geo("Singapore", True)[0] == fz.REMOTE_UNVERIFIED
    assert fz.ELIGIBILITY[fz.REMOTE_UNVERIFIED] == "unknown"


def test_import_records_eligibility_levels_and_excludes_non_india_and_off_topic(conn):
    run(conn, Feed([[
        row(1, company="a", title="Business Analyst", location="Bangalore"),
        row(2, company="b", title="Operations Associate", location="Remote, India"),
        row(3, company="c", title="AI Associate", location="Chennai, Tamil Nadu"),
        row(4, company="d", title="Data Analyst", location="Remote"),
        row(5, company="e", title="Data Analyst", location="Austin, TX"),          # not India, not remote
        row(6, company="f", title="Warehouse Forklift Driver", location="Bengaluru"),  # fails the title prefilter
    ]]))
    got = {r["title"] + "|" + r["location"]: r["remote_eligibility"] for r in conn.execute("SELECT * FROM jobs")}
    assert got == {"Business Analyst|Bangalore": "eligible", "Operations Associate|Remote, India": "eligible",
                   "AI Associate|Chennai, Tamil Nadu": "possible", "Data Analyst|Remote": "unknown"}


# 19-23 identity ---------------------------------------------------------------------
def test_same_title_different_urls_are_different_jobs_and_never_merge_by_title(conn):
    run(conn, Feed([[row(1), row(2)]]))
    assert count(conn, "jobs") == 2 and count(conn, "companies") == 1


def test_same_board_code_on_two_ats_systems_are_two_companies(conn):
    lever = row(1, ats="Lever", company="acme", url="https://jobs.lever.co/acme/aaa-111")
    run(conn, Feed([[row(1, ats="Greenhouse", company="acme"), lever]]))
    assert count(conn, "companies") == 2 and count(conn, "jobs") == 2


def test_company_is_provisional_with_no_domain_and_not_published(conn):
    run(conn, Feed([[row(1, company="acme-ai")]]))
    c = conn.execute("SELECT * FROM companies").fetchone()
    facts = json.loads(c["facts"])
    assert c["identity_status"] == "provisional" and c["domain"] is None and c["name"] == "acme-ai"
    assert facts["feashliaa"]["name_is_board_code"] is True and facts["sheet_publish"] is False


def test_other_source_job_with_same_title_is_not_merged(conn):
    db.register_source(conn, "other", "Other", "aggregator")
    cid, _ = db.get_or_create_company(conn, "acme", "acme.example")
    db.observe_job(conn, source_id="other", source_url="https://o.example/1", company_id=cid,
                   title="Business Analyst", external_id="1", location="Bengaluru")
    run(conn, Feed([[row(1, company="acme")]]))
    assert count(conn, "jobs") == 2 and count(conn, "companies") == 2


def test_possible_yc_match_is_recorded_for_review_but_never_merged(conn):
    yc_id, _ = db.get_or_create_company(conn, "Acme AI", None, facts={"yc": {"yc_id": 7}, "sheet_publish": True})
    db.register_source(conn, "yc_oss", "YC", "yc")
    db.record_sighting(conn, "yc_oss", "https://www.ycombinator.com/companies/acme-ai", company_id=yc_id, external_id="7")
    before = [tuple(r) for r in conn.execute("SELECT * FROM companies WHERE company_id = ?", (yc_id,))]
    sightings_before = count(conn, "sightings")
    report = run(conn, Feed([[row(1, company="acme-ai")]]))
    assert report.possible_yc_matches_est == 1
    assert [tuple(r) for r in conn.execute("SELECT * FROM companies WHERE company_id = ?", (yc_id,))] == before
    new = conn.execute("SELECT * FROM companies WHERE company_id != ?", (yc_id,)).fetchone()
    assert json.loads(new["facts"])["feashliaa"]["possible_yc_matches"][0]["yc_id"] == "7"
    assert count(conn, "companies") == 2 and count(conn, "sightings") == sightings_before + 2  # board + job only


def test_identity_conflict_aborts_safely_and_keeps_earlier_batches_consistent(conn, monkeypatch):
    feed = Feed([[row(1), row(2, company="beta")]])
    run(conn, feed)
    other, _ = db.get_or_create_company(conn, "Someone Else", "else.example")
    monkeypatch.setattr(fz, "_resolve_company", lambda *a, **k: other)
    changed = Feed([[row(1), row(2, company="beta")]], generated="2026-10-20T10:30:00.000000Z")
    with pytest.raises(db.IdentityConflict):
        run(conn, changed, now="2026-10-20T13:00:00+00:00")
    assert count(conn, "jobs") == 2 and conn.execute("SELECT result FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()[0] == "failed"
    assert conn.execute("SELECT COUNT(*) FROM source_checks WHERE source_snapshot_at = '2026-10-20T10:30:00.000000Z'").fetchone()[0] == 0


# 24 closure safety -------------------------------------------------------------------
def test_no_failure_or_partial_check_closes_anything_and_feed_is_never_closure_evidence(conn):
    run(conn, Feed([[row(1, scraped=OLD)]]))
    jid = job_row(conn)["job_id"]
    broken = Feed([[row(1)], [row(2)]])
    broken.fail[broken.names[1]] = fz.SourceError("down")
    run(conn, broken, now="2026-10-20T13:00:00+00:00")
    stale = Feed([[row(9)]], generated="2026-10-20T10:30:00.000000Z")
    assert run(conn, stale, now="2026-10-30T12:00:00+00:00").status == "refused"  # newer than ours but stale
    states = {r[0] for r in conn.execute("SELECT new_state FROM status_events")}
    assert not states & {"Closed", "Not Seen", "Active"}
    check = conn.execute("SELECT check_id FROM source_checks WHERE outcome = 'success' AND complete = 1").fetchone()[0]
    with pytest.raises(ValueError, match="supplementary feed"):   # even a complete, successful snapshot check
        db.append_status_event(conn, "Not Seen", "absent from feed", job_id=jid, source_check_id=check)
    assert job_row(conn)["lifecycle_state"] == "Unknown"


# 25-26 protection of existing data ----------------------------------------------------
def test_existing_yc_records_and_human_verdicts_are_preserved(conn):
    yc_id, _ = db.get_or_create_company(conn, "Beta Works", None, facts={"yc": {"yc_id": 9}})
    db.save_verdict(conn, "Yes", "yc favourite", company_id=yc_id)
    run(conn, Feed([[row(1)]]))
    jid = job_row(conn)["job_id"]
    db.save_verdict(conn, "No", "not for me", job_id=jid)
    changed = Feed([[row(1, title="Senior Business Analyst", location="Bengaluru, India")]], generated="2026-10-20T10:30:00.000000Z")
    report = run(conn, changed, now="2026-10-20T13:00:00+00:00")
    assert report.counts.jobs_changed == 1 and count(conn, "jobs") == 1
    v = db.get_verdict(conn, job_id=jid)
    assert (v["decision"], v["notes"]) == ("No", "not for me")
    assert db.get_verdict(conn, company_id=yc_id)["notes"] == "yc favourite"
    assert db.get_company(conn, yc_id)["name"] == "Beta Works"


# 27 bounded memory ---------------------------------------------------------------------
def test_scanning_memory_does_not_grow_with_the_number_of_chunks():
    class Store:
        def __init__(self, n):
            self.manifest = fz.Manifest([f"jobs_chunk_{i}.json.gz" for i in range(n)], n * 4000, GEN)

        def load_chunk(self, name):
            return [row(i, company=f"c{i}", title="Forklift Operator", location="Berlin") for i in range(4000)]

    def peak(n):
        tracemalloc.start()
        tracemalloc.reset_peak()
        cands, metrics = fz.scan(Store(n).manifest, Store(n), PF)
        _, top = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return top, cands, metrics

    small, _, _ = peak(2)
    large, cands, metrics = peak(12)
    assert cands == [] and metrics.scanned == 48000 and metrics.kept == 0
    assert large < small * 2.5   # 6x the data, roughly the same peak (one chunk at a time)


# 28 spreadsheets ---------------------------------------------------------------------------
def test_adapter_has_no_spreadsheet_code_and_jobs_are_not_published_by_default(conn):
    assert "gspread" not in inspect.getsource(fz) and "sheets_sync" not in inspect.getsource(fz)
    from tests.test_sheets_sync import FakeBackend, T3, sync
    run(conn, Feed([[row(1), row(2, company="beta")]]))
    backend = FakeBackend()
    report = sync(conn, backend, now=T3)
    assert report.appended["Posts"] == 0 and report.appended["Companies"] == 0
    jid = job_row(conn)["job_id"]
    db.save_verdict(conn, None, "interesting", job_id=jid)   # a human note publishes just that job
    report = sync(conn, backend, now="2026-10-22T10:00:00+00:00")
    assert report.appended["Posts"] == 1 and [r[0] for r in backend.data("Posts")] == [jid]
    from discovery import sheets_sync
    with pytest.raises(sheets_sync.SyncError, match="LIVE"):
        sheets_sync.open_backend("live")


# CLI, config, migration ---------------------------------------------------------------------
def test_shipped_prefilter_config_loads_and_filters():
    pf = fz.Prefilter.load()
    assert pf.matches("Associate Product Manager") and pf.matches("Forward Deployed Engineer")
    assert pf.matches("AI Implementation Lead") and not pf.matches("Warehouse Forklift Driver")
    assert not pf.matches("Maintenance Technician")   # 'ai' must not match inside words
    with pytest.raises(fz.SourceError):
        fz.Prefilter([])


def test_dry_run_cli_changes_nothing(conn, monkeypatch, capsys):
    run(conn, Feed([[row(1)]]))
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    tables = ("companies", "jobs", "sightings", "runs", "source_checks", "status_events")
    before = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_of(conn))
    monkeypatch.setenv(db.DB_PATH_ENV, path)
    feed = Feed([[row(1), row(2, company="newco")]], generated="2026-10-20T10:30:00.000000Z")
    monkeypatch.setattr(fz, "Prefilter", type("P", (), {"load": staticmethod(lambda: PF)}))
    code = cli.cmd_import_feashliaa(argparse.Namespace(dry_run=True, apply=False), fetch=feed, cache_dir=tmp_of(conn) / "c2")
    out = capsys.readouterr().out
    assert code == 0 and "DRY RUN" in out and "Estimated new jobs: 1" in out
    assert {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables} == before


def test_cli_requires_exactly_one_mode_and_migration_4_preserves_sightings(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["import-feashliaa"])
    from discovery.schema import MIGRATIONS, SCHEMA_VERSION
    path = tmp_path / "v3.db"
    raw = sqlite3.connect(path)
    for m in MIGRATIONS[:3]:
        raw.executescript(m)
    raw.execute("INSERT INTO sources (source_id, name, source_type, created_at, updated_at) VALUES ('s','S','other','t','t')")
    raw.execute("INSERT INTO companies (company_id, name, normalized_name, identity_status, created_at, updated_at)"
                " VALUES ('c','C','c','provisional','t','t')")
    raw.execute("INSERT INTO sightings (sighting_id, source_id, company_id, external_id, source_url, first_seen_at, last_seen_at)"
                " VALUES ('x','s','c','e','https://x.example','a','b')")
    raw.commit()
    raw.close()
    assert db.initialize_database(path) == SCHEMA_VERSION == 4
    c = db.connect(path)
    try:
        s = c.execute("SELECT * FROM sightings").fetchone()
        assert s["provider_first_seen_at"] is None and s["provider_freshness"] is None and s["first_seen_at"] == "a"
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE sightings SET provider_freshness = 'bogus'")
    finally:
        c.close()
