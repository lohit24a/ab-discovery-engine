"""Two-way synchronization between SQLite and the TEST Google Sheet.

Order of operations (see sync_to_sheets):
  1. Validate the four tabs, then READ every tab (nothing is written if any read fails).
  2. Validate headers and record IDs (duplicate or unknown IDs abort the sync).
  3. Plan: reconcile human verdicts/notes, compute machine-owned cell changes, find missing rows.
  4. Dry run stops here. Otherwise re-read the ID columns to confirm the sheet did not move
     under us, import human decisions into SQLite, then write to the sheet in batches.

Human decisions are only ever added to SQLite or restored into blank sheet cells; they are never
replaced by blanks or defaults. Machine columns flow one way, SQLite -> sheet.

Remaining risk: Google Sheets has no atomic compare-and-swap. A person editing or sorting the sheet
in the few milliseconds between the final ID re-check and the batch write could still race with it.
Cells are addressed by row position inside one write, so such an edit could land on the wrong row;
the re-check makes this unlikely, not impossible. Do not edit the sheet while a sync is running.

Notes without a verdict: a note typed against a row whose verdict is blank is imported (decision
stays NULL = unreviewed). Later choosing Yes/Maybe/No keeps the note.

Clearing: a blank cell never clears anything - the stored verdict/note is restored into it. To change
a verdict, type another of Yes/Maybe/No; to change a note, type new text. Deliberately clearing a
verdict or note from the sheet is NOT supported yet (deferred). From Python it is explicit:
save_verdict(conn, None, job_id=...) clears the verdict (a note must remain) and
save_verdict(conn, notes="", job_id=...) clears the note (a verdict must remain).
"""

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import database as db
from .sheets_schema import (HUMAN, KEY, KEY_COLUMN, MACHINE, NOTES_COLUMN, TAB_COLUMNS, TAB_ORDER,
                            VERDICT_COLUMN, VERDICT_VALUES, column_names)

WRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
LIVE_BLOCKED = "LIVE synchronization is not enabled; only --target test is allowed"
LIVE_STATES = ("New", "Active", "Changed", "Reopened")
BATCH_LIMIT = 500


class SyncError(Exception):
    """Synchronization stopped safely."""


@dataclass
class SyncReport:
    dry_run: bool
    spreadsheet: str = ""
    appended: dict = field(default_factory=dict)       # tab -> rows added
    cells_updated: dict = field(default_factory=dict)  # tab -> machine cells rewritten
    kept_nonblank: int = 0                              # machine cells left alone because new value was blank
    verdicts_imported: list = field(default_factory=list)
    verdicts_restored: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    headers_written: list = field(default_factory=list)
    stages_completed: list = field(default_factory=list)
    run_id: str = ""

    def lines(self):
        out = [f"{'DRY RUN - no changes were made' if self.dry_run else 'Sync complete'}"
               f" | spreadsheet: {self.spreadsheet}"]
        if self.run_id:
            out.append(f"Run ID: {self.run_id}")
        out.append(f"Headers to write: {', '.join(self.headers_written) or 'none'}")
        for tab in TAB_ORDER:
            out.append(f"{tab}: {self.appended.get(tab, 0)} row(s) to add, "
                       f"{self.cells_updated.get(tab, 0)} machine cell(s) to update")
        out.append(f"Machine cells kept because the new value was blank: {self.kept_nonblank}")
        out.append(f"Verdicts/notes imported into SQLite: {len(self.verdicts_imported)}")
        out.extend(f"  - {x}" for x in self.verdicts_imported)
        out.append(f"Verdicts/notes restored into blank sheet cells: {len(self.verdicts_restored)}")
        out.extend(f"  - {x}" for x in self.verdicts_restored)
        out.append(f"CONFLICTS needing your decision: {len(self.conflicts)}")
        out.extend(f"  ! {x}" for x in self.conflicts)
        out.append(f"Warnings: {len(self.warnings)}")
        out.extend(f"  ? {x}" for x in self.warnings)
        return out


# ------------------------------------------------------------------ backends

class GspreadBackend:
    """Thin wrapper over gspread so the sync logic can be tested with a fake."""

    RETRY_WAITS = (20, 40)  # seconds; only for HTTP 429 (per-minute quota), which Google rejects unapplied

    def __init__(self, spreadsheet):
        self._ss = spreadsheet
        self.spreadsheet_id = spreadsheet.id
        self.title = spreadsheet.title
        self._worksheets = None  # fetched once: each worksheet() lookup would cost an extra API read

    def _call(self, fn, *args, **kwargs):
        import time

        import gspread

        for wait in (*self.RETRY_WAITS, None):
            try:
                return fn(*args, **kwargs)
            except gspread.exceptions.APIError as exc:
                if wait is None or getattr(exc.response, "status_code", None) != 429:
                    raise
                time.sleep(wait)

    def _ws(self, tab):
        return self._worksheets[tab]

    def tab_names(self):
        self._worksheets = {ws.title: ws for ws in self._call(self._ss.worksheets)}
        return list(self._worksheets)

    def read_tab(self, tab):
        return self._call(self._ws(tab).get_all_values)

    def write_cells(self, tab, cells):
        items = list(cells.items())
        for i in range(0, len(items), BATCH_LIMIT):
            data = [{"range": a1, "values": [[v]]} for a1, v in items[i:i + BATCH_LIMIT]]
            self._call(self._ws(tab).batch_update, data, raw=True)

    def append_rows(self, tab, rows):
        if rows:
            self._call(self._ws(tab).append_rows, rows, value_input_option="RAW")


def open_backend(target, root=db.PROJECT_ROOT):
    """Open the TEST spreadsheet with write scope. LIVE is deliberately unavailable."""
    if target != "test":
        raise SyncError(LIVE_BLOCKED)
    from dotenv import dotenv_values

    env = {**dotenv_values(Path(root) / ".env"), **{k: v for k, v in os.environ.items() if k.startswith("GOOGLE_")}}
    test_id = (env.get("GOOGLE_SHEET_ID_TEST") or "").strip()
    live_id = (env.get("GOOGLE_SHEET_ID_LIVE") or "").strip()
    cred_rel = (env.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    if not test_id or not cred_rel:
        raise SyncError("GOOGLE_SHEET_ID_TEST and GOOGLE_SERVICE_ACCOUNT_JSON must be set in .env")
    if test_id == live_id:
        raise SyncError("TEST and LIVE spreadsheet IDs are identical; refusing to write")
    cred_path = (Path(root) / cred_rel).resolve()
    if not cred_path.is_file():
        raise SyncError(f"Credential file not found: {cred_rel}")
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(str(cred_path), scopes=WRITE_SCOPES)
    return GspreadBackend(gspread.authorize(creds).open_by_key(test_id))


# ---------------------------------------------------------------- sheet parsing

def col_letter(index):
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


@dataclass
class TabState:
    tab: str
    needs_headers: bool
    col: dict          # header name -> column index
    width: int
    rows: list         # [(sheet_row_number, cells)] for non-blank data rows


def parse_tab(tab, values):
    spec = column_names(tab)
    non_blank = [any(str(c).strip() for c in row) for row in values]
    if not any(non_blank):
        return TabState(tab, True, {n: i for i, n in enumerate(spec)}, len(spec), [])
    if not non_blank[0]:
        raise SyncError(f"{tab}: row 1 is blank but the tab contains data; expected the header row first")
    header = [str(c).strip() for c in values[0]]
    seen = [h for h in header if h]
    if len(seen) != len(set(seen)):
        raise SyncError(f"{tab}: duplicate column headers")
    col = {h: i for i, h in enumerate(header) if h}
    missing = [n for n in spec if n not in col]
    if missing:
        raise SyncError(f"{tab}: missing expected column(s): {', '.join(missing)}")
    width = max(len(header), *(len(r) for r in values))
    rows = [(i + 1, [str(c) for c in r] + [""] * (width - len(r)))
            for i, r in enumerate(values) if i > 0 and non_blank[i]]
    return TabState(tab, False, col, width, rows)


def _norm_id(value):
    value = (value or "").strip().lower()
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def index_rows(state, valid_ids, report_name):
    """Map record id -> (row_number, cells). Rejects duplicate, malformed and unknown IDs."""
    key = KEY_COLUMN[state.tab]
    problems, found = [], {}
    for row_num, cells in state.rows:
        raw = cells[state.col[key]].strip()
        if not raw:
            continue  # orphan row with no ID: left untouched, reported as a warning by caller
        rid = _norm_id(raw) if state.tab != "Runs" else raw
        if rid is None:
            problems.append(f"row {row_num}: '{raw}' is not a valid record ID")
        elif rid in found:
            problems.append(f"row {row_num}: duplicate {key} {rid} (also row {found[rid][0]})")
        elif state.tab != "Runs" and rid not in valid_ids:
            problems.append(f"row {row_num}: record ID {rid} is not a {report_name} in the database")
        else:
            found[rid] = (row_num, cells)
    if problems:
        raise SyncError(f"{state.tab}: refusing to sync. " + "; ".join(problems))
    return found


# --------------------------------------------------------- SQLite -> row values

def _s(value):
    return "" if value is None else str(value)


def _experience(row):
    lo, hi, text = row["experience_min_years"], row["experience_max_years"], row["experience_text"]
    if lo is not None and hi is not None:
        return f"{lo:g}-{hi:g} years"
    if lo is not None:
        return f"{lo:g}+ years"
    if hi is not None:
        return f"up to {hi:g} years"
    return _s(text)


def load_post_rows(conn):
    sightings = {}
    for r in conn.execute("""SELECT g.job_id, g.source_url, g.first_seen_at, s.name AS source_name
                             FROM sightings g JOIN sources s USING (source_id)
                             WHERE g.job_id IS NOT NULL ORDER BY g.first_seen_at, g.sighting_id"""):
        sightings.setdefault(r["job_id"], []).append(r)
    out = {}
    for j in conn.execute("""SELECT j.*, c.name AS company_name FROM jobs j
                             JOIN companies c USING (company_id) ORDER BY j.first_seen_at, j.job_id"""):
        seen = sightings.get(j["job_id"], [])
        out[j["job_id"]] = {
            "Company": j["company_name"], "Job Title": j["title"], "Location": _s(j["location"]),
            "Remote Eligibility": j["remote_eligibility"], "Experience": _experience(j),
            "State": j["lifecycle_state"], "First Seen": j["first_seen_at"], "Last Seen": j["last_seen_at"],
            "Job Link": _s(j["posting_url"]) or (seen[0]["source_url"] if seen else ""),
            "Sources": ", ".join(sorted({x["source_name"] for x in seen})),
            "Score": "", "Explanation": "",
        }
    return out


def load_company_rows(conn):
    sightings, jobs = {}, {}
    for r in conn.execute("""SELECT g.company_id, g.first_seen_at, g.last_seen_at, s.name AS source_name
                             FROM sightings g JOIN sources s USING (source_id)
                             WHERE g.company_id IS NOT NULL"""):
        sightings.setdefault(r["company_id"], []).append(r)
    for r in conn.execute("SELECT company_id, lifecycle_state FROM jobs"):
        jobs.setdefault(r["company_id"], []).append(r["lifecycle_state"])
    out = {}
    for c in conn.execute("SELECT * FROM companies ORDER BY created_at, company_id"):
        seen, states = sightings.get(c["company_id"], []), jobs.get(c["company_id"], [])
        aliases = json.loads(c["aliases"])
        details = f"Identity: {c['identity_status']}" + (f"; also known as: {', '.join(aliases)}" if aliases else "")
        live = sum(1 for s in states if s in LIVE_STATES)
        out[c["company_id"]] = {
            "Company": c["name"], "Domain": _s(c["domain"]), "Location": _s(c["location"]),
            "Details": details, "Sources": ", ".join(sorted({x["source_name"] for x in seen})),
            "Hiring Evidence": (f"{live} of {len(states)} observed job(s) currently live"
                                if states else "No jobs observed"),
            "First Seen": min((x["first_seen_at"] for x in seen), default=""),
            "Last Seen": max((x["last_seen_at"] for x in seen), default=""),
            "Score": "", "Explanation": "",
        }
    return out


def load_run_rows(conn):
    checks = {}
    for r in conn.execute("SELECT * FROM source_checks"):
        checks.setdefault(r["run_id"], []).append(r)
    out = {}
    # Sync runs are bookkeeping, not discovery executions, so they are not listed in the Runs tab.
    for r in conn.execute("SELECT * FROM runs WHERE run_type != 'sync' ORDER BY started_at, run_id"):
        cs = checks.get(r["run_id"], [])
        errors = [r["error_detail"]] if r["error_detail"] else []
        errors += [f"{c['scope'] or c['source_id']}: {c['error_info']}" for c in cs if c["error_info"]]
        out[r["run_id"]] = {
            "Started": r["started_at"], "Finished": _s(r["finished_at"]), "Status": r["result"],
            "Type": r["run_type"],
            "Sources Succeeded": str(sum(1 for c in cs if c["outcome"] == "success")),
            "Sources Failed/Partial/Skipped": str(sum(1 for c in cs if c["outcome"] != "success")),
            "Records Processed": str(sum(c["record_count"] for c in cs)) if cs else "",
            "Unique Records": "", "New Records": "", "Shortlisted": "",
            "Errors": "; ".join(errors)[:500],
        }
    return out


def load_shortlist_rows(conn):
    """No scoring exists yet (ranking is a later step), so nothing is shortlisted."""
    return []


# -------------------------------------------------------- human-field reconciling

def _verdict_value(raw):
    """Canonical Yes/Maybe/No, '' for blank, None if invalid."""
    text = (raw or "").strip()
    if not text:
        return ""
    for v in VERDICT_VALUES:
        if text.casefold() == v.casefold():
            return v
    return None


def reconcile_human(label, sheet_verdict_raw, sheet_notes_raw, stored, last_sync, report):
    """Decide how one record's human fields move. Returns (import_or_None, restore_cells, ok).

    import_or_None: (decision, notes) to save into SQLite.
    restore_cells: {column name: value} to put into BLANK sheet cells from SQLite.
    A SQLite value is never replaced by a blank, and a conflicting pair is never overwritten.
    """
    sv = _verdict_value(sheet_verdict_raw)
    if sv is None:
        report.warnings.append(f"{label}: '{sheet_verdict_raw.strip()}' is not Yes/Maybe/No; ignored, left as is")
        return None, {}, False
    sn = sheet_notes_raw or ""
    dbd = (stored["decision"] or "") if stored else ""
    dbn = stored["notes"] if stored else ""
    changed_since_sync = bool(stored) and (last_sync is None or stored["updated_at"] > last_sync)
    restore = {}

    # A blank cell never replaces a stored value: the stored value is put back instead.
    if sv == "":
        sv_effective = dbd
        if dbd:
            restore[VERDICT_COLUMN] = dbd
            report.verdicts_restored.append(f"{label}: verdict '{dbd}' restored")
    else:
        sv_effective = sv
    if not sn.strip():
        notes_effective = dbn if dbn.strip() else ""
        if dbn.strip():
            restore[NOTES_COLUMN] = dbn
            report.verdicts_restored.append(f"{label}: notes restored")
    else:
        notes_effective = sn

    verdict_differs = bool(dbd) and sv_effective != dbd
    notes_differ = bool(dbn.strip()) and bool(notes_effective.strip()) and notes_effective.strip() != dbn.strip()
    if (verdict_differs or notes_differ) and changed_since_sync:
        report.conflicts.append(
            f"{label}: sheet says '{sv_effective or '(unreviewed)'}' / '{notes_effective.strip()[:40]}', database "
            f"says '{dbd or '(unreviewed)'}' / '{dbn.strip()[:40]}'; the database value was changed since the "
            "last successful sync. Neither side was changed - make them agree, then re-sync")
        return None, restore, True
    notes_changed = notes_effective.strip() != dbn.strip()
    if (sv_effective != dbd or notes_changed) and (sv_effective or notes_effective.strip()):
        report.verdicts_imported.append(
            f"{label}: {dbd or '(unreviewed)'} -> {sv_effective or '(unreviewed)'}"
            + (" (notes updated)" if notes_changed else ""))
        return (sv_effective or None, notes_effective), restore, True
    return None, restore, True


# ------------------------------------------------------------------- planning

@dataclass
class TabPlan:
    header_cells: dict = field(default_factory=dict)
    appends: list = field(default_factory=list)
    updates: dict = field(default_factory=dict)


def _row_for(state, record_id, machine, human):
    cells = [""] * state.width
    key = KEY_COLUMN[state.tab]
    cells[state.col[key]] = record_id
    for name, value in {**machine, **human}.items():
        cells[state.col[name]] = value
    return cells


def _plan_keyed_tab(state, found, data, human_for, report, tab_label):
    """Plan header, updates and appended rows for Posts / Companies / Runs."""
    plan = TabPlan()
    if state.needs_headers:
        plan.header_cells = {f"{col_letter(i)}1": n for i, n in enumerate(column_names(state.tab))}
        report.headers_written.append(state.tab)
    machine_cols = [c.name for c in TAB_COLUMNS[state.tab] if c.owner == MACHINE]
    for rid, values in data.items():
        if rid in found:
            row_num, cells = found[rid]
            for name in machine_cols:
                new, old = values[name], cells[state.col[name]]
                if new == old:
                    continue
                if new == "" and old.strip():
                    report.kept_nonblank += 1
                    continue
                plan.updates[f"{col_letter(state.col[name])}{row_num}"] = new
        else:
            plan.appends.append(_row_for(state, rid, values, human_for(rid, restore_only=True)))
    report.appended[state.tab] = len(plan.appends)
    report.cells_updated[state.tab] = len(plan.updates)
    return plan


def _plan_shortlist(state, rows, report):
    plan = TabPlan()
    if state.needs_headers:
        plan.header_cells = {f"{col_letter(i)}1": n for i, n in enumerate(column_names("Shortlist"))}
        report.headers_written.append("Shortlist")
    names = column_names("Shortlist")
    existing = {r: cells for r, cells in state.rows}
    for i in range(max(len(rows), len(state.rows))):
        row_num = i + 2
        desired = rows[i] if i < len(rows) else {}
        cells = existing.get(row_num, [""] * state.width)
        for n in names:
            new, old = _s(desired.get(n)), cells[state.col[n]]
            if new != old:
                plan.updates[f"{col_letter(state.col[n])}{row_num}"] = new
    report.cells_updated["Shortlist"] = len(plan.updates)
    report.appended["Shortlist"] = 0
    return plan


def _last_sync(conn):
    row = conn.execute("SELECT MAX(finished_at) FROM runs WHERE run_type = 'sync' AND result = 'success'").fetchone()
    return row[0]


def sync_to_sheets(conn, backend, *, dry_run=False, now=None):
    """Synchronize SQLite and the (TEST) spreadsheet. Returns a SyncReport; raises SyncError if unsafe."""
    now = now or db.utc_now()
    report = SyncReport(dry_run=dry_run, spreadsheet=backend.title)

    # 1. Validate tabs and read EVERYTHING before any write.
    try:
        tabs = backend.tab_names()
    except Exception as exc:
        raise SyncError(f"Could not list the spreadsheet tabs ({type(exc).__name__}); no changes were made") from exc
    if sorted(tabs) != sorted(TAB_ORDER) or len(tabs) != len(TAB_ORDER):
        raise SyncError(f"Expected exactly the tabs {list(TAB_ORDER)}; found {tabs}. No changes were made")
    try:
        raw = {tab: backend.read_tab(tab) for tab in TAB_ORDER}
    except Exception as exc:
        raise SyncError(f"Could not read the spreadsheet ({type(exc).__name__}: {str(exc)[:150]}); "
                        "no changes were made") from exc
    states = {tab: parse_tab(tab, raw[tab]) for tab in TAB_ORDER}

    posts, companies, runs = load_post_rows(conn), load_company_rows(conn), load_run_rows(conn)
    found = {
        "Posts": index_rows(states["Posts"], set(posts), "job"),
        "Companies": index_rows(states["Companies"], set(companies), "company"),
        "Runs": index_rows(states["Runs"], set(), "run"),
    }
    for tab in ("Posts", "Companies"):
        key = KEY_COLUMN[tab]
        orphans = [n for n, c in states[tab].rows if not c[states[tab].col[key]].strip()]
        if orphans:
            report.warnings.append(f"{tab}: row(s) {orphans} have no record_id; left untouched")

    # 2. Reconcile human-owned fields.
    last_sync = _last_sync(conn)
    stored = {"Posts": {r["job_id"]: r for r in conn.execute("SELECT * FROM verdicts WHERE job_id IS NOT NULL")},
              "Companies": {r["company_id"]: r for r in conn.execute("SELECT * FROM verdicts WHERE company_id IS NOT NULL")}}
    imports = {"Posts": [], "Companies": []}
    restores = {"Posts": {}, "Companies": {}}
    for tab, data in (("Posts", posts), ("Companies", companies)):
        state = states[tab]
        for rid in data:
            verdict_row = stored[tab].get(rid)
            if rid in found[tab]:
                row_num, cells = found[tab][rid]
                noun = "Job" if tab == "Posts" else "Company"
                label = f"{noun} {data[rid].get('Job Title') or data[rid].get('Company')} [{rid[:8]}]"
                imp, restore, _ = reconcile_human(label, cells[state.col[VERDICT_COLUMN]],
                                                  cells[state.col[NOTES_COLUMN]], verdict_row, last_sync, report)
                if imp:
                    imports[tab].append((rid, *imp))
                restores[tab][rid] = {"row": row_num, "cells": restore}
            elif verdict_row:
                restores[tab][rid] = {"row": None, "cells": {VERDICT_COLUMN: verdict_row["decision"] or "",
                                                              NOTES_COLUMN: verdict_row["notes"]}}
                report.verdicts_restored.append(
                    f"{'Job' if tab == 'Posts' else 'Company'} [{rid[:8]}]: row was missing; re-added with stored verdict")

    # 3. Plan the sheet writes.
    plans = {}
    for tab, data in (("Posts", posts), ("Companies", companies), ("Runs", runs)):
        def human_for(rid, restore_only=True, _tab=tab):
            return restores[_tab].get(rid, {"cells": {}})["cells"] if _tab != "Runs" else {}
        plans[tab] = _plan_keyed_tab(states[tab], found[tab], data, human_for, report, tab)
        if tab != "Runs":
            for rid, info in restores[tab].items():
                if info["row"] is not None:
                    st = states[tab]
                    for name, value in info["cells"].items():
                        plans[tab].updates[f"{col_letter(st.col[name])}{info['row']}"] = value
                    report.cells_updated[tab] = len(plans[tab].updates)
    plans["Shortlist"] = _plan_shortlist(states["Shortlist"], load_shortlist_rows(conn), report)

    if dry_run:
        return report

    # 4. Confirm the sheet did not move under us, then write.
    try:
        for tab in ("Posts", "Companies"):
            again = parse_tab(tab, backend.read_tab(tab))
            key = KEY_COLUMN[tab]
            before = [(n, c[states[tab].col[key]]) for n, c in states[tab].rows]
            after = [(n, c[again.col[key]]) for n, c in again.rows] if not again.needs_headers else []
            if before != after:
                raise SyncError(f"{tab} changed while the sync was preparing; nothing was written. Re-run the sync")
    except SyncError:
        raise
    except Exception as exc:
        raise SyncError(f"Could not re-check the spreadsheet ({type(exc).__name__}); nothing was written") from exc

    report.run_id = db.start_run(conn, "sync", now=now)
    try:
        with db.transaction(conn):
            for tab, kind in (("Posts", "job_id"), ("Companies", "company_id")):
                for rid, decision, notes in imports[tab]:
                    db.save_verdict(conn, decision, notes, now=now, **{kind: rid})
        report.stages_completed.append("verdicts imported into SQLite")
        for tab in TAB_ORDER:
            plan = plans[tab]
            if plan.header_cells:
                backend.write_cells(tab, plan.header_cells)
            if plan.appends:
                backend.append_rows(tab, plan.appends)
            if plan.updates:
                backend.write_cells(tab, plan.updates)
            report.stages_completed.append(f"{tab} written")
    except Exception as exc:
        summary = {"stages_completed": report.stages_completed}
        db.finish_run(conn, report.run_id, "failed", summary=summary,
                      error_detail=f"{type(exc).__name__}: {str(exc)[:200]}", now=now)
        raise SyncError(f"Sync failed part-way ({type(exc).__name__}: {str(exc)[:150]}). "
                        f"Completed: {report.stages_completed or 'nothing'}. Verdicts already imported are safe; "
                        "re-running the sync is idempotent and will finish the job") from exc
    result = "partial" if report.conflicts else "success"
    db.finish_run(conn, report.run_id, result, now=now, summary={
        "appended": report.appended, "cells_updated": report.cells_updated,
        "verdicts_imported": len(report.verdicts_imported), "conflicts": len(report.conflicts)})
    return report
