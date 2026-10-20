"""Day 12: the read-only ranking preview on a disposable database (canonical identity, multi-location, safety)."""
import argparse
import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from discovery import candidates, cli, database as db, merge, profile as prof, ranking_preview, sheets_sync

AS_OF = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
NOW = "2026-10-20T10:00:00+00:00"
CHECK = "2026-10-19T00:00:00+00:00"


@pytest.fixture(scope="module")
def cfg():
    return prof.load_profile(), prof.load_ranking()


def file_hash(path):
    with db.connect(path) as _:
        pass
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def add_job(conn, source, company_id, title, url, loc, ext, **kw):
    return db.observe_job(conn, source_id=source, source_url=url, company_id=company_id, title=title, external_id=ext, location=loc,
                          provider_first_seen_at=kw.pop("first", "2026-10-15T00:00:00Z"), now=NOW, **kw).job_id


def build(tmp_path):
    path = tmp_path / "t.db"
    db.initialize_database(path)
    c = db.connect(path)
    db.register_source(c, "feashliaa", "Feashliaa", "ats_feed", config_ref=db.NO_CLOSURE_EVIDENCE)
    db.register_source(c, "inv_accel", "Accel", "investor_board")
    db.register_source(c, "yc_jobs", "YC jobs", "yc")
    for sid in ("feashliaa", "inv_accel", "yc_jobs"):
        run = db.start_run(c, "import", now=CHECK)
        db.record_source_check(c, run, sid, "success", scope="jobs:none (all records)", complete=True, record_count=1, started_at=CHECK, finished_at=CHECK)
    co = lambda name, facts=None: db.get_or_create_company(c, name, None, facts=facts or {"sheet_publish": True}, now=NOW)[0]
    ids = {}
    # Mindtickle: one Lever posting seen by two sources with different location text; the two company records and jobs are merged
    m1, m2 = co("mindtickle"), co("Mindtickle")
    lever = "https://jobs.lever.co/mindtickle/864ac315-d7c5-4cd5-ab44-0e33d08f48f1"
    ja = add_job(c, "feashliaa", m1, "Product Operations Associate", lever, "Pune, Maharashtra", "mf1")
    jb = add_job(c, "inv_accel", m2, "Product Operations Associate", lever + "?lever-source%5B%5D=x", "Bengaluru, Karnataka, India / Bangalore Urban, Karnataka, India", "ma1")
    merge.apply_company_merge(c, m1, m2, rule="r", evidence={}, decided_by="t")
    merge.apply_job_merge(c, ja, jb, rule="r", evidence={}, decided_by="t")
    ids["mindtickle"] = (ja, jb)
    # Fiserv / RSM after the Workday reversal: two separate rows each, one per city
    f = co("fiserv")
    ids["fiserv"] = (add_job(c, "feashliaa", f, "Business Operations Associate", "https://fiserv.wd5.myworkdayjobs.com/x/job/Noida-India/Business-Operations-Associate_R-10401444", "Noida, India", "f1"),
                     add_job(c, "feashliaa", f, "Business Operations Associate", "https://fiserv.wd5.myworkdayjobs.com/x/job/Bengaluru---GS-India/Business-Operations-Associate_R-10401444", "Bengaluru - GS, India", "f2"))
    r = co("rsm")
    ids["rsm"] = (add_job(c, "feashliaa", r, "Finance Operations Analyst", "https://rsm.wd1.myworkdayjobs.com/c/job/Hyderabad/Finance-Operations-Analyst_JR121520", "Hyderabad", "r1"),
                  add_job(c, "feashliaa", r, "Finance Operations Analyst", "https://rsm.wd1.myworkdayjobs.com/c/job/Bengaluru/Finance-Operations-Analyst_JR121520", "Bengaluru", "r2"))
    # Xendit: one Greenhouse posting, kept merged, multi-city list that includes Bangalore
    x1, x2 = co("xendit"), co("Xendit")
    gh = "https://job-boards.greenhouse.io/xendit/jobs/8032565003"
    xa = add_job(c, "feashliaa", x1, "Strategy and Operations Associate", gh, "Bangalore, India ; Jakarta, Indonesia; Kuala Lumpur, Malaysia; Manila, Philippines; Mumbai, India; Singapore, Singapore", "x1")
    xb = add_job(c, "inv_accel", x2, "Strategy and Operations Associate", gh, "Mumbai, Maharashtra, India / Maharashtra, India / India", "x2")
    merge.apply_company_merge(c, x1, x2, rule="r", evidence={}, decided_by="t")
    merge.apply_job_merge(c, xa, xb, rule="r", evidence={}, decided_by="t")
    ids["xendit"] = (xa, xb)
    # assorted records
    o = co("OtherCo")
    ids["foreign"] = add_job(c, "feashliaa", o, "Product Operations Associate", "https://boards.greenhouse.io/otherco/jobs/1000001", "Remote, TX", "o1")
    ids["unknown_loc"] = add_job(c, "feashliaa", o, "Strategy Associate - Founder's Office", "https://boards.greenhouse.io/otherco/jobs/1000002", None, "o2")
    ids["retained"] = add_job(c, "feashliaa", o, "Associate Product Manager", "https://boards.greenhouse.io/otherco/jobs/1000003", "Bengaluru", "o3", provider_freshness="provider_retained")
    ids["board_only"] = add_job(c, "inv_accel", o, "Forward Deployed Engineer", "https://boards.greenhouse.io/otherco/jobs/1000004", "Bengaluru, Karnataka, India", "o4")
    ids["dup1"] = add_job(c, "inv_accel", o, "Business Operations Associate", "https://otherco.example/careers", "Bengaluru", "d1")
    ids["dup2"] = add_job(c, "inv_accel", o, "Business Operations Associate", "https://otherco.example/careers", "Bengaluru", "d2")
    n = co("RejectedCo")
    ids["rejected"] = add_job(c, "feashliaa", n, "Associate Product Manager", "https://boards.greenhouse.io/rejectedco/jobs/1000005", "Bengaluru", "n1")
    db.save_verdict(c, "No", "do not show again - my private note", job_id=ids["rejected"])
    # Stream B: a YC company with an AI description, India based, and no jobs at all
    nojobs = co("NoJobs AI", {"sheet_publish": True, "india_relevance": {"class": "india_based"},
                              "yc": {"batch": "Winter 2024", "locations": "Bengaluru, KA, India", "one_liner": "AI agents for enterprise workflows",
                                     "long_description": "A generative AI and large language model platform. B2B SaaS.", "industries": ["B2B"], "team_size": 8}})
    c.close()
    return path, ids, nojobs


def preview(path, cfg, hints=True):
    p, r = cfg
    ro = db.connect(path, readonly=True)
    try:
        return ranking_preview.build_preview(ro, p, r, as_of=AS_OF, with_hints=hints)
    finally:
        ro.close()


def by_title(pv, employer):
    return {tuple(x["locations"]): x for x in pv["regression_cases"][employer]}


def test_confirmed_merges_are_presented_once_with_every_location(tmp_path, cfg):
    path, ids, _ = build(tmp_path)
    pv = preview(path, cfg)
    assert pv["considered"]["job_rows_in_database"] - pv["considered"]["distinct_canonical_jobs"] == 2          # Mindtickle and Xendit aliases
    for employer in ("mindtickle", "xendit"):
        rows = pv["regression_cases"][employer]
        assert len(rows) == 1                                                                                   # the alias is not an independent job
        assert rows[0]["location_category"] == "bengaluru" and rows[0]["classification"] == "confirmed_eligible"
    mind = pv["regression_cases"]["mindtickle"][0]
    assert any("Pune" in l for l in mind["locations"]) and any("Bengaluru" in l for l in mind["locations"])    # Bengaluru is not hidden
    assert any("merged from 1 record" in h for h in mind["hints"])
    assert len(pv["regression_cases"]["xendit"][0]["locations"]) >= 6


def test_reversed_workday_pairs_stay_separate_and_both_remain_available(tmp_path, cfg):
    path, ids, _ = build(tmp_path)
    pv = preview(path, cfg)
    for employer in ("fiserv", "rsm"):
        rows = pv["regression_cases"][employer]
        assert len(rows) == 2 and {r["classification"] for r in rows} == {"confirmed_eligible", "needs_review"}
        bengaluru = [r for r in rows if r["location_category"] == "bengaluru"][0]
        other = [r for r in rows if r["location_category"] == "india_other_city"][0]
        assert bengaluru["classification"] == "confirmed_eligible" and other["classification"] == "needs_review"      # nothing excluded


def test_unresolved_duplicates_are_kept_and_hinted_not_suppressed(tmp_path, cfg):
    path, ids, _ = build(tmp_path)
    p, r = cfg
    ro = db.connect(path, readonly=True)
    jobs, companies, _ = candidates.load_candidates(ro, with_hints=True)
    ro.close()
    both = [j for j in jobs if j["job_id"] in (ids["dup1"], ids["dup2"])]
    assert len(both) == 2 and all(any("possible duplicate" in h for h in j["hints"]) for j in both)
    pv = preview(path, cfg)
    assert pv["jobs"]["with_unresolved_identity_hint"] >= 2


def test_foreign_unknown_retained_and_board_only_records(tmp_path, cfg):
    path, ids, _ = build(tmp_path)
    p, r = cfg
    ro = db.connect(path, readonly=True)
    jobs, companies, _ = candidates.load_candidates(ro, with_hints=False)
    ro.close()
    from discovery.scoring import score_job
    res = {j["job_id"]: score_job(j, p, r, AS_OF) for j in jobs}
    assert res[ids["foreign"]]["hard_exclusion"] == "LOCATION_INCOMPATIBLE"
    assert res[ids["unknown_loc"]]["classification"] == "needs_review" and res[ids["unknown_loc"]]["location_category"] == "unknown"
    assert res[ids["retained"]]["availability"] == "historical" and res[ids["retained"]]["classification"] == "needs_review"
    assert res[ids["board_only"]]["availability"] == "in_latest_check" and "not employer-verified" in res[ids["board_only"]]["explanation"]
    assert res[ids["rejected"]]["hard_exclusion"] == "HUMAN_NO"


def test_shortlist_is_never_padded_and_needs_review_is_separate(tmp_path, cfg):
    path, ids, _ = build(tmp_path)
    pv = preview(path, cfg)
    j = pv["jobs"]
    assert j["shortlist_size_returned"] < j["shortlist_size_target"]
    assert all(x["classification"] == "confirmed_eligible" for x in j["shortlist"])
    assert any(x["classification"] == "needs_review" for x in j["review_queue"])
    assert all("mindtickle" not in x["company"].lower() or x["id"] == ids["mindtickle"][0] for x in j["shortlist"])    # canonical id, never the alias
    assert j["unique_employers_in_shortlist"] == len({x["company"].lower() for x in j["shortlist"]})


def test_stream_b_finds_companies_with_no_jobs(tmp_path, cfg):
    path, ids, nojobs = build(tmp_path)
    pv = preview(path, cfg)
    names = [c["company"] for c in pv["companies"]["shortlist"]]
    assert "NoJobs AI" in names and pv["companies"]["with_no_jobs_but_qualified"] >= 1


def test_preview_is_read_only_deterministic_and_keeps_verdicts_and_states(tmp_path, cfg):
    path, ids, _ = build(tmp_path)
    before = file_hash(path)
    c = db.connect(path, readonly=True)
    states = [tuple(r) for r in c.execute("SELECT job_id, lifecycle_state FROM jobs ORDER BY 1")]
    events = c.execute("SELECT COUNT(*) FROM status_events").fetchone()[0]
    note = c.execute("SELECT decision, notes FROM verdicts").fetchone()
    with pytest.raises(Exception):
        c.execute("INSERT INTO sources (source_id, name, source_type, created_at, updated_at) VALUES ('x','x','other','t','t')")
    c.close()
    a, b = preview(path, cfg), preview(path, cfg, hints=False)
    assert a["determinism_fingerprint"] == b["determinism_fingerprint"] == preview(path, cfg)["determinism_fingerprint"]
    assert [x["id"] for x in a["jobs"]["shortlist"]] == [x["id"] for x in b["jobs"]["shortlist"]]
    assert file_hash(path) == before
    c = db.connect(path, readonly=True)
    assert [tuple(r) for r in c.execute("SELECT job_id, lifecycle_state FROM jobs ORDER BY 1")] == states
    assert c.execute("SELECT COUNT(*) FROM status_events").fetchone()[0] == events
    assert tuple(c.execute("SELECT decision, notes FROM verdicts").fetchone()) == tuple(note)
    c.close()


def test_publication_gates_are_untouched(tmp_path, cfg):
    path, ids, _ = build(tmp_path)
    c = db.connect(path, readonly=True)
    gate_before = sheets_sync.load_publishable_job_ids(c)
    preview(path, cfg)
    assert sheets_sync.load_shortlist_rows(c) == []                       # nothing is shortlisted into Sheets by this checkpoint
    assert sheets_sync.load_publishable_job_ids(c) == gate_before
    assert not any(j in gate_before for j in (ids["board_only"], ids["dup1"]))      # investor-board-only jobs stay unpublished
    c.close()


def test_ranking_modules_cannot_write_or_reach_out():
    import discovery.candidates as a, discovery.filters as b, discovery.profile as c, discovery.ranking_preview as d, discovery.scoring as e, discovery.yamlconfig as f
    for mod in (a, b, c, d, e, f):
        text = inspect.getsource(mod)
        low = text.lower()
        for bad in ("insert into", "update ", "delete from", "gspread", "urllib.request", "requests.", "subprocess", "socket"):
            assert bad not in low.replace("update_", ""), (mod.__name__, bad)


def test_cli_preview_writes_only_the_requested_json(tmp_path, cfg, capsys):
    path, ids, _ = build(tmp_path)
    before = file_hash(path)
    out = tmp_path / "preview.json"
    code = cli.cmd_ranking_preview(argparse.Namespace(db=str(path), as_of="2026-10-20T12:00:00+00:00", output=str(out), no_hints=True))
    text = capsys.readouterr().out
    assert code == 0 and out.is_file() and "Ranking preview (read-only" in text and "never padded" in text
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["ranking_status"] == "proposed_for_operator_review" and data["considered"]["distinct_canonical_jobs"] > 0
    assert file_hash(path) == before


def test_cli_refuses_a_missing_database(tmp_path, capsys):
    assert cli.cmd_ranking_preview(argparse.Namespace(db=str(tmp_path / "none.db"), as_of=None, output=None, no_hints=True)) == 1


def test_claude_md_carries_the_approval_provenance_and_read_only_ranking_rules():
    text = (db.PROJECT_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Approval provenance" in text and "truncated" in text and "Git push" in text and "exact scope" in text
    assert "Ranking and filtering are read-only" in text and "never describes an observed posting as open" in text
