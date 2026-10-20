"""Session-aware review lanes (Day 12 audit). Pure functions plus a tolerant, read-only history loader.

A high-scoring record that was already shown must not keep consuming a "new discovery" slot. Each candidate is placed in
at most one lane, in this order:

  changed               shown in an earlier session and its content fingerprint differs now
  new_discovery         first recorded locally within `new_window_days` of the reference time and never shown
  previously_unreviewed shown in an earlier session, no verdict recorded, unchanged
  uncertain_valuable    needs_review, never shown, decent score (surfaces strong-but-unconfirmed leads)

Slots come from ranking.yaml -> session.slots. Lanes never pad. Records with a stored verdict are never re-offered as new.
History is whatever earlier sessions recorded under `runs.summary["listed"]` ({record_id: fingerprint}); nothing records
that today, so until a publish step starts writing it every record is "never shown" (see docs/design/day12_ranking_audit.md).
Ranking never writes anything: this module only reads.
"""

import hashlib
import json

LANES = ("changed", "new_discovery", "previously_unreviewed", "uncertain_valuable")


def fingerprint(result):
    """Stable digest of the facts a reviewer would act on (not the score, which can move with weights)."""
    parts = [result["id"], result["title"] if result.get("title") else result.get("company", ""), "|".join(result.get("locations", [])),
             result.get("availability", ""), result.get("classification", "")]
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def load_history(conn):
    """{record_id: fingerprint} merged from every run summary that recorded a "listed" mapping, oldest first. Read-only."""
    history = {}
    try:
        rows = conn.execute("SELECT summary FROM runs ORDER BY started_at").fetchall()
    except Exception:
        return history
    for (summary,) in rows:
        try:
            listed = (json.loads(summary or "{}")).get("listed")
        except (TypeError, ValueError):
            continue
        if isinstance(listed, dict):
            history.update({str(k): str(v) for k, v in listed.items()})
    return history


def assign_lanes(results, history, as_of, ranking, min_uncertain_score=None):
    """-> {lane: [result, ...]} sorted best score first, capped by slots. `results` are scored job/company dicts."""
    cfg = ranking["session"]
    window = cfg["new_window_days"]
    floor = min_uncertain_score if min_uncertain_score is not None else ranking["jobs"]["thresholds"]["needs_review_min_score"] * 2
    lanes = {name: [] for name in LANES}
    for r in results:
        if r["classification"] in ("excluded", "low_priority", "exploration") or r.get("verdict") in ("Yes", "No"):
            continue
        shown = history.get(r["id"])
        fp = fingerprint(r)
        first = r.get("local_first_seen")
        recent = False
        if first:
            from .candidates import parse_when
            when = parse_when(first)
            recent = when is not None and (as_of - when).days <= window
        if shown is not None and shown != fp:
            lane = "changed"
        elif shown is None and recent and r["classification"] == "confirmed_eligible":
            lane = "new_discovery"
        elif shown is not None and r.get("verdict") is None:
            lane = "previously_unreviewed"
        elif shown is None and r["classification"] == "needs_review" and r["score"] >= floor:
            lane = "uncertain_valuable"
        else:
            continue
        lanes[lane].append(r)
    out = {}
    for lane, items in lanes.items():
        items.sort(key=lambda r: (-r["score"], r["id"]))
        out[lane] = items[: cfg["slots"][lane]]
    return out
