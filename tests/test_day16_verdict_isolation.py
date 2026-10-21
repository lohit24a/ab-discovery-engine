"""Day 16: TEST/LIVE verdict isolation while one SQLite verdict table serves both sheets (migration-free safeguard).

Policy: LIVE is the authoritative review interface. A TEST sync never imports verdicts/notes and never writes LIVE-origin decisions into
TEST cells; a LIVE sync never copies stored decisions into LIVE cells, imports what the person types, and names any change to older
review memory. Fake sheets and disposable databases only: no real sheet is touched."""
import re
from pathlib import Path

import pytest

from discovery import database as db, sheets_sync as ss
from discovery.shortlist import Shortlist
from tests.test_day15_live_scope import live_sync, stub_shortlist
from tests.test_sheets_sync import FakeBackend, conn, world, T1, T2, T3  # noqa: F401  (fixtures and helpers)

T4 = "2026-10-22T10:00:00+00:00"
T5 = "2026-10-23T10:00:00+00:00"


def verdicts(conn):
    return [tuple(r) for r in conn.execute("SELECT job_id, company_id, decision, notes, updated_at FROM verdicts ORDER BY verdict_id")]


def snapshot(conn):
    return verdicts(conn), [tuple(r) for r in conn.execute("SELECT run_id, run_type, result FROM runs ORDER BY run_id")]


def decision(conn, job):
    row = db.get_verdict(conn, job_id=job)
    return (row["decision"], row["notes"]) if row else None


def test_policy_constant_is_fail_closed():
    assert ss.TEST_VERDICT_IMPORT_ENABLED is False


def test_the_verdict_table_has_exactly_one_writer(tmp_path):
    root = Path(__file__).resolve().parent.parent / "discovery"
    writers = [p.name for p in root.rglob("*.py") if re.search(r"\bsave_verdict\(conn", p.read_text(encoding="utf-8")) and p.name not in ("database.py",)]
    assert writers == ["sheets_sync.py"]


@pytest.mark.parametrize("value", ["Yes", "Maybe", "No"])
def test_live_verdicts_import_by_canonical_id_with_their_notes(conn, world, value):
    live = FakeBackend()
    live_sync(conn, live, stub_shortlist(world, jobs=("job1", "job2")))
    live.set_cell("Posts", world["job2"], "Your Verdict", value)
    live.set_cell("Posts", world["job2"], "Your Notes", "my own reason")
    report = live_sync(conn, live, stub_shortlist(world, jobs=("job1", "job2")), now=T3)
    assert decision(conn, world["job2"]) == (value, "my own reason") and db.get_verdict(conn, job_id=world["job1"]) is None
    assert len(report.verdicts_imported) == 1 and world["job2"][:8] in report.verdicts_imported[0]


def test_a_note_without_a_verdict_and_a_later_verdict_keep_the_note(conn, world):
    live, sl = FakeBackend(), stub_shortlist(world)
    live_sync(conn, live, sl)
    live.set_cell("Posts", world["job1"], "Your Notes", "call them first")
    live_sync(conn, live, sl, now=T3)
    assert decision(conn, world["job1"]) == (None, "call them first")
    live.set_cell("Posts", world["job1"], "Your Verdict", "Maybe")
    live_sync(conn, live, sl, now=T4)
    assert decision(conn, world["job1"]) == ("Maybe", "call them first")


def test_empty_live_cells_never_inherit_legacy_test_values_and_stay_stable(conn, world):
    db.save_verdict(conn, "Yes", "legacy TEST note", job_id=world["job1"], now=T1)
    live, sl = FakeBackend(), stub_shortlist(world)
    live_sync(conn, live, sl)
    i = live.row_of("Posts", world["job1"])
    assert live.cell("Posts", i, "Your Verdict") == "" and live.cell("Posts", i, "Your Notes") == ""
    again = live_sync(conn, live, sl, now=T3)
    assert not again.verdicts_restored and not any(again.cells_updated.values()) and decision(conn, world["job1"]) == ("Yes", "legacy TEST note")


def test_a_conflicting_live_decision_is_named_and_never_rewrites_test(conn, world):
    db.save_verdict(conn, "Yes", "legacy", job_id=world["job1"], now=T1)
    test, live, sl = FakeBackend(), FakeBackend(), stub_shortlist(world)
    ss.sync_to_sheets(conn, test, now=T2)                                      # TEST row carries the legacy Yes
    assert test.cell("Posts", test.row_of("Posts", world["job1"]), "Your Verdict") == "Yes"
    live_sync(conn, live, sl, now=T2)
    live.set_cell("Posts", world["job1"], "Your Verdict", "Maybe")
    report = live_sync(conn, live, sl, now=T3)
    assert any("replaces earlier review memory" in x for x in report.verdicts_imported)       # explicit, identifiable
    assert decision(conn, world["job1"])[0] == "Maybe"
    before_test = [list(r) for r in test.tabs["Posts"]]
    back = ss.sync_to_sheets(conn, test, now=T4)
    assert decision(conn, world["job1"])[0] == "Maybe"                                       # no flip back to TEST's Yes
    assert not back.verdicts_imported and any("ignored" in w for w in back.warnings)
    assert [list(r) for r in test.tabs["Posts"]] == before_test                              # TEST cell untouched (no cross-sheet rewrite)


def test_live_feedback_is_never_copied_into_blank_test_cells_or_readded_test_rows(conn, world):
    test, live, sl = FakeBackend(), FakeBackend(), stub_shortlist(world)
    ss.sync_to_sheets(conn, test, now=T2)
    live_sync(conn, live, sl, now=T2)
    live.set_cell("Posts", world["job1"], "Your Verdict", "Maybe")
    live.set_cell("Posts", world["job1"], "Your Notes", "LIVE only")
    live_sync(conn, live, sl, now=T3)
    report = ss.sync_to_sheets(conn, test, now=T4)
    i = test.row_of("Posts", world["job1"])
    assert test.cell("Posts", i, "Your Verdict") == "" and test.cell("Posts", i, "Your Notes") == "" and not report.verdicts_restored
    del test.tabs["Posts"][i]                                                              # TEST row deleted: it comes back WITHOUT LIVE feedback
    ss.sync_to_sheets(conn, test, now=T5)
    j = test.row_of("Posts", world["job1"])
    assert test.cell("Posts", j, "Your Verdict") == "" and test.cell("Posts", j, "Your Notes") == ""


def test_legacy_test_decisions_stay_intact_and_may_refill_their_own_test_cell(conn, world):
    db.save_verdict(conn, "No", "legacy reject", job_id=world["job2"], now=T1)
    test = FakeBackend()
    ss.sync_to_sheets(conn, test, now=T2)
    i = test.row_of("Posts", world["job2"])
    assert test.cell("Posts", i, "Your Verdict") == "No"
    test.set_cell("Posts", world["job2"], "Your Verdict", "")                                  # blank cell never erases history
    ss.sync_to_sheets(conn, test, now=T3)
    assert decision(conn, world["job2"]) == ("No", "legacy reject")
    assert test.cell("Posts", test.row_of("Posts", world["job2"]), "Your Verdict") == "No"


def test_new_test_verdicts_and_notes_are_refused_and_saved_nowhere(conn, world):
    test = FakeBackend()
    ss.sync_to_sheets(conn, test, now=T2)
    test.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    test.set_cell("Posts", world["job1"], "Your Notes", "typed in TEST")
    snap = snapshot(conn)
    report = ss.sync_to_sheets(conn, test, now=T3)
    assert db.get_verdict(conn, job_id=world["job1"]) is None and not report.verdicts_imported and report.warnings
    assert snapshot(conn)[0] == snap[0]
    assert test.cell("Posts", test.row_of("Posts", world["job1"]), "Your Verdict") == "Yes"   # their typing is left as typed, not erased


def test_a_rejected_record_stays_rejected_unless_the_live_workflow_changes_it(conn, world):
    db.save_verdict(conn, "No", "legacy", job_id=world["job1"], now=T1)
    test, live, sl = FakeBackend(), FakeBackend(), stub_shortlist(world)
    ss.sync_to_sheets(conn, test, now=T2)
    test.set_cell("Posts", world["job1"], "Your Verdict", "Yes")                              # TEST cannot un-reject
    ss.sync_to_sheets(conn, test, now=T3)
    assert decision(conn, world["job1"])[0] == "No"
    live_sync(conn, live, sl, now=T3)                                                           # a blank LIVE cell cannot un-reject either
    live_sync(conn, live, sl, now=T4)
    assert decision(conn, world["job1"])[0] == "No"
    live.set_cell("Posts", world["job1"], "Your Verdict", "Maybe")                            # the operator, in LIVE, can
    live_sync(conn, live, sl, now=T5)
    assert decision(conn, world["job1"])[0] == "Maybe"


def test_a_no_in_live_drops_the_job_without_replacement(conn, world):
    seen = {}

    def shortlist(overrides):
        seen.update(overrides)
        keep = [j for j in ("job1", "job2") if overrides.get(world[j]) != "No"]
        sl = stub_shortlist(world, jobs=tuple(keep))
        return sl

    live = FakeBackend()
    live_sync(conn, live, shortlist)
    assert len(live.data("Shortlist")) == 2
    live.set_cell("Posts", world["job2"], "Your Verdict", "No")
    live_sync(conn, live, shortlist, now=T3)
    assert seen == {world["job2"]: "No"}
    assert [r[1] for r in live.data("Shortlist") if r[1]] == [world["job1"]]                  # the No row left; nothing took its place
    assert world["job2"] in {r[0] for r in live.data("Posts")}                                # and the Posts row was not deleted


def test_dry_runs_never_modify_verdicts_or_runs(conn, world):
    live, sl = FakeBackend(), stub_shortlist(world)
    live_sync(conn, live, sl)
    live.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    before = snapshot(conn)
    plan = live_sync(conn, live, sl, now=T3, dry_run=True)
    ss.sync_to_sheets(conn, FakeBackend(), now=T3, dry_run=True)
    assert snapshot(conn) == before and len(plan.verdicts_imported) == 1


def test_repeated_live_dry_runs_after_an_import_plan_nothing(conn, world):
    live, sl = FakeBackend(), stub_shortlist(world)
    live_sync(conn, live, sl)
    live.set_cell("Posts", world["job1"], "Your Verdict", "Maybe")
    live_sync(conn, live, sl, now=T3)
    for t in (T4, T5):
        r = live_sync(conn, live, sl, now=t, dry_run=True)
        assert not any(r.appended.values()) and not any(r.cells_updated.values()) and not r.verdicts_imported and not r.conflicts


def test_sorting_live_rows_does_not_change_ownership(conn, world):
    live, sl = FakeBackend(), stub_shortlist(world, jobs=("job1", "job2"))
    live_sync(conn, live, sl)
    live.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    live.set_cell("Posts", world["job2"], "Your Verdict", "No")
    live.tabs["Posts"][1:] = list(reversed(live.tabs["Posts"][1:]))
    live_sync(conn, live, sl, now=T3)
    assert decision(conn, world["job1"])[0] == "Yes" and decision(conn, world["job2"])[0] == "No"
    assert live.cell("Posts", live.row_of("Posts", world["job1"]), "Your Verdict") == "Yes"


def test_an_interrupted_live_sync_keeps_the_import_and_heals_on_rerun(conn, world):
    live, sl = FakeBackend(), stub_shortlist(world)
    live_sync(conn, live, sl)
    live.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    live.fail_write_on_call = len(live.writes()) + 1
    with pytest.raises(ss.SyncError):
        live_sync(conn, live, stub_shortlist(world, jobs=("job1", "job2")), now=T3)
    assert decision(conn, world["job1"])[0] == "Yes"                                         # the person's decision is safe in SQLite
    assert live.cell("Posts", live.row_of("Posts", world["job1"]), "Your Verdict") == "Yes"  # and the sheet cell was never blanked
    live.fail_write_on_call = None
    live_sync(conn, live, stub_shortlist(world, jobs=("job1", "job2")), now=T4)
    ids = [r[0] for r in live.data("Posts")]
    assert sorted(ids) == sorted({world["job1"], world["job2"]}) and decision(conn, world["job1"])[0] == "Yes"


def test_live_verdict_provenance_is_recorded_in_the_sync_summary(conn, world):
    live, sl = FakeBackend(), stub_shortlist(world)
    live_sync(conn, live, sl)
    live.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    live_sync(conn, live, sl, now=T3)
    assert ss.live_verdict_ids(conn) == {world["job1"]}
