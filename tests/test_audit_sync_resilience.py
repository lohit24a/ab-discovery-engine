"""Post-implementation audit: synchronization cases not covered elsewhere (fake sheets, disposable databases).

Already covered in tests/test_sheets_sync.py: identical re-runs, duplicate ids, missing rows, changed/reordered headers, sorting,
formula neutralisation, failed writes. Added here: a write that reached Google but whose response was lost (timeout after success),
for TEST and LIVE, a LIVE Runs row lost in flight, and stale saved plans (session change, sheet change)."""
import json

import pytest

from discovery import database as db, live_publish as lpub, sheets_sync as ss
from tests.test_audit_fixes import _saved_plan, gated  # noqa: F401  (fixture)
from tests.test_day15_live_scope import live_sync, stub_shortlist
from tests.test_sheets_sync import FakeBackend, conn, world, T1, T2, T3  # noqa: F401

T4 = "2026-10-22T10:00:00+00:00"


class LostResponseBackend(FakeBackend):
    """Applies the Nth write to the sheet, then raises as if the response timed out."""

    def __init__(self, lose_on):
        super().__init__()
        self.lose_on = lose_on

    def _after(self):
        n = sum(1 for c in self.calls if c[0] in ("write", "append"))
        if self.lose_on is not None and n == self.lose_on:
            self.lose_on = None
            raise TimeoutError("simulated: the request reached the sheet but the response was lost")

    def write_cells(self, tab, cells):
        super().write_cells(tab, cells)
        self._after()

    def append_rows(self, tab, rows):
        super().append_rows(tab, rows)
        self._after()


def ids(b, tab):
    return [r[0] for r in b.data(tab)]


@pytest.mark.parametrize("lose_on", [1, 2, 3, 4, 5, 6])
def test_test_sync_lost_response_then_retry_creates_no_duplicates(conn, world, lose_on):
    db.save_verdict(conn, "Yes", "kept", job_id=world["job1"], now=T1)
    b = LostResponseBackend(lose_on)
    with pytest.raises(ss.SyncError):
        ss.sync_to_sheets(conn, b, now=T2)
    ss.sync_to_sheets(conn, b, now=T3)
    for tab in ("Posts", "Companies", "Runs"):
        assert len(ids(b, tab)) == len(set(ids(b, tab))), tab
    assert set(ids(b, "Posts")) == {world["job1"], world["job2"]}
    assert db.get_verdict(conn, job_id=world["job1"])["decision"] == "Yes"
    again = ss.sync_to_sheets(conn, b, now=T4, dry_run=True)
    assert not any(again.appended.values()) and not any(again.cells_updated.values())


@pytest.mark.parametrize("lose_on", [1, 2, 3, 4, 5, 6, 7, 8])
def test_live_sync_lost_response_then_retry_creates_no_duplicates(conn, world, lose_on):
    b, sl = LostResponseBackend(lose_on), stub_shortlist(world, jobs=("job1", "job2"))
    with pytest.raises(ss.SyncError):
        live_sync(conn, b, sl, now=T2)
    live_sync(conn, b, sl, now=T3)
    for tab in ("Posts", "Companies", "Runs"):
        assert len(ids(b, tab)) == len(set(ids(b, tab))), tab
    assert set(ids(b, "Posts")) == {world["job1"], world["job2"]}
    assert len(ids(b, "Runs")) == 1                                       # one publication row, never a second one for the retry
    again = live_sync(conn, b, sl, now=T4, dry_run=True)
    assert not any(again.appended.values()) and not any(again.cells_updated.values())


def test_a_saved_plan_from_before_a_session_change_is_refused(conn, world, tmp_path, gated):
    saved, path = _saved_plan(conn, world, tmp_path)
    stale = json.loads(json.dumps(saved))
    stale["session"] = "older-session"
    path.write_text(json.dumps(stale), encoding="utf-8")
    gated["install"](saved, ("job1",), world)                             # the fresh plan (current state) has the new session
    with pytest.raises(lpub.PublishRefused, match="session"):
        lpub.apply(path, "b.db", authorized=True, db_path=tmp_path / "sync.db", out=lambda *_: None)
    assert gated["backend"].writes() == []


def test_a_saved_plan_is_refused_when_the_live_sheet_changed(conn, world, tmp_path, gated):
    saved, path = _saved_plan(conn, world, tmp_path)
    fresh = json.loads(json.dumps(saved))
    fresh["inspection"]["tabs"]["Posts"]["data_rows"] = 1                  # someone added a row since the plan was made
    gated["install"](fresh, ("job1",), world)
    with pytest.raises(lpub.PublishRefused, match="no longer matches"):
        lpub.apply(path, "b.db", authorized=True, db_path=tmp_path / "sync.db", out=lambda *_: None)


def test_a_saved_plan_with_violations_or_without_authorisation_is_refused(conn, world, tmp_path, gated):
    saved, path = _saved_plan(conn, world, tmp_path)
    gated["install"](saved, ("job1",), world)
    with pytest.raises(lpub.PublishRefused):
        lpub.apply(path, "b.db", authorized=False, db_path=tmp_path / "sync.db")
    bad = dict(saved, violations=["Posts: 31 rows after the write exceeds the approved maximum of 30"])
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(lpub.PublishRefused, match="violations"):
        lpub.apply(path, "b.db", authorized=True, db_path=tmp_path / "sync.db")
    assert gated["backend"].writes() == []


def test_a_record_merged_away_after_publication_makes_the_sync_fail_closed(conn, world):
    """No wrong-record feedback: if an approved merge later turns a published record into an alias, the sync refuses (nothing is
    written or imported) until the operator resolves the row; the person's decision on it is never moved silently."""
    from discovery import merge
    j3 = db.observe_job(conn, source_id="ats", source_url="https://ats.example/3", company_id=world["acme"], title="Associate Product Manager",
                        external_id="3", now=T1).job_id
    live = FakeBackend()
    live_sync(conn, live, _sl_with(world, j3), now=T2)
    live.set_cell("Posts", j3, "Your Verdict", "Yes")
    merge.apply_job_merge(conn, world["job1"], j3, rule="same_posting", evidence={"test": 1}, decided_by="test")
    writes, runs = len(live.writes()), conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    with pytest.raises(ss.SyncError, match="not a job in the database"):
        live_sync(conn, live, stub_shortlist(world, jobs=("job1",)), now=T3)
    assert len(live.writes()) == writes and conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == runs
    assert db.get_verdict(conn, job_id=world["job1"]) is None and db.get_verdict(conn, job_id=j3) is None


def _sl_with(world, extra_job):
    sl = stub_shortlist(world, jobs=("job1",))
    sl.publish_job_ids.add(extra_job)
    sl.rows.append({"Type": "Job (new)", "record_id": extra_job, "Name / Title": "t", "Company": "c", "Score": "80", "Explanation": "e", "Link": ""})
    return sl
