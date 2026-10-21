"""The guarded first-LIVE publication (Day 15).

Two steps, both manual:
  live-publish --plan-output exports/<file>.json          read-only: reads LIVE, writes nothing, saves the exact plan
  live-publish --apply --plan <file> --backup <db copy> --authorized-first-live-trial
                                                          re-plans, requires the result to equal the saved plan and to stay inside
                                                          the approved limits, then performs that one bounded write

The limits below are the operator's written authorization (Day 15 decision, 2026-10-21). They are code, not a prompt: a plan that exceeds
them, deletes anything, touches a human-owned cell, copies a verdict, is admitted by an older broad gate, or differs from the saved plan
is refused before any request that writes. Widening them needs the operator's explicit approval and a code change.
"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from . import database as db
from . import live_preflight as lp
from . import profile as prof
from . import sheets_sync, shortlist as shortlist_mod
from .sheets_schema import TAB_ORDER

DEFAULT_LIMITS = {"Shortlist": 25, "Posts": 30, "Companies": 30, "Runs": 2}   # the first-trial approval; the file may only be read, never guessed larger


def load_limits(root=db.PROJECT_ROOT):
    """Row limits from config/live_publication.json (the operator's approved numbers). A missing or malformed file means the
    first-trial defaults, never a larger number."""
    path = Path(root) / "config" / "live_publication.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))["max_rows"]
        if set(rows) == set(DEFAULT_LIMITS) and all(isinstance(v, int) and v > 0 for v in rows.values()):
            return dict(rows)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return dict(DEFAULT_LIMITS)


LIMITS = load_limits()
ALLOWED_ADMISSION = {"Posts": {"shortlist"}, "Companies": {"shortlist_company", "shortlist_job_company"}, "Runs": {"live_publication_run"}}
BACKUP_MAX_AGE_MINUTES = 120


class PublishRefused(Exception):
    """A gate failed; nothing was written."""


def db_fingerprint(db_path):
    h = hashlib.sha256()
    with open(db_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_limits(report, inspection, shortlist_rows):
    """-> list of violations (empty = inside the approved boundaries). Pure: works on the report and the sheet inspection."""
    bad = []
    existing = {t: inspection["tabs"][t]["data_rows"] for t in TAB_ORDER if t in inspection["tabs"]}
    final = {"Shortlist": shortlist_rows, **{t: existing.get(t, 0) + report.appended.get(t, 0) for t in ("Posts", "Companies", "Runs")}}
    for tab, cap in LIMITS.items():
        if final[tab] > cap:
            bad.append(f"{tab}: {final[tab]} rows after the write exceeds the approved maximum of {cap}")
    human = lp.human_columns()
    for tab in TAB_ORDER:
        detail = report.planned.get(tab, {})
        for a in detail.get("appends", []):
            if a.get("admitted_by") not in ALLOWED_ADMISSION.get(tab, set()):
                bad.append(f"{tab}: row {a.get('record_id')} admitted by '{a.get('admitted_by')}', which the first-LIVE scope does not allow")
        for u in detail.get("updates", []):
            if u["column"] in human.get(tab, []):
                bad.append(f"{tab}: a human-owned cell ({u['column']}) would be written")
            # The Shortlist tab is a machine-generated view rewritten by position: when a rejected record leaves the batch (no
            # replacement) or a company row moves into a former job row, its cells are cleared. The records themselves stay in
            # Posts/Companies, where nothing may ever be cleared.
            if u["new"] == "" and u["old"] != "" and tab != "Shortlist":
                bad.append(f"{tab}: cell {u['cell']} would be cleared")
    if report.verdicts_restored:
        bad.append("stored verdicts/notes would be written into LIVE human cells")
    if report.conflicts:
        bad.append(f"{len(report.conflicts)} verdict conflict(s)")
    if report.warnings:
        bad.append(f"{len(report.warnings)} warning(s) to resolve first: " + "; ".join(w[:160] for w in report.warnings[:3]))
    for t in inspection["tabs"]:
        if inspection["tabs"][t].get("duplicate_ids"):
            bad.append(f"{t}: duplicate record ids already in LIVE")
    bad.extend(inspection.get("problems", []))
    return bad


def _shortlist_callable(conn, new_session=False):
    holder = {}
    pr, rk = prof.load_profile(), prof.load_ranking()

    def build(overrides):
        holder["sl"] = shortlist_mod.build(conn, pr, rk, verdict_overrides=overrides, new_session=new_session)
        return holder["sl"]
    return build, holder


def make_plan(root=db.PROJECT_ROOT, db_path=None, new_session=False):
    """Read-only: read LIVE, plan, check the limits. -> plan dict (JSON-serialisable)."""
    db_path = Path(db_path) if db_path else db.resolve_db_path(root)
    conn = db.connect(db_path, readonly=True)
    try:
        backend = lp.open_live_readonly(root)
        valid = {"Posts": {r[0] for r in conn.execute("SELECT job_id FROM jobs")}, "Companies": {r[0] for r in conn.execute("SELECT company_id FROM companies")}}
        inspection = lp.inspect_sheet(backend, valid)
        build, holder = _shortlist_callable(conn, new_session=new_session)
        report = lp.plan_sync(conn, backend, shortlist=build)
        sl = holder["sl"]
        violations = check_limits(report, inspection, len(sl.rows))
    finally:
        conn.close()
    plan = {"spreadsheet": backend.title, "db_sha256": db_fingerprint(db_path), "session": sl.session_id, "mode": sl.mode,
            "inspection": inspection, "planned": report.planned, "counts": {"appended": report.appended, "cells_updated": report.cells_updated},
            "headers_to_write": report.headers_written, "shortlist_rows": sl.rows, "violations": violations,
            "report_lines": report.lines()}
    return json.loads(json.dumps(plan, default=str))


def _verify_backup(path, db_path):
    path = Path(path)
    if not path.is_file():
        raise PublishRefused("the verified backup file does not exist")
    age = (time.time() - path.stat().st_mtime) / 60
    if age > BACKUP_MAX_AGE_MINUTES:
        raise PublishRefused(f"the backup is {age:.0f} minutes old; make a fresh one (limit {BACKUP_MAX_AGE_MINUTES})")
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise PublishRefused("the backup fails its integrity check")
        if con.execute("PRAGMA user_version").fetchone()[0] != db.get_schema_version(db.connect(db_path, readonly=True)):
            raise PublishRefused("the backup's schema version differs from the production database")
    finally:
        con.close()
    return path.name


def _final_plan_gate(saved, inspection):
    """-> before_write callable for sync_to_sheets: the plan about to be written must equal the approved plan and respect the limits.
    Seconds pass between the last check and the write; if the sheet or the data changed in that window, nothing is written."""
    approved = {"planned": saved["planned"], "shortlist_rows": saved["shortlist_rows"]}

    def gate(report, sl):
        final = json.loads(json.dumps({"planned": report.planned, "shortlist_rows": sl.rows}, default=str))
        if final != approved:
            raise sheets_sync.SyncError("the plan about to be written differs from the approved plan (the sheet or the data changed in the "
                                        "last moments); nothing was written. Make and approve a new plan")
        bad = check_limits(report, inspection, len(sl.rows))
        if bad:
            raise sheets_sync.SyncError("limit violations at write time: " + "; ".join(bad[:3]) + "; nothing was written")
    return gate


def apply(plan_path, backup_path, *, authorized, root=db.PROJECT_ROOT, db_path=None, out=print):
    """Perform the one bounded LIVE write. Every gate is checked before the first write request, and the plan actually written must
    equal the approved one. A plan made with --new-session starts the next review session inside this same write (nothing earlier)."""
    if not authorized:
        raise PublishRefused("--authorized-first-live-trial is required: this is the operator's bounded first-LIVE authorization")
    db_path = Path(db_path) if db_path else db.resolve_db_path(root)
    saved = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if saved.get("violations"):
        raise PublishRefused("the saved plan lists violations: " + "; ".join(saved["violations"][:3]))
    if db_fingerprint(db_path) != saved["db_sha256"]:
        raise PublishRefused("the production database changed since the plan was saved; make a new plan")
    new_session = saved.get("mode") == "new"
    fresh = make_plan(root, db_path, new_session=new_session)
    for key in ("planned", "shortlist_rows", "headers_to_write", "session", "mode", "counts"):
        if fresh[key] != saved[key]:
            raise PublishRefused(f"the fresh plan differs from the saved plan ({key}); nothing was written")
    if fresh["inspection"] != saved["inspection"]:
        raise PublishRefused("the LIVE sheet no longer matches the preflight inspection; nothing was written")
    if fresh["violations"]:
        raise PublishRefused("limit violations: " + "; ".join(fresh["violations"][:3]))
    if "LIVE" not in fresh["spreadsheet"]:
        raise PublishRefused("the spreadsheet title does not identify LIVE")
    backup_name = _verify_backup(backup_path, db_path)
    out(f"All gates passed (plan identical, limits respected, backup {backup_name} verified). Writing the bounded LIVE publication.")
    conn = db.connect(db_path)
    try:
        build, _ = _shortlist_callable(conn, new_session=new_session)
        report = sheets_sync.sync_to_sheets(conn, lp.open_live_write(root), dry_run=False, shortlist=build, scope=sheets_sync.LIVE_SCOPE,
                                            before_write=_final_plan_gate(saved, fresh["inspection"]))
    finally:
        conn.close()
    return report


def limits_preview(root=db.PROJECT_ROOT, new_session=False):
    """Read-only: the approved limits, what LIVE holds now, and what the NEXT publication would need. Changes nothing, anywhere.
    With new_session the plan is the one the next batch (--new-session) would produce; nothing is recorded."""
    plan = make_plan(root, new_session=new_session)
    cur = {t: plan["inspection"]["tabs"][t]["data_rows"] for t in TAB_ORDER}
    add = plan["counts"]["appended"]
    need = {"Shortlist": len(plan["shortlist_rows"]), **{t: cur[t] + add.get(t, 0) for t in ("Posts", "Companies", "Runs")}}
    limits = load_limits(root)
    return {"limits": limits, "live_rows_now": cur, "new_rows": {"Shortlist": need["Shortlist"], **{t: add.get(t, 0) for t in ("Posts", "Companies", "Runs")}},
            "rows_after_next_publication": need, "session": plan["session"][:8], "mode": plan["mode"],
            "fits": {t: need[t] <= limits[t] for t in limits},
            "smallest_sufficient": {t: max(limits[t], need[t]) for t in limits},
            "other_violations": [v for v in plan["violations"] if "exceeds the approved maximum" not in v]}


def set_limits(changes, root=db.PROJECT_ROOT, note=""):
    """Write new row limits to config/live_publication.json. Only the CLI's explicit --operator-approved path calls this. Limits can be raised
    or lowered but never below the rows LIVE already holds (no deleting records to make capacity, no resetting history)."""
    path = Path(root) / "config" / "live_publication.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    current = load_limits(root)
    plan = make_plan(root)
    held = {t: plan["inspection"]["tabs"][t]["data_rows"] for t in TAB_ORDER}
    for tab, value in changes.items():
        if tab not in current:
            raise PublishRefused(f"unknown tab {tab}")
        if not isinstance(value, int) or value < max(held[tab], 1):
            raise PublishRefused(f"{tab}: a limit must be a whole number of at least the {held[tab]} rows LIVE already holds")
    new = {**current, **changes}
    doc["max_rows"] = new
    doc.setdefault("changes", []).append({"when": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "from": current, "to": new, "note": note[:200]})
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return current, new
