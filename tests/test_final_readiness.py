"""Final operational readiness: legacy TEST verdicts vs LIVE decisions, department-head seniority, and the display-diversity rule.

Fake sheets and disposable databases only."""
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from discovery import backups, candidates, database as db, filters, profile as prof, scoring as sc, session as ses, sheets_sync as ss
from tests.test_day15_live_scope import live_sync, stub_shortlist
from tests.test_scoring import AS_OF, company, job
from tests.test_sheets_sync import FakeBackend, conn, world, T1, T2, T3  # noqa: F401

T4, T5 = "2026-10-22T10:00:00+00:00", "2026-10-23T10:00:00+00:00"


@pytest.fixture(scope="module")
def profile():
    return prof.load_profile()


@pytest.fixture(scope="module")
def ranking():
    return prof.load_ranking()


def origin_of(conn, job_id):
    return candidates._verdicts(conn)["job"].get(job_id, {}).get("origin")


# ---------------------------------------------------------------------------------------------- legacy TEST verdicts

@pytest.mark.parametrize("decision", ["Yes", "Maybe"])
def test_a_legacy_positive_verdict_is_history_only(profile, ranking, decision):
    base = sc.score_job(job(), profile, ranking, AS_OF)
    legacy = sc.score_job(job(verdict={"decision": decision, "notes": "", "origin": "legacy"}), profile, ranking, AS_OF)
    live = sc.score_job(job(verdict={"decision": decision, "notes": "", "origin": "live"}), profile, ranking, AS_OF)
    assert legacy["score"] == base["score"] and legacy["verdict"] is None and legacy["verdict_legacy"] == decision
    assert "VERDICT_LEGACY_TEST" in legacy["reason_codes"] and f"an earlier TEST-sheet review said {decision}" in legacy["explanation"][:140]
    assert "stored verdict" not in legacy["explanation"]                       # never implies a LIVE entry
    assert live["score"] == base["score"] + ranking["jobs"]["verdict"][decision.lower()] and live["verdict"] == decision
    c = sc.score_company(company(verdict={"decision": decision, "notes": "", "origin": "legacy"}), profile, ranking)
    assert c["verdict"] is None and "an earlier TEST-sheet review" in c["explanation"]


def test_a_legacy_no_still_suppresses_and_says_where_it_came_from(profile, ranking):
    r = sc.score_job(job(verdict={"decision": "No", "notes": "", "origin": "legacy"}), profile, ranking, AS_OF)
    assert r["hard_exclusion"] == "HUMAN_NO" and r["verdict"] == "No" and "entered in the TEST sheet" in r["explanation"]
    live = sc.score_job(job(verdict={"decision": "No", "notes": "", "origin": "live"}), profile, ranking, AS_OF)
    assert live["hard_exclusion"] == "HUMAN_NO" and "TEST sheet" not in live["explanation"]


def test_stored_verdicts_are_legacy_until_a_live_sync_records_them(conn, world):
    db.save_verdict(conn, "Yes", "from TEST", job_id=world["job1"], now=T1)
    assert origin_of(conn, world["job1"]) == "legacy"
    live = FakeBackend()
    live_sync(conn, live, stub_shortlist(world))
    assert origin_of(conn, world["job1"]) == "legacy"                          # publishing alone never upgrades a legacy decision
    live.set_cell("Posts", world["job1"], "Your Verdict", "No")
    live_sync(conn, live, stub_shortlist(world), now=T3)
    assert origin_of(conn, world["job1"]) == "live" and db.get_verdict(conn, job_id=world["job1"])["decision"] == "No"


def test_confirming_the_same_value_in_live_makes_it_a_live_decision(conn, world, profile, ranking):
    db.save_verdict(conn, "Yes", "", job_id=world["job1"], now=T1)
    live = FakeBackend()
    live_sync(conn, live, stub_shortlist(world))
    live.set_cell("Posts", world["job1"], "Your Verdict", "Yes")             # equal to the stored value: nothing to import

    seen = {}

    def capture(overrides):
        seen.update(overrides)
        return stub_shortlist(world)
    report = live_sync(conn, live, capture, now=T3, dry_run=True)
    assert seen == {world["job1"]: "Yes"} and not report.verdicts_imported   # the plan already ranks it as a LIVE Yes
    assert origin_of(conn, world["job1"]) == "legacy"                          # a dry run records nothing
    live_sync(conn, live, stub_shortlist(world), now=T3)
    assert origin_of(conn, world["job1"]) == "live"


def test_a_live_note_alone_never_upgrades_a_legacy_decision(conn, world):
    db.save_verdict(conn, "Yes", "", job_id=world["job1"], now=T1)
    live = FakeBackend()
    live_sync(conn, live, stub_shortlist(world))
    live.set_cell("Posts", world["job1"], "Your Notes", "looked at it")
    live_sync(conn, live, stub_shortlist(world), now=T3)
    stored = db.get_verdict(conn, job_id=world["job1"])
    assert (stored["decision"], stored["notes"]) == ("Yes", "looked at it")
    assert origin_of(conn, world["job1"]) == "legacy" and world["job1"] in ss.live_verdict_ids(conn)   # TEST still never receives the note


def test_an_interrupted_live_decision_keeps_its_live_origin(conn, world):
    db.save_verdict(conn, "Maybe", "", job_id=world["job1"], now=T1)
    live = FakeBackend()
    live_sync(conn, live, stub_shortlist(world))
    live.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    live.fail_write_on_call = len(live.writes()) + 1
    with pytest.raises(ss.SyncError):
        live_sync(conn, live, stub_shortlist(world, jobs=("job1", "job2")), now=T3)
    assert origin_of(conn, world["job1"]) == "live" and db.get_verdict(conn, job_id=world["job1"])["decision"] == "Yes"


def test_provenance_survives_restart_and_backup_restore(conn, world, tmp_path):
    db.save_verdict(conn, "Yes", "", job_id=world["job2"], now=T1)              # stays legacy
    live = FakeBackend()
    live_sync(conn, live, stub_shortlist(world))
    live.set_cell("Posts", world["job1"], "Your Verdict", "Maybe")
    live_sync(conn, live, stub_shortlist(world), now=T3)
    made = backups.create_backup(tmp_path / "sync.db", tmp_path / "bk", "prov")
    restored = tmp_path / "restored.db"
    shutil.copy2(made, restored)
    r = db.connect(restored, readonly=True)                                   # a fresh connection: nothing carried in memory
    try:
        assert origin_of(r, world["job1"]) == "live" and origin_of(r, world["job2"]) == "legacy"
    finally:
        r.close()


def test_new_sessions_treat_legacy_positives_as_unreviewed_and_keep_legacy_no_out(ranking):
    as_of = datetime(2026, 10, 20, tzinfo=timezone.utc)

    def rec(i, verdict=None, legacy=None):
        return {"kind": "job", "id": i, "employer_key": "co" + i, "company": "Co" + i, "title": "T" + i, "locations": ["Bengaluru"],
                "availability": "in_latest_check", "classification": "confirmed_eligible", "score": 80, "verdict": verdict, "verdict_legacy": legacy,
                "hard_exclusion": None, "family": "x", "location_category": "bengaluru", "explanation": "e",
                "local_first_seen": (as_of - timedelta(days=30)).isoformat()}
    shown_legacy_yes = rec("a", verdict=None, legacy="Yes")                   # what scoring returns for a legacy Yes
    live_yes = rec("b", verdict="Yes")
    hist = {"a": ses.fingerprint(shown_legacy_yes), "b": ses.fingerprint(live_yes)}
    lanes = ses.assign_lanes([shown_legacy_yes, live_yes], hist, as_of, ranking)
    assert [r["id"] for r in lanes["previously_unreviewed"]] == ["a"]           # not reviewed in LIVE yet: offered again
    assert all(r["id"] != "b" for items in lanes.values() for r in items)       # a LIVE Yes is not re-offered


# ---------------------------------------------------------------------------------------------- department-head seniority

@pytest.mark.parametrize("title", ["Head of Business Finance", "Head - Business Finance | 19681", "Head of Product", "Head of Engineering",
                                   "Department Head - Operations", "Business Head", "Product Head", "Regional Head - Inside Sales", "Head, Group FP&A"])
def test_department_head_titles_carry_an_executive_signal(profile, title):
    assert filters.classify_experience(title, profile).category == "executive"


@pytest.mark.parametrize("title", ["Founder's Office Associate - reporting to the Head of Growth", "Chief of Staff to the Head of Sales",
                                   "Assistant to Department Head", "Associate, Office of the Head of Strategy", "Founder's Office (reports to Head of Product)",
                                   "Headquarters Operations Associate", "Head Office Accounts Associate",
                                   "Senior Subsystem Program Manager and Tools Specialist - Machine Head"])
def test_head_mentions_that_are_not_the_role_are_not_executive(profile, title):
    assert filters.classify_experience(title, profile).category != "executive"


def test_a_description_mentioning_a_head_changes_nothing(profile):
    r = filters.classify_experience("Business Operations Associate", profile, text="You will work with the Head of Finance and the department head.")
    assert r.category == "early_career"


def test_the_lead_policy_is_unchanged(profile):
    r = filters.classify_experience("Lead - Business Operations", profile, min_years=2, employer_stated=True)
    assert r.category == "early_career" and "EXP_TITLE_CONFLICT" in r.flags
    assert filters.classify_experience("Lead - Business Operations", profile).category == "senior"


def test_head_of_business_finance_is_held_for_review_not_qualified(profile, ranking):
    r = sc.score_job(job("Head - Business Finance | 19681", source_ids=["inv_peakxv"], source_types=["investor_board"]), profile, ranking, AS_OF)
    assert r["experience"] == "executive" and r["classification"] != "confirmed_eligible"


# ---------------------------------------------------------------------------------------------- display diversity

def _r(i, employer="bestdoc", title="Associate Product Manager", locs=("Bengaluru, Karnataka, India",), score=80, cls="confirmed_eligible",
       posted=None, days=30):
    as_of = datetime(2026, 10, 20, tzinfo=timezone.utc)
    return {"kind": "job", "id": i, "employer_key": employer, "company": employer, "title": title, "locations": list(locs), "availability": "in_latest_check",
            "classification": cls, "score": score, "verdict": None, "hard_exclusion": None, "family": "x", "location_category": "bengaluru",
            "explanation": "e", "posting_date": posted, "local_first_seen": (as_of - timedelta(days=days)).isoformat()}


AS = datetime(2026, 10, 20, tzinfo=timezone.utc)


def offered(lanes):
    return [r["id"] for items in lanes.values() for r in items]


def test_identical_postings_compete_for_one_slot_and_the_newer_posting_wins(ranking):
    old, new, other = _r("old", posted="2024-10-12T00:00:00+00:00"), _r("new", posted="2026-03-01T00:00:00+00:00"), _r("x", employer="niyo", score=79)
    alts = {}
    lanes = ses.assign_lanes([old, new, other], {}, AS, ranking, alternatives=alts)
    assert offered(lanes).count("new") == 1 and "old" not in offered(lanes) and "x" in offered(lanes)
    assert alts == {"new": ["old"]}


def test_the_rule_frees_the_slot_the_cap_would_have_given_the_twin(ranking):
    lanes = ses.assign_lanes([_r("a"), _r("b")], {}, AS, ranking, max_per_employer=2)
    assert offered(lanes) == ["a"]                                                # the per-employer cap alone would have listed both


@pytest.mark.parametrize("twin", [
    _r("b", locs=("Pune, Maharashtra, India",)),                                  # a different location
    _r("b", title="Associate Product Manager II"),                                # a different level
    _r("b", title="Product Analyst"),                                             # a different role
    _r("b", employer="niyo"),                                                     # a different employer
])
def test_distinct_postings_are_never_folded_together(ranking, twin):
    alts = {}
    lanes = ses.assign_lanes([_r("a"), twin], {}, AS, ranking, alternatives=alts)
    assert sorted(offered(lanes)) == ["a", "b"] and alts == {}


def test_a_previously_shown_posting_keeps_its_place(ranking):
    seen, fresh = _r("seen", score=70), _r("fresh", score=90)
    hist = {"seen": ses.fingerprint(seen)}
    alts = {}
    lanes = ses.assign_lanes([seen, fresh], hist, AS, ranking, alternatives=alts)
    assert "seen" in offered(lanes) and "fresh" not in offered(lanes) and alts == {"seen": ["fresh"]}


def test_a_qualified_posting_beats_a_held_for_review_twin(ranking):
    weak, strong = _r("weak", cls="needs_review", score=95), _r("strong", score=60)
    alts = {}
    lanes = ses.assign_lanes([weak, strong], {}, AS, ranking, alternatives=alts)
    assert offered(lanes) == ["strong"] and alts == {"strong": ["weak"]}


def test_a_twin_with_a_live_decision_is_never_displaced(ranking):
    decided = dict(_r("decided"), verdict="Yes")                                   # not offered (decided), so it cannot hide the other
    lanes = ses.assign_lanes([decided, _r("other")], {}, AS, ranking)
    assert offered(lanes) == ["other"]
