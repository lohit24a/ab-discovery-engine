"""Day 14: source health, retry policy, interruption/partial/stale recovery, and an end-to-end failure simulation. Offline, disposable databases only.

Most per-adapter failure cases already live beside their adapters (test_feashliaa, test_yc_*, test_investor_boards, test_pipeline, test_jobspy).
This file adds what was missing: the health classifier, the one-line retry policy for every adapter, mid-scan result-set mutation, an interrupted
investor import with replay, and one run that mixes a good company source, a good job source, a failed source and an incomplete source."""
import urllib.error

import pytest

from discovery import database as db
from discovery import lifecycle, shortlist, source_health
from discovery import profile as prof
from discovery import sheets_sync as ss
from discovery.adapters import feashliaa as fz
from discovery.adapters import investor_boards as ib
from discovery.adapters import yc_companies as yc
from tests.test_feashliaa import Feed
from tests.test_investor_boards import ACCEL, TECHSTARS, Web, getro_html, getro_job, getro_scans, make_snapshot, run
from tests.test_pipeline import fz_feed, go, job, status_of, yc_fetch
from tests.test_sheets_sync import FakeBackend

T = "2026-10-20T10:00:00+00:00"
GONE = ("Closed", "Not Seen")


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "d14.db"
    db.initialize_database(path)
    c = db.connect(path)
    yield c
    c.close()


def closures(conn):
    return conn.execute("SELECT COUNT(*) FROM status_events WHERE new_state IN ('Closed','Not Seen')").fetchone()[0]


# ------------------------------------------------------------------ source health classifier

def check(conn, source, outcome, *, complete=False, scope="", n=0, err=None, stype="investor_board", started=T, run_result="success"):
    db.register_source(conn, source, source, stype, now=T)
    run_id = db.start_run(conn, "discovery", now=started)
    db.record_source_check(conn, run_id, source, outcome, scope=scope, complete=complete, record_count=n, started_at=started, finished_at=started, error_info=err)
    db.finish_run(conn, run_id, run_result, now=started)
    return run_id


@pytest.mark.parametrize("outcome,complete,scope,n,err,expected", [
    ("success", True, "jobs:none", 10, None, "complete"),
    ("success", True, "jobs:locations = Bengaluru", 10, None, "complete_filtered"),
    ("success", True, "jobs:none", 0, None, "verified_empty"),
    ("success", False, "jobs:none", 0, None, "unverified_zero"),
    ("success", False, "jobs:none", 5, None, "incomplete"),
    ("partial", False, "jobs:none", 5, "count mismatch", "partial"),
    ("failed", False, "jobs:none", 0, "HTTP 403: access restricted; nothing retried", "blocked"),
    ("failed", False, "jobs:none", 0, "HTTP 429 (rate limited; not retried)", "blocked"),
    ("failed", False, "jobs:none", 0, "TimeoutError: timed out", "network_error"),
    ("failed", False, "jobs:none", 0, "board page has no initialState.jobs.found list", "schema_error"),
    ("failed", False, "jobs:none", 0, "something odd", "failed_other"),
])
def test_every_check_outcome_has_one_honest_state(conn, outcome, complete, scope, n, err, expected):
    check(conn, "s", outcome, complete=complete, scope=scope, n=n, err=err)
    row = next(r for r in source_health.source_health(conn, now=db_now()) if r["source_id"] == "s")
    assert row["state"] == expected and row["may_close_jobs"] is False
    if expected in ("unverified_zero", "partial", "incomplete"):
        assert row["completeness"] == "low"
    if expected == "complete_filtered":
        assert row["completeness"] == "scoped" and any("never global completeness" in n_ for n_ in row["notes"])


def db_now():
    import datetime
    return datetime.datetime(2026, 10, 20, 12, 0, tzinfo=datetime.timezone.utc)


def test_skipped_runs_never_replace_the_last_collecting_run_and_stale_is_flagged(conn):
    check(conn, "feed", "success", complete=True, n=100, stype="ats_feed", started="2026-10-10T10:00:00+00:00")
    check(conn, "feed", "skipped", started="2026-10-20T10:00:00+00:00")
    row = next(r for r in source_health.source_health(conn, now=db_now()) if r["source_id"] == "feed")
    assert row["state"] == "complete" and row["stale"] is True and row["completeness"] == "low" and row["last_skipped"].startswith("2026-10-20")
    assert any("skipped" in n for n in row["notes"]) and any("old" in n for n in row["notes"])


def test_interrupted_run_is_visible_and_never_complete(conn):
    check(conn, "board", "success", complete=True, scope="jobs:none", n=5, run_result="interrupted")
    row = next(r for r in source_health.source_health(conn, now=db_now()) if r["source_id"] == "board")
    assert row["state"] == "interrupted" and row["completeness"] == "low"


def test_multi_scope_runs_are_judged_on_scopes_with_data(conn):
    db.register_source(conn, "yc_jobs", "yc_jobs", "yc", now=T)
    run_id = db.start_run(conn, "import", now=T)
    for i in range(8):
        db.record_source_check(conn, run_id, "yc_jobs", "success", scope=f"company{i}", complete=False, record_count=3 if i < 2 else 0, started_at=T, finished_at=T)
    db.finish_run(conn, run_id, "success", now=T)
    row = next(r for r in source_health.source_health(conn, now=db_now()) if r["source_id"] == "yc_jobs")
    assert row["state"] == "incomplete" and row["state_counts"] == {"incomplete": 2, "unverified_zero": 6}
    assert any("6 of 8 scopes returned zero records" in n for n in row["notes"])


def test_health_report_is_read_only_and_closure_capability_comes_from_lifecycle(conn):
    check(conn, "inv_x", "success", complete=True, scope="jobs:none", n=3)
    before = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0]
    rows = source_health.source_health(conn, now=db_now())
    assert (conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0]) == before
    assert rows[0]["closure_capable"] == lifecycle.closure_capable("investor_board", None) is False
    assert "never closed" in source_health.render(rows)[0] or "no job is ever closed" in source_health.render(rows)[0]


# ----------------------------------------------------------------------------- retry policy

def http_error(code):
    def opener(req, timeout=0):
        calls.append(code)
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
    calls = []
    return opener, calls


@pytest.mark.parametrize("fetch,err", [(fz.fetch_bytes, fz.SourceError), (yc.fetch_json, yc.SourceError)])
@pytest.mark.parametrize("code", [403, 404, 429])
def test_refusals_and_rate_limits_are_never_retried(fetch, err, code):
    opener, calls = http_error(code)
    sleeps = []
    with pytest.raises(err, match=str(code)):
        fetch("https://example.com/x", opener=opener, sleep=sleeps.append)
    assert len(calls) == 1 and sleeps == []                                   # one request, no waiting, no second try


@pytest.mark.parametrize("fetch,err", [(fz.fetch_bytes, fz.SourceError), (yc.fetch_json, yc.SourceError)])
def test_transient_errors_use_a_finite_budget_with_growing_pauses(fetch, err):
    sleeps, calls = [], []

    def opener(req, timeout=0):
        calls.append(1)
        raise TimeoutError("timed out")
    with pytest.raises(err, match="after 3 attempts"):
        fetch("https://example.com/x", opener=opener, sleep=sleeps.append)
    assert len(calls) == 3 and sleeps == [1, 2]
    opener5, calls5 = http_error(503)
    with pytest.raises(err):
        fetch("https://example.com/x", opener=opener5, sleep=lambda s: None)
    assert len(calls5) == 3


# ------------------------------------------------------------ partial pagination, mutation

def test_result_set_that_changes_size_while_being_read_stays_partial():
    pages = [getro_html([getro_job(1), getro_job(2)], 5), getro_html([getro_job(3), getro_job(4)], 6), getro_html([getro_job(5), getro_job(6)], 6)]
    r = run(ACCEL, Web(pages))["jobs"]
    assert r.status == "partial" and not r.complete and len(r.records) == 6
    assert any("reported total changed during the scan (5 -> 6)" in a for a in r.anomalies)


def test_skipped_middle_records_are_a_count_mismatch_not_a_complete_scan():
    r = run(ACCEL, Web([getro_html([getro_job(1), getro_job(2)], 5), getro_html([getro_job(5)], 5), getro_html([], 5)]))["jobs"]
    assert r.status == "partial" and any("count mismatch" in a for a in r.anomalies)


def test_a_mutated_scan_never_authorises_closure(conn):
    first = getro_scans(ACCEL, [getro_job(i, org=f"c{i}", org_id=200 + i, domain=f"c{i}.com") for i in range(1, 4)])
    ib.import_snapshot(conn, make_snapshot([(ACCEL, first)]))
    mutated = run(ACCEL, Web([getro_html([getro_job(1, org="c1", org_id=201, domain="c1.com")], 3), getro_html([getro_job(2, org="c2", org_id=202, domain="c2.com")], 4),
                              getro_html([], 4)]))
    ib.import_snapshot(conn, make_snapshot([(ACCEL, mutated)]))
    assert closures(conn) == 0 and conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 3


# ------------------------------------------------------- interruption, replay, partial snapshot

def two_board_snapshot():
    a = getro_scans(ACCEL, [getro_job(i, org=f"acme{i}", org_id=300 + i, domain=f"acme{i}.com") for i in range(1, 4)])
    t = getro_scans(TECHSTARS, [getro_job(i + 10, org=f"tech{i}", org_id=400 + i, domain=f"tech{i}.com") for i in range(1, 4)])
    return make_snapshot([(ACCEL, a), (TECHSTARS, t)])


def test_interrupted_investor_import_keeps_committed_work_and_a_replay_finishes_cleanly(conn, monkeypatch):
    snap = two_board_snapshot()
    original = ib._import_board

    def flaky(c, b, *a, **k):
        if b["key"] == "techstars":
            raise KeyboardInterrupt()
        return original(c, b, *a, **k)
    monkeypatch.setattr(ib, "_import_board", flaky)
    with pytest.raises(KeyboardInterrupt):
        ib.import_snapshot(conn, snap)
    assert conn.execute("SELECT result FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()[0] == "interrupted"
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 3                              # the first board's work is intact
    assert conn.execute("SELECT COUNT(*) FROM source_checks WHERE source_id = 'inv_techstars'").fetchone()[0] == 0     # nothing claimed for the unfinished board
    assert closures(conn) == 0
    health = {r["source_id"]: r for r in source_health.source_health(conn, now=db_now())}
    assert health["inv_techstars"]["state"] == "no_checks"
    monkeypatch.setattr(ib, "_import_board", original)
    ib.import_snapshot(conn, snap)                                                                   # the retry
    counts = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("jobs", "companies", "sightings", "status_events")]
    ib.import_snapshot(conn, snap)                                                                   # and a second replay
    assert counts == [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("jobs", "companies", "sightings", "status_events")]
    assert counts[0] == 6 and closures(conn) == 0


def test_partial_snapshot_then_retry_is_idempotent_and_keeps_human_verdicts(conn):
    good = go(conn)
    assert good.overall == "success"
    jid = conn.execute("SELECT job_id FROM jobs ORDER BY rowid LIMIT 1").fetchone()[0]
    db.save_verdict(conn, "Yes", "keep me", job_id=jid, now=T)
    state = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("jobs", "companies", "sightings", "status_events")]
    broken = fz_feed()
    broken.set_manifest(totalJobs=...)
    bad = go(conn, fetchers={"yc": yc_fetch(), "feashliaa": broken})
    assert bad.overall == "partial" and status_of(bad, "feashliaa").status == "failed"
    assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("jobs", "companies", "sightings", "status_events")] == state
    assert closures(conn) == 0 and dict(db.get_verdict(conn, job_id=jid))["notes"] == "keep me"
    retry = go(conn, fetchers={"yc": yc_fetch(), "feashliaa": fz_feed()})
    assert retry.overall == "success" and dict(db.get_verdict(conn, job_id=jid))["decision"] == "Yes"
    assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("jobs", "companies", "status_events")] == [state[0], state[1], state[3]]


def test_stale_provider_snapshot_is_refused_without_touching_history(conn):
    go(conn)
    jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    old = Feed([[job(1)]], generated="2020-01-01T00:00:00.000000Z")
    res = go(conn, sources=("feashliaa",), fetchers={"feashliaa": old})
    assert status_of(res, "feashliaa").status in ("failed", "partial", "skipped")
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == jobs and closures(conn) == 0
    assert not conn.execute("SELECT 1 FROM jobs WHERE lifecycle_state IN ('Closed','Not Seen','Active')").fetchone()


# --------------------------------------------------------------- end-to-end failure simulation

def test_mixed_run_one_good_job_source_one_good_company_source_one_failed_one_incomplete(conn):
    # history first: a clean run, a complete investor board, and the operator's verdicts
    go(conn)
    board = getro_scans(ACCEL, [getro_job(i, org=f"bd{i}", org_id=500 + i, domain=f"bd{i}.com", title="Associate Product Manager") for i in range(1, 5)])
    ib.import_snapshot(conn, make_snapshot([(ACCEL, board)]))
    jobs = [r[0] for r in conn.execute("SELECT job_id FROM jobs ORDER BY rowid")]
    db.save_verdict(conn, "Yes", "good", job_id=jobs[0], now=T)
    db.save_verdict(conn, "No", "no thanks", job_id=jobs[1], now=T)
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("jobs", "companies", "sightings", "verdicts")}

    # the failing run: YC (company source) succeeds, Feashliaa (job source) fails, the investor board scan comes back incomplete
    bad_feed = fz_feed()
    bad_feed.set_manifest(totalJobs=...)
    result = go(conn, fetchers={"yc": yc_fetch(), "feashliaa": bad_feed})
    incomplete = run(TECHSTARS, Web([getro_html([getro_job(40, org="late", org_id=640, domain="late.com")], 9), getro_html([], 9)]))
    ib.import_snapshot(conn, make_snapshot([(TECHSTARS, incomplete)]))

    assert result.overall == "partial" and status_of(result, "yc").status == "success" and status_of(result, "feashliaa").status == "failed"
    assert closures(conn) == 0 and not conn.execute("SELECT 1 FROM jobs WHERE lifecycle_state IN ('Closed','Not Seen')").fetchone()
    assert {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before if t != "jobs"}["verdicts"] == before["verdicts"]
    assert dict(db.get_verdict(conn, job_id=jobs[0]))["decision"] == "Yes" and dict(db.get_verdict(conn, job_id=jobs[1]))["decision"] == "No"
    health = {r["source_id"]: r for r in source_health.source_health(conn, now=db_now())}
    assert health["feashliaa"]["state"] in ("schema_error", "failed_other", "network_error") and health["inv_techstars"]["state"] == "partial"
    assert health["yc_oss"]["state"] in ("complete", "complete_filtered") and all(h["may_close_jobs"] is False for h in health.values())

    # the shortlist and a sync preview still work, respect the verdicts, and publish nothing from the failed or incomplete sources unprompted
    cfg = prof.load_profile(), prof.load_ranking()
    sl = shortlist.build(conn, *cfg, with_hints=False)
    assert jobs[1] not in {r["id"] for r in sl.jobs}                                              # the rejected job stays out
    backend = FakeBackend()
    rep = ss.sync_to_sheets(conn, backend, dry_run=True, now="2026-10-21T00:00:00+00:00", shortlist=lambda ov: shortlist.build(conn, *cfg, verdict_overrides=ov, with_hints=False))
    assert backend.writes() == [] and rep.session_note.startswith("Review session")
    assert conn.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0] == 2
