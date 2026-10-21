"""Session-aware review lanes (Day 12 audit). Pure functions plus a tolerant, read-only history loader.

A high-scoring record that was already shown must not keep consuming a "new discovery" slot. Each candidate is placed in
at most one lane, in this order:

  changed               shown in an earlier session and its content fingerprint differs now
  new_discovery         never shown and qualified; recently recorded ones (`new_window_days`) sort first
  previously_unreviewed shown in an earlier session, no verdict recorded, unchanged
  uncertain_valuable    needs_review, never shown, decent score (surfaces strong-but-unconfirmed leads)

Slots come from ranking.yaml -> session.slots. Lanes never pad. Records with a stored verdict are never re-offered as new.
History is whatever earlier sessions recorded under `runs.summary["listed"]` ({record_id: fingerprint}); the TEST sync
writes it on a real (non dry-run) run. Until then every record is "never shown". No schema change is needed.
Ranking never writes anything: this module only reads.
"""

import hashlib
import json

LANES = ("new_discovery", "changed", "previously_unreviewed", "uncertain_valuable")   # display order


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


LANE_PRIORITY = ("changed", "previously_unreviewed", "uncertain_valuable", "new_discovery")


def _is_recent(r, as_of, window):
    first = r.get("local_first_seen")
    if not first:
        return False
    from .candidates import parse_when
    when = parse_when(first)
    return when is not None and (as_of - when).days <= window


def classify_lane(r, history, as_of, ranking, min_score):
    """The one lane a record belongs to, or None. Pure.

      changed               listed before, its fingerprint changed (also for a Yes/Maybe job: a change is worth another look)
      new_discovery         never listed, qualified (>= min_score); jobs you imported by hand sort first, then recent ones, but an older never-listed record
                            still qualifies, so a first session is not starved and a backlog is eventually reached
      previously_unreviewed listed before, unchanged, no verdict yet
      uncertain_valuable    never listed, held for review with a decent score
    A stored No is never offered; a stored Yes/Maybe is offered only when it changed.
    """
    verdict = r.get("verdict")
    if r["classification"] in ("excluded", "low_priority", "exploration") or verdict == "No":
        return None
    shown = history.get(r["id"])
    if shown is not None and shown != fingerprint(r):
        return "changed"
    if verdict in ("Yes", "Maybe"):
        return None
    if shown is not None:
        return "previously_unreviewed"
    if r["classification"] == "confirmed_eligible" and r["score"] >= min_score:
        return "new_discovery"
    if r["classification"] == "needs_review" and r["score"] >= (min_score // 2 + 10):
        return "uncertain_valuable"
    return None


def assign_lanes(results, history, as_of, ranking, min_score=None, max_per_employer=None):
    """-> {lane: [result, ...]}: each lane sorted (recent first inside new_discovery, then score) and limited to its slots,
    sharing one generic per-employer cap across lanes. Never pads with unqualified records; slots left unused by the
    changed / previously-unreviewed lanes are given to further qualified new discoveries. Deterministic."""
    cfg = ranking["session"]
    min_score = ranking["shortlist"]["minimum_score_to_list_jobs"] if min_score is None else min_score
    cap = ranking["shortlist"]["max_per_employer"] if max_per_employer is None else max_per_employer
    buckets = {name: [] for name in LANES}
    for r in results:
        lane = classify_lane(r, history, as_of, ranking, min_score)
        if lane:
            buckets[lane].append(r)
    window = cfg["new_window_days"]
    out, per_employer, spare = {}, {}, 0
    for lane in LANE_PRIORITY:
        slots = cfg["slots"][lane] + (spare if lane == "new_discovery" else 0)
        items = sorted(buckets[lane], key=lambda r: (not r.get("user_supplied"), not _is_recent(r, as_of, window), -r["score"], r["id"])
                       if lane == "new_discovery" else (-r["score"], r["id"]))
        chosen = []
        for r in items:
            if len(chosen) >= slots:
                break
            if per_employer.get(r["employer_key"], 0) >= cap:
                continue
            per_employer[r["employer_key"]] = per_employer.get(r["employer_key"], 0) + 1
            chosen.append(r)
        out[lane] = chosen
        if lane != "new_discovery":
            spare += cfg["slots"][lane] - len(chosen)         # unused slots of the other lanes go to new qualified discoveries, never to padding
    return {lane: out[lane] for lane in LANES}
