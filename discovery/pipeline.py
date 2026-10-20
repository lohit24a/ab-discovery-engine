"""Integrated, manually started discovery run: YC + Feashliaa collection, optional TEST sync, one report.

Run identity (no schema change): each user-initiated `run` creates ONE parent row in `runs`
(run_type 'discovery'). Every source execution keeps its own audit rows exactly as the adapters wrote them
(a run_type 'import' row plus its source_check); the pipeline links them by writing `parent_run_id` into the
child run's JSON summary. Consolidated numbers are built only from children linked to this parent, never by
adding up unrelated historical runs. Replaying after an interruption passes the same parent id
(`--resume`): sources that already finished successfully under that parent are not repeated, and because
every adapter is idempotent a repeat could not duplicate data anyway.

Safety: this module never closes or ages out anything, never targets LIVE, and the sheet publishing gates
(Feashliaa jobs stay out of Posts) live in the sync module and are untouched here.
"""

import json
import time
from dataclasses import dataclass, field

from . import database as db
from . import sheets_sync
from .adapters import feashliaa as fz
from .adapters import yc_companies as yc
from .schema import SCHEMA_VERSION

SOURCE_ORDER = ("yc", "feashliaa")
SOURCE_LABEL = {"yc": "YC companies (Stream B)", "feashliaa": "Feashliaa jobs (Stream A, supplementary)"}
ALLOWED_TARGETS = ("test",)   # LIVE is not authorized


class PipelineError(Exception):
    """The run cannot start safely. Nothing was changed."""


def parse_sources(text):
    names = [s.strip().lower() for s in (text or "").split(",") if s.strip()]
    if not names:
        raise PipelineError("No sources requested; use --sources yc,feashliaa")
    unknown = [n for n in names if n not in SOURCE_ORDER]
    if unknown:
        raise PipelineError(f"Unknown source(s): {', '.join(unknown)}. Available: {', '.join(SOURCE_ORDER)}")
    return [n for n in SOURCE_ORDER if n in names]   # fixed, documented order; no duplicates


@dataclass
class SourceOutcome:
    name: str
    status: str                    # success | partial | failed | skipped | dry-run | resumed
    child_run_id: str = ""
    message: str = ""
    seconds: float = 0.0
    metrics: dict = field(default_factory=dict)
    snapshot: dict = field(default_factory=dict)


@dataclass
class SyncOutcome:
    status: str                    # not_requested | preview | applied | read_error | write_failed
    message: str = ""
    run_id: str = ""
    counts: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    run_id: str
    dry_run: bool
    target: str
    sources: list
    started_at: str
    outcomes: list = field(default_factory=list)
    sync: SyncOutcome = None
    overall: str = "success"
    pending: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    integrity: str = "not checked"
    finished_at: str = ""
    seconds: float = 0.0
    resumed: bool = False

    def to_summary(self):
        return {"run_id": self.run_id, "dry_run": self.dry_run, "target": self.target, "sources": self.sources,
                "started_at": self.started_at, "finished_at": self.finished_at, "seconds": round(self.seconds, 1),
                "overall": self.overall, "integrity": self.integrity, "pending": self.pending,
                "warnings": self.warnings, "resumed": self.resumed,
                "outcomes": [vars(o) for o in self.outcomes], "sync": vars(self.sync) if self.sync else None}


# ---------------------------------------------------------------- source steps

def _link(conn, parent_id, child_id, source=None):
    """Tag a child run with its parent (and source name). Idempotent; only the child's JSON summary is touched."""
    if parent_id and child_id:
        with db.transaction(conn):
            conn.execute("UPDATE runs SET summary = json_set(summary, '$.parent_run_id', ?, '$.source', COALESCE(?, "
                         "json_extract(summary, '$.source'))) WHERE run_id = ?", (parent_id, source, child_id))


def _child_summary(conn, run_id):
    row = conn.execute("SELECT summary FROM runs WHERE run_id = ?", (run_id,)).fetchone() if run_id else None
    return json.loads(row[0]) if row else {}


def run_yc_step(conn, apply, fetchers, now):
    rep = yc.execute(conn, apply=apply, fetch=(fetchers or {}).get("yc", yc.fetch_json), now=now)
    snap = {}
    if rep.snapshot:
        snap = {"source_updated_at": rep.snapshot.source_updated_at, "retrieved_at": rep.snapshot.retrieved_at,
                "meta_count": rep.snapshot.meta_count}
    if rep.status == "failed":
        return "failed", rep.run_id, rep.error, {}, snap
    a, pv = rep.analysis, rep.preview
    metrics = {"records_scanned": len(rep.snapshot.records), "importable": len(a.companies),
               "malformed": len(a.malformed), "identity_conflicts": len(a.conflicts),
               "india_based": a.classes[yc.INDIA_BASED], "potentially_relevant": a.classes[yc.POTENTIALLY_RELEVANT],
               "unverified": a.classes[yc.UNVERIFIED], "no_india_evidence_yet": a.classes[yc.NO_INDIA_EVIDENCE],
               "new": pv["new"], "existing": pv["existing"], "would_refresh": pv["would_refresh"]}
    if not apply:
        return "dry-run", "", a.issue_summary(), metrics, snap
    r = rep.result
    metrics.update({"new": r.created, "refreshed": r.refreshed, "unchanged": r.unchanged})
    return r.result, r.run_id, a.issue_summary(), metrics, snap


def run_feashliaa_step(conn, apply, fetchers, cache_dir, prefilter, now):
    rep = fz.execute(conn, apply=apply, cache_dir=cache_dir, fetch=(fetchers or {}).get("feashliaa", fz.fetch_bytes),
                     prefilter=prefilter, now=now)
    m, mf = rep.metrics, rep.manifest
    snap = {"snapshot_id": mf.snapshot_id, "snapshot_at": mf.generated_at} if mf else {}
    metrics = {}
    if m:
        metrics = {"rows_scanned": m.scanned, "candidates_kept": m.kept, "malformed": m.malformed,
                   "duplicate_rows": m.duplicates, "chunks_read": f"{m.chunks_ok}/{m.chunks_total}",
                   "latest_collection": m.freshness.get(fz.LATEST, 0), "provider_retained": m.freshness.get(fz.RETAINED, 0),
                   "unknown_freshness": m.freshness.get(fz.UNKNOWN_FRESHNESS, 0),
                   "new_jobs_est": rep.new_jobs_est, "existing_jobs": rep.existing_jobs,
                   "new_boards_est": rep.new_companies_est, "possible_yc_matches": rep.possible_yc_matches_est,
                   "runtime_s": round(m.seconds), "peak_mb": round(m.peak_mb)}
    if rep.counts:
        c = rep.counts
        metrics.update({"jobs_created": c.jobs_created, "companies_created": c.companies_created,
                        "jobs_changed": c.jobs_changed, "jobs_unchanged": c.jobs_unchanged,
                        "marked_unknown": c.marked_unknown})
    message = "; ".join(rep.reasons)
    if rep.status == "failed":
        return "failed", rep.run_id, message, metrics, snap
    if rep.status == "already_processed":
        return "skipped", rep.run_id, message, metrics, snap
    if rep.status == "refused":
        return "partial", rep.run_id, message, metrics, snap
    return ("success" if apply else "dry-run"), rep.run_id, message, metrics, snap


# ------------------------------------------------------------------ pipeline

def _check_health(conn):
    version = db.get_schema_version(conn)
    if version != SCHEMA_VERSION:
        raise PipelineError(f"Database schema is version {version}, expected {SCHEMA_VERSION}. "
                            "Back it up, then run: python -m discovery init-db")
    quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    if quick != "ok":
        raise PipelineError(f"SQLite quick_check failed: {quick}")


def _overall(outcomes, sync):
    """success = every requested source succeeded, was skipped as already processed, or finished earlier in this
    run, and any requested sync did not fail; failed = every source failed; otherwise partial."""
    if outcomes and all(o.status == "dry-run" for o in outcomes):
        return "dry-run"
    if outcomes and all(o.status == "failed" for o in outcomes):
        return "failed"
    if any(o.status in ("failed", "partial") for o in outcomes) or (sync and sync.status in ("read_error", "write_failed")):
        return "partial"
    return "success"


def run_pipeline(conn, *, sources, target="test", dry_run=False, apply_sync=False, fetchers=None, backend_factory=None,
                 cache_dir=None, prefilter=None, now=None, resume_run_id=None) -> PipelineResult:
    """Run the requested sources, optionally preview/apply the TEST sync, and return one consolidated result."""
    if target is not None and target not in ALLOWED_TARGETS:
        raise PipelineError("Only --target test is allowed; LIVE is not authorized")
    if apply_sync and (dry_run or target is None):
        raise PipelineError("--apply-sync needs a real (non dry-run) run with --target test")
    sources = list(sources)
    _check_health(conn)
    clock = time.monotonic()
    now = now or db.utc_now()
    cache_dir = cache_dir or (db.PROJECT_ROOT / "data" / "cache" / "feashliaa")
    parent_id, finished_children, resumed = None, {}, False
    if not dry_run:
        if resume_run_id:
            row = conn.execute("SELECT result, run_type FROM runs WHERE run_id = ?", (resume_run_id,)).fetchone()
            if row is None or row["run_type"] != "discovery":
                raise PipelineError(f"No discovery run {resume_run_id} to resume")
            if row["result"] == "success":
                raise PipelineError("That run already finished successfully; start a new run instead")
            resumed = True
            for child in conn.execute("""SELECT run_id, result, summary FROM runs
                                         WHERE json_extract(summary, '$.parent_run_id') = ?""", (resume_run_id,)):
                s = json.loads(child["summary"])
                if child["result"] == "success" and s.get("source") in SOURCE_ORDER:
                    finished_children[s["source"]] = child["run_id"]
        parent_id = db.start_run(conn, "discovery", run_id=resume_run_id, now=now)
    result = PipelineResult(run_id=parent_id or "(dry run: not recorded)", dry_run=dry_run, target=target or "none",
                            sources=sources, started_at=now, resumed=resumed)
    try:
        for name in sources:
            if name in finished_children:
                result.outcomes.append(SourceOutcome(name, "resumed", finished_children[name],
                                                     "already completed earlier in this run; not repeated"))
                continue
            t0 = time.monotonic()
            try:
                if name == "yc":
                    status, child, msg, metrics, snap = run_yc_step(conn, not dry_run, fetchers, now)
                else:
                    status, child, msg, metrics, snap = run_feashliaa_step(conn, not dry_run, fetchers, cache_dir,
                                                                           prefilter, now)
            except (fz.SourceError, yc.SourceError, db.IdentityConflict) as exc:
                status, child, msg, metrics, snap = "failed", "", f"{type(exc).__name__}: {exc}", {}, {}
            except Exception as exc:   # one source's bug must not stop the other source
                status, child, msg, metrics, snap = "failed", "", f"unexpected {type(exc).__name__}: {str(exc)[:200]}", {}, {}
            _link(conn, parent_id, child, name)
            result.outcomes.append(SourceOutcome(name, status, child, msg, round(time.monotonic() - t0, 1), metrics, snap))
        result.sync = _sync_step(conn, result, target, dry_run, apply_sync, backend_factory, now, parent_id)
    except BaseException as exc:
        if parent_id:
            db.finish_run(conn, parent_id, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                          error_detail=f"{type(exc).__name__}: {str(exc)[:200]}", now=db.utc_now(),
                          summary=result.to_summary())
        raise
    _finalize(conn, result, parent_id, clock)
    return result


def _sync_step(conn, result, target, dry_run, apply_sync, backend_factory, now, parent_id):
    if target is None:
        return SyncOutcome("not_requested", "no spreadsheet target given")
    try:
        backend = (backend_factory or sheets_sync.open_backend)(target)
        preview = sheets_sync.sync_to_sheets(conn, backend, dry_run=True, now=now)
    except sheets_sync.SyncError as exc:
        return SyncOutcome("read_error", f"{exc}")   # nothing was written; sources are unaffected
    counts = {"posts_to_add": preview.appended.get("Posts", 0), "companies_to_add": preview.appended.get("Companies", 0),
              "runs_to_add": preview.appended.get("Runs", 0),
              "cells_to_update": sum(preview.cells_updated.values()), "verdicts_to_import": len(preview.verdicts_imported),
              "conflicts": len(preview.conflicts), "warnings": len(preview.warnings)}
    if not apply_sync:
        return SyncOutcome("preview", "TEST sync previewed, not applied", counts=counts)
    try:
        applied = sheets_sync.sync_to_sheets(conn, backend, dry_run=False, now=db.utc_now())
    except sheets_sync.SyncError as exc:
        return SyncOutcome("write_failed", f"{exc} (SQLite data is kept; re-run the sync)", counts=counts)
    _link(conn, parent_id, applied.run_id)
    counts["conflicts"] = len(applied.conflicts)
    return SyncOutcome("applied", "TEST sync applied", run_id=applied.run_id, counts=counts)


def _finalize(conn, result, parent_id, clock):
    result.overall = _overall(result.outcomes, result.sync)
    if result.dry_run and result.overall == "success":
        result.overall = "dry-run"   # a dry run never reports a real success
    pending = result.pending
    for o in result.outcomes:
        if o.status in ("failed", "partial"):
            pending.append(f"{SOURCE_LABEL[o.name]}: {o.status} ({o.message or 'see source check'}); re-run to retry")
    s = result.sync
    if s and s.status == "preview":
        c = s.counts
        if any(c.get(k) for k in ("posts_to_add", "companies_to_add", "runs_to_add", "cells_to_update", "verdicts_to_import")):
            pending.append(f"TEST sync not applied: {c['companies_to_add']} company rows, {c['posts_to_add']} job rows, "
                           f"{c['runs_to_add']} run rows to add, {c['cells_to_update']} cells to update. "
                           "Approve, then re-run with --apply-sync")
    if s and s.status in ("read_error", "write_failed"):
        pending.append(f"TEST sync {s.status.replace('_', ' ')}: {s.message}")
    if s and s.counts.get("conflicts"):
        pending.append(f"{s.counts['conflicts']} verdict/note conflict(s) need your decision")
    result.integrity = "ok"
    if not result.dry_run:
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        result.integrity = "ok" if quick == "ok" and not fk else f"PROBLEM (quick_check={quick}, fk_violations={len(fk)})"
        if result.integrity != "ok":
            result.warnings.append(result.integrity)
    result.finished_at = db.utc_now()
    result.seconds = time.monotonic() - clock
    if parent_id:
        final = {"success": "success", "partial": "partial", "failed": "failed"}.get(result.overall, "success")
        db.finish_run(conn, parent_id, final, now=result.finished_at, summary=result.to_summary(),
                      error_detail=None if final == "success" else "; ".join(result.pending)[:300])


# -------------------------------------------------------------------- report

NA = "Not available"


def _fmt(metrics, key):
    value = metrics.get(key)
    return NA if value is None else str(value)


def render(summary):
    """Human-readable consolidated report from a stored or fresh summary dict."""
    s = summary
    head = "DRY RUN (nothing recorded or changed)" if s["dry_run"] else f"Run {s['run_id']}"
    lines = [f"=== Discovery report: {head} ===",
             f"Overall: {s['overall'].upper()}{' (resumed)' if s.get('resumed') else ''} | target: {s['target']} | "
             f"started {s['started_at']} | finished {s.get('finished_at') or NA} | runtime ~{s.get('seconds', NA)} s",
             f"Sources attempted: {', '.join(s['sources'])}"]
    for o in s["outcomes"]:
        m, snap = o["metrics"], o["snapshot"]
        mark = {"skipped": "SKIPPED (already processed, not a failure)", "failed": "FAILED", "partial": "PARTIAL",
                "success": "SUCCESS", "dry-run": "DRY-RUN", "resumed": "ALREADY DONE (earlier in this run)"}[o["status"]]
        lines.append(f"- {SOURCE_LABEL[o['name']]}: {mark}" + (f" - {o['message']}" if o["message"] else ""))
        if o["child_run_id"]:
            lines.append(f"    source run: {o['child_run_id']}")
        if o["name"] == "yc":
            lines.append(f"    snapshot (publisher time): {snap.get('source_updated_at', NA)}")
            lines.append(f"    records scanned: {_fmt(m, 'records_scanned')} | importable: {_fmt(m, 'importable')} | malformed: "
                         f"{_fmt(m, 'malformed')} | identity conflicts: {_fmt(m, 'identity_conflicts')}")
            lines.append(f"    companies new: {_fmt(m, 'new')} | existing: {_fmt(m, 'existing')} | facts refreshed/to refresh: "
                         f"{_fmt(m, 'refreshed') if 'refreshed' in m else _fmt(m, 'would_refresh')} | unchanged: {_fmt(m, 'unchanged')}")
            lines.append(f"    India relevance: india_based {_fmt(m, 'india_based')}, potentially_relevant "
                         f"{_fmt(m, 'potentially_relevant')}, unverified {_fmt(m, 'unverified')}, no_india_evidence_yet "
                         f"{_fmt(m, 'no_india_evidence_yet')}")
        else:
            lines.append(f"    snapshot: {snap.get('snapshot_at', NA)} (id {snap.get('snapshot_id', NA)})")
            lines.append(f"    rows scanned: {_fmt(m, 'rows_scanned')} | candidates kept: {_fmt(m, 'candidates_kept')} | malformed: "
                         f"{_fmt(m, 'malformed')} | chunks read: {_fmt(m, 'chunks_read')}")
            lines.append(f"    freshness of kept rows: latest_collection {_fmt(m, 'latest_collection')}, provider_retained "
                         f"{_fmt(m, 'provider_retained')} (retained rows are NOT verified open; New = first seen by us)")
            created = m.get("jobs_created", m.get("new_jobs_est"))
            lines.append(f"    jobs {'created' if 'jobs_created' in m else 'expected new'}: {NA if created is None else created} | "
                         f"changed: {_fmt(m, 'jobs_changed')} | unchanged: {_fmt(m, 'jobs_unchanged')} | boards new: "
                         f"{_fmt(m, 'companies_created') if 'companies_created' in m else _fmt(m, 'new_boards_est')}")
    sy = s.get("sync")
    if sy:
        lines.append(f"TEST sync: {sy['status'].upper().replace('_', ' ')} - {sy['message']}")
        if sy["counts"]:
            c = sy["counts"]
            lines.append(f"    company rows {c.get('companies_to_add')}, job rows {c.get('posts_to_add')} (Feashliaa jobs stay "
                         f"unpublished), run rows {c.get('runs_to_add')}, cells {c.get('cells_to_update')}, "
                         f"verdicts to import {c.get('verdicts_to_import')}, conflicts {c.get('conflicts')}")
    lines.append(f"Database integrity: {s['integrity']}")
    lines.extend(f"Warning: {w}" for w in s["warnings"])
    lines.append("Pending actions: " + ("none" if not s["pending"] else ""))
    lines.extend(f"  * {p}" for p in s["pending"])
    return "\n".join(lines)


def load_summary(conn, run_id=None):
    if run_id:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ? AND run_type = 'discovery'", (run_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM runs WHERE run_type = 'discovery' ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
    if row is None:
        return None
    summary = json.loads(row["summary"])
    if not summary:   # a run that died before writing its summary
        summary = {"run_id": row["run_id"], "dry_run": False, "target": "unknown", "sources": [],
                   "started_at": row["started_at"], "finished_at": row["finished_at"] or "", "overall": row["result"],
                   "integrity": "not checked", "pending": ["run did not finish; re-run with --resume " + row["run_id"]],
                   "warnings": [row["error_detail"] or "no details"], "outcomes": [], "sync": None, "resumed": False}
    return summary
