"""Day 15: the LIVE preflight can read and plan, and cannot write; the LIVE guard in open_backend stays closed."""
import pytest

from discovery import live_preflight as lp
from discovery import sheets_sync
from discovery.sheets_schema import TAB_COLUMNS, TAB_ORDER


class FakeWorksheet:
    def __init__(self, title, rows):
        self.title, self._rows = title, rows

    def get_all_values(self):
        return self._rows


class FakeSpreadsheet:
    id = "fake-id"
    title = "fake sheet"

    def __init__(self, tabs):
        self._tabs = tabs

    def worksheets(self):
        return [FakeWorksheet(t, r) for t, r in self._tabs.items()]


def header(tab):
    return [c.name for c in TAB_COLUMNS[tab]]


def empty_tabs():
    return {t: [[""] * 3] for t in TAB_ORDER}


def test_readonly_scope_is_the_only_scope():
    assert lp.READONLY_SCOPES == ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def test_backend_refuses_every_write():
    b = lp.ReadOnlyBackend(FakeSpreadsheet(empty_tabs()))
    with pytest.raises(lp.ReadOnlyError):
        b.write_cells("Posts", {"A1": "x"})
    with pytest.raises(lp.ReadOnlyError):
        b.append_rows("Posts", [["x"]])


def test_backend_reads_tabs():
    b = lp.ReadOnlyBackend(FakeSpreadsheet(empty_tabs()))
    assert sorted(b.tab_names()) == sorted(TAB_ORDER)
    assert b.read_tab("Posts") == [[""] * 3]


def test_inspect_blank_sheet_has_no_problems():
    b = lp.ReadOnlyBackend(FakeSpreadsheet(empty_tabs()))
    out = lp.inspect_sheet(b)
    assert out["problems"] == []
    assert all(i["header_state"] == "empty" and i["data_rows"] == 0 for i in out["tabs"].values())


def test_inspect_flags_missing_tab_and_blank_header_with_data():
    tabs = empty_tabs()
    del tabs["Runs"]
    tabs["Posts"] = [[""] * 3, ["a", "b", "c"]]
    out = lp.inspect_sheet(lp.ReadOnlyBackend(FakeSpreadsheet(tabs)))
    assert any("expected exactly the tabs" in p for p in out["problems"])
    assert any("blank header row with data" in p for p in out["problems"])


def test_inspect_counts_verdicts_and_detects_duplicates():
    h = header("Posts")
    key = h.index("record_id")
    v, n = h.index("Your Verdict"), h.index("Your Notes")
    r1, r2 = [""] * len(h), [""] * len(h)
    r1[key], r1[v], r1[n] = "id1", "Yes", "note"
    r2[key] = "id1"
    tabs = empty_tabs()
    tabs["Posts"] = [h, r1, r2]
    info = lp.inspect_sheet(lp.ReadOnlyBackend(FakeSpreadsheet(tabs)), valid_ids={"Posts": {"id1"}})["tabs"]["Posts"]
    assert info["data_rows"] == 2 and info["duplicate_ids"] == 1
    assert info["verdict_cells_filled"] == 1 and info["notes_cells_filled"] == 1 and info["unknown_ids"] == 0


def test_inspect_flags_unknown_record_ids():
    h = header("Companies")
    row = [""] * len(h)
    row[h.index("record_id")] = "ghost"
    tabs = empty_tabs()
    tabs["Companies"] = [h, row]
    out = lp.inspect_sheet(lp.ReadOnlyBackend(FakeSpreadsheet(tabs)), valid_ids={"Companies": set()})
    assert any("unknown to SQLite" in p for p in out["problems"])


def test_live_guard_in_open_backend_stays_closed(tmp_path):
    with pytest.raises(sheets_sync.SyncError):
        sheets_sync.open_backend("live", tmp_path)


def test_human_columns_are_not_machine_columns():
    human, machine = lp.human_columns(), lp.machine_columns()
    for tab, cols in human.items():
        assert not set(cols) & set(machine[tab])
