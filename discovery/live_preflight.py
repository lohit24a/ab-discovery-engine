"""Read-only inspection and dry-run planning for the LIVE spreadsheet (Day 15 preflight).

This module can READ the LIVE spreadsheet and plan what a sync would do; it cannot write to it:
* it opens the sheet with the Google `spreadsheets.readonly` scope, so the service itself would refuse a write;
* its backend raises on every write method;
* it never touches `sheets_sync.open_backend`, whose LIVE guard stays exactly as it was (LIVE writes remain unavailable in code).
Nothing here writes SQLite: the planner runs `sync_to_sheets(dry_run=True)`, which returns before any run record or write.
"""
import os
from pathlib import Path

from . import database as db
from . import sheets_sync
from .sheets_schema import HUMAN, KEY_COLUMN, MACHINE, TAB_COLUMNS, TAB_ORDER, VERDICT_COLUMN, NOTES_COLUMN

READONLY_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class ReadOnlyError(RuntimeError):
    """A write was attempted through a read-only backend."""


class ReadOnlyBackend:
    """Wraps a spreadsheet for reading. Same read interface as GspreadBackend; every write method refuses."""

    def __init__(self, spreadsheet):
        self._ss = spreadsheet
        self.spreadsheet_id = spreadsheet.id
        self.title = spreadsheet.title
        self._worksheets = {}

    def tab_names(self):
        self._worksheets = {ws.title: ws for ws in self._ss.worksheets()}
        return list(self._worksheets)

    def read_tab(self, tab):
        return self._worksheets[tab].get_all_values()

    def write_cells(self, tab, cells):
        raise ReadOnlyError("read-only LIVE backend: write refused")

    def append_rows(self, tab, rows):
        raise ReadOnlyError("read-only LIVE backend: append refused")


def open_live_readonly(root=db.PROJECT_ROOT):
    """-> ReadOnlyBackend for the LIVE spreadsheet. Raises sheets_sync.SyncError with a plain message when something is missing. Secrets are never printed."""
    return ReadOnlyBackend(_open_live_spreadsheet(root, READONLY_SCOPES))


def open_live_write(root=db.PROJECT_ROOT):
    """-> a WRITE backend for the LIVE spreadsheet. Only live_publish.apply may call this, after every gate has passed;
    sheets_sync.open_backend('live') stays closed."""
    return sheets_sync.GspreadBackend(_open_live_spreadsheet(root, sheets_sync.WRITE_SCOPES))


def _open_live_spreadsheet(root, scopes):
    from dotenv import dotenv_values

    env = {**dotenv_values(Path(root) / ".env"), **{k: v for k, v in os.environ.items() if k.startswith("GOOGLE_")}}
    live_id, test_id = (env.get("GOOGLE_SHEET_ID_LIVE") or "").strip(), (env.get("GOOGLE_SHEET_ID_TEST") or "").strip()
    cred_rel = (env.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    if not live_id:
        raise sheets_sync.SyncError("GOOGLE_SHEET_ID_LIVE is not set in .env")
    if not cred_rel:
        raise sheets_sync.SyncError("GOOGLE_SERVICE_ACCOUNT_JSON is not set in .env")
    if live_id == test_id:
        raise sheets_sync.SyncError("TEST and LIVE spreadsheet IDs are identical; refusing")
    cred_path = (Path(root) / cred_rel).resolve()
    if not cred_path.is_file():
        raise sheets_sync.SyncError("Credential file not found")
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(str(cred_path), scopes=scopes)
    return gspread.authorize(creds).open_by_key(live_id)


def inspect_sheet(backend, valid_ids=None):
    """Structure and content facts about a spreadsheet through a read interface only. `valid_ids` = {"Posts": set, "Companies": set} to detect unknown ids."""
    out = {"title": backend.title, "tabs_found": [], "tabs_expected": list(TAB_ORDER), "problems": [], "tabs": {}}
    out["tabs_found"] = backend.tab_names()
    if sorted(out["tabs_found"]) != sorted(TAB_ORDER):
        out["problems"].append(f"expected exactly the tabs {list(TAB_ORDER)}; found {out['tabs_found']}")
    for tab in TAB_ORDER:
        if tab not in out["tabs_found"]:
            continue
        rows = backend.read_tab(tab)
        want = [c.name for c in TAB_COLUMNS[tab]]
        info = {"rows_including_header": len(rows), "header_state": "empty", "extra_columns": [], "missing_columns": [], "data_rows": 0}
        if rows and any(c.strip() for c in rows[0]):
            header = [c.strip() for c in rows[0]]
            info["extra_columns"] = [c for c in header if c and c not in want]
            info["missing_columns"] = [c for c in want if c not in header]
            info["header_state"] = "ok" if not info["missing_columns"] else "missing columns"
            key = KEY_COLUMN[tab]
            if key in header:
                k = header.index(key)
                ids = [r[k].strip() for r in rows[1:] if len(r) > k and r[k].strip()]
                info["data_rows"] = len(ids)
                info["duplicate_ids"] = len(ids) - len(set(ids))
                if valid_ids is not None and tab in valid_ids:
                    info["unknown_ids"] = len([i for i in set(ids) if i not in valid_ids[tab]])
            if tab in ("Posts", "Companies") and VERDICT_COLUMN in header and NOTES_COLUMN in header:
                v, n = header.index(VERDICT_COLUMN), header.index(NOTES_COLUMN)
                info["verdict_cells_filled"] = sum(1 for r in rows[1:] if len(r) > v and r[v].strip())
                info["notes_cells_filled"] = sum(1 for r in rows[1:] if len(r) > n and r[n].strip())
        elif len(rows) > 1:
            info["header_state"] = "blank header but data present"
            out["problems"].append(f"{tab}: blank header row with data below it")
        if info["header_state"] == "missing columns":
            out["problems"].append(f"{tab}: missing columns {info['missing_columns']}")
        if info.get("duplicate_ids"):
            out["problems"].append(f"{tab}: {info['duplicate_ids']} duplicate record id(s)")
        if info.get("unknown_ids"):
            out["problems"].append(f"{tab}: {info['unknown_ids']} record id(s) unknown to SQLite")
        out["tabs"][tab] = info
    return out


def plan_sync(conn, backend, shortlist=None):
    """The exact LIVE-scope sync plan against `backend` (read-only). Never writes the sheet and never records a run."""
    return sheets_sync.sync_to_sheets(conn, backend, dry_run=True, shortlist=shortlist, scope=sheets_sync.LIVE_SCOPE)


def human_columns():
    return {tab: [c.name for c in TAB_COLUMNS[tab] if c.owner == HUMAN] for tab in ("Posts", "Companies")}


def machine_columns():
    return {tab: [c.name for c in TAB_COLUMNS[tab] if c.owner == MACHINE] for tab in TAB_ORDER}
