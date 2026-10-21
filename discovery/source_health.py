"""Source health (Day 14): what does each source's recent history actually let us say? READ-ONLY, built on the existing `source_checks` and `runs`.

It never closes, hides or ages a job. It only reports, per source, the newest collecting run and how far that run can be trusted:

  state            complete | complete_filtered | verified_empty | unverified_zero | incomplete | partial | blocked | network_error | schema_error |
                   failed_other | skipped_unchanged | interrupted | no_checks
  completeness     high (whole scope walked, unfiltered, not stale) | scoped (whole requested scope, but a filtered one) | low | none
  stale            the newest collecting run is older than the stale threshold
  may_close_jobs   ALWAYS False here; whether any source may ever be closure evidence is decided by lifecycle.closure_capable and the operator, not by health

A source that returned zero records is `verified_empty` only if the adapter proved it (complete=1); otherwise it is `unverified_zero`.
A skipped check collected nothing and is shown separately; it never replaces the last collecting run.
"""
import re
from datetime import datetime, timezone

from . import lifecycle

BLOCK = re.compile(r"\b(403|429)\b|blocked|captcha|forbidden|refus|rate.?limit|too many requests", re.I)
NETWORK = re.compile(r"timed? ?out|timeout|connection|urlerror|remote end|name or service|network|unreachable", re.I)
SCHEMA = re.compile(r"schema|unexpected|malformed|json|missing field|parse|decode|shape|not a list|structure|corrupt|no initialstate|has no .*(list|data)|embedded data|page token|csrf|changed", re.I)
DEFAULT_STALE_HOURS = {"ats_feed": 72, "yc": 168, "investor_board": 168, "aggregator": 24, "manual": 24 * 365, "other": 168}


def _parse(ts):
    try:
        d = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def classify_error(text):
    t = text or ""
    if BLOCK.search(t):
        return "blocked"
    if NETWORK.search(t):
        return "network_error"
    if SCHEMA.search(t):
        return "schema_error"
    return "failed_other"


def classify_check(check):
    """State of ONE check row (a dict/Row with outcome, complete, scope, record_count, error_info)."""
    outcome, complete, n = check["outcome"], bool(check["complete"]), check["record_count"] or 0
    if outcome == "skipped":
        return "skipped_unchanged"
    if outcome == "failed":
        return classify_error(check["error_info"])
    if outcome == "partial":
        # a company list derived only from job listings is never complete by construction; it is not a failure of the job scan
        return "derived_only" if "derived from job listings" in (check["scope"] or "") else "partial"
    if outcome == "success":
        if n == 0:
            return "verified_empty" if complete else "unverified_zero"
        if not complete:
            return "incomplete"
        return "complete_filtered" if lifecycle.scope_is_filtered(check["scope"]) else "complete"
    return "failed_other"


WORST = ["blocked", "network_error", "schema_error", "failed_other", "interrupted", "partial", "unverified_zero", "incomplete", "complete_filtered", "verified_empty", "complete",
         "derived_only", "skipped_unchanged", "no_checks"]


def source_health(conn, now=None, stale_hours=None):
    """-> list of dicts, one per registered source, sorted by source id."""
    now = now or datetime.now(timezone.utc)
    stale_hours = {**DEFAULT_STALE_HOURS, **(stale_hours or {})}
    out = []
    for src in conn.execute("SELECT source_id, name, source_type, enabled, config_ref FROM sources ORDER BY source_id"):
        rows = conn.execute("""SELECT c.*, r.result AS run_result FROM source_checks c JOIN runs r USING (run_id)
                               WHERE c.source_id = ? ORDER BY c.started_at, c.rowid""", (src["source_id"],)).fetchall()
        collecting = [c for c in rows if c["outcome"] != "skipped"]
        entry = {"source_id": src["source_id"], "type": src["source_type"], "enabled": bool(src["enabled"]), "state": "no_checks", "completeness": "none", "stale": None,
                 "age_hours": None, "latest_collecting_run": None, "scopes": [], "records_in_latest_run": 0, "errors": [], "last_skipped": None, "may_close_jobs": False, "closure_capable": False, "scope_count": 0, "state_counts": {}, "by_scope_kind": {},
                 "notes": []}
        skipped = [c for c in rows if c["outcome"] == "skipped"]
        if skipped:
            entry["last_skipped"] = skipped[-1]["started_at"]
        if not collecting:
            if skipped:
                entry["state"] = "skipped_unchanged"
                entry["notes"].append("only skipped checks: nothing was collected from this source")
            out.append(entry)
            continue
        run_id = collecting[-1]["run_id"]
        latest = [c for c in collecting if c["run_id"] == run_id]
        states = [classify_check(c) for c in latest]
        if any(c["run_result"] == "interrupted" for c in latest):
            states.append("interrupted")
        kinds = {}
        for c, st in zip(latest, states):
            kinds.setdefault((c["scope"] or "(whole source)").split(":")[0].split("/")[0], []).append(st)
        by_kind = {k: min(v, key=WORST.index) for k, v in sorted(kinds.items())}
        informative_states = [x for x in states if x != "derived_only"] or states
        states_for_rollup = informative_states
        zero_like = ("unverified_zero", "verified_empty")
        informative = [x for x in states_for_rollup if x not in zero_like] if len(states_for_rollup) > 1 and any(x not in zero_like for x in states_for_rollup) else states_for_rollup
        state = min(informative, key=WORST.index)                    # a multi-scope run (one scope per company) is judged on the scopes that returned data
        unverified = sum(1 for x in states if x == "unverified_zero")
        started = min(_parse(c["started_at"]) or now for c in latest)
        age = (now - started).total_seconds() / 3600
        entry.update({"state": state, "latest_collecting_run": run_id, "age_hours": round(age, 1), "scopes": sorted({c["scope"] or "(whole source)" for c in latest}),
                      "records_in_latest_run": sum(c["record_count"] or 0 for c in latest), "scope_count": len(latest), "by_scope_kind": by_kind,
                      "state_counts": {k: states.count(k) for k in sorted(set(states))}, "errors": sorted({(c["error_info"] or "")[:120] for c in latest if c["error_info"]})})
        entry["stale"] = age > stale_hours.get(src["source_type"], 168)
        if state == "complete":
            entry["completeness"] = "high" if not entry["stale"] else "low"
        elif state in ("complete_filtered", "verified_empty"):
            entry["completeness"] = "scoped" if not entry["stale"] else "low"
        elif state in ("partial", "incomplete", "unverified_zero", "interrupted"):
            entry["completeness"] = "low"
        if unverified and len(states) > 1:
            entry["notes"].append(f"{unverified} of {len(states)} scopes returned zero records without proving them empty (never read as 'no jobs')")
        entry["closure_capable"] = lifecycle.closure_capable(src["source_type"], src["config_ref"])
        if not entry["closure_capable"]:
            entry["notes"].append("this source can never be closure evidence, whatever its health")
        if state == "unverified_zero":
            entry["notes"].append("zero records were returned and the source did not prove the scope empty: do not read this as 'no jobs'")
        if state == "complete_filtered":
            entry["notes"].append("complete for a filtered scope only (" + "; ".join(entry["scopes"])[:100] + "): never global completeness")
        if "derived_only" in states:
            entry["notes"].append("its company list is derived from job listings only (no directory): by design it is never complete, and it does not affect the job scan's state")
        if state in ("partial", "incomplete"):
            entry["notes"].append("part of the scope was not walked: absence from this source proves nothing")
        if entry["stale"]:
            entry["notes"].append(f"newest collecting run is {entry['age_hours']} h old (threshold {stale_hours.get(src['source_type'], 168)} h)")
        if skipped and skipped[-1]["started_at"] > collecting[-1]["started_at"]:
            entry["notes"].append("a later run skipped this source because its snapshot was unchanged (nothing new was collected)")
        out.append(entry)
    return out


def render(rows):
    lines = ["Source health (read-only; no job is ever closed from this report):"]
    for r in rows:
        age = f"{r['age_hours']} h" if r["age_hours"] is not None else "-"
        lines.append(f"  {r['source_id']:<16} {r['state']:<18} completeness={r['completeness']:<6} age={age:<9} stale={r['stale']}  records={r['records_in_latest_run']}")
        if len(r.get("by_scope_kind", {})) > 1:
            lines.append("      scope kinds: " + ", ".join(f"{k}={v}" for k, v in r["by_scope_kind"].items()))
        for n in r["notes"]:
            lines.append(f"      - {n}")
        for e in r["errors"][:2]:
            lines.append(f"      ! {e}")
    quiet = ("complete", "complete_filtered", "verified_empty", "derived_only", "skipped_unchanged", "no_checks")
    attention = [f"{r['source_id']} ({r['state']})" for r in rows if r["state"] not in quiet or r["stale"]]
    if attention:
        lines.append("Sources needing attention: " + ", ".join(attention) + ". A job missing from them proves nothing.")
    else:
        lines.append("All sources with a collecting run were complete or verified-empty for their scope and not stale "
                     "(this still does not make any listing a confirmed vacancy).")
    return lines
