"""Day 11: identity evidence, reviewable merges, lifecycle policy, field precedence and evidence labels.

Everything runs on disposable SQLite databases; nothing touches the production database or the spreadsheets."""
import json
import sqlite3

import pytest

from discovery import database as db, identity, lifecycle, merge, sheets_sync
from discovery.adapters import investor_boards as ib
from discovery.schema import MIGRATIONS, SCHEMA_VERSION

ASHBY = "https://jobs.ashbyhq.com/cartesia/6d860f5a-b9d9-4df2-b5e5-b12ac80632a4"


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.initialize_database(path)
    c = db.connect(path)
    yield c
    c.close()


def source(conn, sid, stype="ats_feed", config_ref=None):
    db.register_source(conn, sid, sid, stype, None, config_ref=config_ref)


def company(conn, name, domain=None, facts=None):
    cid, _ = db.get_or_create_company(conn, name, domain, facts=facts)
    return cid


def job(conn, cid, title, sid, url, ext=None, **kw):
    return db.observe_job(conn, source_id=sid, source_url=url, company_id=cid, title=title, external_id=ext, **kw)


def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("companies", "jobs", "sightings", "status_events", "verdicts")}


# ----------------------------------------------------------- names and URLs

def test_name_key_ignores_case_punctuation_and_legal_suffix_only():
    assert identity.name_key("Meesho") == identity.name_key("meesho")
    assert identity.name_key("The Acme Co., Inc.") == identity.name_key("acme co")
    assert identity.name_key("Acme Labs") != identity.name_key("Acme")
    assert identity.company_relation("ema", "Ema Unlimited") == "compatible"
    assert identity.company_relation("Pismo", "visa") == "unrelated"
    assert identity.company_relation("Sumo Logic", "sumologic") == "equivalent"
    assert identity.company_relation("Ethos Life", "ethoslife") == "equivalent"


@pytest.mark.parametrize("url,kind", [
    (ASHBY, "specific"),
    ("https://jobs.lever.co/meesho/974e9975-c536-4219-bdaa-9cd0f2d4d030?lever-source%5B%5D=careers", "specific"),
    ("https://boards.greenhouse.io/falconx/jobs/4740719005", "specific"),
    ("https://careers.hpe.com/us/en/job/1216217", "specific"),
    ("https://intel.wd1.myworkdayjobs.com/External/job/india-bangalore/Systems-Engineer_JR0287102", "specific"),
    ("https://elixiirfoods.com/careers", "generic"),
    ("https://careers.elivaas.com", "generic"),
    ("https://sharechat.mynexthire.com/employer/jobs", "generic"),
])
def test_url_signature_separates_job_specific_from_generic(url, kind):
    assert identity.url_signature(url).kind == kind


def test_ats_keys_ignore_tracking_params_and_host_spelling():
    a = identity.url_signature("https://job-boards.greenhouse.io/falconx/jobs/4740719005?gh_src=abc")
    b = identity.url_signature("https://boards.greenhouse.io/FalconX/jobs/4740719005")
    assert a.key == b.key
    lever = identity.url_signature("https://jobs.lever.co/meesho/974e9975-c536-4219-bdaa-9cd0f2d4d030?lever-source%5B%5D=x")
    assert lever.key == identity.url_signature("https://jobs.lever.co/meesho/974e9975-c536-4219-bdaa-9cd0f2d4d030").key


# ------------------------------------------------------------ job evidence

def two_sources(conn):
    source(conn, "feed")
    source(conn, "board", "investor_board")


def tiers(conn):
    return {(p.verdict.tier, p.verdict.rule) for p in identity.job_pairs(identity.load_job_refs(conn))}


def test_same_ats_posting_same_title_equivalent_company_is_tier_a(conn):
    two_sources(conn)
    job(conn, company(conn, "cartesia"), "Forward Deployed Engineer", "feed", ASHBY)
    job(conn, company(conn, "Cartesia"), "Forward Deployed Engineer", "board", ASHBY + "?utm_source=x", ext="6d860f5a")
    assert tiers(conn) == {("A", "same_posting_equivalent_company_names")}


def test_same_posting_with_compatible_company_names_is_only_possible(conn):
    two_sources(conn)
    job(conn, company(conn, "ema"), "Product Manager", "feed", "https://jobs.ashbyhq.com/ema/8268af44-d5d8-4a29-9638-3ea30f35f3a9")
    job(conn, company(conn, "Ema Unlimited"), "Product Manager", "board", "https://jobs.ashbyhq.com/ema/8268af44-d5d8-4a29-9638-3ea30f35f3a9")
    assert tiers(conn) == {("B", "same_posting_company_names_compatible")}


def test_same_posting_attributed_to_unrelated_companies_is_a_conflict(conn):
    two_sources(conn)
    url = "https://careers.hpe.com/us/en/job/1216217"
    job(conn, company(conn, "Nimble Storage"), "Systems Engineer", "board", url, ext="1")
    job(conn, company(conn, "Hpe Simplivity"), "Systems Engineer", "board", url, ext="2")
    assert tiers(conn) == {("D", "same_posting_conflicting_companies")}


def test_generic_careers_url_shared_by_different_titles_is_never_evidence(conn):
    source(conn, "board", "investor_board")
    cid = company(conn, "Elixiir Foods")
    for i, t in enumerate(["Category Manager", "E-Commerce Manager", "Quality Control Manager"]):
        job(conn, cid, t, "board", "https://elixiirfoods.com/careers", ext=str(i))
    assert tiers(conn) == {("C", "same_url_different_titles")}


def test_same_title_on_a_generic_url_is_possible_not_confirmed(conn):
    source(conn, "board", "investor_board")
    cid = company(conn, "Acme")
    job(conn, cid, "Analyst", "board", "https://acme.example/careers", ext="1")
    job(conn, cid, "Analyst", "board", "https://acme.example/careers", ext="2")
    assert tiers(conn) == {("B", "generic_url_same_title")}


def test_specific_url_shared_by_other_titles_is_not_unique(conn):
    two_sources(conn)
    url = "https://careers.example/job/1234567"
    cid = company(conn, "Acme")
    job(conn, cid, "Analyst", "feed", url, ext="1")
    job(conn, cid, "Analyst", "board", url, ext="2")
    job(conn, cid, "Designer", "board", url, ext="3")
    got = {(p.verdict.tier) for p in identity.job_pairs(identity.load_job_refs(conn))}
    assert "A" not in got


def test_title_and_company_alone_never_create_a_candidate(conn):
    two_sources(conn)
    job(conn, company(conn, "Acme"), "Analyst", "feed", "https://a.example/job/11111")
    job(conn, company(conn, "Acme"), "Analyst", "board", "https://b.example/job/22222")
    assert identity.job_pairs(identity.load_job_refs(conn)) == []


# --------------------------------------------------------- company evidence

def test_company_pair_rules(conn):
    a = identity.CompanyRef("a", "Bloom", None, {"bloom.diy"}, set(), {"yc_oss"})
    b = identity.CompanyRef("b", "bloom", None, set(), {"bloom"}, {"feed"})
    assert identity.classify_company_pair(a, b).tier == "B"                       # name (+slug) alone is not enough
    assert identity.classify_company_pair(a, b, "A").tier == "A"                  # plus a shared confirmed posting
    c = identity.CompanyRef("c", "Bloom", None, {"bloom.io"}, set(), {"yc_oss"})
    assert identity.classify_company_pair(a, c).tier == "C"                       # different reported domains: distinct
    d = identity.CompanyRef("d", "Bloom", None, {"bloom.diy"}, set(), {"board"})
    assert identity.classify_company_pair(a, d).rule == "shared_reported_domain"
    e = identity.CompanyRef("e", "Other Name", None, set(), set(), {"feed"})
    assert identity.classify_company_pair(a, e, "A").tier == "D"


def test_shared_hosts_are_not_company_domains():
    assert identity._clean_domain("https://boards.greenhouse.io/x") is None
    assert identity._clean_domain("https://www.cartesia.ai/careers") == "cartesia.ai"


def test_report_is_read_only_and_idempotent(conn):
    two_sources(conn)
    job(conn, company(conn, "cartesia"), "FDE", "feed", ASHBY)
    job(conn, company(conn, "Cartesia"), "FDE", "board", ASHBY, ext="x")
    before = counts(conn)
    r1, r2 = identity.build_report(conn), identity.build_report(conn)
    assert r1 == r2 and counts(conn) == before
    assert r1["jobs"]["by_tier"] == {"A": 1}
    assert identity.render_report(r1)


# ---------------------------------------------------------------- merging

def merged_setup(conn):
    two_sources(conn)
    c1, c2 = company(conn, "cartesia"), company(conn, "Cartesia")
    j1 = job(conn, c1, "FDE", "feed", ASHBY).job_id
    j2 = job(conn, c2, "FDE", "board", ASHBY, ext="x").job_id
    return c1, c2, j1, j2


def test_company_then_job_merge_keeps_every_sighting_event_and_the_alias_row(conn):
    c1, c2, j1, j2 = merged_setup(conn)
    db.save_verdict(conn, "Yes", "good", job_id=j2)
    events_before = conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0]
    with pytest.raises(merge.MergeRefused):                       # different company records: refuse, change nothing
        merge.apply_job_merge(conn, j1, j2, rule="r", evidence={}, decided_by="test")
    r = merge.apply_company_merge(conn, c1, c2, rule="same_name_and_shared_posting", evidence={"x": 1}, decided_by="test")
    assert r.jobs_moved == 1 and r.sightings_moved >= 0
    r = merge.apply_job_merge(conn, j1, j2, rule="same_posting", evidence={"url": ASHBY}, decided_by="test")
    assert r.sightings_moved == 1 and r.verdict_moved
    assert conn.execute("SELECT COUNT(*) FROM sightings WHERE job_id = ?", (j1,)).fetchone()[0] == 2
    assert db.get_job(conn, j2) is not None and db.get_company(conn, c2) is not None        # nothing deleted
    assert conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0] == events_before  # history untouched
    assert db.get_verdict(conn, job_id=j1)["decision"] == "Yes" and db.get_verdict(conn, job_id=j2) is None
    assert identity.alias_ids(conn, "job") == {j2} and identity.alias_ids(conn, "company") == {c2}
    assert "Cartesia" in json.loads(db.get_company(conn, c1)["aliases"]) or identity.name_key("Cartesia") == identity.name_key("cartesia")


def test_merge_replay_is_a_noop(conn):
    c1, c2, j1, j2 = merged_setup(conn)
    merge.apply_company_merge(conn, c1, c2, rule="r", evidence={}, decided_by="t")
    merge.apply_job_merge(conn, j1, j2, rule="r", evidence={}, decided_by="t")
    snap = counts(conn), conn.execute("SELECT COUNT(*) FROM identity_links").fetchone()[0]
    assert merge.apply_company_merge(conn, c1, c2, rule="r", evidence={}, decided_by="t").already_applied
    assert merge.apply_job_merge(conn, j1, j2, rule="r", evidence={}, decided_by="t").already_applied
    assert (counts(conn), conn.execute("SELECT COUNT(*) FROM identity_links").fetchone()[0]) == snap


def test_later_import_of_an_alias_record_lands_on_the_canonical_job(conn):
    c1, c2, j1, j2 = merged_setup(conn)
    merge.apply_company_merge(conn, c1, c2, rule="r", evidence={}, decided_by="t")
    merge.apply_job_merge(conn, j1, j2, rule="r", evidence={}, decided_by="t")
    before = counts(conn)
    obs = job(conn, c1, "FDE", "board", ASHBY, ext="x")           # the board reports its record again
    assert obs.job_id == j1 and not obs.created and counts(conn)["jobs"] == before["jobs"]


def test_merge_refuses_two_verdicts_two_domains_and_chains(conn):
    two_sources(conn)
    c1, c2 = company(conn, "A", "a.example"), company(conn, "A2", "a2.example")
    with pytest.raises(merge.MergeRefused, match="domains"):
        merge.apply_company_merge(conn, c1, c2, rule="r", evidence={}, decided_by="t")
    p1, p2 = company(conn, "P"), company(conn, "p")
    db.save_verdict(conn, "Yes", company_id=p1)
    db.save_verdict(conn, "No", company_id=p2)
    with pytest.raises(merge.MergeRefused, match="verdict"):
        merge.apply_company_merge(conn, p1, p2, rule="r", evidence={}, decided_by="t")
    assert db.get_verdict(conn, company_id=p1)["decision"] == "Yes" and db.get_verdict(conn, company_id=p2)["decision"] == "No"
    q = company(conn, "Q")
    merge.apply_company_merge(conn, c1, q, rule="r", evidence={}, decided_by="t")
    with pytest.raises(merge.MergeRefused, match="chain"):
        merge.apply_company_merge(conn, q, company(conn, "Q3"), rule="r", evidence={}, decided_by="t")


def test_domain_moves_to_the_canonical_company_without_breaking_uniqueness(conn):
    keep, gone = company(conn, "Acme"), company(conn, "acme", "acme.example")
    merge.apply_company_merge(conn, keep, gone, rule="r", evidence={}, decided_by="t")
    k, g = db.get_company(conn, keep), db.get_company(conn, gone)
    assert k["domain"] == "acme.example" and k["identity_status"] == "verified" and g["domain"] is None


def test_plan_proposes_only_tier_a_and_defers_jobs_until_companies_merge(conn):
    c1, c2, j1, j2 = merged_setup(conn)
    plan = merge.plan_merges(conn)
    assert len(plan["companies"]) == 1 and len(plan["jobs"]) == 1 and plan["jobs"][0]["ready"] is True and plan["blocked"] == []
    assert counts(conn)["jobs"] == 2 and identity.alias_ids(conn, "job") == set()       # planning changed nothing


def test_rejected_link_survives_and_links_cannot_be_deleted(conn):
    c1, c2, _, _ = merged_setup(conn)
    link, created = merge.record_link(conn, "company", c1, c2, "rejected", "B", "human_no", {"why": "different firms"}, "operator")
    assert created and identity.alias_ids(conn, "company") == set()
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM identity_links")


def test_migration_5_is_additive_and_preserves_existing_rows(tmp_path):
    path = tmp_path / "v4.db"
    raw = sqlite3.connect(path)
    for m in MIGRATIONS[:4]:
        raw.executescript(m)
    raw.execute("INSERT INTO sources (source_id, name, source_type, created_at, updated_at) VALUES ('s','S','other','t','t')")
    raw.commit()
    raw.close()
    assert db.initialize_database(path) == SCHEMA_VERSION == 7
    c = db.connect(path)
    try:
        assert c.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM identity_links").fetchone()[0] == 0
    finally:
        c.close()


# --------------------------------------------------------------- lifecycle

def complete_check(conn, sid, scope="", outcome="success", complete=True):
    run = db.start_run(conn, "import")
    return db.record_source_check(conn, run, sid, outcome, scope=scope, complete=complete, record_count=1)


def test_investor_board_and_filtered_scans_can_never_close_a_job(conn):
    source(conn, "board", "investor_board", "investor-boards-config")
    source(conn, "ats", "yc")
    cid = company(conn, "Acme")
    jb = job(conn, cid, "Analyst", "board", "https://a.example/job/11111", ext="1").job_id
    ja = job(conn, cid, "Analyst2", "ats", "https://a.example/job/22222", ext="2").job_id
    with pytest.raises(ValueError, match="closure evidence"):
        db.append_status_event(conn, "Closed", "gone", job_id=jb, source_check_id=complete_check(conn, "board", "jobs:none (all records)"))
    with pytest.raises(ValueError, match="filtered"):
        db.append_status_event(conn, "Not Seen", "gone", job_id=ja, source_check_id=complete_check(conn, "ats", "jobs:locations = Bengaluru"))
    with pytest.raises(ValueError):
        db.append_status_event(conn, "Not Seen", "gone", job_id=ja, source_check_id=complete_check(conn, "ats", "partial", "partial", False))
    assert db.get_job(conn, jb)["lifecycle_state"] == "New" and db.get_job(conn, ja)["lifecycle_state"] == "New"
    assert db.append_status_event(conn, "Not Seen", "absent from a complete unfiltered check", job_id=ja,
                                  source_check_id=complete_check(conn, "ats", "feed")) is not None


def test_reopen_needs_same_identity_and_a_fresh_authoritative_source(conn):
    source(conn, "ats", "yc")
    source(conn, "board", "investor_board")
    cid = company(conn, "Acme")
    j = job(conn, cid, "Analyst", "ats", "https://a.example/job/11111", ext="1").job_id
    db.append_status_event(conn, "Closed", "closed", job_id=j, source_check_id=complete_check(conn, "ats", "feed"))
    board_job = job(conn, cid, "Analyst", "board", "https://a.example/job/11111", ext="b1", requisition_id="R1")
    assert db.get_job(conn, j)["lifecycle_state"] == "Closed"                 # a new board listing is not an employer reopening
    job(conn, cid, "Analyst", "ats", "https://a.example/job/11111", ext="1", provider_freshness="provider_retained")
    assert db.get_job(conn, j)["lifecycle_state"] == "Closed"                 # a provider-retained row is not fresh evidence
    job(conn, cid, "Analyst", "ats", "https://a.example/job/11111", ext="1", provider_freshness="latest_collection")
    assert db.get_job(conn, j)["lifecycle_state"] == "Reopened"
    again = counts(conn)
    job(conn, cid, "Analyst", "ats", "https://a.example/job/11111", ext="1", provider_freshness="latest_collection")
    assert counts(conn) == again                                               # repeating an identical observation adds no history
    assert board_job.created


def test_identical_repeat_adds_no_history_and_a_real_change_is_recorded_once(conn):
    source(conn, "ats", "ats_feed")
    cid = company(conn, "Acme")
    first = job(conn, cid, "Analyst", "ats", "https://a.example/job/11111", ext="1", location="Pune")
    base = counts(conn)
    job(conn, cid, "Analyst", "ats", "https://a.example/job/11111", ext="1", location="Pune")
    assert counts(conn)["status_events"] == base["status_events"]
    changed = job(conn, cid, "Analyst", "ats", "https://a.example/job/11111", ext="1", location="Bengaluru")
    assert changed.changed and db.get_job(conn, first.job_id)["location"] == "Bengaluru"
    n = counts(conn)["status_events"]
    job(conn, cid, "Analyst", "ats", "https://a.example/job/11111", ext="1", location="Bengaluru")
    assert counts(conn)["status_events"] == n


def test_lower_precedence_source_cannot_overwrite_canonical_fields(conn):
    source(conn, "ats", "ats_feed")
    source(conn, "board", "investor_board")
    cid = company(conn, "Acme")
    j = job(conn, cid, "Analyst", "ats", "https://a.example/job/11111", ext="1", location="Bengaluru").job_id
    db.record_sighting(conn, "board", "https://a.example/job/11111", job_id=j, external_id="b1")
    job(conn, cid, "Analyst", "board", "https://a.example/job/11111", ext="b1", location="Pune")   # board hash first set
    job(conn, cid, "Analyst", "board", "https://a.example/job/11111", ext="b1", location="Mumbai")  # board disagrees
    row = db.get_job(conn, j)
    assert row["location"] == "Bengaluru"
    reasons = [r[0] for r in conn.execute("SELECT reason FROM status_events WHERE job_id = ?", (j,))]
    assert any("canonical fields kept" in r for r in reasons)
    assert row["lifecycle_state"] == "New"


def test_field_precedence_is_deterministic():
    c = [("Pune", "investor_board", None, "2026-10-20T10:00:00+00:00", "inv_a"),
         ("Bengaluru", "ats_feed", "no-closure-evidence", "2026-10-01T10:00:00+00:00", "feashliaa"),
         ("Mumbai", "aggregator", None, "2026-10-21T10:00:00+00:00", "jobspy")]
    assert lifecycle.pick_field(c) == "Bengaluru"
    assert lifecycle.pick_field(list(reversed(c))) == "Bengaluru"
    tie = [("A", "investor_board", None, "2026-10-01", "inv_a"), ("B", "investor_board", None, "2026-10-02", "inv_b")]
    assert lifecycle.pick_field(tie) == "B" and lifecycle.pick_field(tie[::-1]) == "B"
    assert lifecycle.pick_field([(None, "ats_feed", None, "x", "s"), ("", "yc", None, "x", "s")]) is None


def test_scope_filters_are_recognised():
    assert lifecycle.scope_is_filtered("jobs:locations = Bengaluru, Karnataka, India")
    assert lifecycle.scope_is_filtered("jobs:searchable_locations = Bengaluru")
    assert not lifecycle.scope_is_filtered("jobs:none (all records on the board)")
    assert not lifecycle.scope_is_filtered("")


# ---------------------------------------------------------- evidence labels

def evidence_for(conn, cid):
    return sheets_sync.load_company_rows(conn)[cid]["Hiring Evidence"]


def test_investor_board_jobs_are_never_described_as_open_or_employer_confirmed(conn):
    source(conn, "board", "investor_board", "investor-boards-config")
    cid = company(conn, "Acme")
    job(conn, cid, "Analyst", "board", "https://a.example/job/11111", ext="1")
    text = evidence_for(conn, cid)
    assert "no successful source check on record" in text and "employer-confirmed" not in text and "currently live" not in text
    run = db.start_run(conn, "import")
    db.record_source_check(conn, run, "board", "success", scope="jobs:none (all records)", complete=True, record_count=1,
                           started_at="2020-01-01T00:00:00+00:00")
    text = evidence_for(conn, cid)
    assert "listed in the latest source check (not employer-verified)" in text and "employer-confirmed open" not in text
    assert "sources: board" in text and "last observed" in text                      # dates and sources are kept


def test_employer_confirmed_open_needs_an_employer_direct_complete_unfiltered_check(conn):
    source(conn, "direct", "other", lifecycle.EMPLOYER_DIRECT)
    source(conn, "board", "investor_board")
    cid = company(conn, "Acme")
    job(conn, cid, "Analyst", "direct", "https://a.example/job/11111", ext="1")
    job(conn, company(conn, "Other"), "Analyst", "board", "https://b.example/job/22222", ext="1")
    run = db.start_run(conn, "import")
    for sid, scope, ok in (("direct", "feed", True), ("board", "feed", True)):
        db.record_source_check(conn, run, sid, "success", scope=scope, complete=ok, record_count=1, started_at="2020-01-01T00:00:00+00:00")
    rows = sheets_sync.load_company_rows(conn)
    assert rows[cid]["Hiring Evidence"].startswith("1 job(s) observed: 1 employer-confirmed open")
    other = [v for k, v in rows.items() if v["Company"] == "Other"][0]["Hiring Evidence"]
    assert "employer-confirmed" not in other
    filtered = db.start_run(conn, "import")
    db.record_source_check(conn, filtered, "direct", "success", scope="jobs:locations = Pune", complete=True, record_count=1,
                           started_at="2020-01-02T00:00:00+00:00")
    assert "employer-confirmed" not in evidence_for(conn, cid)                       # a filtered check proves nothing broader


def test_retained_and_closed_jobs_are_historical_and_no_jobs_say_so(conn):
    source(conn, "feed", "ats_feed")
    cid = company(conn, "Acme")
    job(conn, cid, "Analyst", "feed", "https://a.example/job/11111", ext="1", provider_freshness="provider_retained")
    assert "1 historical or provider-retained" in evidence_for(conn, cid)
    assert evidence_for(conn, company(conn, "Empty Co")) == "No jobs observed"


def test_merged_alias_rows_are_not_published_twice(conn):
    c1, c2, j1, j2 = merged_setup(conn)
    merge.apply_company_merge(conn, c1, c2, rule="r", evidence={}, decided_by="t")
    merge.apply_job_merge(conn, j1, j2, rule="r", evidence={}, decided_by="t")
    names = [r["Company"] for r in sheets_sync.load_company_rows(conn).values()]
    assert len(names) == 1
    assert j2 not in sheets_sync.load_post_rows(conn) and j1 in sheets_sync.load_post_rows(conn)


# ------------------------------------------------------------- Nexus / empty

def test_empty_parent_board_is_a_verified_empty_result_not_a_failure():
    nexus = ib.load_boards()["nexus"]
    page = b'<html><script>window.__C={"fixedBoard":false,"csrfToken":"tok-9"}</script></html>'
    calls = []

    def web(board, req):
        calls.append(req)
        if req["method"] == "GET":
            return 200, page, None
        return 200, json.dumps({"jobs": [], "total": 0, "meta": {}}).encode(), None

    r = ib.scan_board(nexus, web, ("jobs",), sleep=lambda s: None)["jobs"]
    assert calls[1]["json"]["board"] == {"id": "nexus-venture-partners", "isParent": True}
    assert r.complete and r.status == "complete" and r.records == [] and r.reported_total == 0 and not r.error
    bad = ib.scan_board(nexus, lambda b, q: (200, page, None) if q["method"] == "GET" else (200, b"[]", None), ("jobs",),
                        sleep=lambda s: None)["jobs"]
    assert not bad.complete                                                          # a malformed answer is never "verified empty"


def test_empty_complete_snapshot_records_a_successful_check_and_no_jobs(conn):
    nexus = ib.load_boards()["nexus"]
    scans = {"jobs": ib.ScopeResult(scope="jobs", status="complete", complete=True, reported_total=0, filters="none (all records on the board)",
                                    pages_completed=1, requests=2),
             "companies": ib.ScopeResult(scope="companies", status="partial", complete=False, filters="derived from job listings only")}
    snap = {"format": ib.SNAPSHOT_FORMAT, "source": "investor_boards", "collected_at": "2026-10-20T00:00:00+00:00",
            "finished_at": "2026-10-20T00:00:01+00:00", "seconds": 1,
            "boards": [{"key": nexus.key, "name": nexus.name, "platform": nexus.platform, "url": nexus.url, "permission": nexus.permission,
                        "scopes": {k: vars(v) for k, v in scans.items()}}]}
    import hashlib
    snap["records_sha256"] = hashlib.sha256(json.dumps(snap["boards"], sort_keys=True).encode()).hexdigest()
    before = counts(conn)
    ib.import_snapshot(conn, snap)
    after = counts(conn)
    assert (after["jobs"], after["companies"]) == (before["jobs"], before["companies"])
    checks = conn.execute("SELECT scope, outcome, complete, record_count FROM source_checks WHERE source_id = 'inv_nexus' ORDER BY scope").fetchall()
    assert [(c["outcome"], c["complete"], c["record_count"]) for c in checks if c["scope"].startswith("jobs")] == [("success", 1, 0)]


def test_three_way_cluster_points_every_alias_straight_at_one_canonical_record(conn):
    source(conn, "feed")
    source(conn, "board", "investor_board")
    source(conn, "board2", "investor_board")
    c1, c2 = company(conn, "falconx"), company(conn, "FalconX")
    j1 = job(conn, c1, "Compliance Associate", "feed", "https://boards.greenhouse.io/falconx/jobs/4740719005").job_id
    j2 = job(conn, c2, "Compliance Associate", "board", "https://job-boards.greenhouse.io/falconx/jobs/4740719005", ext="a").job_id
    j3 = job(conn, c2, "Compliance Associate", "board2", "https://boards.greenhouse.io/falconx/jobs/4740719005?gh_src=z", ext="b").job_id
    plan = merge.plan_merges(conn)
    assert len(plan["jobs"]) == 2 and {e["canonical"] for e in plan["jobs"]} == {merge.choose_canonical(conn, "job", j1, j2, j3)}
    assert all(e["cluster_size"] == 3 for e in plan["jobs"])


def test_same_posting_with_conflicting_company_domains_is_blocked_not_merged(conn):
    source(conn, "board", "investor_board")
    a = company(conn, "Mihup", facts={"investor_boards": {"reported_domains": ["mihup.ai"]}})
    b = company(conn, "Mihup", facts={"investor_boards": {"reported_domains": ["mihup.com"]}})
    job(conn, a, "Senior PM", "board", "https://mihup.keka.com/careers/jobdetails/12345", ext="1")
    job(conn, b, "Senior PM", "board", "https://mihup.keka.com/careers/jobdetails/12345", ext="2")
    got = {(p.verdict.tier, p.verdict.rule) for p in identity.job_pairs(identity.load_job_refs(conn), identity.load_company_refs(conn))}
    assert got == {("D", "same_posting_conflicting_company_domains")}


def test_reverse_merge_restores_sightings_jobs_verdict_and_domain(conn):
    c1, c2, j1, j2 = merged_setup(conn)
    db.save_verdict(conn, "Maybe", "n", job_id=j2)
    owner = {r[0]: r[1] for r in conn.execute("SELECT job_id, company_id FROM jobs")}
    keyed = lambda: sorted((r["sighting_id"], r["source_id"], r["job_id"], r["company_id"]) for r in conn.execute("SELECT * FROM sightings"))
    verdict = lambda: [(r["decision"], r["notes"], r["job_id"]) for r in conn.execute("SELECT * FROM verdicts")]
    before_s, before_v = keyed(), verdict()
    merge.apply_company_merge(conn, c1, c2, rule="r", evidence={}, decided_by="t")
    merge.apply_job_merge(conn, j1, j2, rule="r", evidence={}, decided_by="t")
    merge.reverse_merge(conn, "job", j2, decided_by="operator")
    merge.reverse_merge(conn, "company", c2, decided_by="operator")
    assert {r[0]: r[1] for r in conn.execute("SELECT job_id, company_id FROM jobs")} == owner
    assert keyed() == before_s and verdict() == before_v
    assert identity.alias_ids(conn, "job") == set() and identity.alias_ids(conn, "company") == set()
    assert conn.execute("SELECT COUNT(*) FROM identity_links WHERE status = 'possible'").fetchone()[0] == 2
    merge.apply_company_merge(conn, c1, c2, rule="r", evidence={}, decided_by="t")        # can be merged again later
    assert identity.alias_ids(conn, "company") == {c2}


# ---------------------------------------------- Day 14: the same identifier written two ways (candidates only, never merges)

WK_BARE = "https://apply.workable.com/j/E75B63B3A7"
WK_TENANT = "https://apply.workable.com/apna/j/E75B63B3A7/apply"


def test_workable_shortcode_helper_reads_both_forms_and_nothing_else():
    from discovery.idkeys import path_uuid, workable_shortcode
    assert workable_shortcode(WK_BARE) == ("", "E75B63B3A7") and workable_shortcode(WK_TENANT) == ("apna", "E75B63B3A7")
    assert workable_shortcode("https://apply.workable.com/apna/") is None and workable_shortcode("https://jobs.lever.co/apna/j/E75B63B3A7") is None
    assert workable_shortcode("https://apply.workable.com/j/short") is None
    u = "6d860f5a-6a76-4c89-9f3e-0123456789ab"
    assert path_uuid(f"https://sarvam.ai/careers/jobs/{u}") == ("sarvam.ai", u) and path_uuid("https://x.example/jobs/12345") is None


def test_tenantless_and_tenant_workable_urls_become_a_review_candidate_not_a_merge(conn):
    two_sources(conn)
    a = job(conn, company(conn, "Apna"), "Product Operations Associate", "feed", WK_BARE, ext="a").job_id
    b = job(conn, company(conn, "apna"), "Product Operations Associate", "board", WK_TENANT, ext="b").job_id
    assert tiers(conn) == {("B", "workable_shortcode_tenantless_vs_tenant")}
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2 and a != b and conn.execute("SELECT COUNT(*) FROM identity_links").fetchone()[0] == 0


def test_one_shortcode_under_two_tenants_is_a_conflict_not_a_match(conn):
    two_sources(conn)
    job(conn, company(conn, "Apna"), "Product Operations Associate", "feed", "https://apply.workable.com/apna/j/E75B63B3A7", ext="a")
    job(conn, company(conn, "Other Corp"), "Product Operations Associate", "board", "https://apply.workable.com/othercorp/j/E75B63B3A7", ext="b")
    got = tiers(conn)
    assert {t for t, _ in got} == {"D"} and any(r == "workable_shortcode_tenant_conflict" for _, r in got)


def test_same_shortcode_at_unrelated_companies_or_with_other_titles_never_reaches_tier_a(conn):
    two_sources(conn)
    job(conn, company(conn, "Apna"), "Product Operations Associate", "feed", WK_BARE, ext="a")
    job(conn, company(conn, "Zeta Holdings"), "Product Operations Associate", "board", WK_TENANT, ext="b")
    assert {t for t, _ in tiers(conn)} == {"D"}


def test_same_shortcode_different_title_is_possible_only(conn):
    two_sources(conn)
    job(conn, company(conn, "Apna"), "Product Operations Associate", "feed", WK_BARE, ext="a")
    job(conn, company(conn, "apna"), "Product Analyst", "board", WK_TENANT, ext="b")
    assert tiers(conn) == {("B", "same_identifier_different_titles")}


def test_ats_uuid_quoted_on_an_employer_careers_page_is_a_candidate(conn):
    two_sources(conn)
    u = "48c228f1-0762-4d4e-9d83-ae8f751ce19d"
    job(conn, company(conn, "Sarvam AI"), "Deployment Strategist", "feed", f"https://jobs.ashbyhq.com/sarvam/{u}", ext="a")
    job(conn, company(conn, "Sarvam AI"), "Deployment Strategist", "board", f"https://www.sarvam.ai/careers/jobs/{u}", ext="b")
    assert ("B", "same_uuid_different_hosts") in tiers(conn)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
