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
from . import identity, lifecycle
from .sheets_schema import (HUMAN, KEY, KEY_COLUMN, MACHINE, NOTES_COLUMN, TAB_COLUMNS, TAB_ORDER,
                            VERDICT_COLUMN, VERDICT_VALUES, column_names, safe_cell)

WRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
LIVE_BLOCKED = "LIVE synchronization is not enabled; only --target test is allowed"
LIVE_STATES = ("New", "Active", "Changed", "Reopened")
# While one SQLite verdict table serves both sheets, a TEST sync never imports verdicts/notes (LIVE is authoritative) and never writes
# LIVE-origin decisions into TEST cells. The switch exists only so the reconcile mechanics stay unit-testable; nothing in the CLI sets it.
TEST_VERDICT_IMPORT_ENABLED = False
LIVE_SCOPE = "live"        # the explicit first-LIVE publication scope (see sync_to_sheets)
LIVE_RUN_TYPE = "LIVE publication (stored snapshots; no new source collection)"
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
    planned: dict = field(default_factory=dict)         # tab -> {"appends": [...], "updates": [...]} exact planned changes
    session_note: str = ""

    def lines(self):
        out = [f"{'DRY RUN - no changes were made' if self.dry_run else 'Sync complete'}"
               f" | spreadsheet: {self.spreadsheet}"]
        if self.run_id:
            out.append(f"Run ID: {self.run_id}")
        if self.session_note:
            out.append(self.session_note)
        out.append(f"Headers to write: {', '.join(self.headers_written) or 'none'}")
        for tab in TAB_ORDER:
            out.append(f"{tab}: {self.appended.get(tab, 0)} row(s) to add, "
                       f"{self.cells_updated.get(tab, 0)} machine cell(s) to update")
            detail = self.planned.get(tab) or {}
            reasons = {}
            for a in detail.get("appends", []):
                reasons[a.get("admitted_by", "?")] = reasons.get(a.get("admitted_by", "?"), 0) + 1
            if reasons:
                out.append("    rows added, by the rule that admits them: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))
            cols = {}
            for u in detail.get("updates", []):
                cols[u["column"]] = cols.get(u["column"], 0) + 1
            if cols:
                out.append("    cells updated by column: " + ", ".join(f"{k} {v}" for k, v in sorted(cols.items())))
            cleared = sum(1 for u in detail.get("updates", []) if u["new"] == "" and u["old"] != "")
            if cleared:
                out.append(f"    cells cleared (record left the generated view): {cleared}")
        out.append("Rows deleted: 0 (the sync never deletes a row; nothing the person typed is removed)")
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
    merged_away = identity.alias_ids(conn, "job")
    for j in conn.execute("""SELECT j.*, c.name AS company_name FROM jobs j
                             JOIN companies c USING (company_id) ORDER BY j.first_seen_at, j.job_id"""):
        if j["job_id"] in merged_away:
            continue
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


def load_hiring_evidence(conn):
    """company_id -> source-accurate label of what is known about its jobs (see lifecycle.hiring_evidence_text).

    A listing on a board or feed is an OBSERVATION; it is never described as an open vacancy unless an
    employer-direct source supports it. Dates and sources are kept in the text."""
    checks = lifecycle.latest_run_checks(conn, "jobs")   # each source's newest run of JOB scans (company scans never count)
    best, names, latest = {}, {}, {}
    order = {c: i for i, c in enumerate(lifecycle.CLASS_ORDER)}
    for r in conn.execute("""SELECT g.job_id, j.company_id, j.lifecycle_state, g.last_seen_at, g.provider_freshness,
                                    g.source_id, src.name, src.source_type, src.config_ref
                             FROM sightings g JOIN jobs j USING (job_id) JOIN sources src ON src.source_id = g.source_id
                             WHERE g.job_id IS NOT NULL"""):
        cls = lifecycle.classify_observation(r, r, checks.get(r["source_id"]), r["lifecycle_state"])
        key = r["job_id"]
        if key not in best or order[cls] < order[best[key][1]]:
            best[key] = (r["company_id"], cls)
        names.setdefault(r["company_id"], set()).add(r["name"])
        latest[r["company_id"]] = max(latest.get(r["company_id"], ""), r["last_seen_at"])
    by_company = {}
    for company_id, cls in best.values():
        by_company.setdefault(company_id, []).append(cls)
    return {cid: lifecycle.hiring_evidence_text(classes, names.get(cid, ()), latest.get(cid))
            for cid, classes in by_company.items()}


def load_company_rows(conn):
    sightings, jobs = {}, {}
    evidence = load_hiring_evidence(conn)
    merged_away = identity.alias_ids(conn, "company")
    for r in conn.execute("""SELECT g.company_id, g.first_seen_at, g.last_seen_at, s.name AS source_name
                             FROM sightings g JOIN sources s USING (source_id)
                             WHERE g.company_id IS NOT NULL"""):
        sightings.setdefault(r["company_id"], []).append(r)
    for r in conn.execute("SELECT company_id, lifecycle_state FROM jobs"):
        jobs.setdefault(r["company_id"], []).append(r["lifecycle_state"])
    out = {}
    for c in conn.execute("SELECT * FROM companies ORDER BY created_at, company_id"):
        if c["company_id"] in merged_away:
            continue                                    # merged into another record: that record carries the row
        seen, states = sightings.get(c["company_id"], []), jobs.get(c["company_id"], [])
        aliases = json.loads(c["aliases"])
        facts = json.loads(c["facts"])
        yc = facts.get("yc") or {}
        details = f"Identity: {c['identity_status']}" + (f"; also known as: {', '.join(aliases)}" if aliases else "")
        hiring = evidence.get(c["company_id"]) or "No jobs observed"
        if yc:  # source-reported YC facts, labelled as such
            bits = [yc.get("batch"), yc.get("stage"), yc.get("status"), yc.get("industry"),
                    f"team {yc['team_size']}" if yc.get("team_size") else None,
                    f"site (YC-reported): {yc['website_domain_reported']}" if yc.get("website_domain_reported") else None,
                    (relevance := facts.get("india_relevance")) and f"India relevance: {relevance['class']}",
                    yc.get("one_liner")]
            details += " | YC: " + " · ".join(b for b in bits if b)
            details = details[:500]
            flag = yc.get("is_hiring_reported")
            if flag is not None:
                hiring += f"; YC-reported hiring flag: {'true' if flag else 'false'} (unverified)"
        out[c["company_id"]] = {
            "Company": c["name"], "Domain": _s(c["domain"]), "Location": _s(c["location"]),
            "Details": details, "Sources": ", ".join(sorted({x["source_name"] for x in seen})),
            "Hiring Evidence": hiring,
            "First Seen": min((x["first_seen_at"] for x in seen), default=""),
            "Last Seen": max((x["last_seen_at"] for x in seen), default=""),
            "Score": "", "Explanation": "",
        }
    return out


# Jobs seen by these sources are never appended to the review sheet until relevance and freshness rules are approved
# (YC job pages show years-old listings; restricted JobSpy boards are never published). Existing rows and human
# verdicts are unaffected. Investor-board sources (ids starting inv_) are held back as well.
UNPUBLISHED_JOB_SOURCES = ("yc_jobs", "manual", "jobspy_indeed", "jobspy_naukri", "jobspy_linkedin")


def load_publishable_company_ids(conn):
    """Companies that may get a NEW row in the review sheet.

    A company whose facts say sheet_publish = false (e.g. outside the India-relevant subset) stays in
    SQLite but is not added to the sheet - unless it has a human verdict/note. Companies already in
    the sheet are still kept up to date, and companies with no flag (older data) are published.
    """
    rows = conn.execute("""SELECT company_id FROM companies c
                           WHERE json_extract(c.facts, '$.sheet_publish') IS NOT 0
                              OR EXISTS (SELECT 1 FROM verdicts v WHERE v.company_id = c.company_id)""")
    return {r[0] for r in rows}


def load_publishable_job_ids(conn):
    """Jobs that may get a NEW row in the review sheet.

    Jobs of companies flagged sheet_publish = false (for example board-code companies imported from the
    Feashliaa feed) stay in SQLite only, unless they carry a human verdict/note. Publishing a reviewed subset
    of such jobs is a separate, explicitly approved step. Companies without a flag (older data) publish.
    """
    marks = ",".join("?" * len(UNPUBLISHED_JOB_SOURCES))
    rows = conn.execute(f"""SELECT j.job_id FROM jobs j JOIN companies c USING (company_id)
                           WHERE (json_extract(c.facts, '$.sheet_publish') IS NOT 0
                                  AND NOT EXISTS (SELECT 1 FROM sightings s WHERE s.job_id = j.job_id
                                                  AND (s.source_id IN ({marks}) OR s.source_id LIKE 'inv!_%' ESCAPE '!')))
                              OR EXISTS (SELECT 1 FROM verdicts v WHERE v.job_id = j.job_id)""",
                        UNPUBLISHED_JOB_SOURCES)
    return {r[0] for r in rows}


def load_run_rows(conn):
    checks = {}
    for r in conn.execute("SELECT * FROM source_checks"):
        checks.setdefault(r["run_id"], []).append(r)
    out = {}
    # Sync runs are bookkeeping, not discovery executions, so they are not listed in the Runs tab.
    # A run still in progress (for example the discovery run that is syncing right now) is listed once it has finished.
    for r in conn.execute("SELECT * FROM runs WHERE run_type != 'sync' AND result != 'running' ORDER BY started_at, run_id"):
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


def live_run_row(started, finished, result, summary):
    """The one Runs-tab row of a LIVE publication. Built from stored values only, so a later sync reproduces it exactly."""
    return {"Started": started, "Finished": finished, "Status": result, "Type": LIVE_RUN_TYPE,
            "Sources Succeeded": "", "Sources Failed/Partial/Skipped": "", "Records Processed": "",
            "Unique Records": "", "New Records": str(summary.get("new_records", "")), "Shortlisted": str(summary.get("shortlisted", "")),
            "Errors": _s(summary.get("notes"))[:500]}


def load_live_run_rows(conn):
    """Runs-tab rows in the LIVE scope: only LIVE publications. The development/TEST history stays in SQLite and is never backfilled."""
    out = {}
    for r in conn.execute("SELECT * FROM runs WHERE run_type = 'sync' AND result IN ('success', 'partial') ORDER BY started_at, run_id"):
        try:
            summary = json.loads(r["summary"] or "{}")
        except ValueError:
            continue
        if summary.get("live_runs_row"):
            out[r["run_id"]] = live_run_row(r["started_at"], _s(r["finished_at"]), r["result"], summary)
    return out


def _live_notes(conn, now):
    """Source-health caveats, stated honestly, for the LIVE Runs row (stored evidence only)."""
    from datetime import datetime

    from . import source_health
    try:
        as_of = datetime.fromisoformat(now) if isinstance(now, str) else now
        text = source_health.render(source_health.source_health(conn, now=as_of))[-1]
    except Exception:                                  # a reporting problem must never block the sync
        text = "source health unavailable"
    return ("Published from existing stored snapshots; no source was contacted. " + text)[:500]


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
    detail: dict = field(default_factory=lambda: {"appends": [], "updates": []})   # exactly what changes (for the dry-run plan)


def _row_for(state, record_id, machine, human):
    cells = [""] * state.width
    key = KEY_COLUMN[state.tab]
    cells[state.col[key]] = record_id
    for name, value in {**machine, **human}.items():
        cells[state.col[name]] = value
    return cells


def _plan_keyed_tab(state, found, data, human_for, report, tab_label, appendable=None, admitted_by=None):
    """Plan header, updates and appended rows for Posts / Companies / Runs.

    appendable: optional set of record ids allowed to receive a NEW row (rows already in the sheet are
    always kept up to date).
    """
    plan = TabPlan()
    if state.needs_headers:
        plan.header_cells = {f"{col_letter(i)}1": n for i, n in enumerate(column_names(state.tab))}
        report.headers_written.append(state.tab)
    machine_cols = [c.name for c in TAB_COLUMNS[state.tab] if c.owner == MACHINE]
    for rid, values in data.items():
        values = {k: safe_cell(v) for k, v in values.items()}
        if rid in found:
            row_num, cells = found[rid]
            for name in machine_cols:
                new, old = values[name], cells[state.col[name]]
                if new == old:
                    continue
                if new == "" and old.strip():
                    report.kept_nonblank += 1
                    continue
                a1 = f"{col_letter(state.col[name])}{row_num}"
                plan.updates[a1] = new
                plan.detail["updates"].append({"cell": a1, "record_id": rid, "column": name, "old": old, "new": new})
        elif appendable is None or rid in appendable:
            plan.appends.append(_row_for(state, rid, values, human_for(rid, restore_only=True)))
            plan.detail["appends"].append({"record_id": rid, "summary": " | ".join(values.get(n, "")[:60] for n in ("Company", "Job Title") if values.get(n)),
                                           "admitted_by": (admitted_by or {}).get(rid, "publication_gate")})
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
            new, old = safe_cell(desired.get(n)), cells[state.col[n]]
            if new != old:
                a1 = f"{col_letter(state.col[n])}{row_num}"
                plan.updates[a1] = new
                plan.detail["updates"].append({"cell": a1, "record_id": _s(desired.get("record_id")) or cells[state.col["record_id"]],
                                               "column": n, "old": old, "new": new})
    report.cells_updated["Shortlist"] = len(plan.updates)
    report.appended["Shortlist"] = 0
    return plan


def live_verdict_ids(conn):
    """Record ids whose stored verdict/note was imported from the LIVE sheet (kept in each LIVE sync run's summary; no schema change).

    Every sync run counts, whatever its result: the import is committed before any sheet write, so a LIVE sync that later failed or
    was interrupted still imported LIVE feedback, and that feedback must never be written into TEST."""
    ids = set()
    for (text,) in conn.execute("SELECT summary FROM runs WHERE run_type = 'sync'"):
        try:
            ids.update(json.loads(text or "{}").get("live_verdicts", []))
        except ValueError:
            continue
    return ids


def live_decision_ids(conn):
    """Record ids whose stored DECISION (Yes/Maybe/No) was typed, or confirmed, in a LIVE Verdict cell. LIVE human cells are only ever
    typed by the person (stored values are never copied into them), so a non-blank LIVE Verdict is a genuine LIVE decision. A stored
    decision NOT in this set is legacy review memory (from the TEST sheet before LIVE): ranking treats a legacy Yes/Maybe as history only
    and keeps a legacy No in force. Notes typed in LIVE are provenance for TEST isolation (live_verdict_ids) but never turn a legacy
    decision into a LIVE one."""
    ids = set()
    for (text,) in conn.execute("SELECT summary FROM runs WHERE run_type = 'sync'"):
        try:
            ids.update(json.loads(text or "{}").get("live_decisions", []))
        except ValueError:
            continue
    return ids


def _interrupted_live_publication(conn, runs_in_sheet):
    """The newest LIVE sync if it failed or never finished and has no row in the LIVE Runs tab, else None. Its sheet writes may have
    landed (a lost response, a stopped process), so the next LIVE sync must record the publication even if nothing is left to write."""
    for run_id, result, text in conn.execute("SELECT run_id, result, summary FROM runs WHERE run_type = 'sync' ORDER BY started_at DESC, rowid DESC"):
        try:
            summary = json.loads(text or "{}")
        except ValueError:
            continue
        if summary.get("target") != "live":
            continue
        return run_id if result in ("failed", "running") and run_id not in runs_in_sheet else None
    return None


def _last_sync(conn):
    row = conn.execute("SELECT MAX(finished_at) FROM runs WHERE run_type = 'sync' AND result = 'success'").fetchone()
    return row[0]


def sync_to_sheets(conn, backend, *, dry_run=False, now=None, shortlist=None, scope=None, before_write=None):
    """Synchronize SQLite and the (TEST) spreadsheet. Returns a SyncReport; raises SyncError if unsafe.

    shortlist: optional shortlist.Shortlist (or a callable taking {record_id: verdict read from the sheet}). When given, the Shortlist tab is generated from it, Score/Explanation are filled for
    ranked records, and ONLY its selected records may receive new Posts/Companies rows beyond the normal publication gate
    (restricted sources are never selected). The human Verdict/Notes columns are untouched by any of it.

    scope="live" is the first-LIVE publication scope (requires a shortlist): NEW rows come only from the displayed shortlist
    (Posts: its jobs; Companies: the companies of those jobs plus the company-only picks; never the older broad gates), the Runs tab
    shows only LIVE publications (one row, written only when the sync changes something), and stored verdicts are NOT copied into
    LIVE's human cells (LIVE's Verdict/Notes are only ever typed by the person; what they type is still imported). Nothing is deleted.

    before_write: optional callable(report, shortlist) run on the exact final plan after the last sheet re-check and before anything
    is recorded or written; raising SyncError there stops the sync with nothing changed (live_publish uses it to hold the write to the
    plan the operator approved)."""
    now = now or db.utc_now()
    live = scope == LIVE_SCOPE
    if live and shortlist is None:
        raise SyncError("The LIVE scope needs a shortlist; nothing was written")
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

    posts, companies = load_post_rows(conn), load_company_rows(conn)
    runs = load_live_run_rows(conn) if live else load_run_rows(conn)
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
    live_ids = live_verdict_ids(conn)
    stored = {"Posts": {r["job_id"]: r for r in conn.execute("SELECT * FROM verdicts WHERE job_id IS NOT NULL")},
              "Companies": {r["company_id"]: r for r in conn.execute("SELECT * FROM verdicts WHERE company_id IS NOT NULL")}}
    imports = {"Posts": [], "Companies": []}
    restores = {"Posts": {}, "Companies": {}}
    live_typed, live_decided = set(), {}                    # LIVE only: records with person-typed human cells / a valid typed decision
    for tab, data in (("Posts", posts), ("Companies", companies)):
        state = states[tab]
        for rid in data:
            verdict_row = stored[tab].get(rid)
            if rid in found[tab]:
                row_num, cells = found[tab][rid]
                noun = "Job" if tab == "Posts" else "Company"
                label = f"{noun} {data[rid].get('Job Title') or data[rid].get('Company')} [{rid[:8]}]"
                scratch = SyncReport(dry_run=True)          # reconcile into a scratch report; the target's policy decides what is kept
                imp, restore, _ = reconcile_human(label, cells[state.col[VERDICT_COLUMN]],
                                                  cells[state.col[NOTES_COLUMN]], verdict_row, last_sync, scratch)
                report.warnings.extend(scratch.warnings)
                if live:
                    # LIVE is the authoritative review interface. Its human cells are the person's alone: never filled from the shared
                    # store. What the person types is imported; a change to older review memory is named, and never copied to TEST.
                    restore = {}
                    typed_verdict = _verdict_value(cells[state.col[VERDICT_COLUMN]])
                    if typed_verdict or (cells[state.col[NOTES_COLUMN]] or "").strip():
                        live_typed.add(rid)
                    if typed_verdict:
                        live_decided[rid] = typed_verdict
                    report.conflicts.extend(scratch.conflicts)
                    for line in scratch.verdicts_imported:
                        older = verdict_row["decision"] if verdict_row and verdict_row["decision"] else ""
                        report.verdicts_imported.append(line + ("; replaces earlier review memory, which is not copied to TEST" if older or (verdict_row and verdict_row["notes"]) else ""))
                elif TEST_VERDICT_IMPORT_ENABLED:
                    # test-only emulation of the pre-LIVE world, where TEST was the review sheet and its decisions were authoritative
                    if _verdict_value(cells[state.col[VERDICT_COLUMN]]):
                        live_decided[rid] = _verdict_value(cells[state.col[VERDICT_COLUMN]])
                    report.conflicts.extend(scratch.conflicts)
                    report.verdicts_imported.extend(scratch.verdicts_imported)
                    report.verdicts_restored.extend(scratch.verdicts_restored)
                else:
                    # TEST never imports verdicts or notes while one store serves both sheets, and never receives LIVE feedback.
                    if rid in live_ids:
                        restore = {}                        # this record's stored decision came from LIVE: never write it into TEST
                    else:
                        report.verdicts_restored.extend(scratch.verdicts_restored)    # legacy TEST decisions may still refill a blank TEST cell
                    ignored = scratch.verdicts_imported + scratch.conflicts
                    if imp or ignored:
                        report.warnings.append(f"{label}: an edit or difference in the TEST sheet was ignored (LIVE is authoritative while one verdict "
                                               "store serves both sheets); nothing was saved or overwritten")
                    imp = None
                if imp:
                    imports[tab].append((rid, *imp))
                restores[tab][rid] = {"row": row_num, "cells": restore}
            elif verdict_row and not live and rid not in live_ids:
                restores[tab][rid] = {"row": None, "cells": {VERDICT_COLUMN: verdict_row["decision"] or "",
                                                              NOTES_COLUMN: verdict_row["notes"]}}
                report.verdicts_restored.append(
                    f"{'Job' if tab == 'Posts' else 'Company'} [{rid[:8]}]: row was missing; re-added with stored verdict")

    # 2b. Rank with the verdicts just read from the sheet (they are imported into SQLite only after this plan), so a record you
    # rejected a moment ago is not listed again. `shortlist` may be a ready Shortlist or a callable(overrides) -> Shortlist.
    if callable(shortlist):
        overrides = {rid: decision for tab in ("Posts", "Companies") for rid, decision, _ in imports[tab] if decision}
        overrides.update(live_decided)                      # a LIVE decision equal to the stored one still counts as a LIVE decision
        shortlist = shortlist(overrides)
    if shortlist is not None:
        report.session_note = (f"Review session: CONTINUING {shortlist.session_id[:8]} (same records, re-checked; nothing new is added; "
                               f"{len(shortlist.dropped)} dropped: " + "; ".join(f"{l[:40]} ({why})" for _, l, why in shortlist.dropped[:5]) + ")"
                               if shortlist.mode == "continue" else "Review session: NEW (the next batch; start it only on purpose)")
    if shortlist is not None:
        for data in (posts, companies):
            for rid, row in data.items():
                if rid in shortlist.scores:
                    row["Score"], row["Explanation"] = str(shortlist.scores[rid][0]), shortlist.scores[rid][1]

    # 3. Plan the sheet writes.
    plans = {}
    for tab, data in (("Posts", posts), ("Companies", companies), ("Runs", runs)):
        def human_for(rid, restore_only=True, _tab=tab):
            return restores[_tab].get(rid, {"cells": {}})["cells"] if _tab != "Runs" else {}
        appendable = {"Companies": load_publishable_company_ids, "Posts": load_publishable_job_ids}.get(tab, lambda c: None)(conn)
        admitted = {}
        if live:
            if tab == "Runs":
                appendable = set()                           # the single publication row is added at write time
            elif tab == "Posts":
                appendable = set(shortlist.publish_job_ids)
                admitted = {rid: "shortlist" for rid in appendable}
            else:
                of_jobs = {r[0] for r in conn.execute(
                    f"SELECT DISTINCT company_id FROM jobs WHERE job_id IN ({','.join('?' * len(shortlist.publish_job_ids)) or 'NULL'})",
                    tuple(shortlist.publish_job_ids))}
                appendable = of_jobs | set(shortlist.publish_company_ids)
                admitted = {rid: ("shortlist_company" if rid in shortlist.publish_company_ids else "shortlist_job_company") for rid in appendable}
        elif shortlist is not None and appendable is not None:
            chosen = shortlist.publish_job_ids if tab == "Posts" else shortlist.publish_company_ids
            if tab == "Posts":
                # With a shortlist, NEW job rows come only from the shortlist (plus jobs that carry a human verdict, so they are never
                # lost). The older broad gate (every job of a publishable company) is not applied: rows it admitted earlier stay.
                appendable = set(chosen) | {r[0] for r in conn.execute("SELECT job_id FROM verdicts WHERE job_id IS NOT NULL")}
            else:
                appendable = appendable | chosen
            admitted = {rid: "shortlist" for rid in chosen}
        plans[tab] = _plan_keyed_tab(states[tab], found[tab], data, human_for, report, tab, appendable=appendable, admitted_by=admitted)
        if tab != "Runs":
            for rid, info in restores[tab].items():
                if info["row"] is not None:
                    st = states[tab]
                    for name, value in info["cells"].items():
                        plans[tab].updates[f"{col_letter(st.col[name])}{info['row']}"] = value
                    report.cells_updated[tab] = len(plans[tab].updates)
    plans["Shortlist"] = _plan_shortlist(states["Shortlist"], shortlist.rows if shortlist is not None else load_shortlist_rows(conn), report)
    pending_run = None
    if live:
        changed = any(plans[t].header_cells or plans[t].appends or plans[t].updates for t in ("Shortlist", "Posts", "Companies"))
        interrupted = _interrupted_live_publication(conn, found["Runs"])
        if changed or interrupted:
            notes = _live_notes(conn, now)
            if interrupted:
                notes = (f"Completes the publication interrupted in run {interrupted[:8]}. " + notes)[:500]
            summary_extra = {"new_records": len(plans["Posts"].appends) + len(plans["Companies"].appends), "shortlisted": len(shortlist.rows),
                             "notes": notes}
            pending_run = summary_extra
            plans["Runs"].detail["appends"].append({"record_id": "(assigned when the sync runs)", "summary": LIVE_RUN_TYPE, "admitted_by": "live_publication_run"})
            report.appended["Runs"] = report.appended.get("Runs", 0) + 1
    report.planned = {tab: plans[tab].detail for tab in TAB_ORDER}

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
    if before_write is not None:
        before_write(report, shortlist)

    report.run_id = db.start_run(conn, "sync", now=now)
    finished_at = db.utc_now()
    result = "partial" if report.conflicts else "success"
    if pending_run is not None:
        plans["Runs"].appends.append(_row_for(states["Runs"], report.run_id, {k: safe_cell(v) for k, v in
                                     live_run_row(now, finished_at, result, pending_run).items()}, {}))
    live_provenance = ({"target": "live", "live_verdicts": sorted(live_typed | {rid for tab in imports for rid, _, _ in imports[tab]}),
                        "live_decisions": sorted(live_decided)} if live
                       else {"live_decisions": sorted(live_decided)} if TEST_VERDICT_IMPORT_ENABLED and live_decided else {})
    try:
        with db.transaction(conn):
            for tab, kind in (("Posts", "job_id"), ("Companies", "company_id")):
                for rid, decision, notes in imports[tab]:
                    db.save_verdict(conn, decision, notes, now=now, **{kind: rid})
            if live_provenance:
                # recorded in the SAME transaction as the import, so no crash or failed write can separate a LIVE decision from its origin
                # (and an interrupted LIVE publication stays identifiable as one)
                conn.execute("UPDATE runs SET summary = ? WHERE run_id = ?", (json.dumps(live_provenance), report.run_id))
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
        summary = {"stages_completed": report.stages_completed, **live_provenance}
        db.finish_run(conn, report.run_id, "failed", summary=summary,
                      error_detail=f"{type(exc).__name__}: {str(exc)[:200]}", now=now)
        raise SyncError(f"Sync failed part-way ({type(exc).__name__}: {str(exc)[:150]}). "
                        f"Completed: {report.stages_completed or 'nothing'}. Verdicts already imported are safe; "
                        "re-running the sync is idempotent and will finish the job") from exc
    db.finish_run(conn, report.run_id, result, now=finished_at if pending_run is not None else now, summary={
        "appended": report.appended, "cells_updated": report.cells_updated,
        "verdicts_imported": len(report.verdicts_imported), "conflicts": len(report.conflicts),
        **({**live_provenance, **({"live_runs_row": True, **pending_run} if pending_run is not None else {})} if live else live_provenance),
        **(shortlist.summary(report.run_id) if shortlist is not None else {})})
    return report
