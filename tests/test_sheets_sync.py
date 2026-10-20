"""Sheets synchronization tests using an in-memory fake spreadsheet. No network, fictional data."""

import copy
import re

import pytest

from discovery import cli, database as db, sheets_sync as ss
from discovery.sheets_schema import TAB_COLUMNS, TAB_ORDER, column_names

T1, T2, T3 = "2026-10-19T10:00:00+00:00", "2026-10-20T10:00:00+00:00", "2026-10-21T10:00:00+00:00"


def a1_to_rc(a1):
    letters, row = re.match(r"([A-Z]+)(\d+)", a1).groups()
    col = 0
    for ch in letters:
        col = col * 26 + ord(ch) - 64
    return int(row) - 1, col - 1


class FakeBackend:
    title = "A+B Discovery - TEST (fake)"
    spreadsheet_id = "fake-test-id"

    def __init__(self, tabs=TAB_ORDER):
        self.tabs = {t: [] for t in tabs}
        self.calls = []
        self.fail_read = None
        self.fail_write_on_call = None
        self.before_second_read = None
        self._reads = 0

    def tab_names(self):
        return list(self.tabs)

    def read_tab(self, tab):
        self._reads += 1
        self.calls.append(("read", tab))
        if self.fail_read == tab:
            raise RuntimeError("simulated read failure")
        if self.before_second_read and self._reads == len(TAB_ORDER) + 1:
            self.before_second_read(self)
        return copy.deepcopy(self.tabs[tab])

    def _maybe_fail(self):
        n = sum(1 for c in self.calls if c[0] in ("write", "append"))
        if self.fail_write_on_call is not None and n >= self.fail_write_on_call:
            raise RuntimeError("simulated API failure")

    def write_cells(self, tab, cells):
        self._maybe_fail()
        self.calls.append(("write", tab, dict(cells)))
        grid = self.tabs[tab]
        for a1, value in cells.items():
            r, c = a1_to_rc(a1)
            while len(grid) <= r:
                grid.append([])
            grid[r] += [""] * (c + 1 - len(grid[r]))
            grid[r][c] = value

    def append_rows(self, tab, rows):
        self._maybe_fail()
        self.calls.append(("append", tab, copy.deepcopy(rows)))
        self.tabs[tab].extend(copy.deepcopy(rows))

    # helpers for tests acting as the human
    def header(self, tab):
        return self.tabs[tab][0]

    def data(self, tab):
        return self.tabs[tab][1:]

    def cell(self, tab, row_index, name):
        return self.tabs[tab][row_index][self.header(tab).index(name)]

    def row_of(self, tab, record_id):
        for i, row in enumerate(self.tabs[tab]):
            if row and row[0] == record_id:
                return i
        raise KeyError(record_id)

    def set_cell(self, tab, record_id, name, value):
        i = self.row_of(tab, record_id)
        self.tabs[tab][i][self.header(tab).index(name)] = value

    def writes(self):
        return [c for c in self.calls if c[0] in ("write", "append")]


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "sync.db"
    db.initialize_database(path)
    c = db.connect(path)
    db.register_source(c, "yc", "YC Directory", "yc")
    db.register_source(c, "ats", "Fake ATS", "ats_feed")
    yield c
    c.close()


@pytest.fixture
def world(conn):
    """Two fictional companies and two fictional jobs."""
    a, _ = db.observe_company(conn, source_id="yc", source_url="https://yc.example/a", name="Acme AI",
                              external_id="a", domain="acme.example", location="Bengaluru", now=T1)
    b, _ = db.observe_company(conn, source_id="yc", source_url="https://yc.example/b", name="Beta Works",
                              external_id="b", domain="beta.example", now=T1)
    j1 = db.observe_job(conn, source_id="ats", source_url="https://ats.example/1", company_id=a,
                        title="Associate Product Manager", external_id="1", requisition_id="APM-1",
                        location="Bengaluru", experience_min_years=0, experience_max_years=3, now=T1).job_id
    j2 = db.observe_job(conn, source_id="ats", source_url="https://ats.example/2", company_id=b,
                        title="Operations Associate", external_id="2", now=T1).job_id
    return {"acme": a, "beta": b, "job1": j1, "job2": j2}


def sync(conn, backend, **kw):
    return ss.sync_to_sheets(conn, backend, now=kw.pop("now", T2), **kw)


def snapshot(conn):
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in
            ("companies", "jobs", "sightings", "verdicts", "status_events", "runs")}


# 1
def test_creates_headers_on_empty_spreadsheet(conn):
    backend = FakeBackend()
    report = sync(conn, backend)
    for tab in TAB_ORDER:
        assert backend.header(tab) == column_names(tab)
    assert sorted(report.headers_written) == sorted(TAB_ORDER)


# 2
def test_adds_new_jobs_and_companies(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    assert {r[0] for r in backend.data("Posts")} == {world["job1"], world["job2"]}
    assert {r[0] for r in backend.data("Companies")} == {world["acme"], world["beta"]}
    i = backend.row_of("Posts", world["job1"])
    assert backend.cell("Posts", i, "Job Title") == "Associate Product Manager"
    assert backend.cell("Posts", i, "Experience") == "0-3 years"
    assert backend.cell("Posts", i, "Company") == "Acme AI"
    assert backend.cell("Posts", i, "Score") == ""  # nothing invented
    i = backend.row_of("Companies", world["acme"])
    assert backend.cell("Companies", i, "Domain") == "acme.example"
    assert backend.cell("Companies", i, "Hiring Evidence") == "1 of 1 observed job(s) currently live"


# 3
def test_updates_machine_owned_fields(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    db.observe_job(conn, source_id="ats", source_url="https://ats.example/1", company_id=world["acme"],
                   title="Associate Product Manager", external_id="1", location="Remote - India", now=T2)
    sync(conn, backend, now=T3)
    i = backend.row_of("Posts", world["job1"])
    assert backend.cell("Posts", i, "Location") == "Remote - India"
    assert backend.cell("Posts", i, "State") == "Changed"
    assert len(backend.data("Posts")) == 2


def test_hand_edits_to_machine_columns_never_reach_sqlite(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.set_cell("Posts", world["job1"], "Job Title", "VANDALIZED")
    sync(conn, backend, now=T3)
    assert db.get_job(conn, world["job1"])["title"] == "Associate Product Manager"
    assert backend.cell("Posts", backend.row_of("Posts", world["job1"]), "Job Title") == "Associate Product Manager"


# 4 + 5
@pytest.mark.parametrize("verdict", ["Yes", "Maybe", "No"])
def test_verdicts_and_notes_are_imported_and_preserved(conn, world, verdict):
    backend = FakeBackend()
    sync(conn, backend)
    backend.set_cell("Posts", world["job1"], "Your Verdict", verdict.lower())
    backend.set_cell("Posts", world["job1"], "Your Notes", "my note")
    backend.set_cell("Companies", world["acme"], "Your Verdict", verdict)
    backend.set_cell("Companies", world["acme"], "Your Notes", "company note")
    sync(conn, backend, now=T3)
    sync(conn, backend, now=T3)
    v = db.get_verdict(conn, job_id=world["job1"])
    assert (v["decision"], v["notes"]) == (verdict, "my note")
    assert db.get_verdict(conn, company_id=world["acme"])["notes"] == "company note"
    i = backend.row_of("Posts", world["job1"])
    assert backend.cell("Posts", i, "Your Notes") == "my note"
    assert db.get_verdict(conn, job_id=world["job2"]) is None


def test_blank_sheet_cell_never_erases_a_stored_verdict_or_notes(conn, world):
    db.save_verdict(conn, "No", "dismissed", job_id=world["job1"], now=T1)
    backend = FakeBackend()
    sync(conn, backend)  # new row is created WITH the stored verdict
    i = backend.row_of("Posts", world["job1"])
    assert (backend.cell("Posts", i, "Your Verdict"), backend.cell("Posts", i, "Your Notes")) == ("No", "dismissed")
    backend.set_cell("Posts", world["job1"], "Your Verdict", "")
    backend.set_cell("Posts", world["job1"], "Your Notes", "")
    report = sync(conn, backend, now=T3)
    assert backend.cell("Posts", i, "Your Verdict") == "No" and backend.cell("Posts", i, "Your Notes") == "dismissed"
    assert db.get_verdict(conn, job_id=world["job1"])["decision"] == "No"
    assert report.verdicts_restored


def test_invalid_verdict_text_is_ignored_with_warning(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.set_cell("Posts", world["job1"], "Your Verdict", "Definitely")
    report = sync(conn, backend, now=T3)
    assert db.get_verdict(conn, job_id=world["job1"]) is None
    assert backend.cell("Posts", backend.row_of("Posts", world["job1"]), "Your Verdict") == "Definitely"
    assert any("not Yes/Maybe/No" in w for w in report.warnings)


def test_verdict_edit_in_sheet_overrides_unchanged_database_value(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.set_cell("Posts", world["job1"], "Your Verdict", "Maybe")
    sync(conn, backend, now=T2)
    backend.set_cell("Posts", world["job1"], "Your Verdict", "No")
    report = sync(conn, backend, now=T3)
    assert db.get_verdict(conn, job_id=world["job1"])["decision"] == "No"
    assert not report.conflicts


def test_conflicting_decisions_are_reported_not_overwritten(conn, world):
    backend = FakeBackend()
    sync(conn, backend, now=T1)
    backend.set_cell("Posts", world["job1"], "Your Verdict", "Maybe")
    sync(conn, backend, now=T2)                                    # last successful sync = T2
    db.save_verdict(conn, "Yes", "changed in database", job_id=world["job1"], now=T3)  # after the sync
    backend.set_cell("Posts", world["job1"], "Your Verdict", "No")  # and a different edit in the sheet
    report = sync(conn, backend, now="2026-10-22T10:00:00+00:00")
    assert len(report.conflicts) == 1
    assert db.get_verdict(conn, job_id=world["job1"])["decision"] == "Yes"
    assert backend.cell("Posts", backend.row_of("Posts", world["job1"]), "Your Verdict") == "No"
    # A conflicted sync must not count as a clean sync, so the conflict is reported again.
    assert len(sync(conn, backend, now="2026-10-23T10:00:00+00:00").conflicts) == 1


# 6
def test_sorting_rows_keeps_verdicts_with_their_records(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    backend.set_cell("Posts", world["job2"], "Your Verdict", "No")
    backend.tabs["Posts"][1:] = sorted(backend.tabs["Posts"][1:], key=lambda r: r[2], reverse=True)
    backend.tabs["Posts"][1:] = list(reversed(backend.tabs["Posts"][1:]))
    sync(conn, backend, now=T3)
    assert db.get_verdict(conn, job_id=world["job1"])["decision"] == "Yes"
    assert db.get_verdict(conn, job_id=world["job2"])["decision"] == "No"
    for jid, expected in ((world["job1"], "Associate Product Manager"), (world["job2"], "Operations Associate")):
        assert backend.cell("Posts", backend.row_of("Posts", jid), "Job Title") == expected


def test_reordered_columns_are_located_by_header_name(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    grid = backend.tabs["Posts"]
    order = [0, 3, 2] + [i for i in range(len(grid[0])) if i not in (0, 2, 3)]
    backend.tabs["Posts"] = [[row[i] for i in order] for row in grid]
    backend.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    sync(conn, backend, now=T3)
    assert db.get_verdict(conn, job_id=world["job1"])["decision"] == "Yes"


# 7
def test_rerun_without_changes_makes_no_writes_and_no_duplicates(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    before_sheet, before_db = copy.deepcopy(backend.tabs), snapshot(conn)
    n_writes = len(backend.writes())
    for when in (T2, T3):
        report = sync(conn, backend, now=when)
        assert not report.conflicts
    assert backend.tabs == before_sheet
    assert len(backend.writes()) == n_writes
    assert len(backend.data("Posts")) == 2 and len(backend.data("Companies")) == 2
    after = snapshot(conn)
    assert {k: v for k, v in after.items() if k != "runs"} == {k: v for k, v in before_db.items() if k != "runs"}


# 8
def test_duplicate_record_ids_abort_before_any_write(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.tabs["Posts"].append(list(backend.tabs["Posts"][1]))
    backend.calls.clear()
    with pytest.raises(ss.SyncError, match="duplicate"):
        sync(conn, backend, now=T3)
    assert backend.writes() == []


@pytest.mark.parametrize("bad", ["not-a-uuid", "6f9619ff-8b86-d011-b42d-00c04fc964ff"])
def test_invalid_or_unknown_record_ids_abort(conn, world, bad):
    backend = FakeBackend()
    sync(conn, backend)
    backend.tabs["Posts"][1][0] = bad
    backend.calls.clear()
    with pytest.raises(ss.SyncError):
        sync(conn, backend, now=T3)
    assert backend.writes() == []


def test_company_id_in_posts_tab_is_rejected(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.tabs["Posts"][1][0] = world["acme"]
    with pytest.raises(ss.SyncError):
        sync(conn, backend, now=T3)


def test_missing_or_extra_tabs_abort(conn):
    backend = FakeBackend(tabs=("Shortlist", "Posts", "Companies"))
    with pytest.raises(ss.SyncError, match="exactly the tabs"):
        sync(conn, backend)
    assert backend.writes() == []


def test_changed_headers_abort(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.tabs["Companies"][0][1] = "Renamed"
    with pytest.raises(ss.SyncError, match="missing expected column"):
        sync(conn, backend, now=T3)


# 9
@pytest.mark.parametrize("tab", TAB_ORDER)
def test_read_failure_blocks_all_writes(conn, world, tab):
    backend = FakeBackend()
    backend.fail_read = tab
    before = snapshot(conn)
    with pytest.raises(ss.SyncError, match="no changes were made"):
        sync(conn, backend)
    assert backend.writes() == []
    assert snapshot(conn) == before


# 10
def test_missing_rows_do_not_delete_sqlite_data_and_are_restored(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.set_cell("Posts", world["job1"], "Your Verdict", "No")
    backend.set_cell("Posts", world["job1"], "Your Notes", "not for me")
    sync(conn, backend, now=T2)
    backend.tabs["Posts"] = [r for r in backend.tabs["Posts"] if r[0] != world["job1"]]  # user deletes the row
    sync(conn, backend, now=T3)
    assert db.get_job(conn, world["job1"]) is not None
    v = db.get_verdict(conn, job_id=world["job1"])
    assert (v["decision"], v["notes"]) == ("No", "not for me")
    i = backend.row_of("Posts", world["job1"])  # re-added, with the dismissal intact
    assert (backend.cell("Posts", i, "Your Verdict"), backend.cell("Posts", i, "Your Notes")) == ("No", "not for me")
    assert len(backend.data("Posts")) == 2


# 11
def test_live_target_is_refused_and_never_opened(tmp_path):
    with pytest.raises(ss.SyncError, match="LIVE"):
        ss.open_backend("live", tmp_path)
    with pytest.raises(SystemExit):  # argparse refuses --target live
        cli.main(["sync", "--target", "live"])


def test_identical_test_and_live_ids_are_refused(tmp_path):
    (tmp_path / ".env").write_text("GOOGLE_SHEET_ID_TEST=same\nGOOGLE_SHEET_ID_LIVE=same\n"
                                   "GOOGLE_SERVICE_ACCOUNT_JSON=.secrets/x.json\n")
    with pytest.raises(ss.SyncError, match="identical"):
        ss.open_backend("test", tmp_path)


# 12
def test_blank_new_values_do_not_wipe_meaningful_sheet_cells(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.set_cell("Companies", world["beta"], "Location", "Pune")  # a hand-typed value in a blank machine cell
    report = sync(conn, backend, now=T3)
    assert backend.cell("Companies", backend.row_of("Companies", world["beta"]), "Location") == "Pune"
    assert report.kept_nonblank >= 1


# 13
def test_everything_is_read_before_anything_is_written(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    backend.calls.clear()
    backend.tabs["Posts"][1][2] = "stale"
    sync(conn, backend, now=T3)
    kinds = [c[0] for c in backend.calls]
    first_write = next(i for i, k in enumerate(kinds) if k != "read")
    assert kinds[:first_write].count("read") >= len(TAB_ORDER)
    assert "read" not in kinds[first_write:]


def test_sheet_changing_between_read_and_write_aborts(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    backend.tabs["Posts"][1][2] = "stale-title"

    def user_sorts_sheet(b):  # a person re-sorts the tab right after our first read
        b.tabs["Posts"][1], b.tabs["Posts"][2] = b.tabs["Posts"][2], b.tabs["Posts"][1]

    backend.before_second_read = user_sorts_sheet
    backend._reads = 0
    backend.calls.clear()
    before = snapshot(conn)
    with pytest.raises(ss.SyncError, match="changed while"):
        sync(conn, backend, now=T3)
    assert backend.writes() == []
    assert snapshot(conn) == before


# 14
def test_api_failure_midway_is_reported_and_rerun_heals(conn, world):
    backend = FakeBackend()
    backend.fail_write_on_call = 3
    with pytest.raises(ss.SyncError, match="part-way"):
        sync(conn, backend)
    assert conn.execute("SELECT result FROM runs WHERE run_type = 'sync'").fetchone()[0] == "failed"
    backend.fail_write_on_call = None
    partial = len(backend.data("Posts"))
    sync(conn, backend, now=T3)
    assert len(backend.data("Posts")) == 2 and len(backend.data("Companies")) == 2
    assert partial <= 2
    sync(conn, backend, now="2026-10-22T10:00:00+00:00")
    assert len(backend.data("Posts")) == 2


def test_verdict_import_survives_a_failed_sheet_write(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    backend.set_cell("Posts", world["job1"], "Your Notes", "keep me")
    backend.tabs["Posts"][1][2] = "will need rewrite"
    backend.calls.clear()
    backend.fail_write_on_call = 0  # the very first write fails
    with pytest.raises(ss.SyncError):
        sync(conn, backend, now=T3)
    v = db.get_verdict(conn, job_id=world["job1"])
    assert (v["decision"], v["notes"]) == ("Yes", "keep me")


# dry run
def test_dry_run_changes_nothing_anywhere(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    backend.set_cell("Posts", world["job1"], "Your Verdict", "Yes")
    backend.tabs["Posts"][1][2] = "stale"
    before_sheet, before_db = copy.deepcopy(backend.tabs), snapshot(conn)
    backend.calls.clear()
    report = sync(conn, backend, now=T3, dry_run=True)
    assert backend.writes() == [] and backend.tabs == before_sheet and snapshot(conn) == before_db
    assert report.dry_run and report.cells_updated["Posts"] >= 1 and len(report.verdicts_imported) == 1
    assert "DRY RUN" in report.lines()[0]


def test_dry_run_on_readonly_connection_works(tmp_path, world, conn):
    backend = FakeBackend()
    ro = db.connect(conn.execute("PRAGMA database_list").fetchone()[2], readonly=True)
    try:
        report = ss.sync_to_sheets(ro, backend, dry_run=True)
        assert report.appended["Posts"] == 2 and backend.writes() == []
    finally:
        ro.close()


def test_runs_tab_lists_discovery_runs_with_check_counts_and_errors(conn, world):
    run = db.start_run(conn, "discovery", now=T1)
    db.record_source_check(conn, run, "ats", "success", scope="b1", complete=True, record_count=5)
    db.record_source_check(conn, run, "yc", "failed", scope="q1", error_info="timeout")
    db.finish_run(conn, run, "partial", now=T2)
    backend = FakeBackend()
    sync(conn, backend)
    row = backend.data("Runs")[0]
    h = backend.header("Runs")
    get = lambda n: row[h.index(n)]
    assert get("Run ID") == run and get("Status") == "partial"
    assert (get("Sources Succeeded"), get("Sources Failed/Partial/Skipped"), get("Records Processed")) == ("1", "1", "5")
    assert "timeout" in get("Errors") and get("New Records") == ""
    assert len(backend.data("Runs")) == 1  # the sync's own bookkeeping run is not listed


def test_shortlist_has_no_human_columns_and_stays_empty_without_scores(conn, world):
    backend = FakeBackend()
    sync(conn, backend)
    assert backend.header("Shortlist") == column_names("Shortlist")
    assert "Your Verdict" not in backend.header("Shortlist")
    assert backend.data("Shortlist") == []


def test_formula_like_text_is_written_as_plain_text_by_design(conn):
    c, _ = db.observe_company(conn, source_id="yc", source_url="https://yc.example/x", name="=HYPERLINK(1)",
                              external_id="x", now=T1)
    backend = FakeBackend()
    sync(conn, backend)
    assert backend.cell("Companies", backend.row_of("Companies", c), "Company") == "=HYPERLINK(1)"


def test_schema_owner_summary_is_consistent():
    for tab in ("Posts", "Companies"):
        owners = {c.name: c.owner for c in TAB_COLUMNS[tab]}
        assert owners["record_id"] == "key"
        assert owners["Your Verdict"] == owners["Your Notes"] == "human"
        assert sum(1 for o in owners.values() if o == "human") == 2


def test_sync_cli_dry_run_requires_target():
    with pytest.raises(SystemExit):
        cli.main(["sync", "--dry-run"])
