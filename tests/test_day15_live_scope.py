"""Day 15: the shortlist-only first-LIVE publication scope, its Runs row, verdict handling and the code-enforced limits."""
import pytest

from discovery import database as db, live_preflight as lp, live_publish as lpub, sheets_sync as ss
from discovery.shortlist import Shortlist
from tests.test_sheets_sync import FakeBackend, conn, world, T2, T3  # noqa: F401  (fixtures and helpers)


def stub_shortlist(world, jobs=("job1",), companies=()):
    sl = Shortlist(mode="continue", session_id="sess-1")
    sl.publish_job_ids = {world[j] for j in jobs}
    sl.publish_company_ids = {world[c] for c in companies}
    sl.rows = [{"Type": "Job (new)", "record_id": world[j], "Name / Title": "t", "Company": "c", "Score": "90", "Explanation": "e", "Link": ""} for j in jobs]
    sl.scores = {world[j]: (90, "because") for j in jobs}
    return sl


def live_sync(conn, backend, sl, **kw):
    return ss.sync_to_sheets(conn, backend, now=kw.pop("now", T2), shortlist=sl, scope=ss.LIVE_SCOPE, **kw)


def test_live_scope_needs_a_shortlist(conn):
    with pytest.raises(ss.SyncError):
        ss.sync_to_sheets(conn, FakeBackend(), scope=ss.LIVE_SCOPE)


def test_only_the_shortlisted_job_and_its_company_are_published(conn, world):
    b = FakeBackend()
    report = live_sync(conn, b, stub_shortlist(world))
    assert {r[0] for r in b.data("Posts")} == {world["job1"]}
    assert {r[0] for r in b.data("Companies")} == {world["acme"]}            # the company of the listed job, not Beta Works
    assert report.planned["Posts"]["appends"][0]["admitted_by"] == "shortlist"
    assert report.planned["Companies"]["appends"][0]["admitted_by"] == "shortlist_job_company"


def test_company_only_picks_are_published_without_any_job(conn, world):
    b = FakeBackend()
    live_sync(conn, b, stub_shortlist(world, companies=("beta",)))
    assert {r[0] for r in b.data("Companies")} == {world["acme"], world["beta"]}


def test_the_runs_tab_gets_one_honest_live_row_and_no_history(conn, world):
    run = db.start_run(conn, "discovery", now=T2)                     # development history must not be backfilled
    db.finish_run(conn, run, "success", now=T2)
    b = FakeBackend()
    report = live_sync(conn, b, stub_shortlist(world))
    rows = b.data("Runs")
    assert len(rows) == 1 and rows[0][0] == report.run_id and run not in rows[0]
    assert b.cell("Runs", 1, "Type") == ss.LIVE_RUN_TYPE and "no source was contacted" in b.cell("Runs", 1, "Errors")
    assert b.cell("Runs", 1, "Status") == "success" and b.cell("Runs", 1, "Shortlisted") == "1"


def test_a_repeat_sync_changes_nothing_and_adds_no_second_run_row(conn, world):
    b, sl = FakeBackend(), stub_shortlist(world)
    live_sync(conn, b, sl)
    before = [list(map(list, t)) for t in b.tabs.values()]
    n_writes = len(b.writes())
    again = live_sync(conn, b, sl, now=T3)
    assert not any(again.appended.values()) and not any(again.cells_updated.values())
    assert [list(map(list, t)) for t in b.tabs.values()] == before and len(b.writes()) == n_writes
    assert len(b.data("Runs")) == 1


def test_stored_verdicts_are_not_copied_into_live_human_cells(conn, world):
    db.save_verdict(conn, "Yes", "from the TEST sheet", job_id=world["job1"], now=T2)
    b = FakeBackend()
    report = live_sync(conn, b, stub_shortlist(world))
    i = b.row_of("Posts", world["job1"])
    assert b.cell("Posts", i, "Your Verdict") == "" and b.cell("Posts", i, "Your Notes") == "" and report.verdicts_restored == []
    again = live_sync(conn, b, stub_shortlist(world), now=T3)                  # and never later either
    assert again.cells_updated["Posts"] == 0


def test_what_the_person_types_in_live_is_still_imported_and_kept(conn, world):
    b, sl = FakeBackend(), stub_shortlist(world)
    live_sync(conn, b, sl)
    b.set_cell("Posts", world["job1"], "Your Verdict", "Maybe")
    report = live_sync(conn, b, sl, now=T3)
    assert len(report.verdicts_imported) == 1
    assert db.get_verdict(conn, job_id=world["job1"])["decision"] == "Maybe"
    assert b.cell("Posts", b.row_of("Posts", world["job1"]), "Your Verdict") == "Maybe"


def test_the_older_broad_gate_is_not_used_in_the_live_scope(conn, world):
    b = FakeBackend()
    report = live_sync(conn, b, stub_shortlist(world))
    assert all(a["admitted_by"] != "publication_gate" for t in ("Posts", "Companies") for a in report.planned[t]["appends"])


def _inspection(posts=0, companies=0, runs=0):
    tabs = {t: {"data_rows": n} for t, n in (("Shortlist", 0), ("Posts", posts), ("Companies", companies), ("Runs", runs))}
    return {"tabs": tabs, "problems": []}


def test_the_limits_flag_oversized_or_unsafe_plans(conn, world):
    b = FakeBackend()
    report = live_sync(conn, b, stub_shortlist(world), dry_run=True)
    assert lpub.check_limits(report, _inspection(), 1) == []
    assert any("exceeds" in v for v in lpub.check_limits(report, _inspection(posts=30), 1))
    assert any("exceeds" in v for v in lpub.check_limits(report, _inspection(), 26))
    report.planned["Posts"]["appends"][0]["admitted_by"] = "publication_gate"
    assert any("does not allow" in v for v in lpub.check_limits(report, _inspection(), 1))
    report.planned["Posts"]["updates"].append({"cell": "N2", "record_id": "x", "column": "Your Verdict", "old": "", "new": "Yes"})
    assert any("human-owned" in v for v in lpub.check_limits(report, _inspection(), 1))
    report.verdicts_restored.append("x")
    assert any("human cells" in v for v in lpub.check_limits(report, _inspection(), 1))


def test_apply_refuses_without_the_authorization_flag(tmp_path):
    with pytest.raises(lpub.PublishRefused):
        lpub.apply(tmp_path / "plan.json", tmp_path / "b.db", authorized=False)


def test_apply_refuses_a_stale_backup(tmp_path):
    old = tmp_path / "old.db"
    old.write_bytes(b"x")
    import os, time
    os.utime(old, (time.time() - 3 * 3600,) * 2)
    with pytest.raises(lpub.PublishRefused):
        lpub._verify_backup(old, tmp_path / "none.db")


def test_the_general_live_guard_is_still_closed():
    with pytest.raises(ss.SyncError):
        ss.open_backend("live")


def test_a_partial_live_write_is_healed_by_rereading_and_adds_no_duplicates(conn, world):
    b, sl = FakeBackend(), stub_shortlist(world)
    b.fail_write_on_call = 4                                  # dies part-way through the batches
    with pytest.raises(ss.SyncError):
        live_sync(conn, b, sl)
    assert len(b.data("Runs")) == 0                           # the Runs row is the last thing written
    b.fail_write_on_call = None                               # the connection is back
    live_sync(conn, b, sl, now=T3)
    ids = [r[0] for r in b.data("Posts")]
    assert ids == [world["job1"]] and len({r[0] for r in b.data("Companies")}) == len(b.data("Companies")) == 1
    assert len(b.data("Runs")) == 1
    final = live_sync(conn, b, sl, now="2026-10-22T10:00:00+00:00")
    assert not any(final.appended.values()) and not any(final.cells_updated.values())


def test_the_live_runs_row_carries_the_source_health_caveats(conn, world):
    from tests.test_day14_recovery import check
    check(conn, "half", "partial", n=3, err="count mismatch", stype="investor_board", started=T2)
    b = FakeBackend()
    live_sync(conn, b, stub_shortlist(world))
    errors = b.cell("Runs", 1, "Errors")
    assert "Sources needing attention: half (partial)" in errors and "unavailable" not in errors
