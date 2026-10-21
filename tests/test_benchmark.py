"""Day 13: benchmark evaluator. Read-only, deterministic, offline, on disposable databases with fictional data."""
import copy
import datetime
import hashlib
from pathlib import Path

import pytest

from discovery import benchmark as bm
from discovery import database as db
from discovery import merge
from discovery import profile as prof
from discovery.schema import SCHEMA_VERSION

T = "2026-10-20T06:00:00+00:00"
CREATED = "2026-10-21T07:00:00+00:00"
FIXTURE = Path(__file__).parent / "fixtures" / "benchmark_day13_independent_2026.csv"
GH = "https://job-boards.greenhouse.io/acme/jobs/111222"
WHEN = datetime.datetime(2026, 10, 21, tzinfo=datetime.timezone.utc)


@pytest.fixture(scope="module")
def cfg():
    return prof.load_profile(), prof.load_ranking()


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "bm.db"
    db.initialize_database(path)
    c = db.connect(path)
    for sid, st in (("feashliaa", "ats_feed"), ("inv_testboard", "investor_board"), ("manual", "manual")):
        db.register_source(c, sid, sid, st, now=T)
    yield c
    c.close()


def add(conn, company, title, url, source="feashliaa", location="Bengaluru, Karnataka, India", ext=None, now=T, domain=None):
    cid = db.get_or_create_company(conn, company, domain, facts={"sheet_publish": False}, now=now)[0]
    return db.observe_job(conn, source_id=source, source_url=url, company_id=cid, title=title, external_id=ext or url, location=location, posting_url=url, now=now).job_id


def rec(i="A01", employer="Acme", title="Associate Product Manager", url=GH, ats="greenhouse:acme:111222", **over):
    r = {"benchmark_id": i, "group": bm.GROUP_POSITIVE, "employer": employer, "exact_title": title, "original_discovery_source": "web search", "original_url": url,
         "direct_employer_or_ats_url": url, "stable_ats_identifier": ats, "location_text": "Bengaluru", "remote_restriction_text": "", "experience_text": "1-3 years",
         "expected_role_family": "associate_product_ops", "availability_evidence": "page loaded with 'Apply for this job' on 2026-10-21", "availability_limits": "",
         "source_observation_date": "2026-10-21", "selection_rationale": "", "evidence_reference": "", "unresolved_or_missing": "", "fixture_created_utc": CREATED}
    r.update(over)
    return r


def run(conn, cfg, rows):
    out, ctx = bm.evaluate(conn, rows, cfg[0], cfg[1], CREATED)
    return {r["benchmark_id"]: r for r in out}, bm.metrics(out), ctx


# ------------------------------------------------------------------------------ identity

def test_exact_ats_key_is_confirmed_and_cites_the_evidence(conn, cfg):
    jid = add(conn, "Acme", "Associate Product Manager", GH)
    res, m, _ = run(conn, cfg, [rec()])
    r = res["A01"]
    assert r["match_class"] == "confirmed_exact" and r["canonical_job_ids"] == jid and "platform + tenant + posting id" in r["match_evidence"][0]
    assert r["automatic_discovery"] and r["automatic_sources"] == "feashliaa" and r["in_database_before_benchmark"] is True
    assert m["confirmed_exact"]["count"] == 1 and m["denominator_positive"] == 1


def test_same_company_and_title_without_a_shared_id_is_only_probable(conn, cfg):
    add(conn, "Acme", "Associate Product Manager", "https://job-boards.greenhouse.io/acme/jobs/999000")
    res, m, _ = run(conn, cfg, [rec()])
    assert res["A01"]["match_class"] == "probable_review" and m["confirmed_exact"]["count"] == 0 and m["probable_review"]["count"] == 1
    assert not res["A01"]["automatic_discovery"] or res["A01"]["match_class"] != "confirmed_exact"


def test_similar_title_at_a_different_company_is_not_a_match(conn, cfg):
    add(conn, "Other Corp", "Associate Product Manager", "https://job-boards.greenhouse.io/othercorp/jobs/111222")      # same numeric id, different tenant
    res, _, _ = run(conn, cfg, [rec()])
    assert res["A01"]["match_class"] == "no_match"


def test_same_company_and_title_but_different_requisitions_stay_separate_and_ambiguous(conn, cfg):
    a = add(conn, "Acme", "Associate Product Manager", "https://job-boards.greenhouse.io/acme/jobs/500001")
    b = add(conn, "Acme", "Associate Product Manager", "https://job-boards.greenhouse.io/acme/jobs/500002")
    res, _, _ = run(conn, cfg, [rec()])
    r = res["A01"]
    assert r["match_class"] == "probable_review" and set(r["canonical_job_ids"].split(";")) == {a, b}            # never auto-resolved to one


def test_cross_source_duplicate_counts_once_and_alias_resolves_to_the_canonical_job(conn, cfg):
    cid = db.get_or_create_company(conn, "Acme", None, facts={"sheet_publish": False}, now=T)[0]
    j1 = db.observe_job(conn, source_id="feashliaa", source_url=GH, company_id=cid, title="Associate Product Manager", external_id="f1", location="Bengaluru", now=T).job_id
    j2 = db.observe_job(conn, source_id="inv_testboard", source_url="https://job-boards.greenhouse.io/acme/jobs/111222?utm_source=x", company_id=cid,
                        title="Associate Product Manager", external_id="b1", location="Bengaluru", now=T).job_id
    assert j1 != j2
    res, m, _ = run(conn, cfg, [rec()])
    assert res["A01"]["match_class"] == "probable_review"                         # two canonical jobs share the key until someone merges them
    merge.apply_job_merge(conn, j1, j2, rule="same_posting", evidence={"t": 1}, decided_by="test")
    res, m, _ = run(conn, cfg, [rec()])
    r = res["A01"]
    assert r["match_class"] == "confirmed_exact" and r["canonical_job_ids"] == j1 and r["automatic_sources"] == "feashliaa;inv_testboard"
    assert m["confirmed_exact"]["count"] == 1 and sum(len(v) for v in m["per_source_confirmed"].values()) == 2          # per-source counts overlap
    assert m["combined_unique_confirmed"] == 1                                                                             # combined coverage does not
    merge.reverse_merge(conn, "job", j2, decided_by="test")
    res, _, _ = run(conn, cfg, [rec()])
    assert res["A01"]["match_class"] == "probable_review"                         # a reversed merge is honoured


def test_record_without_a_job_specific_identifier_cannot_prove_absence(conn, cfg):
    r = rec(url="https://builtinbengaluru.in/job/x/123", ats="none", direct_employer_or_ats_url="NOT FOUND")
    res, m, _ = run(conn, cfg, [r])
    assert res["A01"]["match_class"] == "unverifiable_identity" and m["unverifiable_identity"]["count"] == 1
    assert any(c == "unverifiable_original_evidence" for c, _, _ in res["A01"]["diagnosis"])


# -------------------------------------------------------------- automatic vs manual, timing, coverage

def test_a_manual_entry_is_never_automatic_discovery(conn, cfg):
    add(conn, "Acme", "Associate Product Manager", GH, source="manual", ext="manual|1")
    res, m, _ = run(conn, cfg, [rec()])
    r = res["A01"]
    assert r["match_class"] == "confirmed_exact" and not r["automatic_discovery"] and r["manual_sources"] == "manual" and r["automatic_sources"] == ""
    assert m["confirmed_exact"]["count"] == 0 and m["matched_only_through_manual_entries"]["count"] == 1


def test_pre_existing_versus_later_first_observation(conn, cfg):
    add(conn, "Acme", "Associate Product Manager", GH, now="2026-10-22T00:00:00+00:00")
    res, _, _ = run(conn, cfg, [rec()])
    assert res["A01"]["in_database_before_benchmark"] is False and res["A01"]["first_observed_by_engine"].startswith("2026-10-22")


def test_missing_job_with_employer_in_a_source_is_a_demonstrated_absence_with_timing_hypothesis(conn, cfg):
    add(conn, "Acme", "Customer Success Lead", "https://job-boards.greenhouse.io/acme/jobs/777")
    run_id = db.start_run(conn, "discovery", now=T)
    db.record_source_check(conn, run_id, "feashliaa", "partial", scope="jobs", complete=False, started_at=T, finished_at=T)
    res, _, _ = run(conn, cfg, [rec()])
    cats = {(c, kind) for c, kind, _ in res["A01"]["diagnosis"]}
    assert res["A01"]["match_class"] == "no_match" and ("job_absent_from_sources_that_cover_the_employer", "demonstrated") in cats
    assert ("collection_predates_posting_or_scan_incomplete", "hypothesis") in cats                  # a cause is never claimed without proof
    assert any("not complete" in n for n in res["A01"]["collection_notes"])


def test_employer_absent_everywhere_is_reported_as_an_upstream_coverage_gap(conn, cfg):
    res, _, _ = run(conn, cfg, [rec()])
    cats = {c for c, _, _ in res["A01"]["diagnosis"]}
    assert "upstream_source_does_not_include_the_employer" in cats


# ------------------------------------------------------------------------------------ ranking

def test_hypothetical_ranking_uses_only_the_fixture_and_flags_missing_role_signals(conn, cfg):
    res, _, _ = run(conn, cfg, [rec("A01", title="Forward Deployment Engineer"), rec("A02", title="Associate Product Manager", url="https://x.example/2", ats="none"),
                                rec("N01", title="Senior Full Stack Engineer - Frontend Focused", url="https://x.example/3", ats="none", group=bm.GROUP_NEGATIVE)])
    assert res["A01"]["hypothetical_outcome"] == "needs_review" and res["A01"]["hypothetical_family"] == "forward_deployed_solutions"     # approved weak alias (Day 14)
    assert res["A02"]["hypothetical_outcome"] == "confirmed_eligible"
    assert res["N01"]["hypothetical_outcome"].startswith("excluded")                                                   # a negative control never reaches the shortlist


def test_multiple_locations_and_a_wrongful_geographic_exclusion_check(cfg):
    one = bm.hypothetical(rec(location_text="Bengaluru; Remote"), *cfg, WHEN)
    assert one["location_category"] == "bengaluru" and one["classification"] != "excluded"
    india = bm.hypothetical(rec(location_text="India - Remote"), *cfg, WHEN)
    assert india["location_category"] == "india_remote_explicit"
    us = bm.hypothetical(rec(location_text="Remote, TX"), *cfg, WHEN)
    assert us["hard_exclusion"] == "LOCATION_INCOMPATIBLE"


def test_senior_experience_in_the_fixture_is_read_by_the_hypothetical_check(cfg):
    when = WHEN
    senior = bm.hypothetical(rec(experience_text="'7 or more years'", title="Chief of Staff"), *cfg, when)
    assert senior["experience"] == "senior" and senior["classification"] != "confirmed_eligible"


def test_found_but_not_qualified_is_a_ranking_outcome_not_a_discovery_failure(conn, cfg):
    add(conn, "Acme", "Associate Product Manager", GH, location="Remote, TX")                       # found, but the stored location excludes it
    res, m, _ = run(conn, cfg, [rec()])
    assert res["A01"]["match_class"] == "confirmed_exact" and res["A01"]["actual_outcome"].startswith("excluded:LOCATION")
    assert m["confirmed_found_ranking"]["low_or_excluded"] == 1
    assert any(c == "found_but_not_qualified" for c, _, _ in res["A01"]["diagnosis"])


def test_freshness_keeps_page_evidence_and_database_class_apart():
    f = bm.freshness(rec(), "in_latest_check")
    assert f["employer_page_evidence"].startswith("employer_page_loaded") and f["database_availability_class"] == "in_latest_check" and "not a verified open vacancy" in f["note"]
    assert bm.freshness(rec(availability_limits="CONFLICT: the board index did not list it"), None)["employer_page_evidence"] == "conflicting"
    assert bm.freshness(rec(availability_limits="AGGREGATOR-ONLY evidence"), None)["employer_page_evidence"] == "non_authoritative_only"
    assert bm.freshness(rec(availability_evidence="nothing"), None)["database_availability_class"] == "not_in_database"


# ------------------------------------------------------------------- manual recovery, safety

def test_manual_recovery_is_a_dry_run_and_adds_nothing(conn, cfg):
    before = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("jobs", "sightings", "companies", "status_events", "manual_inputs", "runs")]
    res, m, _ = run(conn, cfg, [rec(), rec("A02", url="ftp://bad.example/x", ats="none", direct_employer_or_ats_url="NOT FOUND")])
    assert res["A01"]["manual_recoverable"] and res["A01"]["manual_action"] == "create"
    assert not res["A02"]["manual_recoverable"]                                                       # an unsafe URL is rejected by the importer
    assert before == [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("jobs", "sightings", "companies", "status_events", "manual_inputs", "runs")]


def test_evaluation_is_deterministic_and_never_writes(tmp_path, cfg):
    path = tmp_path / "ro.db"
    db.initialize_database(path)
    w = db.connect(path)
    for sid, st in (("feashliaa", "ats_feed"), ("manual", "manual")):
        db.register_source(w, sid, sid, st, now=T)
    add(w, "Acme", "Associate Product Manager", GH)
    w.close()
    digest = lambda: hashlib.sha256(path.read_bytes()).hexdigest()
    cfg_hash = [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ("config/search_profile.yaml", "config/ranking.yaml")]
    before = digest()
    ro = db.connect(path, readonly=True)
    rows = [rec(), rec("A02", title="Business Operations Associate", url="https://x.example/2", ats="none")]
    a = bm.evaluate(ro, rows, *cfg, CREATED)[0]
    b = bm.evaluate(ro, copy.deepcopy(rows), *cfg, CREATED)[0]
    with pytest.raises(Exception):
        ro.execute("INSERT INTO companies (company_id, name, normalized_name, created_at, updated_at) VALUES ('x','x','x','t','t')")
    ro.close()
    assert a == b and digest() == before                                                              # same input, same output, file untouched
    assert cfg_hash == [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ("config/search_profile.yaml", "config/ranking.yaml")]   # no rule changed
    assert SCHEMA_VERSION == 7


def test_frozen_fixture_is_intact_complete_and_not_in_the_database(cfg):
    rows = bm.load_fixture(FIXTURE)
    groups = {g: [r for r in rows if r["group"] == g] for g in {r["group"] for r in rows}}
    assert len(groups[bm.GROUP_POSITIVE]) == 11 and len(groups[bm.GROUP_NEGATIVE]) == 5 and len(groups[bm.GROUP_SENIOR]) == 3
    assert len({r["benchmark_id"] for r in rows}) == len(rows) and len({r["fixture_created_utc"] for r in rows}) == 1
    assert all(r["original_url"].startswith("https://") for r in rows) and all(r["source_observation_date"] == "2026-10-21" for r in rows)
    with pytest.raises(ValueError):
        bm.load_fixture(FIXTURE, expected_sha256="0" * 64)


# ------------------------------------------------------------- Day 14: stronger evidence, same labels

def test_tenantless_workable_url_vs_tenant_form_is_probable_with_strong_identifier_evidence(conn, cfg):
    add(conn, "Apna", "Product Operations Associate", "https://apply.workable.com/j/E75B63B3A7")
    r = rec(employer="Apna", title="Product Operations Associate", url="https://standout.work/jobs/x", ats="workable:apna:E75B63B3A7",
            direct_employer_or_ats_url="https://apply.workable.com/apna/j/E75B63B3A7/apply (as quoted by the mirror)")
    res, m, _ = run(conn, cfg, [r])
    got = res["A01"]
    assert got["match_class"] == "probable_review" and got["match_strength"] == "strong_identifier_unscoped" and "Workable shortcode E75B63B3A7" in got["match_evidence"][0]
    assert m["confirmed_exact"]["count"] == 0 and m["probable_review"]["by_strength"] == {"A01": "strong_identifier_unscoped"}      # still not counted as found


def test_ats_uuid_quoted_on_an_employer_page_is_strong_but_unscoped(conn, cfg):
    u = "48c228f1-0762-4d4e-9d83-ae8f751ce19d"
    add(conn, "Sarvam AI", "Deployment Strategist", f"https://jobs.ashbyhq.com/sarvam/{u}")
    r = rec(employer="Sarvam AI", title="Deployment Strategist", url=f"https://www.sarvam.ai/careers/jobs/{u}", ats="sarvam-careers:" + u, direct_employer_or_ats_url="same")
    got = run(conn, cfg, [r])[0]["A01"]
    assert got["match_class"] == "probable_review" and got["match_strength"] == "strong_identifier_unscoped" and "posting UUID" in got["match_evidence"][0]


def test_the_same_identifier_at_another_employer_is_not_even_probable(conn, cfg):
    add(conn, "Other Corp", "Product Operations Associate", "https://apply.workable.com/j/E75B63B3A7")
    r = rec(employer="Apna", title="Product Operations Associate", url="https://standout.work/jobs/x", ats="workable:apna:E75B63B3A7",
            direct_employer_or_ats_url="https://apply.workable.com/apna/j/E75B63B3A7/apply")
    assert run(conn, cfg, [r])[0]["A01"]["match_class"] == "no_match"


def test_title_only_probable_matches_are_labelled_as_the_weakest_kind(conn, cfg):
    add(conn, "Acme", "Associate Product Manager", "https://job-boards.greenhouse.io/acme/jobs/999000")
    assert run(conn, cfg, [rec()])[0]["A01"]["match_strength"] == "employer_and_title_only"
