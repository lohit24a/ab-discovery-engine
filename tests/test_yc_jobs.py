"""YC job-page adapter (`yc_jobs`): selection, parsing, failure states, import, idempotency, protections.

Offline only. Page structure here is the structure ASSUMED from the reference reader (see the adapter docstring);
these fixtures must be re-checked against a real page once the live probe is allowed.
"""

import html
import json
import socket

import pytest

from discovery import cli, database as db, pipeline
from discovery.adapters import jobspy_source as js
from discovery.adapters import yc_jobs as yj


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("network access attempted in an offline test")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(js, "_default_scrape", lambda **k: pytest.fail("JobSpy must not run for YC jobs"))


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.initialize_database(path)
    c = db.connect(path)
    yield c
    c.close()


def add_yc_company(conn, yc_id, slug, *, cls="india_based", hiring=False, name=None):
    facts = {"yc": {"yc_id": yc_id, "slug": slug, "is_hiring_reported": hiring, "locations": "Bengaluru, Karnataka, India"},
             "india_relevance": {"class": cls, "reason": "test", "source": "yc_oss"}, "sheet_publish": cls == "india_based"}
    if hiring is None:
        del facts["yc"]["is_hiring_reported"]
    db.register_source(conn, "yc_oss", "YC (yc-oss API)", "yc")
    cid, _ = db.get_or_create_company(conn, name or slug.title(), None, facts=facts)
    db.record_sighting(conn, "yc_oss", f"https://www.ycombinator.com/companies/{slug}", company_id=cid, external_id=str(yc_id))
    return cid


def page(company_id, jobs, extra_props=None, slug="acme"):
    props = {"company": {"id": company_id, "name": "Acme", "slug": slug}, "jobPostings": jobs}
    props.update(extra_props or {})
    data = html.escape(json.dumps({"props": props}), quote=True)
    return (f'<html><body><div id="WaasShowJobsPage-react-component-abc" data-page="{data}"></div></body></html>').encode()


def job(i, **over):
    j = {"id": 1000 + i, "title": f"Product Analyst {i}", "url": f"/companies/acme/jobs/{1000 + i}-product-analyst",
         "applyUrl": f"https://www.workatastartup.com/jobs/{1000 + i}", "createdAt": "2026-10-10T08:00:00Z",
         "location": "Bengaluru, KA, IN"}
    j.update(over)
    return j


class FakeWeb:
    """Stands in for fetch_page. Maps slug -> (status, body, error)."""

    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, url):
        self.calls.append(url)
        slug = url.split("/companies/")[1].split("/")[0]
        return self.table.get(slug, (404, None, "HTTP 404"))


def run_collect(companies, web, **kw):
    return yj.collect(companies, fetch=web, sleep=lambda s: None, now_fn=lambda: "2026-10-20T12:00:00+00:00", **kw)


def sel(conn, **kw):
    return yj.select_companies(conn, **kw)


# ------------------------------------------------------------ selection

def test_selection_ignores_the_hiring_flag_and_includes_missing_flags(conn):
    add_yc_company(conn, 1, "a", hiring=True)
    add_yc_company(conn, 2, "b", hiring=False)
    add_yc_company(conn, 3, "c", hiring=None)
    add_yc_company(conn, 4, "d", cls="no_india_evidence_yet", hiring=True)
    chosen = sel(conn)
    assert [c["slug"] for c in chosen] == ["a", "b", "c"]
    assert {c["hiring_flag"] for c in chosen} == {True, False, None}
    counts = yj.selection_counts(chosen)
    assert (counts["hiring_true"], counts["hiring_false"], counts["hiring_unknown"]) == (1, 1, 1)
    assert [c["slug"] for c in sel(conn, classes=("india_based", "no_india_evidence_yet"))] == ["a", "b", "c", "d"]
    with pytest.raises(yj.YCJobsError):
        sel(conn, classes=("everything",))


def test_representative_sample_keeps_every_hiring_flag_group(conn):
    for i in range(1, 7):
        add_yc_company(conn, i, f"c{i}", hiring=(i % 3 == 0) or (None if i == 1 else False))
    sample = yj.representative_sample(sel(conn), 3)
    assert {c["hiring_flag"] for c in sample} == {True, False, None} and len(sample) == 3


# ---------------------------------------------------------------- parsing

def test_page_with_jobs_is_parsed_with_all_fields():
    p = yj.parse_jobs_page(page(7, [job(1), job(2)]), "acme")
    assert p.state == yj.CHECKED_LISTINGS and len(p.jobs) == 2 and p.page_company_id == "7"
    first = p.jobs[0]
    assert first["external_id"] == "1001" and first["title"] == "Product Analyst 1"
    assert first["url"] == "https://www.ycombinator.com/companies/acme/jobs/1001-product-analyst"
    assert first["apply_url"].startswith("https://www.workatastartup.com/") and first["posted_date"] == "2026-10-10"
    assert first["location"] == "Bengaluru, KA, IN"


def test_successful_empty_page_is_checked_no_listings_not_not_hiring():
    p = yj.parse_jobs_page(page(7, []), "acme")
    assert p.state == yj.CHECKED_NONE and p.jobs == []


@pytest.mark.parametrize("body", [b"<html>Please sign in</html>", b"", page(7, [])[:60],
                                  b'<div id="WaasShowJobsPage-react-component-x" data-page="{not json}"></div>',
                                  b'<div id="WaasShowJobsPage-react-component-x" data-page="{&quot;props&quot;:{}}"></div>'])
def test_unexpected_or_malformed_pages_are_failed_never_empty(body):
    p = yj.parse_jobs_page(body, "acme")
    assert p.state == yj.FAILED and p.jobs == []


def test_missing_optional_fields_are_kept_as_unknown():
    p = yj.parse_jobs_page(page(7, [{"title": "Analyst", "url": "https://www.ycombinator.com/companies/acme/jobs/9-x"}]), "acme")
    j = p.jobs[0]
    assert j["external_id"] == "9" and j["location"] is None and j["posted_date"] is None and j["apply_url"] is None


def test_duplicate_postings_collapse_and_unusable_ones_make_the_page_partial():
    p = yj.parse_jobs_page(page(7, [job(1), job(1), {"title": "", "url": "/x"}, "junk"]), "acme")
    assert len(p.jobs) == 1 and p.state == yj.PARTIAL and any("unusable" in n for n in p.notes)


def test_pagination_markers_make_the_scan_partial_not_complete():
    p = yj.parse_jobs_page(page(7, [job(1)], extra_props={"nextPage": 2}), "acme")
    assert p.state == yj.PARTIAL and any("pagination" in n for n in p.notes)
    assert yj.parse_jobs_page(page(7, [], extra_props={"hasMore": True}), "acme").state == yj.PARTIAL


# ----------------------------------------------------------------- collection

def test_collect_isolates_failures_and_stops_on_a_block_without_retry(conn):
    for i, slug in enumerate(["a", "b", "c", "d"], 1):
        add_yc_company(conn, i, slug, hiring=bool(i % 2))
    web = FakeWeb({"a": (200, page(1, [job(1)]), None), "b": (500, None, "HTTP 500"), "c": (429, None, "HTTP 429"),
                   "d": (200, page(4, [job(2)]), None)})
    snap = run_collect(sel(conn), web)
    states = {c["slug"]: c["state"] for c in snap["companies"]}
    assert states == {"a": yj.CHECKED_LISTINGS, "b": yj.FAILED, "c": yj.BLOCKED, "d": yj.NOT_CHECKED}
    assert len(web.calls) == 3 and snap["requests"] == 3 and "blocked" in snap["stopped"]       # d was never requested
    assert web.calls.count("https://www.ycombinator.com/companies/c/jobs") == 1                 # no retry


def test_timeouts_and_redirects_are_uncertain_checks_not_empty_ones(conn):
    add_yc_company(conn, 1, "a")
    add_yc_company(conn, 2, "b")
    web = FakeWeb({"a": (None, None, "TimeoutError: timed out"), "b": (302, None, "HTTP 302")})
    snap = run_collect(sel(conn), web)
    assert [c["state"] for c in snap["companies"]] == [yj.FAILED, yj.FAILED]
    assert all(not c["jobs"] for c in snap["companies"])


def test_request_cap_and_runtime_cap_are_enforced(conn):
    for i in range(1, 6):
        add_yc_company(conn, i, f"c{i}")
    web = FakeWeb({f"c{i}": (200, page(i, []), None) for i in range(1, 6)})
    snap = run_collect(sel(conn), web, max_requests=2)
    assert snap["requests"] == 2 and [c["state"] for c in snap["companies"]].count(yj.NOT_CHECKED) == 3
    ticks = iter(range(0, 1000, 100))
    web2 = FakeWeb({f"c{i}": (200, page(i, []), None) for i in range(1, 6)})
    snap2 = yj.collect(sel(conn), fetch=web2, sleep=lambda s: None, clock=lambda: next(ticks), max_seconds=150,
                       now_fn=lambda: "2026-10-20T12:00:00+00:00")
    assert snap2["stopped"] == "runtime cap reached" and snap2["requests"] < 5


def test_company_id_mismatch_is_a_failed_check(conn):
    add_yc_company(conn, 1, "a")
    snap = run_collect(sel(conn), FakeWeb({"a": (200, page(999, [job(1)]), None)}))
    c = snap["companies"][0]
    assert c["state"] == yj.FAILED and c["jobs"] == [] and "does not match" in c["error"]


def test_fetch_refuses_other_hosts():
    with pytest.raises(yj.YCJobsError):
        yj.fetch_page("https://example.com/companies/a/jobs")


def test_delay_and_ceilings_cannot_be_weakened(conn):
    for i in range(1, 4):
        add_yc_company(conn, i, f"c{i}")
    waits = []
    web = FakeWeb({f"c{i}": (200, page(i, []), None) for i in range(1, 4)})
    yj.collect(sel(conn), fetch=web, sleep=waits.append, delay=0.0, now_fn=lambda: "t")
    assert waits and min(waits) >= yj.MIN_DELAY_SECONDS
    snap = run_collect(sel(conn), web, max_requests=10**6)
    assert snap["params"]["max_requests"] <= yj.MAX_REQUESTS_CEILING


# --------------------------------------------- the Peoplebox-style contradiction

def test_directory_false_but_listings_observed_is_flagged_and_flag_is_kept(conn, tmp_path):
    cid = add_yc_company(conn, 5, "peoplebox", hiring=False, name="Peoplebox")
    snap = run_collect(sel(conn), FakeWeb({"peoplebox": (200, page(5, [job(1), job(2)], slug="peoplebox"), None)}))
    c = snap["companies"][0]
    assert c["state"] == yj.CHECKED_LISTINGS and len(c["jobs"]) == 2
    assert c["disagreement"] == "directory_not_hiring_but_recent_listings_observed"
    counts = yj.import_snapshot(conn, snap)
    assert counts.jobs_created == 2
    facts = json.loads(db.get_company(conn, cid)["facts"])
    assert facts["yc"]["is_hiring_reported"] is False                                   # directory fact untouched
    last = facts["yc_jobs"]["last_successful_check"]
    assert last["state"] == yj.CHECKED_LISTINGS and last["listed_count"] == 2 and last["disagreement"]
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE company_id = ?", (cid,)).fetchone()[0] == 2


def test_directory_true_but_empty_page_does_not_establish_not_hiring(conn):
    cid = add_yc_company(conn, 6, "acme", hiring=True)
    snap = run_collect(sel(conn), FakeWeb({"acme": (200, page(6, []), None)}))
    c = snap["companies"][0]
    assert c["state"] == yj.CHECKED_NONE and c["disagreement"] == "directory_hiring_but_no_listings_observed"
    yj.import_snapshot(conn, snap)
    facts = json.loads(db.get_company(conn, cid)["facts"])
    assert facts["yc"]["is_hiring_reported"] is True and facts["yc_jobs"]["last_attempt"]["listed_count"] == 0
    assert "not_hiring" not in json.dumps(facts["yc_jobs"])


# -------------------------------------------------------------------- import

def snapshot_for(conn, web_table, **kw):
    return run_collect(sel(conn), FakeWeb(web_table), **kw)


def test_import_links_jobs_to_the_existing_company_without_creating_duplicates(conn):
    cid = add_yc_company(conn, 1, "acme", hiring=False)
    before_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    snap = snapshot_for(conn, {"acme": (200, page(1, [job(1), job(2)]), None)})
    counts = yj.import_snapshot(conn, snap)
    assert (counts.jobs_created, counts.companies) == (2, 1) and counts.result == "success"
    assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == before_companies
    rows = conn.execute("SELECT j.company_id, j.title, j.posting_url, j.posted_at, j.lifecycle_state, j.location FROM jobs j").fetchall()
    assert {r["company_id"] for r in rows} == {cid} and {r["lifecycle_state"] for r in rows} == {"New"}
    assert rows[0]["posting_url"].startswith("https://www.ycombinator.com/companies/acme/jobs/")
    s = conn.execute("SELECT * FROM sightings WHERE source_id = 'yc_jobs'").fetchall()
    assert len(s) == 2 and all(x["provider_first_seen_at"] == "2026-10-10" for x in s) and {x["external_id"] for x in s} == {"1001", "1002"}


def test_replay_creates_no_duplicates_and_only_adds_source_checks(conn):
    add_yc_company(conn, 1, "acme")
    snap = snapshot_for(conn, {"acme": (200, page(1, [job(1)]), None)})
    yj.import_snapshot(conn, snap)
    first = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("companies", "jobs", "sightings", "status_events")}
    again = yj.import_snapshot(conn, snap)
    second = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in first}
    assert first == second and again.jobs_created == 0 and again.jobs_unchanged == 1
    assert conn.execute("SELECT COUNT(*) FROM source_checks WHERE source_id = 'yc_jobs'").fetchone()[0] == 2


def test_changed_job_content_appends_a_changed_event_not_a_duplicate(conn):
    add_yc_company(conn, 1, "acme")
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (200, page(1, [job(1)]), None)}))
    counts = yj.import_snapshot(conn, snapshot_for(conn, {"acme": (200, page(1, [job(1, location="Remote (India)")]), None)}))
    assert counts.jobs_changed == 1 and conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_failed_or_blocked_or_empty_later_scan_never_closes_or_erases_jobs(conn):
    cid = add_yc_company(conn, 1, "acme")
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (200, page(1, [job(1)]), None)}))
    for outcome in ({"acme": (500, None, "HTTP 500")}, {"acme": (429, None, "HTTP 429")}, {"acme": (200, page(1, []), None)}):
        yj.import_snapshot(conn, snapshot_for(conn, outcome))
    jobs = conn.execute("SELECT lifecycle_state FROM jobs WHERE company_id = ?", (cid,)).fetchall()
    assert [j[0] for j in jobs] == ["New"]
    states = {r[0] for r in conn.execute("SELECT new_state FROM status_events")}
    assert states <= {"New", "Changed"}                                              # nothing Closed / Not Seen
    facts = json.loads(db.get_company(conn, cid)["facts"])["yc_jobs"]
    assert facts["last_successful_check"]["listed_count"] == 0                      # the later verified-empty page
    assert conn.execute("SELECT COUNT(*) FROM source_checks WHERE source_id='yc_jobs' AND complete = 1").fetchone()[0] == 0


def test_failed_scan_keeps_the_last_successful_check_record(conn):
    cid = add_yc_company(conn, 1, "acme")
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (200, page(1, [job(1)]), None)}))
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (500, None, "HTTP 500")}))
    facts = json.loads(db.get_company(conn, cid)["facts"])["yc_jobs"]
    assert facts["last_attempt"]["state"] == yj.FAILED
    assert facts["last_successful_check"]["state"] == yj.CHECKED_LISTINGS and facts["last_successful_check"]["listed_count"] == 1


def test_source_checks_are_per_company_with_the_right_outcomes(conn):
    for i, slug in enumerate(["a", "b", "c"], 1):
        add_yc_company(conn, i, slug)
    snap = snapshot_for(conn, {"a": (200, page(1, [job(1)]), None), "b": (200, page(2, []), None), "c": (500, None, "HTTP 500")})
    counts = yj.import_snapshot(conn, snap)
    rows = {r["scope"]: (r["outcome"], r["complete"], r["record_count"]) for r in
            conn.execute("SELECT * FROM source_checks WHERE source_id = 'yc_jobs'")}
    assert rows == {"company/a": ("success", 0, 1), "company/b": ("success", 0, 0), "company/c": ("failed", 0, 0)}
    assert counts.result == "partial"


def test_verdicts_and_notes_survive_imports(conn):
    cid = add_yc_company(conn, 1, "acme")
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (200, page(1, [job(1)]), None)}))
    job_id = conn.execute("SELECT job_id FROM jobs").fetchone()[0]
    db.save_verdict(conn, "Yes", "great fit", job_id=job_id)
    db.save_verdict(conn, "Maybe", "check later", company_id=cid)
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (200, page(1, [job(1, title="Product Analyst 1")]), None)}))
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (500, None, "HTTP 500")}))
    assert db.get_verdict(conn, job_id=job_id)["notes"] == "great fit"
    assert db.get_verdict(conn, company_id=cid)["decision"] == "Maybe"


def test_directory_refresh_does_not_overwrite_job_scan_status(conn):
    cid = add_yc_company(conn, 1, "acme")
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (200, page(1, [job(1)]), None)}))
    db.update_company_details(conn, cid, facts_patch={"yc": {"slug": "acme", "is_hiring_reported": True},
                                                      "india_relevance": {"class": "india_based"}})
    assert "yc_jobs" in json.loads(db.get_company(conn, cid)["facts"])


def test_cross_source_identity_conflict_isolated_to_one_company(conn):
    a = add_yc_company(conn, 1, "a")
    b = add_yc_company(conn, 2, "b")
    # the same job URL already belongs to company b via this source -> importing it under a is an identity conflict
    db.register_source(conn, yj.SOURCE_ID, yj.SOURCE_NAME, "yc")
    db.observe_job(conn, source_id=yj.SOURCE_ID, source_url="https://www.ycombinator.com/companies/a/jobs/1001-product-analyst",
                   company_id=b, title="Product Analyst 1", external_id="1001")
    snap = snapshot_for(conn, {"a": (200, page(1, [job(1)], slug="a"), None), "b": (200, page(2, [job(2)], slug="b"), None)})
    counts = yj.import_snapshot(conn, snap)
    assert len(counts.company_errors) == 1 and counts.company_errors[0][0] == "a" and counts.result == "partial"
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE company_id = ?", (b,)).fetchone()[0] == 2     # b still imported
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE company_id = ?", (a,)).fetchone()[0] == 0


# -------------------------------------------------------- snapshot and report

def test_snapshot_round_trip_and_tamper_detection(conn, tmp_path):
    add_yc_company(conn, 1, "acme")
    snap = snapshot_for(conn, {"acme": (200, page(1, [job(1)]), None)})
    path = yj.write_snapshot(snap, tmp_path / "snaps")
    assert yj.load_snapshot(path)["authorization"] == {"basis": "user_attested", "reference": None}
    data = json.loads(path.read_text(encoding="utf-8"))
    data["companies"][0]["jobs"][0]["title"] = "tampered"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(yj.YCJobsError, match="hash"):
        yj.load_snapshot(path)


def test_summary_reports_all_requested_numbers(conn):
    add_yc_company(conn, 1, "a", hiring=False)
    add_yc_company(conn, 2, "b", hiring=True)
    add_yc_company(conn, 3, "c", hiring=None)
    add_yc_company(conn, 4, "d", hiring=False)
    snap = snapshot_for(conn, {"a": (200, page(1, [job(1), job(2)]), None), "b": (200, page(2, []), None),
                               "c": (500, None, "HTTP 500"), "d": (200, page(4, [job(3)], {"nextPage": 2}), None)})
    s = yj.summarize(snap, conn)
    assert (s["companies_in_snapshot"], s["hiring_true"], s["hiring_false"], s["hiring_unknown"]) == (4, 1, 2, 1)
    assert (s["checked"], s["companies_with_listings"], s["jobs_retrieved"], s["unique_jobs"]) == (2, 2, 3, 3)
    assert s["by_state"][yj.PARTIAL] == 1 and s["by_state"][yj.FAILED] == 1
    assert {x[4] for x in s["contradictions"]} == {"directory_not_hiring_but_recent_listings_observed",
                                                    "directory_hiring_but_no_listings_observed"}
    assert s["jobs_new_if_imported"] == 3 and s["jobs_existing"] == 0
    assert any(x[0] == "c" for x in s["failed_or_blocked"]) and any(x[0] == "d" for x in s["incomplete"])


# ------------------------------------------------------------- CLI and safety

def run_cli_collect(tmp_path, conn_path, **kw):
    import argparse
    args = argparse.Namespace(authorized_by_operator=True, classes="india_based", sample=None, max_requests=None,
                              max_seconds=900, plan=False, recent_days=90, newest_first=False, limit=None, skip_checked_in=[])
    for k, v in kw.items():
        setattr(args, k, v)
    return args


def test_cli_plan_sends_nothing_and_collect_requires_the_attestation_flag(tmp_path, capsys):
    path = tmp_path / "c.db"
    db.initialize_database(path)
    c = db.connect(path)
    add_yc_company(c, 1, "acme")
    c.close()
    calls = []
    rc = cli.cmd_yc_jobs_collect(run_cli_collect(tmp_path, path, plan=True), fetch=lambda u: calls.append(u), db_path=path)
    assert rc == 0 and calls == [] and "PLAN ONLY" in capsys.readouterr().out
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["yc-jobs-collect"])                                    # the flag is mandatory


def test_cli_collect_then_dry_run_then_apply_with_backup(tmp_path, capsys):
    path = tmp_path / "c.db"
    db.initialize_database(path)
    c = db.connect(path)
    add_yc_company(c, 1, "acme", hiring=False)
    c.close()
    web = FakeWeb({"acme": (200, page(1, [job(1)]), None)})
    rc = cli.cmd_yc_jobs_collect(run_cli_collect(tmp_path, path), fetch=web, sleep=lambda s: None, db_path=path,
                                 snapshot_dir=tmp_path / "snaps")
    out = capsys.readouterr().out
    assert rc == 0 and "contradiction: acme" in out and "Nothing was written to SQLite" in out
    snap_file = next((tmp_path / "snaps").glob("*.json"))
    import argparse
    rc = cli.cmd_yc_jobs_import(argparse.Namespace(snapshot=str(snap_file), dry_run=True, apply=False), db_path=path,
                                backup_dir=tmp_path / "bk")
    assert rc == 0 and "DRY RUN" in capsys.readouterr().out and not (tmp_path / "bk").exists()
    check = db.connect(path)
    assert check.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    check.close()
    rc = cli.cmd_yc_jobs_import(argparse.Namespace(snapshot=str(snap_file), dry_run=False, apply=True), db_path=path,
                                backup_dir=tmp_path / "bk")
    assert rc == 0 and list((tmp_path / "bk").glob("*.db"))
    check = db.connect(path)
    assert check.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    check.close()


def test_yc_jobs_never_touches_sheets_or_jobspy_and_stays_out_of_the_default_pipeline():
    source = open(yj.__file__, encoding="utf-8").read()
    for forbidden in ("gspread", "sheets_sync", "jobspy", "restricted_policy", "proxies", "verify=False", "cookie"):
        assert forbidden not in source.replace("no cookies", "").replace("Cookie", ""), forbidden
    assert "yc_jobs" not in pipeline.SOURCE_ORDER
    with pytest.raises(pipeline.PipelineError):
        pipeline.parse_sources("yc_jobs")


def test_snapshot_folder_is_git_ignored():
    import subprocess
    from pathlib import Path
    rel = (yj.SNAPSHOT_DIR / "20261020120000_yc_jobs.json").relative_to(Path(yj.__file__).resolve().parents[2]).as_posix()
    assert subprocess.run(["git", "check-ignore", "-q", rel], cwd=Path(yj.__file__).resolve().parents[2]).returncode == 0


# ---------------------------------------- shapes observed on the live pages (2026-10-20, sanitized)

def shape_page(name):
    fixtures = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures" / "yc_job_page_shapes.json").read_text(encoding="utf-8"))
    data = html.escape(json.dumps({"props": fixtures[name]["props"], "component": "WaasShowJobsPage"}), quote=True)
    return f'<html><div id="WaasShowJobsPage-react-component-1" data-page="{data}"></div></html>'.encode()


def test_live_shape_with_listings_is_parsed_without_inventing_dates():
    p = yj.parse_jobs_page(shape_page("razorpay"), "razorpay")
    assert p.state == yj.CHECKED_LISTINGS and p.page_company_id == "827" and len(p.jobs) == 3
    j = p.jobs[0]
    assert j["external_id"] == "16046" and j["url"].endswith("/companies/razorpay/jobs/Xpdifpfmk-senior-principal-engineer-manager-frontend-engineering")
    assert j["location"] == "Bangalore, Karnataka" and j["job_type"] == "Full-time"
    assert j["posted_date"] is None and j["posted_text"] == "about 7 years"            # relative age is not a date
    assert j["experience_min_years"] == 6.0 and j["experience_text"] == "6+ years"
    assert j["apply_url_gated"] is True and "authenticate" in j["apply_url"]           # YC login gate, not an employer link
    assert {x["external_id"] for x in p.jobs} == {"16046", "6516", "77"}


def test_live_shape_empty_page_is_a_verified_empty_page():
    p = yj.parse_jobs_page(shape_page("cleartax"), "cleartax")
    assert p.state == yj.CHECKED_NONE and p.page_company_id == "664" and p.jobs == []


def test_live_shape_import_keeps_years_old_listings_as_company_evidence_only(conn):
    cid = add_yc_company(conn, 827, "razorpay", hiring=True, name="Razorpay")
    cid2 = add_yc_company(conn, 664, "cleartax", hiring=False, name="Clear")
    web = FakeWeb({"razorpay": (200, shape_page("razorpay"), None), "cleartax": (200, shape_page("cleartax"), None)})
    snap = run_collect(sel(conn), web)
    states = {c["slug"]: c["state"] for c in snap["companies"]}
    assert states == {"razorpay": yj.CHECKED_LISTINGS, "cleartax": yj.CHECKED_NONE}
    s = yj.summarize(snap, conn)
    assert s["jobs_with_iso_posting_date"] == 0 and s["jobs_with_relative_age_only"] == 3 and s["direct_employer_apply_urls"] == 0
    assert s["evidence"][yj.HISTORICAL_STALE] == 3 and s["jobs_new_if_imported"] == 0 and s["companies_only_historical"] == 1
    counts = yj.import_snapshot(conn, snap)
    assert counts.jobs_created == 0 and conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    facts = json.loads(db.get_company(conn, cid)["facts"])
    attempt = facts["yc_jobs"]["last_attempt"]
    assert attempt["state"] == yj.CHECKED_LISTINGS and attempt["listed_count"] == 3 and attempt["job_rows_imported"] == 0
    assert attempt["current_hiring_evidence"] == yj.HISTORICAL_STALE                    # never "currently hiring"
    assert attempt["disagreement"] == "directory_hiring_but_only_historical_listings_observed"
    hist = attempt["historical_listings"]
    assert len(hist) == 3 and {h["external_id"] for h in hist} == {"16046", "6516", "77"}
    assert all(h["url"].startswith("https://www.ycombinator.com/companies/razorpay/jobs/") and h["observed_at"] for h in hist)
    assert {h["posted_text"] for h in hist} == {"about 7 years", "over 8 years"} and attempt["observed_jobs"] == []
    assert facts["yc"]["is_hiring_reported"] is True                                    # directory fact untouched
    empty = json.loads(db.get_company(conn, cid2)["facts"])["yc_jobs"]["last_attempt"]
    assert empty["state"] == yj.CHECKED_NONE and empty["listed_count"] == 0 and empty["hiring_flag_at_check"] is False
    assert empty["current_hiring_evidence"] == "none_observed"


def test_related_jobs_of_other_companies_are_never_ingested():
    fixtures = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures" / "yc_job_page_shapes.json").read_text(encoding="utf-8"))
    props = dict(fixtures["cleartax"]["props"], relatedJobs=[{"id": 1, "title": "Other company job", "url": "/companies/other/jobs/1-x"}])
    data = html.escape(json.dumps({"props": props}), quote=True)
    body = f'<div id="WaasShowJobsPage-react-component-1" data-page="{data}"></div>'.encode()
    p = yj.parse_jobs_page(body, "cleartax")
    assert p.state == yj.CHECKED_NONE and p.jobs == []


@pytest.mark.parametrize("text,days", [("about 7 years", 2555), ("over 8 years", 2920), ("3 months", 90), ("a day", 1),
                                        ("less than 1 day", 1), ("", None), (None, None), ("recently", None)])
def test_relative_age_estimate_never_invents_a_date(text, days):
    assert yj.age_days(text) == days


def test_summary_counts_stale_listings_separately_from_open_ones(conn):
    add_yc_company(conn, 827, "razorpay", hiring=True)
    snap = run_collect(sel(conn), FakeWeb({"razorpay": (200, shape_page("razorpay"), None)}))
    s = yj.summarize(snap)
    assert s["jobs_older_than_1y"] == 3 and s["jobs_age_unknown"] == 0 and s["jobs_retrieved"] == 3


# ------------------------------------------------------------- freshness / evidence model

def dated_job(i, days_old, ref="2026-10-20"):
    from datetime import date, timedelta
    d = (date.fromisoformat(ref) - timedelta(days=days_old)).isoformat()
    return job(i, createdAt=f"{d}T08:00:00Z")


def test_evidence_categories_follow_a_configurable_threshold_and_never_claim_confirmation():
    ref = "2026-10-20T12:00:00+00:00"
    recent = {"posted_date": "2026-10-01"}
    old = {"posted_date": "2020-01-01"}
    undated = {"posted_text": None}
    assert yj.categorize(recent, ref) == yj.RECENT_UNVERIFIED and yj.categorize(old, ref) == yj.HISTORICAL_STALE
    assert yj.categorize(undated, ref) == yj.UNKNOWN_AGE and yj.categorize({"posted_text": "recently"}, ref) == yj.UNKNOWN_AGE
    sixty = {"posted_date": "2026-08-21"}                                    # 60 days old
    assert yj.categorize(sixty, ref, 90) == yj.RECENT_UNVERIFIED and yj.categorize(sixty, ref, 30) == yj.HISTORICAL_STALE
    for j in (recent, old, undated, sixty):
        assert yj.categorize(j, ref) != yj.CURRENT_CONFIRMED                   # a YC page cannot confirm an opening
    assert yj.CURRENT_CONFIRMED in yj.CATEGORIES and len(yj.CATEGORIES) == 5


def test_recent_listings_are_imported_as_unverified_jobs_and_stale_ones_as_company_evidence(conn):
    cid = add_yc_company(conn, 1, "acme", hiring=True)
    snap = snapshot_for(conn, {"acme": (200, page(1, [dated_job(1, 10), dated_job(2, 4000), job(3, createdAt=None)]), None)})
    counts = yj.import_snapshot(conn, snap)
    assert counts.jobs_created == 2                                           # recent + undated; the 4000-day listing is evidence only
    titles = {r[0] for r in conn.execute("SELECT title FROM jobs")}
    assert titles == {"Product Analyst 1", "Product Analyst 3"}
    attempt = json.loads(db.get_company(conn, cid)["facts"])["yc_jobs"]["last_attempt"]
    cats = {o["external_id"]: o["category"] for o in attempt["observed_jobs"]}
    assert cats == {"1001": yj.RECENT_UNVERIFIED, "1003": yj.UNKNOWN_AGE}
    assert [h["external_id"] for h in attempt["historical_listings"]] == ["1002"]
    assert attempt["current_hiring_evidence"] == yj.RECENT_UNVERIFIED and attempt["evidence"][yj.HISTORICAL_STALE] == 1
    assert {r[0] for r in conn.execute("SELECT lifecycle_state FROM jobs")} == {"New"}      # none verified, none closed


def test_historical_listings_alone_do_not_create_a_contradiction_or_current_hiring(conn):
    cid = add_yc_company(conn, 1, "acme", hiring=False)
    snap = snapshot_for(conn, {"acme": (200, page(1, [dated_job(1, 3000), dated_job(2, 5000)]), None)})
    c = snap["companies"][0]
    assert c["state"] == yj.CHECKED_LISTINGS and c["disagreement"] is None            # old listings do not contradict "not hiring"
    assert yj.summarize(snap)["contradictions"] == []
    yj.import_snapshot(conn, snap)
    attempt = json.loads(db.get_company(conn, cid)["facts"])["yc_jobs"]["last_attempt"]
    assert attempt["current_hiring_evidence"] == yj.HISTORICAL_STALE and attempt["job_rows_imported"] == 0
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_undated_listing_for_a_not_hiring_company_is_a_weaker_flagged_contradiction(conn):
    add_yc_company(conn, 1, "acme", hiring=False)
    snap = snapshot_for(conn, {"acme": (200, page(1, [job(1, createdAt=None)]), None)})
    assert snap["companies"][0]["disagreement"] == "directory_not_hiring_but_undated_listings_observed"


def test_stale_postings_are_not_auto_closed_and_never_close_earlier_imports(conn):
    cid = add_yc_company(conn, 1, "acme")
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (200, page(1, [dated_job(1, 10)]), None)}))
    # the same posting later counts as older than a stricter threshold, and then the page fails
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (200, page(1, [dated_job(1, 10)]), None)}, recency_days=5))
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (500, None, "HTTP 500")}))
    assert [r[0] for r in conn.execute("SELECT lifecycle_state FROM jobs")] == ["New"]
    assert {r[0] for r in conn.execute("SELECT new_state FROM status_events")} <= {"New", "Changed"}
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE company_id = ?", (cid,)).fetchone()[0] == 1


def test_incomplete_scans_report_unavailable_evidence_not_zero_jobs(conn):
    cid = add_yc_company(conn, 1, "acme", hiring=True)
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (500, None, "HTTP 500")}))
    attempt = json.loads(db.get_company(conn, cid)["facts"])["yc_jobs"]["last_attempt"]
    assert attempt["state"] == yj.FAILED and attempt["listed_count"] is None
    assert attempt["current_hiring_evidence"] == yj.UNAVAILABLE and attempt["disagreement"] is None


def test_recency_threshold_is_recorded_with_the_snapshot_and_configurable(conn):
    add_yc_company(conn, 1, "acme")
    snap = snapshot_for(conn, {"acme": (200, page(1, [dated_job(1, 60)]), None)}, recency_days=30)
    assert snap["params"]["recency_days"] == 30 and yj.summarize(snap)["evidence"][yj.HISTORICAL_STALE] == 1
    snap = snapshot_for(conn, {"acme": (200, page(1, [dated_job(1, 60)]), None)})
    assert snap["params"]["recency_days"] == yj.DEFAULT_RECENCY_DAYS and yj.summarize(snap)["evidence"][yj.RECENT_UNVERIFIED] == 1


def test_yc_job_page_jobs_are_never_appended_to_the_review_sheet(conn):
    from discovery import sheets_sync
    cid = add_yc_company(conn, 1, "acme", cls="india_based")                  # sheet_publish = true company
    yj.import_snapshot(conn, snapshot_for(conn, {"acme": (200, page(1, [dated_job(1, 10)]), None)}))
    job_id = conn.execute("SELECT job_id FROM jobs").fetchone()[0]
    other = db.insert_job(conn, cid, "Directly entered job")
    publishable = sheets_sync.load_publishable_job_ids(conn)
    assert job_id not in publishable and other in publishable                 # only the yc_jobs-sourced job is held back
    db.save_verdict(conn, "Yes", "reviewed by hand", job_id=job_id)
    assert job_id in sheets_sync.load_publishable_job_ids(conn)               # a human verdict keeps a row visible


def test_plan_batch_orders_skips_and_limits_without_using_the_hiring_flag(conn):
    for i, flag in enumerate([True, False, None, False, True], 1):
        add_yc_company(conn, i * 10, f"c{i}", hiring=flag)
    everyone = sel(conn)
    assert [c["slug"] for c in yj.plan_batch(everyone, newest_first=True)] == ["c5", "c4", "c3", "c2", "c1"]
    assert [c["slug"] for c in yj.plan_batch(everyone, newest_first=True, limit=2, skip_slugs=["c5"])] == ["c4", "c3"]
    assert [c["slug"] for c in yj.plan_batch(everyone)] == ["c1", "c2", "c3", "c4", "c5"]


def test_slugs_in_snapshot_excludes_unrequested_companies(conn, tmp_path):
    add_yc_company(conn, 1, "a")
    add_yc_company(conn, 2, "b")
    snap = run_collect(sel(conn), FakeWeb({"a": (200, page(1, []), None), "b": (200, page(2, []), None)}), max_requests=1)
    path = yj.write_snapshot(snap, tmp_path)
    assert yj.slugs_in_snapshot(path) == ["a"]


def test_cli_next_batch_plan_skips_earlier_snapshot_and_sends_nothing(tmp_path, capsys):
    path = tmp_path / "c.db"
    db.initialize_database(path)
    c = db.connect(path)
    for i in range(1, 6):
        add_yc_company(c, i, f"c{i}", hiring=bool(i % 2))
    c.close()
    web = FakeWeb({"c1": (200, page(1, []), None)})
    cli.cmd_yc_jobs_collect(run_cli_collect(tmp_path, path, limit=1), fetch=web, sleep=lambda s: None, db_path=path,
                            snapshot_dir=tmp_path / "snaps")
    snap_file = str(next((tmp_path / "snaps").glob("*.json")))
    capsys.readouterr()
    calls = []
    rc = cli.cmd_yc_jobs_collect(run_cli_collect(tmp_path, path, plan=True, newest_first=True, limit=2, skip_checked_in=[snap_file]),
                                 fetch=lambda u: calls.append(u), db_path=path)
    out = capsys.readouterr().out
    assert rc == 0 and calls == [] and "Skipping 1 companies" in out and "checking 2" in out


# ------------------------------------- behaviours seen in the 40-company live sample (sanitized shapes)

def test_mixed_recent_and_historical_page_splits_job_rows_from_company_evidence(conn):
    cid = add_yc_company(conn, 31424, "lab0", hiring=True, name="Lab0")
    jobs = [job(1, createdAt="11 days", location="Bengaluru, KA, IN", minExperience="1+ years"),
            job(2, createdAt="about 1 month"), job(3, createdAt="27 days"),
            job(4, createdAt="5 months", location="Bengaluru, KA, IN")]
    snap = snapshot_for(conn, {"lab0": (200, page(31424, jobs, slug="lab0"), None)})
    s = yj.summarize(snap, conn)
    assert s["jobs_retrieved"] == 4 and s["evidence"] == {yj.RECENT_UNVERIFIED: 3, yj.HISTORICAL_STALE: 1, yj.UNKNOWN_AGE: 0}
    assert s["jobs_new_if_imported"] == 3                          # raw listings (4) and current-eligible candidates (3) stay separate
    yj.import_snapshot(conn, snap)
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE company_id = ?", (cid,)).fetchone()[0] == 3
    attempt = json.loads(db.get_company(conn, cid)["facts"])["yc_jobs"]["last_attempt"]
    assert attempt["listed_count"] == 4 and attempt["job_rows_imported"] == 3 and len(attempt["historical_listings"]) == 1
    assert attempt["current_hiring_evidence"] == yj.RECENT_UNVERIFIED and attempt["disagreement"] is None


def test_not_hiring_company_with_a_recent_listing_is_the_peoplebox_style_contradiction(conn):
    cid = add_yc_company(conn, 26877, "peoplebox", hiring=False, name="Peoplebox")
    snap = snapshot_for(conn, {"peoplebox": (200, page(26877, [job(1, createdAt="4 days")], slug="peoplebox"), None)})
    assert snap["companies"][0]["disagreement"] == "directory_not_hiring_but_recent_listings_observed"
    yj.import_snapshot(conn, snap)
    facts = json.loads(db.get_company(conn, cid)["facts"])
    assert facts["yc"]["is_hiring_reported"] is False and facts["yc_jobs"]["last_attempt"]["current_hiring_evidence"] == yj.RECENT_UNVERIFIED


def test_old_listing_with_an_employer_apply_url_is_kept_as_evidence_not_dropped_or_closed(conn):
    cid = add_yc_company(conn, 1, "acme")
    old = job(1, createdAt="over 3 years", applyUrl="https://boards.greenhouse.io/acme/jobs/777")
    snap = snapshot_for(conn, {"acme": (200, page(1, [old]), None)})
    row = yj.listing_rows(snap)[0]
    assert row["apply_url_is_login_gate"] is False and row["evidence_category"] == yj.HISTORICAL_STALE
    assert yj.summarize(snap)["direct_employer_apply_urls"] == 1
    yj.import_snapshot(conn, snap)
    hist = json.loads(db.get_company(conn, cid)["facts"])["yc_jobs"]["last_attempt"]["historical_listings"][0]
    assert hist["apply_url"] == "https://boards.greenhouse.io/acme/jobs/777" and hist["apply_url_is_login_gate"] is False
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0                 # still not a job row


@pytest.mark.parametrize("created", ["garbage", "", None, "someday", "2099-01-01T00:00:00Z"])
def test_missing_or_contradictory_dates_never_crash_and_never_become_dates(conn, created):
    add_yc_company(conn, 1, "acme")
    snap = snapshot_for(conn, {"acme": (200, page(1, [job(1, createdAt=created)]), None)})
    row = yj.listing_rows(snap)[0]
    assert row["evidence_category"] in (yj.UNKNOWN_AGE, yj.RECENT_UNVERIFIED)           # a future date is age 0, not a crash
    if created in ("garbage", "", None, "someday"):
        assert row["evidence_category"] == yj.UNKNOWN_AGE and row["posted_date"] is None


def test_listing_rows_carry_every_requested_field_and_no_eligibility_claims(conn):
    add_yc_company(conn, 31178, "cardboard", hiring=True, name="Cardboard")
    jobs = [job(1, createdAt="about 2 months", location="Bengaluru / San Francisco (Remote)")]
    snap = snapshot_for(conn, {"cardboard": (200, page(31178, jobs, slug="cardboard"), None)})
    rows = yj.listing_rows(snap)
    assert set(rows[0]) == set(yj.LISTING_COLUMNS) and len(rows) == 1
    r = rows[0]
    assert r["yc_id"] == "31178" and r["company"] == "Cardboard" and r["directory_hiring_flag"] is True
    assert r["retrieved_at"] == "2026-10-20T12:00:00+00:00" and r["remote_hint"] is True
    assert r["remote_eligibility"] == "unknown" and r["scan_complete"] is False and r["scan_state"] == yj.CHECKED_LISTINGS


def test_selection_carries_batch_and_location_evidence(conn):
    cid = add_yc_company(conn, 5, "acme")
    row = sel(conn)[0]
    assert row["batch"] is None or isinstance(row["batch"], str)
    assert row["locations"] == "Bengaluru, Karnataka, India" and row["status"] is None and row["company_id"] == cid


def test_report_command_is_read_only_and_writes_a_csv_next_to_the_snapshot(conn, tmp_path, capsys):
    import argparse
    add_yc_company(conn, 1, "acme", hiring=True)
    snap = snapshot_for(conn, {"acme": (200, page(1, [job(1, createdAt="3 days")]), None)})
    path = yj.write_snapshot(snap, tmp_path)
    rc = cli.cmd_yc_jobs_report(argparse.Namespace(snapshot=[str(path)], csv=True))
    out = capsys.readouterr().out
    assert rc == 0 and "Snapshots combined: 1" in out and (tmp_path / (path.name + ".listings.csv")).is_file()
    header = (tmp_path / (path.name + ".listings.csv")).read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(yj.LISTING_COLUMNS)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
