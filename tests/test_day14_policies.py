"""Day 14 operator-approved ranking refinements: investor-membership cap, the weak 'forward deployment' alias, and the experience precedence policy."""
from datetime import datetime, timezone

import pytest

from discovery import scoring as sc
from tests.test_scoring import AS_OF, job, profile, ranking  # noqa: F401  (fixtures)


def boards(*names):
    return {"investor_boards": list(names)}


# ------------------------------------------------------------------ investor membership cap

def test_investor_membership_is_capped_per_job_however_many_boards(profile, ranking):
    assert ranking["jobs"]["company"]["investor_membership_cap"] == 3
    zero = sc.score_job(job(source_ids=["feashliaa"], source_types=["ats_feed"]), profile, ranking, AS_OF)
    one = sc.score_job(job(source_ids=["feashliaa"], source_types=["ats_feed"], company_evidence=boards("accel")), profile, ranking, AS_OF)
    many = sc.score_job(job(source_ids=["feashliaa", "inv_accel", "inv_peakxv"], source_types=["ats_feed", "investor_board"],
                            company_evidence=boards("accel", "peakxv", "lightspeed")), profile, ranking, AS_OF)
    assert "investor_membership" not in zero["components"]
    assert one["components"]["investor_membership"] == 3 and many["components"]["investor_membership"] == 3
    assert "INVESTOR_MEMBERSHIP_CAPPED" in many["reason_codes"] and "INVESTOR_MEMBERSHIP_CAPPED" not in one["reason_codes"]
    assert one["score"] - zero["score"] == 3 and many["score"] - one["score"] == 0


def test_feed_only_jobs_are_not_penalised_and_membership_never_replaces_independent_evidence(profile, ranking):
    feed = sc.score_job(job(source_ids=["feashliaa"], source_types=["ats_feed"]), profile, ranking, AS_OF)
    both = sc.score_job(job(source_ids=["feashliaa", "inv_accel"], source_types=["ats_feed", "investor_board"], company_evidence=boards("accel")), profile, ranking, AS_OF)
    board_only = sc.score_job(job(source_ids=["inv_accel"], source_types=["investor_board"], company_evidence=boards("accel")), profile, ranking, AS_OF)
    assert feed["components"]["source_quality"] == both["components"]["source_quality"] == ranking["jobs"]["source_quality"]["ats_feed"]
    assert board_only["components"].get("source_quality", 0) == 0 and board_only["components"]["investor_membership"] == 3
    assert both["score"] - feed["score"] == 3 and feed["classification"] == both["classification"] == "confirmed_eligible"
    assert "confirmed open" not in board_only["explanation"] and "not employer-verified" in board_only["explanation"]


def test_provider_dates_and_availability_stay_separate_from_the_membership_cap(profile, ranking):
    dated = job(source_ids=["inv_accel"], source_types=["investor_board"], company_evidence=boards("accel"),
                dated=[{"source_type": "investor_board", "source_id": "inv_accel", "when": datetime(2026, 10, 15, tzinfo=timezone.utc)}])
    r = sc.score_job(dated, profile, ranking, AS_OF)
    assert r["components"]["listing_age"] == ranking["jobs"]["listing_age"]["fresh_points"]
    assert r["components"]["availability"] == ranking["jobs"]["availability"]["in_latest_check"] and r["components"]["investor_membership"] == 3


# ------------------------------------------------------------------------- forward deployment

def test_forward_deployment_is_only_a_weak_contextual_alias(profile, ranking):
    plain = sc.score_job(job("Forward Deployment Engineer"), profile, ranking, AS_OF)
    assert plain["family"] == "forward_deployed_solutions" and plain["role_strength"] == "weak" and plain["classification"] == "needs_review"
    assert "ROLE_WEAK_UNCORROBORATED" in plain["reason_codes"]
    ai = sc.score_job(job("AI Forward Deployment Engineer"), profile, ranking, AS_OF)
    assert ai["role_strength"] == "weak_corroborated" and ai["classification"] != "low_priority"
    strong = sc.score_job(job("Forward Deployed Engineer"), profile, ranking, AS_OF)
    assert strong["role_strength"] == "strong" and strong["classification"] == "confirmed_eligible"


@pytest.mark.parametrize("title", ["Deployment Engineer", "Release Deployment Manager", "Software Deployment Specialist", "Deployment Lead - ERP Solutions", "DevOps Deployment Engineer"])
def test_infrastructure_and_release_deployment_titles_are_not_forward_deployment(profile, ranking, title):
    assert sc.score_job(job(title), profile, ranking, AS_OF)["family"] != "forward_deployed_solutions"


def test_forward_deployment_keeps_location_and_experience_rules(profile, ranking):
    foreign = sc.score_job(job("Forward Deployment Engineer", locations=("Remote, TX",)), profile, ranking, AS_OF)
    assert foreign["classification"] == "excluded" and foreign["hard_exclusion"] == "LOCATION_INCOMPATIBLE"
    senior = sc.score_job(job("Staff Forward Deployment Engineer"), profile, ranking, AS_OF)
    assert senior["classification"] != "confirmed_eligible" and "TITLE_SENIOR" in senior["reason_codes"]


# ------------------------------------------------------------------- experience precedence

def test_employer_stated_years_outrank_a_lead_title_but_the_conflict_stays_visible(profile, ranking):
    stated = sc.score_job(job("AI Deployment Lead", exp_min=2.0, exp_reliable=True), profile, ranking, AS_OF)
    assert "TITLE_SENIOR" not in stated["reason_codes"] and "TITLE_SENIOR_OVERRIDDEN" in stated["reason_codes"] and "EXP_TITLE_CONFLICT" in stated["reason_codes"]
    assert stated["experience"] != "senior" and "takes precedence" in stated["explanation"] and "not proof the role is junior" in stated["explanation"]
    unknown = sc.score_job(job("AI Deployment Lead"), profile, ranking, AS_OF)
    assert unknown["experience"] == "senior" and unknown["classification"] == "needs_review"                 # missing evidence keeps the title warning
    user = sc.score_job(job("AI Deployment Lead", exp_min=2.0, exp_reliable=False), profile, ranking, AS_OF)
    assert "TITLE_SENIOR" in user["reason_codes"]                                                             # user-supplied or aggregator figures do not override
    high = sc.score_job(job("Lead Product Manager", exp_min=7.0, exp_reliable=True), profile, ranking, AS_OF)
    assert high["experience"] == "senior" and "EXP_TITLE_CONFLICT" not in high["reason_codes"]
    director = sc.score_job(job("Director, Business Operations", exp_min=2.0, exp_reliable=True), profile, ranking, AS_OF)
    assert "TITLE_EXECUTIVE" in director["reason_codes"]                                                      # executive titles are never overridden


def test_a_noisy_source_label_never_overrides_the_employers_own_figure(profile, ranking):
    r = sc.score_job(job("Business Operations Associate", exp_min=2.0, exp_reliable=True, source_level="senior"), profile, ranking, AS_OF)
    assert "SRC_LEVEL_SENIOR" not in r["reason_codes"] and "SRC_LEVEL_IGNORED_EMPLOYER_EXPERIENCE_STATED" in r["reason_codes"]
    assert "source_reported_level" not in r["components"] and "which takes precedence" in r["explanation"]
    unstated = sc.score_job(job("Business Operations Associate", source_level="senior"), profile, ranking, AS_OF)
    assert unstated["classification"] == "needs_review" and "SRC_LEVEL_SENIOR" in unstated["reason_codes"]


def test_only_the_employer_posting_sources_count_as_reliable_experience(tmp_path):
    from discovery import candidates, database as db
    path = tmp_path / "c.db"
    db.initialize_database(path)
    c = db.connect(path)
    for sid, st in (("yc_jobs", "yc"), ("manual", "manual"), ("feashliaa", "ats_feed")):
        db.register_source(c, sid, sid, st)
    cid = db.get_or_create_company(c, "Acme", None)[0]
    for sid, ext in (("yc_jobs", "1"), ("manual", "2"), ("feashliaa", "3")):
        db.observe_job(c, source_id=sid, source_url=f"https://x.example/{ext}", company_id=cid, title=f"Role {ext}", external_id=ext, experience_min_years=2.0, location="Bengaluru")
    got = {j["title"]: j["exp_reliable"] for j in candidates.load_candidates(c, with_hints=False)[0]}
    assert got == {"Role 1": True, "Role 2": False, "Role 3": False}
    c.close()
