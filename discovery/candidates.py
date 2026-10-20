"""Build ranking candidates from the database (Day 12). READ-ONLY: only SELECTs; nothing is written.

A candidate is a plain dict of recorded facts. Canonical identity is respected: a job or company that has been merged
into another record (identity_links, status confirmed) is not a candidate of its own, and a merged job carries every
location of its merged records (identity.effective_locations). Unresolved duplicate candidates are never suppressed;
they only add review hints.
"""

import json
from datetime import date, datetime, timezone

from . import identity, lifecycle
from .database import normalize_url as db_normalize
from .identity import effective_locations


def parse_when(value):
    """ISO timestamp or date -> aware datetime (UTC), or None."""
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text) if "T" in text or " " in text else datetime.combine(date.fromisoformat(text), datetime.min.time())
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def database_as_of(conn):
    """The newest observation time in the database: a frozen database therefore always ranks the same way."""
    row = conn.execute("SELECT MAX(last_seen_at) FROM sightings").fetchone()
    return parse_when(row[0]) or datetime(1970, 1, 1, tzinfo=timezone.utc)


def _latest_checks(conn):
    """Each source's newest run of JOB scans (company-directory scans never count for a job)."""
    return lifecycle.latest_run_checks(conn, "jobs")


def _verdicts(conn):
    out = {"job": {}, "company": {}}
    for r in conn.execute("SELECT job_id, company_id, decision, notes FROM verdicts"):
        key = ("job", r["job_id"]) if r["job_id"] else ("company", r["company_id"])
        out[key[0]][key[1]] = {"decision": r["decision"], "notes": r["notes"]}
    return out


def company_evidence(facts):
    """Recorded descriptive evidence for a company (no inference): description text, industries, YC and investor facts."""
    yc = facts.get("yc") or {}
    boards = (facts.get("investor_boards") or {}).get("boards") or {}
    texts, industries = [], []
    for k in ("one_liner", "long_description"):
        if yc.get(k):
            texts.append(str(yc[k]))
    industries += list(yc.get("industries") or [])
    for b in boards.values():
        if b.get("description"):
            texts.append(str(b["description"]))
        industries += list(b.get("industries") or [])
    locations = [str(yc["locations"])] if yc.get("locations") else []
    locations += [str(b["location"]) for b in boards.values() if b.get("location")]
    return {"text": " \n ".join(texts), "industries": industries, "locations": locations,
            "yc": bool(yc), "yc_batch": yc.get("batch"), "yc_stage": yc.get("stage"), "yc_team_size": yc.get("team_size"),
            "yc_status": yc.get("status"), "investor_boards": sorted(boards), "headcount": [b.get("headcount") for b in boards.values() if b.get("headcount")],
            "india_relevance": (facts.get("india_relevance") or {}).get("class"), "open_jobs_reported": [b.get("open_jobs_reported") for b in boards.values()]}


def duplicate_hints(conn, jobs, cos, per_record=3):
    """Review hints from unresolved identity candidates (tier B/D) and recorded relations. Hints only; nothing is suppressed."""
    job_hints, co_hints = {}, {}
    pairs = identity.job_pairs(jobs, cos, identity.rejected_pairs(conn, "job"))
    for p in pairs:
        if p.verdict.tier not in ("B", "D"):
            continue
        a, b = jobs[p.a], jobs[p.b]
        for me, other in ((a, b), (b, a)):
            if me.job_id == other.job_id:
                continue
            kind = "related company label" if p.verdict.tier == "D" else "possible duplicate"
            job_hints.setdefault(me.job_id, []).append(f"{kind}: {p.verdict.rule} with '{other.company_name}' job {other.job_id[:8]}")
    co_pairs, _ = identity.company_pairs(conn, jobs)
    for a, b, v in co_pairs:
        if v.tier in ("B", "D"):
            for me, other in ((a, b), (b, a)):
                co_hints.setdefault(me.company_id, []).append(f"{'related company candidate' if v.tier == 'D' else 'possible duplicate company'}: {v.rule} with '{other.name}' {other.company_id[:8]}")
    trim = lambda d: {k: sorted(set(v))[:per_record] for k, v in d.items()}
    return trim(job_hints), trim(co_hints)


def _alias_location_evidence(conn):
    """alias_job_id -> (location text, set of source ids that reported it) for jobs merged into another job, so the location a
    source reported is still attributable to that source after a merge."""
    out = {}
    if not identity.table_exists(conn, "identity_links"):
        return out
    for r in conn.execute("SELECT alias_id, evidence FROM identity_links WHERE entity_type = 'job' AND status = 'confirmed'"):
        moved = (json.loads(r["evidence"]).get("moved") or {}).get("sighting_ids", [])
        srcs = set()
        for sid in moved:
            row = conn.execute("SELECT source_id FROM sightings WHERE sighting_id = ?", (sid,)).fetchone()
            if row:
                srcs.add(row[0])
        loc = conn.execute("SELECT location FROM jobs WHERE job_id = ?", (r["alias_id"],)).fetchone()
        out[r["alias_id"]] = ((loc[0] or "").strip() if loc else "", srcs)
    return out


def load_candidates(conn, with_hints=True, upstream=None):
    """upstream: optional {normalised url: {"source_level":..., "recruiter_posted":...}} read OFFLINE from cached source data
    (see upstream_attrs); it is evidence the database does not store today. Read-only either way."""
    """-> (job candidates, company candidates, context). Read-only."""
    verdicts = _verdicts(conn)
    checks = _latest_checks(conn)
    alias_jobs, alias_cos = identity.alias_ids(conn, "job"), identity.alias_ids(conn, "company")
    merged_into = {}
    if identity.table_exists(conn, "identity_links"):
        for r in conn.execute("SELECT entity_type, canonical_id, alias_id FROM identity_links WHERE status = 'confirmed' ORDER BY created_at, link_id"):
            merged_into.setdefault((r["entity_type"], r["canonical_id"]), []).append(r["alias_id"])
    alias_loc = _alias_location_evidence(conn)

    companies = {}
    for r in conn.execute("SELECT company_id, name, domain, facts, identity_status FROM companies"):
        if r["company_id"] in alias_cos:
            continue
        facts = json.loads(r["facts"])
        companies[r["company_id"]] = {"company_id": r["company_id"], "name": r["name"], "domain": r["domain"], "facts": facts,
                                      "evidence": company_evidence(facts), "sources": set(), "n_jobs": 0, "merged_records": len(merged_into.get(("company", r["company_id"]), []))}
    for r in conn.execute("SELECT g.company_id, g.source_id FROM sightings g WHERE g.company_id IS NOT NULL"):
        if r["company_id"] in companies:
            companies[r["company_id"]]["sources"].add(r["source_id"])

    sources = {r["source_id"]: dict(r) for r in conn.execute("SELECT source_id, name, source_type, config_ref FROM sources")}
    sight = {}
    for r in conn.execute("""SELECT g.job_id, g.source_id, g.source_url, g.last_seen_at, g.first_seen_at, g.provider_first_seen_at, g.provider_freshness
                             FROM sightings g WHERE g.job_id IS NOT NULL"""):
        sight.setdefault(r["job_id"], []).append(r)

    order = {c: i for i, c in enumerate(lifecycle.CLASS_ORDER)}
    jobs = []
    for r in conn.execute("""SELECT j.job_id, j.company_id, j.title, j.location, j.lifecycle_state, j.experience_min_years, j.experience_max_years,
                                    j.experience_text, j.first_seen_at, j.last_seen_at, j.posting_url, j.requisition_id
                             FROM jobs j ORDER BY j.job_id"""):
        if r["job_id"] in alias_jobs or r["company_id"] not in companies:
            continue
        ss = sight.get(r["job_id"], [])
        classes, dated = [], []
        for s_ in ss:
            src = sources.get(s_["source_id"], {"source_type": "other", "config_ref": None})
            check = checks.get(s_["source_id"])
            classes.append((lifecycle.classify_observation(s_, src, check, r["lifecycle_state"]), s_["source_id"], check))
            if s_["provider_first_seen_at"]:
                dated.append({"source_type": src["source_type"], "source_id": s_["source_id"], "when": parse_when(s_["provider_first_seen_at"])})
        best = min(classes, key=lambda t: order[t[0]]) if classes else ("unknown", None, None)
        best_class, best_source, best_check = best
        merged = merged_into.get(("job", r["job_id"]), [])
        locs = effective_locations(conn, r["job_id"]) if merged else ([r["location"]] if r["location"] and r["location"].strip() else [])
        own_sources = sorted({s["source_id"] for s in ss})
        loc_evidence = [{"text": (r["location"] or "").strip(), "sources": own_sources}] if (r["location"] or "").strip() else []
        for alias in merged:
            text, srcs = alias_loc.get(alias, ("", set()))
            if text and text.lower() not in {e["text"].lower() for e in loc_evidence}:
                loc_evidence.append({"text": text, "sources": sorted(srcs)})
        attrs = {}
        if upstream:
            for s_ in ss:
                if s_["source_id"] == "feashliaa":
                    attrs = upstream.get(db_normalize(s_["source_url"]), attrs)
        c = companies[r["company_id"]]
        c["n_jobs"] += 1
        jobs.append({"job_id": r["job_id"], "company_id": r["company_id"], "company_name": c["name"], "title": r["title"], "locations": locs,
                     "location_evidence": loc_evidence,
                     "exp_min": r["experience_min_years"], "exp_max": r["experience_max_years"], "exp_text": r["experience_text"],
                     "state": r["lifecycle_state"], "availability": best_class,
                     "availability_source": best_source, "scan_partial": bool(best_check and (best_check.get("partial") or not best_check.get("complete"))),
                     "source_ids": sorted({s["source_id"] for s in ss}),
                     "source_types": sorted({(sources.get(s["source_id"]) or {}).get("source_type", "other") for s in ss}),
                     "dated": dated, "local_first_seen": r["first_seen_at"], "last_seen": max((s["last_seen_at"] for s in ss), default=r["last_seen_at"]),
                     "freshness_values": sorted({s["provider_freshness"] or "none" for s in ss}), "verdict": verdicts["job"].get(r["job_id"]),
                     "source_level": attrs.get("source_level"), "recruiter_posted": attrs.get("recruiter_posted"),
                     "merged_records": len(merged), "url": r["posting_url"], "hints": [], "description": ""})
    context = {"sources": sources, "checks": {k: dict(v) for k, v in checks.items()}}
    if with_hints:
        jr = identity.load_job_refs(conn)
        cr = identity.load_company_refs(conn, jr)
        jh, ch = duplicate_hints(conn, jr, cr)
        for j in jobs:
            j["hints"] = jh.get(j["job_id"], [])
            if j["merged_records"]:
                j["hints"] = [f"merged from {j['merged_records']} record(s); showing every recorded location"] + j["hints"]
        for cid, c in companies.items():
            c["hints"] = ch.get(cid, [])
    else:
        for c in companies.values():
            c["hints"] = []
    for c in companies.values():
        c["verdict"] = verdicts["company"].get(c["company_id"])
    return jobs, list(companies.values()), context
