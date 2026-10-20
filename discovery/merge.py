"""Reviewable identity decisions and the mechanism that applies a confirmed one (Day 11).

Nothing here runs automatically. `plan_merges` only proposes; `apply_job_merge` / `apply_company_merge` change a
database ONLY when called by code the operator has approved for that database. A merge never deletes a row:
the alias row, its status events and its history stay in place, its sightings (and, for a company, its jobs) move
to the canonical record, and an `identity_links` row records who/why. Replaying a merge is a no-op.

Refused (MergeRefused) instead of guessed: two human verdicts, two different domains or requisition ids, a chain of
merges, mismatching companies for a job merge, or any unique-key collision the move would cause.
"""

import json
import sqlite3
from dataclasses import dataclass

from . import database as db
from . import identity


class MergeRefused(Exception):
    """The merge would be unsafe or ambiguous; nothing was changed."""


@dataclass
class MergeResult:
    entity_type: str
    canonical_id: str
    alias_id: str
    already_applied: bool = False
    sightings_moved: int = 0
    jobs_moved: int = 0
    verdict_moved: bool = False


# ------------------------------------------------------------- link records

def _link_columns(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(identity_links)")}


def record_link(conn, entity_type, canonical_id, alias_id, status, tier, rule, evidence, decided_by, *, now=None,
                review_status=None, rule_version=None):
    """Create or update a link decision (with the rule version and review status once schema 6 exists).
    A rejected pair stays rejected unless explicitly re-decided."""
    now = now or db.utc_now()
    version = rule_version or identity.RULE_VERSION
    with db.transaction(conn):
        extra = "rule_version" in _link_columns(conn)
        review = review_status or ("reviewed" if status in ("confirmed", "rejected") else "unreviewed")
        row = conn.execute("SELECT * FROM identity_links WHERE entity_type = ? AND canonical_id = ? AND alias_id = ?",
                           (entity_type, canonical_id, alias_id)).fetchone()
        if row:
            if (row["status"], row["tier"], row["rule"]) != (status, tier, rule):
                conn.execute("UPDATE identity_links SET status = ?, tier = ?, rule = ?, evidence = ?, decided_by = ?, "
                             "updated_at = ? WHERE link_id = ?",
                             (status, tier, rule, json.dumps(evidence, sort_keys=True), decided_by, now, row["link_id"]))
                if extra:
                    conn.execute("UPDATE identity_links SET rule_version = ?, review_status = ? WHERE link_id = ?",
                                 (version, review, row["link_id"]))
            return row["link_id"], False
        link_id = db.new_id()
        conn.execute("""INSERT INTO identity_links (link_id, entity_type, canonical_id, alias_id, status, tier, rule,
                                                    evidence, decided_by, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (link_id, entity_type, canonical_id, alias_id, status, tier, rule,
                      json.dumps(evidence, sort_keys=True), decided_by, now, now))
        if extra:
            conn.execute("UPDATE identity_links SET rule_version = ?, review_status = ? WHERE link_id = ?", (version, review, link_id))
        return link_id, True


def _confirmed(conn, entity_type, alias_id):
    return conn.execute("SELECT * FROM identity_links WHERE entity_type = ? AND alias_id = ? AND status = 'confirmed'",
                        (entity_type, alias_id)).fetchone()


def _is_canonical_of_something(conn, entity_type, record_id):
    return conn.execute("SELECT 1 FROM identity_links WHERE entity_type = ? AND canonical_id = ? AND status = 'confirmed'",
                        (entity_type, record_id)).fetchone() is not None


def _members(conn, entity_type, record_id):
    """A record plus everything already merged into it."""
    out = {record_id}
    out |= {r[0] for r in conn.execute("SELECT alias_id FROM identity_links WHERE entity_type = ? AND canonical_id = ? AND status = 'confirmed'",
                                       (entity_type, record_id))}
    return out


def _check_negative(conn, entity_type, canonical_id, alias_id):
    """Refuse when a person said 'not the same' about ANY member of the two sides (merge chains cannot override it)."""
    rejected = identity.rejected_pairs(conn, entity_type)
    if not rejected:
        return
    left, right = _members(conn, entity_type, canonical_id), _members(conn, entity_type, alias_id)
    for x in left:
        for y in right:
            if frozenset((x, y)) in rejected:
                raise MergeRefused("a person marked these records (or records already merged into them) as not the same")


def _check_pair(conn, entity_type, canonical_id, alias_id):
    if canonical_id == alias_id:
        raise MergeRefused("a record cannot be merged into itself")
    table, key = ("jobs", "job_id") if entity_type == "job" else ("companies", "company_id")
    for rid in (canonical_id, alias_id):
        if conn.execute(f"SELECT 1 FROM {table} WHERE {key} = ?", (rid,)).fetchone() is None:
            raise MergeRefused(f"unknown {entity_type} {rid}")
    if _confirmed(conn, entity_type, canonical_id):
        raise MergeRefused("the canonical record is itself an alias; resolve the chain first")
    if _is_canonical_of_something(conn, entity_type, alias_id):
        raise MergeRefused("the alias is already the canonical record of other merges; resolve the chain first")
    _check_negative(conn, entity_type, canonical_id, alias_id)


def _move_verdict(conn, entity_type, canonical_id, alias_id, now):
    kind = "job_id" if entity_type == "job" else "company_id"
    mine = conn.execute(f"SELECT * FROM verdicts WHERE {kind} = ?", (canonical_id,)).fetchone()
    theirs = conn.execute(f"SELECT * FROM verdicts WHERE {kind} = ?", (alias_id,)).fetchone()
    if theirs is None:
        return False
    if mine is not None:
        raise MergeRefused("both records carry a human verdict or note; a person must reconcile them first")
    conn.execute(f"UPDATE verdicts SET {kind} = ?, updated_at = ? WHERE verdict_id = ?", (canonical_id, now, theirs["verdict_id"]))
    return True


def choose_canonical(conn, entity_type, *ids):
    """Deterministic canonical choice among 2+ records: a verified domain first (companies), then the earlier
    record, then the id. The same inputs always give the same answer."""
    ids = sorted(set(ids))
    if entity_type == "job":
        rows = {i: conn.execute("SELECT first_seen_at AS t FROM jobs WHERE job_id = ?", (i,)).fetchone() for i in ids}
        return min(ids, key=lambda i: (rows[i]["t"], i))
    rows = {i: conn.execute("SELECT domain, created_at AS t FROM companies WHERE company_id = ?", (i,)).fetchone() for i in ids}
    return min(ids, key=lambda i: (rows[i]["domain"] is None, rows[i]["t"], i))


# ------------------------------------------------------------------- jobs

def apply_job_merge(conn, canonical_id, alias_id, *, rule, evidence, decided_by, now=None):
    now = now or db.utc_now()
    result = MergeResult("job", canonical_id, alias_id)
    try:
        with db.transaction(conn):
            existing = _confirmed(conn, "job", alias_id)
            if existing and existing["canonical_id"] == canonical_id:
                result.already_applied = True
                return result
            if existing:
                raise MergeRefused("the alias is already merged into a different record")
            _check_pair(conn, "job", canonical_id, alias_id)
            keep = db.get_job(conn, canonical_id)
            gone = db.get_job(conn, alias_id)
            if keep["company_id"] != gone["company_id"]:
                raise MergeRefused("the jobs belong to different company records; reconcile the companies first")
            if keep["requisition_id"] and gone["requisition_id"] and keep["requisition_id"] != gone["requisition_id"]:
                raise MergeRefused("the jobs carry different employer requisition ids")
            old_verdict = db.get_verdict(conn, job_id=alias_id)
            moved = {"sighting_ids": [r[0] for r in conn.execute("SELECT sighting_id FROM sightings WHERE job_id = ?", (alias_id,))],
                     "verdict_id": old_verdict["verdict_id"] if old_verdict else None,
                     "requisition_id": gone["requisition_id"] if gone["requisition_id"] and not keep["requisition_id"] else None}
            result.verdict_moved = _move_verdict(conn, "job", canonical_id, alias_id, now)
            if gone["requisition_id"] and not keep["requisition_id"]:
                conn.execute("UPDATE jobs SET requisition_id = NULL WHERE job_id = ?", (alias_id,))
                conn.execute("UPDATE jobs SET requisition_id = ? WHERE job_id = ?", (gone["requisition_id"], canonical_id))
            result.sightings_moved = conn.execute("UPDATE sightings SET job_id = ? WHERE job_id = ?",
                                                  (canonical_id, alias_id)).rowcount
            conn.execute("""UPDATE jobs SET first_seen_at = MIN(first_seen_at, ?), last_seen_at = MAX(last_seen_at, ?),
                                   updated_at = ? WHERE job_id = ?""",
                         (gone["first_seen_at"], gone["last_seen_at"], now, canonical_id))
            record_link(conn, "job", canonical_id, alias_id, "confirmed", "A", rule, {**evidence, "moved": moved}, decided_by, now=now)
    except sqlite3.IntegrityError as exc:
        raise MergeRefused(f"the move would break a uniqueness rule ({exc}); nothing was changed") from exc
    return result


# -------------------------------------------------------------- companies

def apply_company_merge(conn, canonical_id, alias_id, *, rule, evidence, decided_by, now=None):
    now = now or db.utc_now()
    result = MergeResult("company", canonical_id, alias_id)
    try:
        with db.transaction(conn):
            existing = _confirmed(conn, "company", alias_id)
            if existing and existing["canonical_id"] == canonical_id:
                result.already_applied = True
                return result
            if existing:
                raise MergeRefused("the alias is already merged into a different record")
            _check_pair(conn, "company", canonical_id, alias_id)
            keep = db.get_company(conn, canonical_id)
            gone = db.get_company(conn, alias_id)
            if keep["domain"] and gone["domain"] and keep["domain"] != gone["domain"]:
                raise MergeRefused("the companies have different verified domains")
            old_verdict = db.get_verdict(conn, company_id=alias_id)
            moved = {"job_ids": [r[0] for r in conn.execute("SELECT job_id FROM jobs WHERE company_id = ?", (alias_id,))],
                     "sighting_ids": [r[0] for r in conn.execute("SELECT sighting_id FROM sightings WHERE company_id = ?", (alias_id,))],
                     "verdict_id": old_verdict["verdict_id"] if old_verdict else None,
                     "domain": gone["domain"] if gone["domain"] and not keep["domain"] else None}
            result.verdict_moved = _move_verdict(conn, "company", canonical_id, alias_id, now)
            if gone["domain"] and not keep["domain"]:
                conn.execute("UPDATE companies SET domain = NULL, identity_status = 'provisional' WHERE company_id = ?", (alias_id,))
                conn.execute("UPDATE companies SET domain = ?, identity_status = 'verified' WHERE company_id = ?",
                             (gone["domain"], canonical_id))
            result.jobs_moved = conn.execute("UPDATE jobs SET company_id = ?, updated_at = ? WHERE company_id = ?",
                                             (canonical_id, now, alias_id)).rowcount
            result.sightings_moved = conn.execute("UPDATE sightings SET company_id = ? WHERE company_id = ?",
                                                  (canonical_id, alias_id)).rowcount
            gone_facts, keep_facts = json.loads(gone["facts"]), json.loads(keep["facts"])
            patch = {}
            ib_keep, ib_gone = keep_facts.get("investor_boards") or {}, gone_facts.get("investor_boards") or {}
            if ib_gone:
                boards = {**(ib_gone.get("boards") or {}), **(ib_keep.get("boards") or {})}
                domains = sorted(set(ib_keep.get("reported_domains") or []) | set(ib_gone.get("reported_domains") or []))
                patch["investor_boards"] = {**ib_gone, **ib_keep, "boards": boards, "reported_domains": domains}
            for k in ("yc", "yc_jobs", "feashliaa", "india_relevance"):
                if k in gone_facts and k not in keep_facts:
                    patch[k] = gone_facts[k]
            db.update_company_details(conn, canonical_id, aliases_add=[gone["name"], *json.loads(gone["aliases"])],
                                      location=gone["location"] if not keep["location"] else None,
                                      facts_patch=patch, now=now)
            record_link(conn, "company", canonical_id, alias_id, "confirmed", "A", rule, {**evidence, "moved": moved}, decided_by, now=now)
    except sqlite3.IntegrityError as exc:
        raise MergeRefused(f"the move would break a uniqueness rule ({exc}); nothing was changed") from exc
    return result


# ---------------------------------------------------------------- reversal

def reverse_merge(conn, entity_type, alias_id, *, decided_by, now=None):
    """Undo one confirmed merge from the record it left: sightings, jobs, the human verdict and a moved domain go back
    to the alias; the link becomes 'possible' again (kept as history, never deleted). Added alias names and merged
    source facts on the canonical record, and widened first/last-seen times, are harmless and are not reverted."""
    now = now or db.utc_now()
    with db.transaction(conn):
        link = _confirmed(conn, entity_type, alias_id)
        if link is None:
            raise MergeRefused("no confirmed merge for this alias")
        moved = json.loads(link["evidence"]).get("moved") or {}
        canonical_id = link["canonical_id"]
        kind = "job_id" if entity_type == "job" else "company_id"
        if entity_type == "company":
            marks = ",".join("?" * len(moved.get("job_ids", [])))
            dependent = conn.execute(f"""SELECT COUNT(*) FROM identity_links l WHERE l.entity_type = 'job' AND l.status = 'confirmed'
                                         AND (l.alias_id IN ({marks}) OR l.canonical_id IN ({marks}))""", [*moved.get("job_ids", []), *moved.get("job_ids", [])]
                                     ).fetchone()[0] if moved.get("job_ids") else 0
            if dependent:
                raise MergeRefused(f"{dependent} confirmed job merge(s) depend on this company merge; reverse those first")
        for sid in moved.get("sighting_ids", []):
            conn.execute(f"UPDATE sightings SET {kind} = ? WHERE sighting_id = ? AND {kind} = ?", (alias_id, sid, canonical_id))
        if entity_type == "company":
            for jid in moved.get("job_ids", []):
                conn.execute("UPDATE jobs SET company_id = ?, updated_at = ? WHERE job_id = ? AND company_id = ?",
                             (alias_id, now, jid, canonical_id))
            if moved.get("domain"):
                conn.execute("UPDATE companies SET domain = NULL, identity_status = 'provisional' WHERE company_id = ? AND domain = ?",
                             (canonical_id, moved["domain"]))
                conn.execute("UPDATE companies SET domain = ?, identity_status = 'verified' WHERE company_id = ?", (moved["domain"], alias_id))
        elif moved.get("requisition_id"):
            conn.execute("UPDATE jobs SET requisition_id = NULL WHERE job_id = ? AND requisition_id = ?", (canonical_id, moved["requisition_id"]))
            conn.execute("UPDATE jobs SET requisition_id = ? WHERE job_id = ?", (moved["requisition_id"], alias_id))
        if moved.get("verdict_id"):
            conn.execute(f"UPDATE verdicts SET {kind} = ?, updated_at = ? WHERE verdict_id = ? AND {kind} = ?",
                         (alias_id, now, moved["verdict_id"], canonical_id))
        record_link(conn, entity_type, canonical_id, alias_id, "possible", link["tier"], "reversed:" + link["rule"],
                    {**json.loads(link["evidence"]), "reversed_at": now}, decided_by, now=now, review_status="reviewed")
    return canonical_id


# ---------------------------------------------------------------- planning

def _clusters(edges):
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b, _ in edges:
        parent[find(a)] = find(b)
    out = {}
    for x in list(parent):
        out.setdefault(find(x), set()).add(x)
    return list(out.values())


def _cluster_conflict(conn, entity_type, members, refs):
    """Why a cluster must NOT be merged as a whole, or None. Transitivity is never assumed: a hard conflict anywhere in
    the cluster stops the whole cluster, even if every individual pair looked fine."""
    rejected = identity.rejected_pairs(conn, entity_type)
    ids = sorted(members)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if frozenset((ids[i], ids[j])) in rejected:
                return "a person marked two members of this cluster as not the same"
    kind = "job_id" if entity_type == "job" else "company_id"
    marks = ",".join("?" * len(ids))
    if conn.execute(f"SELECT COUNT(*) FROM verdicts WHERE {kind} IN ({marks})", ids).fetchone()[0] > 1:
        return "more than one member carries a human verdict or note"
    if entity_type == "company":
        domains = {refs[i].domain for i in ids if i in refs and refs[i].domain}
        if len(domains) > 1:
            return "members have different verified domains"
        reported = [refs[i].reported_domains for i in ids if i in refs and refs[i].reported_domains]
        if any(not (reported[0] & r) for r in reported[1:]) and len(reported) > 1:
            return "members report different domains (a chain through a shared member would hide the conflict)"
    else:
        reqs = {refs[i].requisition_id for i in ids if i in refs and refs[i].requisition_id}
        if len(reqs) > 1:
            return "members carry different employer requisition ids"
    return None


def plan_merges(conn, promotions=None):
    """Proposals only (nothing is applied). Tier A pairs are grouped into clusters so that no merge chains; every
    non-canonical member points straight at the deterministic canonical record. Companies come first: a job merge
    is 'ready' only when both jobs will share one company record after the company merges.

    promotions: optional list of {'a': company_id, 'b': company_id, 'rule': ..., 'reasons': [...]} that the operator has
    approved from identity_review (they are never added implicitly).

    Returns {'rule_version', 'companies', 'jobs', 'blocked', 'conflicts', 'review': {'B': n, 'D': n}}."""
    jobs = identity.load_job_refs(conn)
    cos = identity.load_company_refs(conn, jobs)
    pairs = identity.job_pairs(jobs, cos, identity.rejected_pairs(conn, "job"))
    co_pairs, _ = identity.company_pairs(conn, jobs)
    done = {"job": identity.alias_ids(conn, "job"), "company": identity.alias_ids(conn, "company")}
    review = {"B": 0, "D": 0}
    for t in [v.tier for _, _, v in co_pairs] + [p.verdict.tier for p in pairs]:
        if t in review:
            review[t] += 1

    co_edges = [(a.company_id, b.company_id, v) for a, b, v in co_pairs
                if v.tier == "A" and not ({a.company_id, b.company_id} & done["company"])]
    for pr in promotions or []:
        if not ({pr["a"], pr["b"]} & done["company"]):
            co_edges.append((pr["a"], pr["b"], identity.Verdict("A", pr["rule"], pr["reasons"])))
    co_plan, conflicts, canon_company, blocked_company = [], [], {}, set()
    for members in _clusters(co_edges):
        why = _cluster_conflict(conn, "company", members, cos)
        if why:
            conflicts.append({"entity": "company", "members": sorted(members), "reason": why})
            blocked_company |= members
            continue
        canon = choose_canonical(conn, "company", *members)
        for m in sorted(members - {canon}):
            ev = [{"rule": v.rule, "reasons": v.reasons} for a, b, v in co_edges if m in (a, b)]
            co_plan.append({"canonical": canon, "alias": m, "cluster_size": len(members), "evidence": ev[:2]})
        for m in members:
            canon_company[m] = canon

    job_edges = [(p.a, p.b, p) for p in pairs if p.verdict.tier == "A" and not ({p.a, p.b} & done["job"])]
    job_plan, blocked = [], []
    for members in _clusters(job_edges):
        why = _cluster_conflict(conn, "job", members, jobs)
        if why:
            conflicts.append({"entity": "job", "members": sorted(members), "reason": why})
            continue
        canon = choose_canonical(conn, "job", *members)
        for m in sorted(members - {canon}):
            ev = [{"rule": p.verdict.rule, "reasons": p.verdict.reasons} for a, b, p in job_edges if m in (a, b)]
            cm = canon_company.get(jobs[m].company_id, jobs[m].company_id)
            cc = canon_company.get(jobs[canon].company_id, jobs[canon].company_id)
            entry = {"canonical": canon, "alias": m, "cluster_size": len(members), "evidence": ev[:2],
                     "ready": cm == cc and jobs[m].company_id not in blocked_company and jobs[canon].company_id not in blocked_company}
            (job_plan if entry["ready"] else blocked).append(entry)
    return {"rule_version": identity.RULE_VERSION, "companies": co_plan, "jobs": job_plan, "blocked": blocked,
            "conflicts": conflicts, "review": review}
