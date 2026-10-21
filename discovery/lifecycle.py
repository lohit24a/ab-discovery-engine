"""Lifecycle policy, field precedence and evidence labels (Day 11). Pure rules; the data lives in database.py.

Policy in one place (documented in the implementation guide):

* Only OUR observations create history. A repeated identical observation creates no event.
* 'Not Seen' / 'Closed' need a successful, COMPLETE, UNFILTERED source check from a source that may act as closure
  evidence (not a supplementary feed, not an investor board, not an aggregator) and that has sighted the job.
  A filtered scan (scope text 'key = value') is complete only inside its filter and never proves absence.
* A job listed on an investor board or aggregator is "observed on source", never "employer-confirmed open".
* 'Reopened' needs the same established job identity (the same source record, or an identical-identity sighting)
  reported by a source that may act as evidence of availability, and a fresh (not provider-retained) observation.
* Field precedence between sources is a fixed ranking (below); ties go to the most recently seen sighting, then to
  the source id, so the result never depends on arrival order.
"""

from datetime import datetime

NO_CLOSURE_EVIDENCE = "no-closure-evidence"
EMPLOYER_DIRECT = "employer-direct"          # sources.config_ref marker for a source read directly from the employer

# Highest authority first. Unknown sources rank below everything listed.
SOURCE_PRECEDENCE = (
    ("config_ref", EMPLOYER_DIRECT),       # read directly from the employer's own system (none exists yet)
    ("source_type", "ats_feed"),           # a feed of employer ATS postings (Feashliaa)
    ("source_type", "yc"),                 # YC company / job pages
    ("source_type", "investor_board"),     # investor and accelerator boards
    ("source_type", "aggregator"),         # JobSpy boards
    ("source_type", "manual"),
    ("source_type", "other"),
)

# Source types whose listings can never be evidence that a job is gone or employer-confirmed open on their own.
_NEVER_AUTHORITATIVE = ("investor_board", "aggregator")


def source_rank(source_type, config_ref=None):
    """Lower number = more authoritative."""
    for i, (field_name, value) in enumerate(SOURCE_PRECEDENCE):
        if (field_name == "config_ref" and config_ref == value) or (field_name == "source_type" and source_type == value):
            return i
    return len(SOURCE_PRECEDENCE)


def closure_capable(source_type, config_ref=None):
    """May this source's complete check be used as evidence that a job is no longer listed?"""
    if config_ref == NO_CLOSURE_EVIDENCE or source_type in _NEVER_AUTHORITATIVE:
        return False
    return True


def scope_is_filtered(scope):
    """Adapters write a filter into the scope text as 'key = value' (for example 'locations = Bengaluru...')."""
    return " = " in (scope or "")


def check_proves_absence(check, source_type, config_ref=None):
    """Is this source check usable as closure evidence at all (before asking whether the job was in its scope)?"""
    if check is None or check["outcome"] != "success" or not check["complete"]:
        return False, "the check was not successful and complete"
    if scope_is_filtered(check["scope"]):
        return False, "the check was filtered, so it is complete only inside its filter"
    if not closure_capable(source_type, config_ref):
        return False, "this source can never be closure evidence"
    return True, ""


def reopen_allowed(source_type, config_ref, provider_freshness):
    """May an observation from this source reopen a Closed / Not Seen job?"""
    if provider_freshness == "provider_retained":
        return False
    return closure_capable(source_type, config_ref)


def pick_field(candidates):
    """Deterministic cross-source choice for one field.

    candidates: iterable of (value, source_type, config_ref, last_seen_at, source_id). Blank values are ignored.
    Order: source rank, then newest last_seen_at, then source_id. Returns the chosen value or None."""
    usable = [c for c in candidates if c[0] is not None and str(c[0]).strip()]
    if not usable:
        return None
    best = min(usable, key=lambda c: (source_rank(c[1], c[2]), _neg(c[3]), c[4]))
    return best[0]


def _neg(ts):
    # newest first without parsing: invert by sorting on the negated ordinal of the ISO string
    return tuple(-ord(ch) for ch in (ts or ""))


# --------------------------------------------------------- evidence labels

CLASS_ORDER = ("employer_confirmed_open", "in_latest_check", "observed_earlier", "unknown", "historical")


def _parse(ts):
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def classify_observation(sighting, source, latest_check, job_state):
    """Evidence class of ONE sighting. Never says 'open' unless an employer-direct source supports it.

    sighting: last_seen_at, provider_freshness. source: source_type, config_ref.
    latest_check: the source's newest successful check row (started_at, complete, scope) or None."""
    if job_state in ("Closed", "Not Seen"):
        return "historical"
    if sighting["provider_freshness"] == "provider_retained" or job_state == "Unknown":
        return "historical"
    if source["source_type"] == "manual":
        return "unknown"                  # a person supplied it: not an observation of a source listing, never evidence of being open
    seen, started = _parse(sighting["last_seen_at"]), _parse(latest_check["started_at"]) if latest_check else None
    if not (seen and started):
        return "unknown"                  # no successful source check on record, or no usable timestamps
    if seen < started:
        return "observed_earlier"
    if (source["config_ref"] == EMPLOYER_DIRECT and latest_check["complete"] and not scope_is_filtered(latest_check["scope"])):
        return "employer_confirmed_open"
    return "in_latest_check"


def hiring_evidence_text(job_classes, source_names, latest_seen):
    """Source-accurate label for a company's jobs. job_classes: best class per job. Dates and sources are kept."""
    if not job_classes:
        return "No jobs observed"
    counts = {c: 0 for c in CLASS_ORDER}
    for c in job_classes:
        counts[c] += 1
    parts = []
    if counts["employer_confirmed_open"]:
        parts.append(f"{counts['employer_confirmed_open']} employer-confirmed open")
    if counts["in_latest_check"]:
        parts.append(f"{counts['in_latest_check']} listed in the latest source check (not employer-verified)")
    if counts["observed_earlier"]:
        parts.append(f"{counts['observed_earlier']} observed earlier, not in the latest check")
    if counts["unknown"]:
        parts.append(f"{counts['unknown']} with no successful source check on record (status unknown)")
    if counts["historical"]:
        parts.append(f"{counts['historical']} historical or provider-retained")
    text = f"{len(job_classes)} job(s) observed: " + "; ".join(parts)
    if source_names:
        text += f" | sources: {', '.join(sorted(source_names))}"
    if latest_seen:
        text += f" | last observed {latest_seen[:10]}"
    return text


def latest_run_checks(conn, kind="any"):
    """source_id -> the source's newest RUN, summarised: earliest start among its checks, complete only if all are, scopes
    joined, `partial` if any scope was partial. A source that checks one company at a time (YC job pages) therefore counts a
    job seen anywhere in the newest run as 'in the latest check', not only the last company scanned.

    kind="any"  : successful checks of every scope (the Day 11 behaviour).
    kind="jobs" : checks that cover JOB listings: scopes about companies ("companies:...", "companies/...") are ignored, and
                  a PARTIAL job scan counts (a job really was observed in it) but is flagged `partial`. A job must never be
                  judged against a company-directory scan of the same source."""
    outcomes = ("success",) if kind == "any" else ("success", "partial")
    marks = ",".join("?" * len(outcomes))
    rows = conn.execute(f"""SELECT source_id, run_id, scope, started_at, complete, outcome FROM source_checks
                            WHERE outcome IN ({marks}) ORDER BY started_at""", outcomes).fetchall()
    if kind == "jobs":
        rows = [r for r in rows if not (r["scope"] or "").startswith(("companies:", "companies/"))]
    newest = {}
    for r in rows:
        newest[r["source_id"]] = r["run_id"]
    out = {}
    for r in rows:
        if r["run_id"] != newest[r["source_id"]]:
            continue
        o = out.setdefault(r["source_id"], {"started_at": r["started_at"], "complete": r["complete"], "scope": r["scope"], "partial": False})
        o["started_at"] = min(o["started_at"], r["started_at"])
        o["complete"] = min(o["complete"], r["complete"])
        o["partial"] = o["partial"] or r["outcome"] == "partial"
        if r["scope"] not in o["scope"].split(" | "):
            o["scope"] = o["scope"] + " | " + r["scope"]
    return out
