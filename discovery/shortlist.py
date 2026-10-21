"""The generated review shortlist (Day 12): ~20 jobs (Stream A) and ~5 company-only discoveries (Stream B).

Read-only: this module ranks and selects; it never writes SQLite or Sheets and never assigns a lifecycle state. The Sheets
sync consumes `Shortlist.rows`, `publish_job_ids` and `publish_company_ids` and decides what to write.

* Jobs come from the session lanes (new / changed / previously unreviewed / uncertain), so the same unchanged top records do
  not consume every session. The per-employer cap is generic and shared across lanes; nothing pads the list.
* Company-only discoveries are qualified companies that are not already represented by a shortlisted job, not yet listed, with
  no human verdict. They are ranked on their own evidence, independent of jobs.
* Explanations are the factual ones produced by scoring (source-reported, not employer-verified); nothing here upgrades them.
* Restricted sources (Indeed, Naukri, LinkedIn through JobSpy) are never publishable, whatever their score.
* Exploration candidates (no role family) are reported but not placed in the Sheet until the calibration review supports it.
"""

from dataclasses import dataclass, field

from . import session
from .ranking_preview import rank_all
from .scoring import select_exploration

RESTRICTED_SOURCES = ("jobspy_indeed", "jobspy_naukri", "jobspy_linkedin")
LANE_LABEL = {"new_discovery": "new", "changed": "changed", "previously_unreviewed": "unreviewed", "uncertain_valuable": "uncertain"}


@dataclass
class Shortlist:
    mode: str = "new"                                      # "new" (a new review session) | "continue" (reconcile the current one)
    session_id: str = ""                                   # the session being continued ("" for a new one: the sync run id is used)
    dropped: list = field(default_factory=list)            # (record_id, label, reason): removed from a continued session, not replaced
    as_of: str = ""
    lanes: dict = field(default_factory=dict)             # lane -> [job result]
    companies: list = field(default_factory=list)         # company-only discoveries
    exploration: list = field(default_factory=list)       # reported, not placed in the Sheet
    scores: dict = field(default_factory=dict)            # record_id -> (score, explanation) for every ranked record
    listed: dict = field(default_factory=dict)            # record_id -> fingerprint (stored in the sync run summary)
    rows: list = field(default_factory=list)              # Shortlist tab rows, in display order
    row_order: list = field(default_factory=list)
    publish_job_ids: set = field(default_factory=set)
    publish_company_ids: set = field(default_factory=set)
    skipped_restricted: int = 0
    alternatives: dict = field(default_factory=dict)      # offered job id -> ids of same-employer, same-title, same-location postings not listed separately

    @property
    def lane_of(self):
        return {r["id"]: lane for lane, items in self.lanes.items() for r in items}

    def summary(self, run_id):
        """What the sync run stores so the next sync can continue this exact session (no schema change)."""
        order = [row["record_id"] for row in self.rows]
        lanes = self.lane_of
        return {"session": {"id": self.session_id or run_id, "mode": self.mode, "order": order,
                            "lanes": {i: lanes.get(i, "company") for i in order},
                            "alternatives": {k: v for k, v in sorted(self.alternatives.items()) if k in order}},
                "listed": self.listed}

    @property
    def jobs(self):
        return [r for lane in session.LANES for r in self.lanes.get(lane, [])]


def _short(text, n=600):
    return text if len(text) <= n else text[: n - 1] + "…"


def current_session(conn):
    """The review session a plain sync continues: the newest finished sync run that recorded one. -> dict | None.

    A session is {"id", "order": [record ids], "lanes": {id: lane}}. The first TEST sync (before sessions were named) recorded only
    `listed`; its insertion order is the display order and lanes are re-derived, which reproduces the rows already in the sheet."""
    import json
    rows = conn.execute("SELECT run_id, summary FROM runs WHERE run_type = 'sync' AND result IN ('success', 'partial') ORDER BY started_at DESC").fetchall()
    for r in rows:
        try:
            data = json.loads(r[1] or "{}")
        except ValueError:
            continue
        if isinstance(data.get("session"), dict) and data["session"].get("order"):
            return {**data["session"], "legacy": False}
        if isinstance(data.get("listed"), dict) and data["listed"]:
            return {"id": r[0], "order": list(data["listed"]), "lanes": {}, "legacy": True}
    return None


def build(conn, profile, ranking, *, as_of=None, history=None, upstream=None, with_hints=True, ranked=None, verdict_overrides=None,
          new_session=False):
    """Rank everything and select the shortlist. `ranked` lets a caller reuse an existing rank_all() result."""
    jobs, companies, jscored, cscored, as_of = ranked or rank_all(conn, profile, ranking, as_of=as_of, with_hints=with_hints, upstream=upstream,
                                                                              verdict_overrides=verdict_overrides)
    history = session.load_history(conn) if history is None else history
    sl = profile.raw["shortlist"]
    current = None if new_session else current_session(conn)
    restricted = {j["job_id"] for j in jobs if set(j["source_ids"]) & set(RESTRICTED_SOURCES)}
    eligible = [r for r in jscored if r["id"] not in restricted]
    out = Shortlist(as_of=as_of.isoformat(), skipped_restricted=len(restricted))
    if current is not None:
        return _continue(out, current, jscored, cscored, restricted, profile, ranking, as_of)
    out.lanes = session.assign_lanes(eligible, history, as_of, ranking, alternatives=out.alternatives)
    chosen_jobs = out.jobs[: sl["jobs"]]
    keep = {r["id"] for r in chosen_jobs}
    out.lanes = {lane: [r for r in items if r["id"] in keep] for lane, items in out.lanes.items()}
    employers = {r["employer_key"] for r in chosen_jobs}

    pool = []
    for r in cscored.values():
        if r["classification"] != "confirmed_eligible" or r["score"] < ranking["shortlist"]["minimum_score_to_list_companies"]:
            continue
        if r["verdict"] in ("Yes", "No", "Maybe") or r["employer_key"] in employers:
            continue
        shown = history.get(r["id"])
        if shown is not None and shown == session.fingerprint(r):
            continue
        pool.append(r)
    pool.sort(key=lambda r: (-r["score"], r["id"]))
    seen_employers, picked = set(), []
    for r in pool:
        if len(picked) >= sl["companies_only"]:
            break
        if r["employer_key"] in seen_employers:
            continue
        seen_employers.add(r["employer_key"])
        picked.append(r)
    out.companies = picked
    out.exploration = select_exploration(jscored, sl["exploration_lane"], ranking["shortlist"]["max_per_employer"])

    for r in jscored:
        if r["score"] is not None and r["id"] not in restricted:
            out.scores[r["id"]] = (r["score"], _short(r["explanation"]))
    for r in cscored.values():
        if r["score"] is not None:
            out.scores[r["id"]] = (r["score"], _short(r["explanation"]))
    for lane in session.LANES:
        for r in out.lanes.get(lane, []):
            _add_row(out, "job", lane, r)
    for r in picked:
        _add_row(out, "company", None, r)
    return out


def _legacy_lane(r):
    return "new_discovery" if r["classification"] == "confirmed_eligible" else "uncertain_valuable"


def _continue(out, current, jscored, cscored, restricted, profile, ranking, as_of):
    """Reconcile the CURRENT session: the same records, re-evaluated against today's data and the sheet's verdicts.

    Nothing new is added and nothing is backfilled: a record that was rejected, closed, excluded or merged away is dropped and
    reported, and the list simply gets shorter. Starting the next batch is a separate, explicit act (new_session=True)."""
    out.mode, out.session_id = "continue", current["id"]
    out.alternatives = {k: list(v) for k, v in (current.get("alternatives") or {}).items()}   # the same rows keep the same note
    by_id = {r["id"]: r for r in jscored}
    by_id.update({r["id"]: r for r in cscored.values()})
    out.lanes = {lane: [] for lane in session.LANES}
    for rid in current["order"]:
        r = by_id.get(rid)
        label = (r.get("title") or r["company"]) if r else rid[:8]
        if r is None:
            out.dropped.append((rid, label, "no longer a canonical record"))
        elif rid in restricted:
            out.dropped.append((rid, label, "restricted source"))
        elif r["verdict"] == "No":
            out.dropped.append((rid, label, "you marked it No"))
        elif r["classification"] in ("excluded", "low_priority", "exploration"):
            out.dropped.append((rid, label, f"now {r['classification']}" + (f" ({r['hard_exclusion']})" if r.get("hard_exclusion") else "")))
        elif r["kind"] == "job":
            lane = current["lanes"].get(rid) or _legacy_lane(r)
            lane = lane if lane in session.LANES else _legacy_lane(r)
            out.lanes[lane].append(r)
            out.row_order.append(("job", lane, r))
        else:
            out.companies.append(r)
            out.row_order.append(("company", None, r))
    out.exploration = select_exploration(jscored, profile.raw["shortlist"]["exploration_lane"], ranking["shortlist"]["max_per_employer"])
    for r in jscored:
        if r["score"] is not None and r["id"] not in restricted:
            out.scores[r["id"]] = (r["score"], _short(r["explanation"]))
    for r in cscored.values():
        if r["score"] is not None:
            out.scores[r["id"]] = (r["score"], _short(r["explanation"]))
    for kind, lane, r in out.row_order:
        _add_row(out, kind, lane, r)
    return out


def _add_row(out, kind, lane, r):
    if kind == "job":
        alts = out.alternatives.get(r["id"]) or []
        note = (f"[{len(alts)} more posting(s) at this employer with the same title and location are kept in the database, not listed "
                "separately] " if alts else "")
        out.rows.append({"Type": f"Job ({LANE_LABEL[lane]})", "record_id": r["id"], "Name / Title": r["title"], "Company": r["company"],
                         "Score": str(r["score"]), "Explanation": _short(note + r["explanation"]), "Link": r.get("link", "")})
        out.publish_job_ids.add(r["id"])
    else:
        out.rows.append({"Type": "Company", "record_id": r["id"], "Name / Title": r["company"], "Company": r["company"],
                         "Score": str(r["score"]), "Explanation": _short(r["explanation"]), "Link": ""})
        out.publish_company_ids.add(r["id"])
    out.listed[r["id"]] = session.fingerprint(r)


def render(sl):
    head = (f"CONTINUING review session {sl.session_id[:8]} (same records, re-checked; nothing new is added; {len(sl.dropped)} dropped)"
            if sl.mode == "continue" else "NEW review session (the next batch; previously listed records are deprioritised)")
    lines = [head, f"Shortlist (read-only; reference time {sl.as_of}): {len(sl.jobs)} job(s) + {len(sl.companies)} company-only; "
             f"{len(sl.exploration)} exploration candidate(s) reported, not placed in the Sheet; {sl.skipped_restricted} restricted-source job(s) never publishable"]
    for lane in session.LANES:
        lines.append(f"  lane {lane}: {len(sl.lanes.get(lane, []))}")
    for rid, label, why in sl.dropped:
        lines.append(f"  dropped (not replaced): {label[:50]} [{rid[:8]}]: {why}")
    shown = {row["record_id"] for row in sl.rows}
    for rid, alts in sorted(sl.alternatives.items()):
        if rid in shown:
            lines.append(f"  [{rid[:8]}] stands for {len(alts)} more posting(s) with the same employer, title and location: "
                         + ", ".join(a[:8] for a in alts))
    for i, row in enumerate(sl.rows, 1):
        lines.append(f"  {i:2}. {row['Type']:<18} [{row['Score']}] {row['Company']} | {row['Name / Title']}\n      {row['Explanation']}")
    return lines
