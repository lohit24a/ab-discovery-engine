"""Post-implementation audit: regression tests for the defects it found (each test failed before its fix).

F1  LIVE-verdict provenance was lost when a LIVE sync failed or was interrupted after importing, so the next TEST sync wrote LIVE
    feedback into TEST's human cells.
F2  live-publish re-planned at write time and wrote that plan without checking it against the approved plan or the limits.
F3  The next review session could only be started by a TEST write, which advanced the shared session before LIVE capacity was
    checked; live-publish now plans and applies a new session itself, and live-limits states the smallest sufficient limits.
F5  A stored verdict's points were invisible in LIVE (explanations are cut at 600 characters and Maybe had no text).
F6  A warning blocked a LIVE plan with only "1 warning(s)", without saying what to fix.
F7  ranking-preview called the operator-approved configuration "PROPOSALS awaiting operator review".
F8  If the last write of a LIVE publication (its Runs row) failed or its response was lost, the retry had nothing else to write and
    never recorded the publication in the LIVE Runs tab.
F9  A job whose source's newest attempt FAILED was still described as "listed in the latest <source> check" with no caveat, and a
    stale but complete source appeared in the attention line as "(complete)".
F10 After the operator marked a LIVE job No, every later live-publish was refused: the Shortlist view shrinks (no replacement) and
    the gate counted its cleared cells as deletions.
Fake sheets and disposable databases only."""
import json
from datetime import datetime, timezone

import pytest

from discovery import cli, database as db, live_publish as lpub, sheets_sync as ss
from discovery import profile as prof, ranking_preview, scoring as sc
from tests.test_day15_live_scope import live_sync, stub_shortlist
from tests.test_sheets_sync import FakeBackend, conn, world, T1, T2, T3  # noqa: F401  (fixtures and helpers)
from tests.test_scoring import AS_OF, job, company

T4 = "2026-10-22T10:00:00+00:00"


# ------------------------------------------------------------------------------------------------ F1

def test_f1_a_failed_live_sync_keeps_provenance_and_test_never_receives_the_feedback(conn, world):
    test, live, sl = FakeBackend(), FakeBackend(), stub_shortlist(world)
    ss.sync_to_sheets(conn, test, now=T2)
    live_sync(conn, live, sl, now=T2)
    live.set_cell("Posts", world["job1"], "Your Verdict", "Maybe")
    live.set_cell("Posts", world["job1"], "Your Notes", "LIVE only")
    live.fail_write_on_call = len(live.writes()) + 1                     # the import commits, then the sheet write fails
    with pytest.raises(ss.SyncError):
        live_sync(conn, live, stub_shortlist(world, jobs=("job1", "job2")), now=T3)
    assert db.get_verdict(conn, job_id=world["job1"])["decision"] == "Maybe"
    assert ss.live_verdict_ids(conn) == {world["job1"]}
    report = ss.sync_to_sheets(conn, test, now=T4)
    i = test.row_of("Posts", world["job1"])
    assert test.cell("Posts", i, "Your Verdict") == "" and test.cell("Posts", i, "Your Notes") == "" and not report.verdicts_restored


def test_f1_an_interrupted_process_after_the_import_keeps_provenance(conn, world):
    class Killed(BaseException):
        """Stands in for a process being stopped: not an Exception, so no failure handler runs."""

    live, sl = FakeBackend(), stub_shortlist(world)
    live_sync(conn, live, sl, now=T2)
    live.set_cell("Posts", world["job1"], "Your Verdict", "Yes")

    def die(*a, **k):
        raise Killed()
    live.write_cells = die
    live.append_rows = die
    with pytest.raises(Killed):
        live_sync(conn, live, stub_shortlist(world, jobs=("job1", "job2")), now=T3)
    left = conn.execute("SELECT result FROM runs WHERE run_type = 'sync' ORDER BY started_at DESC LIMIT 1").fetchone()[0]
    assert left == "running" and ss.live_verdict_ids(conn) == {world["job1"]}


def test_f1_test_syncs_record_no_live_provenance(conn, world):
    test = FakeBackend()
    ss.sync_to_sheets(conn, test, now=T2)
    test.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    ss.sync_to_sheets(conn, test, now=T3)
    assert ss.live_verdict_ids(conn) == set() and db.get_verdict(conn, job_id=world["job1"]) is None


# ------------------------------------------------------------------------------------------------ F2 / F3

def _saved_plan(conn, world, tmp_path, jobs=("job1",), mode="continue"):
    sl = stub_shortlist(world, jobs=jobs)
    report = live_sync(conn, FakeBackend(), sl, dry_run=True)
    inspection = {"tabs": {t: {"data_rows": 0} for t in ("Shortlist", "Posts", "Companies", "Runs")}, "problems": []}
    plan = json.loads(json.dumps({"spreadsheet": "A+B Discovery - LIVE", "db_sha256": "x", "session": sl.session_id, "mode": mode,
                                  "inspection": inspection, "planned": report.planned, "counts": {"appended": report.appended, "cells_updated": report.cells_updated},
                                  "headers_to_write": report.headers_written, "shortlist_rows": sl.rows, "violations": []}, default=str))
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return plan, path


@pytest.fixture
def gated(monkeypatch, tmp_path):
    """apply() with its network pieces replaced: the fresh plan equals the saved one, the backup is accepted, LIVE is a fake."""
    state = {"backend": FakeBackend(), "calls": []}

    def install(saved, write_jobs, world):
        monkeypatch.setattr(lpub, "db_fingerprint", lambda p: "x")
        monkeypatch.setattr(lpub, "_verify_backup", lambda p, d: "b.db")

        def fake_make_plan(root=None, db_path=None, new_session=False):
            state["calls"].append(("make_plan", new_session))
            return saved
        monkeypatch.setattr(lpub, "make_plan", fake_make_plan)

        def fake_callable(conn, new_session=False):
            state["calls"].append(("shortlist", new_session))
            return (lambda overrides: stub_shortlist(world, jobs=write_jobs)), {}
        monkeypatch.setattr(lpub, "_shortlist_callable", fake_callable)
        monkeypatch.setattr(lpub.lp, "open_live_write", lambda root=None: state["backend"])
    state["install"] = install
    return state


def test_f2_the_written_plan_must_equal_the_approved_plan(conn, world, tmp_path, gated):
    saved, path = _saved_plan(conn, world, tmp_path, jobs=("job1",))
    gated["install"](saved, ("job1", "job2"), world)                      # the data changed between approval and write
    runs_before = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    with pytest.raises(ss.SyncError, match="differs from the approved plan"):
        lpub.apply(path, "b.db", authorized=True, db_path=tmp_path / "sync.db", out=lambda *_: None)
    assert gated["backend"].writes() == [] and conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == runs_before


def test_f2_an_unchanged_plan_is_written(conn, world, tmp_path, gated):
    saved, path = _saved_plan(conn, world, tmp_path, jobs=("job1",))
    gated["install"](saved, ("job1",), world)
    report = lpub.apply(path, "b.db", authorized=True, db_path=tmp_path / "sync.db", out=lambda *_: None)
    assert report.appended["Posts"] == 1 and {r[0] for r in gated["backend"].data("Posts")} == {world["job1"]}


def test_f3_a_new_session_plan_is_planned_and_applied_as_a_new_session(conn, world, tmp_path, gated):
    saved, path = _saved_plan(conn, world, tmp_path, jobs=("job1",), mode="new")
    gated["install"](saved, ("job1",), world)
    lpub.apply(path, "b.db", authorized=True, db_path=tmp_path / "sync.db", out=lambda *_: None)
    assert ("make_plan", True) in gated["calls"] and ("shortlist", True) in gated["calls"]


def test_f3_a_continue_plan_never_starts_a_new_session(conn, world, tmp_path, gated):
    saved, path = _saved_plan(conn, world, tmp_path, jobs=("job1",), mode="continue")
    gated["install"](saved, ("job1",), world)
    lpub.apply(path, "b.db", authorized=True, db_path=tmp_path / "sync.db", out=lambda *_: None)
    assert all(flag is False for _, flag in gated["calls"])


def test_f3_live_publish_accepts_new_session_only_as_a_planning_flag():
    p = cli.build_parser()
    args = p.parse_args(["live-publish", "--plan-output", "exports/x.json", "--new-session"])
    assert args.new_session is True and args.apply is False


def test_f3_live_limits_states_the_smallest_sufficient_limits(monkeypatch, capsys):
    info = {"limits": {"Shortlist": 25, "Posts": 30, "Companies": 30, "Runs": 2}, "live_rows_now": {"Shortlist": 24, "Posts": 19, "Companies": 23, "Runs": 1},
            "new_rows": {"Shortlist": 25, "Posts": 16, "Companies": 13, "Runs": 1}, "rows_after_next_publication": {"Shortlist": 25, "Posts": 35, "Companies": 36, "Runs": 2},
            "session": "", "mode": "new", "fits": {"Shortlist": True, "Posts": False, "Companies": False, "Runs": True},
            "smallest_sufficient": {"Shortlist": 25, "Posts": 35, "Companies": 36, "Runs": 2}, "other_violations": []}
    monkeypatch.setattr(lpub, "limits_preview", lambda new_session=False: info)
    ns = type("A", (), {"next_session": True, "set": None, "operator_approved": False, "note": ""})()
    assert cli.cmd_live_limits(ns) == 0
    out = capsys.readouterr().out
    assert "Posts=35, Companies=36" in out and "live-limits --set Posts=35 Companies=36 --operator-approved" in out
    assert "deletes nothing" in out and "NEW review session" in out


def test_f3_limits_preview_computes_new_rows_and_smallest_limits(tmp_path, monkeypatch):
    from tests.test_day16_backups import _fake_plan, _limits_root
    root = _limits_root(tmp_path)
    monkeypatch.setattr(lpub, "make_plan", lambda *a, **k: _fake_plan())
    info = lpub.limits_preview(root, new_session=True)
    assert info["new_rows"] == {"Shortlist": 25, "Posts": 16, "Companies": 13, "Runs": 1}
    assert info["smallest_sufficient"] == {"Shortlist": 25, "Posts": 35, "Companies": 36, "Runs": 2}


# ------------------------------------------------------------------------------------------------ F5 / F6 / F7

@pytest.fixture(scope="module")
def profile():
    return prof.load_profile()


@pytest.fixture(scope="module")
def ranking():
    return prof.load_ranking()


@pytest.mark.parametrize("decision", ["Yes", "Maybe"])
def test_f5_a_stored_verdict_is_named_first_and_survives_truncation(profile, ranking, decision):
    r = sc.score_job(job(verdict={"decision": decision, "notes": ""}), profile, ranking, AS_OF)
    points = ranking["jobs"]["verdict"][decision.lower()]
    head = r["explanation"][:120]
    assert f"your stored verdict is {decision} (+{points} points)" in head
    c = sc.score_company(company(verdict={"decision": decision, "notes": ""}), profile, ranking)
    assert f"your stored verdict is {decision}" in c["explanation"][:120]


def test_f5_no_verdict_text_without_a_verdict(profile, ranking):
    assert "stored verdict" not in sc.score_job(job(), profile, ranking, AS_OF)["explanation"]


def test_f6_a_warning_names_what_to_fix(conn, world):
    report = live_sync(conn, FakeBackend(), stub_shortlist(world), dry_run=True)
    report.warnings.append("Job X [abc]: 'Yess' is not Yes/Maybe/No; ignored, left as is")
    inspection = {"tabs": {t: {"data_rows": 0} for t in ("Shortlist", "Posts", "Companies", "Runs")}, "problems": []}
    bad = lpub.check_limits(report, inspection, 1)
    assert any("'Yess' is not Yes/Maybe/No" in v for v in bad)


def test_f7_preview_notes_state_the_real_approval_status():
    approved = ranking_preview._notes("approved_by_operator_2026-10-21", "approved_by_operator_2026-10-21")
    assert "PROPOSALS" not in approved[0] and "operator-approved" in approved[0]
    assert "PROPOSALS" in ranking_preview._notes("proposed", "approved_by_operator_2026-10-21")[0]


# ------------------------------------------------------------------------------------------------ F8

def test_f8_a_publication_whose_runs_row_was_lost_is_recorded_by_the_retry(conn, world):
    from tests.test_audit_sync_resilience import LostResponseBackend
    b, sl = LostResponseBackend(None), stub_shortlist(world)
    original = b.append_rows

    def fail_runs(tab, rows):
        if tab == "Runs":
            raise TimeoutError("simulated: the Runs row never arrived")
        return original(tab, rows)
    b.append_rows = fail_runs
    with pytest.raises(ss.SyncError):
        live_sync(conn, b, sl, now=T2)
    assert b.data("Runs") == [] and len(b.data("Posts")) == 1                # the publication landed, its record did not
    b.append_rows = original
    plan = live_sync(conn, b, sl, now=T3, dry_run=True)
    assert plan.appended["Runs"] == 1 and plan.appended["Posts"] == 0        # the plan shows exactly the missing record
    live_sync(conn, b, sl, now=T3)
    assert len(b.data("Runs")) == 1 and "Completes the publication interrupted" in b.cell("Runs", 1, "Errors")
    again = live_sync(conn, b, sl, now=T4, dry_run=True)
    assert not any(again.appended.values()) and not any(again.cells_updated.values())


# ------------------------------------------------------------------------------------------------ F9

def test_f9_a_failed_newest_attempt_is_stated_in_the_explanation(profile, ranking):
    plain = sc.score_job(job(), profile, ranking, AS_OF)
    flagged = sc.score_job(job(newest_attempt_failed="2026-10-21T09:00:00+00:00"), profile, ranking, AS_OF)
    assert "newest attempt (2026-10-21) failed" in flagged["explanation"] and "SOURCE_NEWEST_ATTEMPT_FAILED" in flagged["reason_codes"]
    assert flagged["score"] == plain["score"] and "newest attempt" not in plain["explanation"]           # wording only, no score change


def test_f9_candidates_detect_a_failed_attempt_after_the_successful_check(conn, world):
    from discovery import candidates
    run = db.start_run(conn, "discovery", now=T2)
    db.record_source_check(conn, run, "ats", "success", scope="jobs:none", complete=True, record_count=2, started_at=T1, finished_at=T1)
    db.finish_run(conn, run, "success", now=T2)
    jobs, _, _ = candidates.load_candidates(conn, with_hints=False)
    assert all(j["newest_attempt_failed"] is None for j in jobs)
    run2 = db.start_run(conn, "discovery", now=T3)
    db.record_source_check(conn, run2, "ats", "failed", scope="jobs:none", started_at=T3, finished_at=T3, error_info="HTTP 403")
    db.finish_run(conn, run2, "failed", now=T3)
    jobs, _, _ = candidates.load_candidates(conn, with_hints=False)
    assert {j["newest_attempt_failed"] for j in jobs if j["availability"] == "in_latest_check"} == {T3}


def test_f9_a_stale_source_is_labelled_stale_in_the_attention_line():
    from discovery import source_health
    rows = [{"source_id": "yc", "state": "complete", "completeness": "low", "age_hours": 400, "stale": True, "records_in_latest_run": 3,
             "notes": [], "errors": []}]
    assert "yc (complete, stale)" in source_health.render(rows)[-1]


# ------------------------------------------------------------------------------------------------ F10

def test_f10_a_live_no_does_not_block_the_next_publication(conn, world):
    live = FakeBackend()
    live_sync(conn, live, stub_shortlist(world, jobs=("job1", "job2"), companies=("beta",)))
    live.set_cell("Posts", world["job1"], "Your Verdict", "No")

    def shrinking(overrides):                                                # the real session drops a No without replacement
        return stub_shortlist(world, jobs=tuple(j for j in ("job1", "job2") if overrides.get(world[j]) != "No"), companies=("beta",))
    report = live_sync(conn, live, shrinking, now=T3, dry_run=True)
    inspection = {"tabs": {t: {"data_rows": len(live.data(t))} for t in ("Shortlist", "Posts", "Companies", "Runs")}, "problems": []}
    assert any(u["new"] == "" and u["old"] for u in report.planned["Shortlist"]["updates"])   # the view really shrinks
    assert lpub.check_limits(report, inspection, 2) == []


def test_f10_clearing_a_records_tab_is_still_refused(conn, world):
    report = live_sync(conn, FakeBackend(), stub_shortlist(world), dry_run=True)
    report.planned["Posts"]["updates"].append({"cell": "C2", "record_id": "x", "column": "Job Title", "old": "APM", "new": ""})
    inspection = {"tabs": {t: {"data_rows": 0} for t in ("Shortlist", "Posts", "Companies", "Runs")}, "problems": []}
    assert any("would be cleared" in v for v in lpub.check_limits(report, inspection, 1))
