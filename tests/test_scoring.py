"""Day 12: deterministic, explainable scoring; conservative classification; never-padded shortlists."""
import copy
import random
from datetime import datetime, timezone

import pytest

from discovery import profile as prof
from discovery import scoring as sc

AS_OF = datetime(2026, 10, 20, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def profile():
    return prof.load_profile()


@pytest.fixture(scope="module")
def ranking():
    return prof.load_ranking()


def job(title="Associate Product Manager", locations=("Bengaluru, Karnataka, India",), **over):
    j = {"job_id": over.pop("job_id", "j1"), "company_id": "c1", "company_name": over.pop("company_name", "Acme AI"), "title": title,
         "locations": list(locations), "exp_min": None, "exp_max": None, "exp_text": None, "state": "New", "availability": "in_latest_check",
         "source_ids": ["feashliaa"], "source_types": ["ats_feed"], "dated": [], "scan_partial": False, "source_level": None, "recruiter_posted": None,
         "verdict": None, "hints": [], "company_text": "", "company_evidence": {}, "also_company_candidate": False, "description": ""}
    j.update(over)
    return j


def company(**over):
    ev = {"text": "Acme builds an AI-native platform with large language model agents for enterprises. B2B SaaS.", "industries": ["AI"], "locations": ["Bengaluru, KA, India"],
          "yc": True, "yc_batch": "Winter 2023", "yc_stage": None, "yc_team_size": 12, "yc_status": "Active", "investor_boards": ["accel"], "headcount": [],
          "india_relevance": "india_based", "open_jobs_reported": []}
    ev.update(over.pop("evidence", {}))
    c = {"company_id": over.pop("company_id", "c1"), "name": over.pop("name", "Acme AI"), "evidence": ev, "sources": {"yc_oss", "inv_accel"}, "n_jobs": 0,
         "verdict": None, "hints": []}
    c.update(over)
    return c


def test_a_clear_match_is_qualified_and_the_explanation_cites_recorded_facts(profile, ranking):
    r = sc.score_job(job(), profile, ranking, AS_OF)
    assert r["classification"] == "confirmed_eligible" and r["family"] == "associate_product_ops" and r["score"] == sum(r["components"].values())
    e = r["explanation"]
    assert "associate product manager" in e and "Bengaluru" in e and "not employer-verified" in e and "early-career title term: associate" in e
    assert "currently open" not in e and "confirmed open" not in e
    assert {"ROLE_STRONG", "LOC_BENGALURU", "AVAIL_IN_LATEST_CHECK", "TITLE_JUNIOR"} <= set(r["reason_codes"])


def test_foreign_only_posting_is_excluded_but_the_company_is_not(profile, ranking):
    r = sc.score_job(job(locations=("Remote, TX",)), profile, ranking, AS_OF)
    assert r["classification"] == "excluded" and r["hard_exclusion"] == "LOCATION_INCOMPATIBLE" and "kept in SQLite" in r["explanation"]
    # an India-headquartered company can advertise a US-only role: judging the posting does not touch the company
    co = sc.score_company(company(), profile, ranking)
    assert co["classification"] == "confirmed_eligible"


def test_unknown_or_unqualified_location_is_held_for_review_never_approved(profile, ranking):
    for locs in ((), ("Remote",), ("",), ("India",)):
        r = sc.score_job(job(locations=locs), profile, ranking, AS_OF)
        assert r["classification"] in ("needs_review", "low_priority") and r["classification"] != "confirmed_eligible"
        assert r["location_eligibility"] in ("unknown", "plausible")


def test_missing_experience_is_neutral_not_senior(profile, ranking):
    r = sc.score_job(job("Product Operations Analyst"), profile, ranking, AS_OF)
    assert r["experience"] == "unknown" and r["components"]["experience"] == 0 and "EXP_UNKNOWN" in r["reason_codes"]
    assert "not treated as senior" in r["explanation"] and r["classification"] == "confirmed_eligible"


def test_senior_and_executive_titles_cap_at_review(profile, ranking):
    r = sc.score_job(job("Senior Product Operations Manager"), profile, ranking, AS_OF)
    assert r["classification"] == "needs_review" and "senior" in " ".join(r["review_reasons"])
    r = sc.score_job(job("Director, Product Operations"), profile, ranking, AS_OF)
    assert r["classification"] == "needs_review" and "TITLE_EXECUTIVE" in r["reason_codes"]       # soft penalty by default
    raw = copy.deepcopy(profile.raw)
    raw["experience"]["executive_title_treatment"] = "hard_exclude"
    strict = prof.build_profile(raw)
    assert sc.score_job(job("Director, Product Operations"), strict, ranking, AS_OF)["hard_exclusion"] == "EXECUTIVE_TITLE"


def test_provider_retained_or_historical_listings_are_never_qualified(profile, ranking):
    r = sc.score_job(job(availability="historical", freshness_values=["provider_retained"]), profile, ranking, AS_OF)
    assert r["classification"] == "needs_review" and "historical" in " ".join(r["review_reasons"]) and "not confirmed open" in r["explanation"]
    assert r["components"]["availability"] < 0


def test_investor_board_observation_is_not_described_as_an_open_vacancy(profile, ranking):
    r = sc.score_job(job(source_ids=["inv_accel"], source_types=["investor_board"]), profile, ranking, AS_OF)
    assert "observed on source, not employer-verified" in r["explanation"] and "AVAIL_EMPLOYER_CONFIRMED_OPEN" not in r["reason_codes"]
    assert r["components"]["availability"] == ranking["jobs"]["availability"]["in_latest_check"] < ranking["jobs"]["availability"]["employer_confirmed_open"]


def test_old_yc_job_evidence_is_historical_and_not_current_hiring(profile, ranking):
    r = sc.score_job(job(source_ids=["yc_jobs"], source_types=["yc"], availability="observed_earlier"), profile, ranking, AS_OF)
    assert "seen earlier" in r["explanation"] and r["components"]["availability"] == 0
    assert r["classification"] == "needs_review" and "seen earlier but not in its source's latest check" in r["review_reasons"]   # never a current opening


def test_human_verdicts_exclude_boost_and_notes_are_untouched(profile, ranking):
    verdict = {"decision": "No", "notes": "private note"}
    r = sc.score_job(job(verdict=verdict), profile, ranking, AS_OF)
    assert r["classification"] == "excluded" and r["hard_exclusion"] == "HUMAN_NO" and verdict == {"decision": "No", "notes": "private note"}
    base = sc.score_job(job(), profile, ranking, AS_OF)["score"]
    yes = sc.score_job(job(verdict={"decision": "Yes", "notes": ""}), profile, ranking, AS_OF)
    assert yes["score"] == base + ranking["jobs"]["verdict"]["yes"] and "VERDICT_YES" in yes["reason_codes"]
    assert sc.score_company(company(verdict={"decision": "No", "notes": "x"}), profile, ranking)["hard_exclusion"] == "HUMAN_NO"


def test_placeholder_company_and_untitled_roles_are_excluded(profile, ranking):
    assert sc.score_job(job(company_name="DO NOT USE"), profile, ranking, AS_OF)["hard_exclusion"] == "PLACEHOLDER_COMPANY"
    assert sc.score_job(job("Truck Driver"), profile, ranking, AS_OF)["hard_exclusion"] == "NON_TARGET_ROLE"
    nr = sc.score_job(job("Zzyzx Wibble"), profile, ranking, AS_OF)          # no role evidence is NOT an exclusion
    assert nr["classification"] == "low_priority" and nr["hard_exclusion"] is None and "NO_ROLE_EVIDENCE" in nr["reason_codes"]
    assert sc.score_job(job(state="Closed"), profile, ranking, AS_OF)["hard_exclusion"] == "CLOSED_OR_NOT_SEEN"
    assert sc.score_company(company(name="Actively Hiring"), profile, ranking)["hard_exclusion"] == "PLACEHOLDER_COMPANY"


def test_weights_come_from_the_yaml_not_the_code(profile, ranking):
    r = copy.deepcopy(ranking)
    r["jobs"]["role_relevance"]["strong_alias"] += 7
    assert sc.score_job(job(), profile, r, AS_OF)["score"] == sc.score_job(job(), profile, ranking, AS_OF)["score"] + 7


def test_scoring_is_deterministic_and_order_independent(profile, ranking):
    jobs = [job(f"Associate Product Manager {i}", job_id=f"j{i}", company_name=f"Co{i % 4}") for i in range(30)]
    a = [sc.score_job(j, profile, ranking, AS_OF) for j in jobs]
    b = [sc.score_job(j, profile, ranking, AS_OF) for j in jobs]
    assert a == b
    shuffled = a[:]
    random.Random(1).shuffle(shuffled)
    s1 = sc.select_shortlist(a, 20, 45, 2)[0]
    s2 = sc.select_shortlist(shuffled, 20, 45, 2)[0]
    assert [r["id"] for r in s1] == [r["id"] for r in s2]


def test_shortlist_is_never_padded_and_applies_a_generic_employer_cap(profile, ranking):
    results = [sc.score_job(job(f"Associate Product Manager {i}", job_id=f"j{i}", company_name="OneCo" if i < 5 else f"Co{i}"), profile, ranking, AS_OF) for i in range(8)]
    results.append(sc.score_job(job("Strategy Analyst", locations=("Remote",), job_id="r1"), profile, ranking, AS_OF))        # needs review
    selected, queue, deferred = sc.select_shortlist(results, 20, 45, 2)
    assert len(selected) == 5 and sum(1 for r in selected if r["company"] == "OneCo") == 2         # 2 + the 3 other employers
    assert any("per-employer cap" in d["why"] for d in deferred)
    assert all(r["classification"] == "confirmed_eligible" for r in selected) and any(r["id"] == "r1" for r in queue)
    assert len(selected) < 20                                                                      # no filling with unsuitable records


def test_company_stream_works_without_any_jobs_and_never_infers_funding(profile, ranking):
    r = sc.score_company(company(n_jobs=0), profile, ranking)
    assert r["classification"] == "confirmed_eligible" and r["n_jobs"] == 0 and r["components"]["jobs_presence"] == 0
    assert "no job listings observed (not a penalty)" in r["explanation"] and "not a funding fact" in sc.score_company(company(n_jobs=0), profile, ranking)["explanation"]
    assert "funded" not in r["explanation"].lower() and "valuation" not in r["explanation"].lower()


def test_company_without_description_or_india_evidence_is_held_not_approved(profile, ranking):
    name_only = sc.score_company(company(evidence={"text": "", "industries": [], "yc": False, "investor_boards": [], "locations": [], "india_relevance": None}),
                                 profile, ranking)
    assert name_only["classification"] == "low_priority" and "CO_NAME_ONLY" in name_only["reason_codes"]
    no_india = sc.score_company(company(evidence={"india_relevance": "no_india_evidence_yet", "locations": ["San Francisco, CA"]}), profile, ranking)
    assert no_india["classification"] == "needs_review" and "no India connection recorded yet" in no_india["review_reasons"]


def test_scoring_code_has_no_source_specific_rules(profile):
    import inspect
    text = inspect.getsource(sc).lower() + inspect.getsource(__import__("discovery.filters", fromlist=["x"])).lower()
    for name in ("accel", "lightspeed", "techstars", "peak xv", "3one4"):
        assert name not in text


def test_aggregator_first_seen_earns_no_freshness_but_provider_creation_does(profile, ranking):
    agg = job(dated=[{"source_type": "ats_feed", "source_id": "feashliaa", "when": datetime(2026, 10, 19, tzinfo=timezone.utc)}])
    r = sc.score_job(agg, profile, ranking, AS_OF)
    assert "listing_age" not in r["components"] and "DATE_AGGREGATOR_FIRST_SEEN_NO_BONUS" in r["reason_codes"] and "POSTING_DATE_UNKNOWN" in r["missing_evidence"]
    inv = job(source_types=["investor_board"], dated=[{"source_type": "investor_board", "source_id": "inv_accel", "when": datetime(2026, 10, 15, tzinfo=timezone.utc)}])
    r2 = sc.score_job(inv, profile, ranking, AS_OF)
    assert r2["components"]["listing_age"] == ranking["jobs"]["listing_age"]["fresh_points"] and "DATE_PROVIDER_REPORTED" in r2["reason_codes"]


def test_no_role_family_goes_to_exploration_never_qualified(profile, ranking):
    r = sc.score_job(job("Strategy & Operations Analyst"), profile, ranking, AS_OF)
    assert r["classification"] in ("exploration", "needs_review", "confirmed_eligible", "low_priority")
    if r["family"] is None:
        assert r["classification"] in ("exploration", "low_priority") and r["hard_exclusion"] is None
    sel = sc.select_exploration([dict(r, classification="exploration", score=20), dict(r, id="j2", classification="exploration", score=19)], 5, 1)
    assert len(sel) == 1                                   # generic per-employer cap


def test_recruiter_and_source_senior_hold_a_job_for_review(profile, ranking):
    assert sc.score_job(job(recruiter_posted=True), profile, ranking, AS_OF)["classification"] == "needs_review"
    assert sc.score_job(job(source_level="senior"), profile, ranking, AS_OF)["classification"] == "needs_review"
    assert sc.score_job(job("Product Operations Intern"), profile, ranking, AS_OF)["classification"] != "confirmed_eligible"


def test_mixed_india_literal_and_inactive_company(profile, ranking):
    assert sc.score_job(job(locations=("Remote (United States or India)",)), profile, ranking, AS_OF)["classification"] != "excluded"
    inactive = sc.score_company(company(evidence={"yc_status": "Inactive"}), profile, ranking)
    assert inactive["classification"] == "needs_review" and "CO_STATUS_INACTIVE_REPORTED" in inactive["reason_codes"]
    generic = sc.score_company(company(evidence={"text": "A B2B SaaS tool.", "industries": []}), profile, ranking)
    assert generic["classification"] != "confirmed_eligible"


def test_confirmed_merges_are_not_reported_as_unresolved_identity(profile, ranking):
    r = sc.score_job(job(merged_records=2), profile, ranking, AS_OF)
    assert "MERGED_RECORDS" in r["reason_codes"] and "HINT_UNRESOLVED_IDENTITY" not in r["reason_codes"]
    assert "combines 2 duplicate record(s) confirmed as the same posting" in r["explanation"] and "Review hint" not in r["explanation"]
    h = sc.score_job(job(hints=["possible duplicate of another record"]), profile, ranking, AS_OF)
    assert "HINT_UNRESOLVED_IDENTITY" in h["reason_codes"] and "Review hint: possible duplicate" in h["explanation"]


def test_a_mid_level_word_is_disclosed_even_when_not_penalised(profile, ranking):
    r = sc.score_job(job("Business Operations Manager"), profile, ranking, AS_OF)
    assert "TITLE_MID_LEVEL" in r["reason_codes"] and "does not penalise" in r["explanation"].replace("do not penalise", "does not penalise")
