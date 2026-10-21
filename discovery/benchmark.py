"""Independent discovery benchmark evaluation (Day 13, revised methodology). READ-ONLY and deterministic.

The benchmark is an EVALUATION REFERENCE, never a discovery source: this module never writes to SQLite or Sheets, never creates a sighting, never
imports a benchmark record, and never changes a rule. It compares a frozen fixture with an existing database and reports, per record:

  identity   confirmed_exact | probable_review | no_match | unverifiable_identity     (ATS-aware; names/titles alone are never "confirmed")
  discovery  which sources hold it, when our engine first saw it, whether it pre-dates the fixture, what each source's scope/timing allows us to say
  ranking    actual result for a matched job, plus a clearly-labelled HYPOTHETICAL result for any record (what the approved rules would do with it)
  freshness  employer-page evidence vs database availability class, never conflated
  manual     whether the existing importer would accept it (dry-run plan only)

Matching hierarchy (Day 11): (1) job-specific ATS key scoped by platform and tenant -> confirmed; (2) alias/merge resolution to the canonical job;
(3) the same platform posting id without a tenant, or employer + title agreement -> probable, reported for review, never merged.
"""
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from . import candidates as cm
from . import identity, imports
from .idkeys import path_uuid, url_signature, workable_shortcode

GROUP_POSITIVE, GROUP_SENIOR, GROUP_NEGATIVE = "A_positive", "B_family_match_but_senior", "N_negative_control"
SOURCE_FAMILY = {"feashliaa": "ats_feed", "yc_jobs": "yc_jobs", "yc_oss": "yc_directory", "jobspy_indeed": "jobspy", "jobspy_naukri": "jobspy",
                 "jobspy_linkedin": "jobspy", "manual": "manual"}


def source_family(source_id):
    return SOURCE_FAMILY.get(source_id, "investor_boards" if source_id.startswith("inv_") else source_id)


# ------------------------------------------------------------------------------------ fixture

def load_fixture(path, expected_sha256=None):
    raw = Path(path).read_bytes()
    if expected_sha256 and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("the benchmark fixture changed after it was frozen")
    rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    ids = [r["benchmark_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate benchmark ids")
    return rows


def _split_ats(identifier):
    parts = (identifier or "").split(":")
    return tuple(p.lower() for p in parts) if len(parts) == 3 else ()


def record_keys(rec):
    """Job-specific scoped keys for a fixture record: from the direct employer/ATS URL, the original URL and the stable identifier."""
    keys, loose = set(), set()
    for url in (rec.get("direct_employer_or_ats_url", ""), rec.get("original_url", "")):
        url = (url or "").split(" ")[0]
        if url.startswith("http"):
            sig = url_signature(url)
            if sig.kind == "specific":
                keys.add(sig.key)
                loose.add((sig.platform, sig.job_key))
    ats = _split_ats(rec.get("stable_ats_identifier"))
    if ats:
        platform, tenant, job = ats
        loose.add((platform, job))
    return keys, loose


# --------------------------------------------------------------------------------- database

class Inventory:
    """Read-only index of the stored sightings, resolved to canonical jobs."""

    def __init__(self, conn):
        self.conn = conn
        self.alias_to_canonical = {r[0]: r[1] for r in conn.execute("SELECT alias_id, canonical_id FROM identity_links WHERE entity_type='job' AND status='confirmed'")}
        self.by_key, self.by_loose, self.by_employer = defaultdict(set), defaultdict(set), defaultdict(set)
        self.by_workable, self.by_uuid = defaultdict(set), defaultdict(set)
        self.sightings = defaultdict(list)
        self.job = {}
        for j in conn.execute("""SELECT j.job_id, j.title, j.location, j.company_id, c.name AS company, c.aliases, c.facts FROM jobs j JOIN companies c USING (company_id)"""):
            self.job[j["job_id"]] = j
        for s in conn.execute("SELECT job_id, source_id, source_url, first_seen_at, last_seen_at, provider_first_seen_at FROM sightings WHERE job_id IS NOT NULL"):
            jid = self.canonical(s["job_id"])
            self.sightings[jid].append(dict(s))
            sig = url_signature(s["source_url"])
            if sig.kind == "specific":
                self.by_key[sig.key].add(jid)
                self.by_loose[(sig.platform, sig.job_key)].add(jid)
            w, u = workable_shortcode(s["source_url"]), path_uuid(s["source_url"])
            if w:
                self.by_workable[w[1]].add(jid)
            if u:
                self.by_uuid[u[1]].add(jid)
        self.company_jobs = defaultdict(set)
        self.company_names = {}
        for jid, j in self.job.items():
            if jid in self.alias_to_canonical:
                continue
            for name in self._names(j):
                self.by_employer[name].add(jid)
            self.company_jobs[j["company_id"]].add(jid)
        self.company_sources = defaultdict(lambda: defaultdict(int))
        for r in conn.execute("SELECT company_id, source_id, COUNT(*) FROM sightings WHERE company_id IS NOT NULL GROUP BY 1, 2"):
            self.company_sources[r[0]][r[1]] += r[2]
        self.company_by_name = defaultdict(set)
        for c in conn.execute("SELECT company_id, name, aliases, facts FROM companies"):
            for name in self._names(c):
                self.company_by_name[name].add(c["company_id"])

    def canonical(self, job_id):
        seen = set()
        while job_id in self.alias_to_canonical and job_id not in seen:
            seen.add(job_id)
            job_id = self.alias_to_canonical[job_id]
        return job_id

    @staticmethod
    def _names(row):
        names = {identity.name_key(row["name"] if "name" in row.keys() else row["company"])}
        try:
            names |= {identity.name_key(a) for a in json.loads(row["aliases"] or "[]")}
            fz = (json.loads(row["facts"] or "{}").get("feashliaa") or {})
            if fz.get("board_code"):
                names.add(identity.name_key(fz["board_code"]))
        except (ValueError, TypeError):
            pass
        return {n for n in names if n}


def employer_keys(rec):
    keys = {identity.name_key(rec["employer"])}
    ats = _split_ats(rec.get("stable_ats_identifier"))
    if ats:
        keys.add(identity.name_key(ats[1]))
    keys.add(identity.name_key(rec["employer"]).replace(" ", ""))
    return {k for k in keys if k}


def _employer_hits(inv, rec):
    hit = set()
    for k in employer_keys(rec):
        hit |= inv.by_employer.get(k, set())
        for cid in inv.company_by_name.get(k, set()):
            hit |= inv.company_jobs.get(cid, set())
        for name, jobs in inv.by_employer.items():            # a board-code style name with no spaces
            if name.replace(" ", "") == k.replace(" ", ""):
                hit |= jobs
    return hit


def employer_company_ids(inv, rec):
    ids = set()
    for k in employer_keys(rec):
        ids |= inv.company_by_name.get(k, set())
        for name, cids in inv.company_by_name.items():
            if name.replace(" ", "") == k.replace(" ", ""):
                ids |= cids
    return ids


def match(inv, rec):
    """-> dict(match_class, canonical_ids, evidence). Never merges or writes anything."""
    keys, loose = record_keys(rec)
    exact = set()
    for k in keys:
        exact |= inv.by_key.get(k, set())
    if len(exact) == 1:
        hit = next((k for k in keys if inv.by_key.get(k)), None)
        return {"match_class": "confirmed_exact", "match_strength": "scoped_ats_key", "canonical_ids": sorted(exact),
                "evidence": ["job-specific ATS key equal (platform + tenant + posting id): " + ":".join(map(str, hit or ()))]}
    if len(exact) > 1:
        return {"match_class": "probable_review", "match_strength": "ambiguous_key", "canonical_ids": sorted(exact), "evidence": ["the job-specific key names several canonical jobs (ambiguous); left separate"]}
    employer = _employer_hits(inv, rec)
    ev, cand = [], set()
    for lk in loose:
        same_id = inv.by_loose.get(lk, set())
        if same_id & employer:
            cand |= same_id & employer
            ev.append(f"same {lk[0]} posting id {lk[1]} at the same employer, but the stored URL has a different or missing tenant form")
    if cand:
        return {"match_class": "probable_review", "match_strength": "same_posting_id_other_tenant_form", "canonical_ids": sorted(cand), "evidence": ev}
    strong, sev = set(), []
    for url in ((rec.get("direct_employer_or_ats_url", "") or "").split(" ")[0], rec.get("original_url", "")):
        w, u = workable_shortcode(url), path_uuid(url)
        if w and inv.by_workable.get(w[1], set()) & employer:
            strong |= inv.by_workable[w[1]] & employer
            sev.append(f"identical Workable shortcode {w[1]} (fixture {'tenant ' + w[0] if w[0] else 'tenant-less'}); the stored URL uses the other form, so the key cannot be compared directly")
        if u and inv.by_uuid.get(u[1], set()) & employer:
            strong |= inv.by_uuid[u[1]] & employer
            sev.append(f"identical posting UUID {u[1]} at a different host ({u[0]}); the UUID is globally unique but its namespace is not documented")
    if strong:
        return {"match_class": "probable_review", "match_strength": "strong_identifier_unscoped", "canonical_ids": sorted(strong), "evidence": sev + ["employer agrees; a person should confirm"]}
    tk = identity.title_key(rec["exact_title"])
    same_title = {j for j in employer if identity.title_key(inv.job[j]["title"]) == tk}
    if same_title:
        ev = ["same employer and identical title, but no shared job-specific key (a title and company alone never prove identity)"]
        ats = _split_ats(rec.get("stable_ats_identifier"))
        if ats and any(ats[2] in s["source_url"].lower() for j in same_title for s in inv.sightings.get(j, [])):
            ev.append(f"the posting id {ats[2]} appears in a stored URL, but that URL form has no {ats[0]} tenant to compare, so it stays probable")
        return {"match_class": "probable_review", "match_strength": "employer_and_title_only", "canonical_ids": sorted(same_title), "evidence": ev}
    if not keys and not loose:
        return {"match_class": "unverifiable_identity", "match_strength": "none", "canonical_ids": [], "evidence": ["the record carries no job-specific identifier, so absence cannot be proven"]}
    return {"match_class": "no_match", "match_strength": "none", "canonical_ids": [], "evidence": ["no stored job shares the job-specific key, and no same-employer job has this title"]}


def discovery_facts(inv, rec, ids, fixture_created_utc, checks, sources):
    """Source-level facts for the matched canonical jobs and for the employer overall."""
    out = {"sources": [], "manual_sources": [], "first_observed": "", "pre_existing": None, "sighting_urls": [], "employer_sources": {}, "employer_company_ids": [], "collection_notes": []}
    seen = []
    for jid in ids:
        for s in inv.sightings.get(jid, []):
            seen.append(s)
    out["sources"] = sorted({s["source_id"] for s in seen if s["source_id"] != "manual"})           # a manual entry is never automatic discovery
    out["manual_sources"] = sorted({s["source_id"] for s in seen if s["source_id"] == "manual"})
    out["sighting_urls"] = sorted({s["source_url"] for s in seen})
    auto = [s for s in seen if s["source_id"] != "manual"]
    if auto:
        out["first_observed"] = min(s["first_seen_at"] for s in auto)
        out["pre_existing"] = all(s["first_seen_at"] < fixture_created_utc for s in auto)
    cids = employer_company_ids(inv, rec)
    out["employer_company_ids"] = sorted(cids)
    es = defaultdict(int)
    for cid in cids:
        for src, n in inv.company_sources.get(cid, {}).items():
            es[src] += n
    for jid in _employer_hits(inv, rec):
        for s in inv.sightings.get(jid, []):
            es[s["source_id"]] += 1
    out["employer_sources"] = dict(sorted(es.items()))
    for src in sorted(set(out["employer_sources"]) | set(out["sources"])):
        c = checks.get(src)
        info = sources.get(src, {})
        out["collection_notes"].append(f"{src}: last sighting {info.get('last_sighting', '?')[:16]}; latest check {c['outcome'] if c else 'none'}"
                                       + (" (complete)" if c and c["complete"] else " (not complete)" if c else ""))
    return out


# --------------------------------------------------------------------------- ranking evaluation

def synthetic_candidate(rec, collected=False):
    """A candidate dict built ONLY from the fixture record, for a hypothetical (never stored, never ranked in production) evaluation."""
    lo, hi, text = imports.parse_experience(rec.get("experience_text", ""))
    if lo is None:                                               # fixture wording such as "7 or more years" or "8+ years experience"
        m = re.search(r"(\d+)\s*(?:\+|or more)\s*(?:years|yrs)", rec.get("experience_text", ""), re.I)
        if m:
            lo = float(m.group(1))
    segs = [s.strip() for s in (rec.get("location_text", "").replace("/", ";")).split(";") if s.strip()]
    return {"job_id": "benchmark:" + rec["benchmark_id"], "company_id": "benchmark-co", "company_name": rec["employer"], "title": rec["exact_title"], "locations": segs,
            "location_evidence": [], "exp_min": lo, "exp_max": hi, "exp_text": rec.get("experience_text") or None, "state": "New", "availability": "in_latest_check" if collected else "unknown",
            "source_ids": ["feashliaa"] if collected else [], "source_types": ["ats_feed"] if collected else ["other"], "dated": [], "scan_partial": False, "verdict": None, "hints": [], "description": "", "company_text": "",
            "company_evidence": {}, "also_company_candidate": False, "local_first_seen": None, "last_seen": None, "link": rec.get("original_url", "")}


def hypothetical(rec, profile, ranking, as_of, collected=False):
    """What the approved rules would do with this record IF it had been collected. collected=True adds the availability and source-quality points an
    ordinary feed listing earns (in the latest check, ATS feed); False leaves availability unknown. Never stored, never ranked in production."""
    from .scoring import score_job
    return score_job(synthetic_candidate(rec, collected), profile, ranking, as_of)


def ranking_context(conn, profile, ranking):
    """Real results for every canonical job plus three shortlist views, all read-only:
    clean (empty history, ignores session novelty), current (the recorded session as it stands), next (a new session with the real history)."""
    from . import shortlist as sl_mod
    from .ranking_preview import rank_all
    ranked = rank_all(conn, profile, ranking, with_hints=False)
    jobs, companies, jscored, cscored, as_of = ranked
    results = {r["id"]: r for r in jscored}
    clean = sl_mod.build(conn, profile, ranking, history={}, new_session=True, ranked=ranked)
    nxt = sl_mod.build(conn, profile, ranking, new_session=True, ranked=ranked)
    cur = sl_mod.current_session(conn)
    ranked_jobs = sorted((r for r in jscored if r["classification"] == "confirmed_eligible"), key=lambda r: (-r["score"], r["id"]))
    cutoff = clean.jobs[-1]["score"] if clean.jobs else None
    return {"results": results, "as_of": as_of, "clean_top": [r["id"] for r in clean.jobs], "clean_cutoff": cutoff, "next_top": [r["id"] for r in nxt.jobs],
            "current_session": list(cur["order"]) if cur else [], "n_qualified": len(ranked_jobs)}


def classify_ranking(result):
    """Short outcome label from a scored result."""
    if result is None:
        return "not_ranked"
    if result["classification"] == "excluded":
        return "excluded:" + str(result["hard_exclusion"])
    return result["classification"]


# ----------------------------------------------------------------------------- freshness

def freshness(rec, db_availability):
    """Employer-page evidence and database evidence stay separate; neither is called 'open'."""
    ev = rec.get("availability_evidence", "")
    lim = rec.get("availability_limits", "")
    if "CONFLICT" in lim:
        page = "conflicting"
    elif "AGGREGATOR-ONLY" in lim or "NOT fetched" in rec.get("direct_employer_or_ats_url", "") or "NOT" in lim and "employer page" in lim:
        page = "non_authoritative_only"
    elif "loaded" in ev.lower() and "apply" in ev.lower():
        page = "employer_page_loaded_with_apply_2026-10-21"
    elif "listed on the employer's board index" in ev:
        page = "employer_board_listing_2026-10-21"
    else:
        page = "uncertain"
    return {"employer_page_evidence": page,
            "database_availability_class": db_availability or "not_in_database",
            "note": "a page that loads with an apply button on one day is evidence of the page, not a verified open vacancy"}


# -------------------------------------------------------------------------- manual recovery

def manual_recovery(conn, rec):
    """Dry-run only: would the existing importer accept this record, and would it attach to an existing job?"""
    url = (rec.get("direct_employer_or_ats_url", "") or "").split(" ")[0]
    url = url if url.startswith("http") else rec.get("original_url", "")
    body = "url,company_hint,job_title_hint,location_hint,experience_hint,source,notes\n" + ",".join(
        '"' + (v or "").replace('"', "'") + '"' for v in (url, rec["employer"], rec["exact_title"], rec.get("location_text", ""), rec.get("experience_text", ""), "manual", "benchmark dry run"))
    try:
        parsed = imports.parse_links(body + "\n", None, origin="benchmark")
        items = imports.plan(conn, parsed)
    except imports.ImportRejected as exc:
        return {"manual_recoverable": False, "manual_action": "rejected", "manual_reason": str(exc)}
    if not items:
        return {"manual_recoverable": False, "manual_action": "none", "manual_reason": "no row"}
    it = items[0]
    return {"manual_recoverable": it.action in ("create", "attach", "refresh", "update_text"), "manual_action": it.action, "manual_reason": it.detail}


# ----------------------------------------------------------------------------- diagnosis

def diagnose(row):
    """-> [(category, 'demonstrated'|'hypothesis', evidence)] for one evaluated item. Conservative: a cause is 'demonstrated' only when data proves it."""
    out = []
    mc = row["match_class"]
    hyp, actual = row["hypothetical_outcome"], row["actual_outcome"]
    if mc in ("no_match", "unverifiable_identity"):
        es = row["employer_sources"]
        if mc == "unverifiable_identity":
            out.append(("unverifiable_original_evidence", "demonstrated", "no job-specific identifier in the record"))
        if not es:
            out.append(("upstream_source_does_not_include_the_employer", "demonstrated", "no stored company or job under this employer in any collected source"))
            out.append(("source_not_integrated_for_this_employer_ats", "hypothesis", f"the employer's board ({row['ats_platform']}) is not read by any enabled adapter for this employer"))
        else:
            out.append(("job_absent_from_sources_that_cover_the_employer", "demonstrated", "employer present in: " + ", ".join(es)))
            out.append(("collection_predates_posting_or_scan_incomplete", "hypothesis", "posting date unknown; " + "; ".join(row["collection_notes"][:3])))
    elif mc == "probable_review":
        out.append(("identity_form_mismatch_or_unconfirmed", "demonstrated", "; ".join(row["match_evidence"])))
    if mc != "no_match" and actual not in ("confirmed_eligible", "not_ranked") and row["group"] == GROUP_POSITIVE:
        out.append(("found_but_not_qualified", "demonstrated", f"actual ranking: {actual}"))
    if row["group"] == GROUP_POSITIVE and hyp not in ("confirmed_eligible",):
        out.append(("rules_would_not_qualify_it_even_if_discovered", "demonstrated", f"hypothetical ranking: {hyp}; {row['hypothetical_reasons']}"))
    return out


# ---------------------------------------------------------------------------------- driver

def evaluate(conn, rows, profile, ranking, fixture_created_utc, frozen_checks=None):
    inv = Inventory(conn)
    checks = {}
    for r in conn.execute("""SELECT source_id, outcome, complete, started_at FROM source_checks WHERE outcome <> 'skipped' ORDER BY started_at"""):   # a skipped check collected nothing
        checks[r["source_id"]] = dict(r)
    sources = {}
    for s in conn.execute("SELECT source_id, MAX(last_seen_at) AS last_sighting FROM sightings GROUP BY 1"):
        sources[s["source_id"]] = dict(s)
    ctx = ranking_context(conn, profile, ranking)
    out = []
    for rec in rows:
        m = match(inv, rec)
        facts = discovery_facts(inv, rec, m["canonical_ids"], fixture_created_utc, checks, sources)
        jid = m["canonical_ids"][0] if len(m["canonical_ids"]) == 1 else None
        actual = ctx["results"].get(jid) if jid else None
        hyp = hypothetical(rec, profile, ranking, ctx["as_of"])
        hyp2 = hypothetical(rec, profile, ranking, ctx["as_of"], collected=True)
        ats = _split_ats(rec.get("stable_ats_identifier"))
        row = {
            "benchmark_id": rec["benchmark_id"], "group": rec["group"], "employer": rec["employer"], "exact_title": rec["exact_title"],
            "match_class": m["match_class"], "match_strength": m["match_strength"], "canonical_job_ids": ";".join(m["canonical_ids"]), "match_evidence": m["evidence"],
            "automatic_sources": ";".join(facts["sources"]), "automatic_discovery": bool(facts["sources"]) and m["match_class"] == "confirmed_exact",
            "manual_sources": ";".join(facts["manual_sources"]), "source_families": ";".join(sorted({source_family(s) for s in facts["sources"]})),
            "first_observed_by_engine": facts["first_observed"], "in_database_before_benchmark": facts["pre_existing"],
            "employer_sources": facts["employer_sources"], "collection_notes": facts["collection_notes"],
            "ats_platform": ats[0] if ats else "unknown", "actual_outcome": classify_ranking(actual) if actual else "not_in_database",
            "actual_score": actual["score"] if actual else None, "actual_family": actual["family"] if actual else None,
            "actual_location_class": actual["location_category"] if actual else None, "actual_experience": actual["experience"] if actual else None,
            "actual_reason_codes": ";".join(actual["reason_codes"]) if actual else "", "actual_availability": actual["availability"] if actual else None,
            "in_clean_top20": bool(jid and jid in ctx["clean_top"]), "in_current_session": bool(jid and jid in ctx["current_session"]),
            "in_next_session_top20": bool(jid and jid in ctx["next_top"]),
            "hypothetical_outcome": classify_ranking(hyp), "hypothetical_score": hyp["score"], "hypothetical_family": hyp["family"], "hypothetical_location_class": hyp["location_category"],
            "hypothetical_experience": hyp["experience"], "hypothetical_reasons": ";".join(hyp["reason_codes"][:8]),
            "hypothetical_would_reach_top20": (hyp["classification"] == "confirmed_eligible" and ctx["clean_cutoff"] is not None and hyp["score"] >= ctx["clean_cutoff"]),
            "hypothetical_collected_outcome": classify_ranking(hyp2), "hypothetical_collected_score": hyp2["score"], "clean_top20_cutoff_score": ctx["clean_cutoff"],
            "hypothetical_collected_would_reach_top20": (hyp2["classification"] == "confirmed_eligible" and ctx["clean_cutoff"] is not None and hyp2["score"] >= ctx["clean_cutoff"]),
            "expected_family": rec.get("expected_role_family", ""),
        }
        row.update(freshness(rec, actual["availability"] if actual else None))
        row.update(manual_recovery(conn, rec))
        row["diagnosis"] = diagnose(row)
        out.append(row)
    return out, ctx


# ---------------------------------------------------------------------------------- metrics

def metrics(rows):
    pos = [r for r in rows if r["group"] == GROUP_POSITIVE]
    n = len(pos)
    conf = [r for r in pos if r["match_class"] == "confirmed_exact" and r["automatic_discovery"]]
    manual_only_match = [r for r in pos if r["match_class"] == "confirmed_exact" and not r["automatic_discovery"]]
    prob = [r for r in pos if r["match_class"] == "probable_review"]
    none = [r for r in pos if r["match_class"] == "no_match"]
    unv = [r for r in pos if r["match_class"] == "unverifiable_identity"]
    per_source = defaultdict(set)
    for r in conf:
        for s in r["automatic_sources"].split(";"):
            if s:
                per_source[s].add(r["benchmark_id"])
    fam = defaultdict(set)
    for r in conf:
        for f in r["source_families"].split(";"):
            if f:
                fam[f].add(r["benchmark_id"])
    unique = {f: sorted(ids - set().union(*[v for k, v in fam.items() if k != f])) for f, ids in fam.items()}
    pct = lambda a: round(100 * a / n, 1) if n else None
    qualified = [r for r in conf if r["actual_outcome"] == "confirmed_eligible"]
    review = [r for r in conf if r["actual_outcome"] == "needs_review"]
    excluded = [r for r in conf if r["actual_outcome"].startswith("excluded") or r["actual_outcome"] in ("low_priority", "exploration")]
    would = [r for r in pos if r["hypothetical_outcome"] == "confirmed_eligible"]
    neg = [r for r in rows if r["group"] == GROUP_NEGATIVE]
    return {
        "denominator_positive": n,
        "confirmed_exact": {"count": len(conf), "pct": pct(len(conf)), "ids": [r["benchmark_id"] for r in conf]},
        "probable_review": {"count": len(prob), "pct": pct(len(prob)), "ids": [r["benchmark_id"] for r in prob],
                            "by_strength": {r["benchmark_id"]: r["match_strength"] for r in prob}},
        "no_match": {"count": len(none), "pct": pct(len(none)), "ids": [r["benchmark_id"] for r in none]},
        "unverifiable_identity": {"count": len(unv), "ids": [r["benchmark_id"] for r in unv]},
        "matched_only_through_manual_entries": {"count": len(manual_only_match), "ids": [r["benchmark_id"] for r in manual_only_match]},
        "per_source_confirmed": {s: sorted(ids) for s, ids in sorted(per_source.items())},
        "per_source_family_confirmed": {f: sorted(ids) for f, ids in sorted(fam.items())},
        "unique_to_one_source_family": unique,
        "combined_unique_confirmed": len(conf),
        "overlap_note": "combined coverage counts each benchmark item once; per-source counts overlap and must not be added",
        "confirmed_found_ranking": {"qualified": len(qualified), "needs_review": len(review), "low_or_excluded": len(excluded),
                                    "in_clean_top20": sum(1 for r in conf if r["in_clean_top20"]), "in_current_session": sum(1 for r in conf if r["in_current_session"])},
        "hypothetical_if_discovered": {"would_qualify": len(would), "would_reach_top20": sum(1 for r in pos if r["hypothetical_would_reach_top20"]),
                                       "would_reach_top20_if_collected_normally": sum(1 for r in pos if r["hypothetical_collected_would_reach_top20"]),
                                       "of": n, "by_outcome": _count([r["hypothetical_outcome"] for r in pos])},
        "manual_recoverable": {"count": sum(1 for r in pos if r["manual_recoverable"]), "of": n},
        "manual_only_recovery": {"count": sum(1 for r in pos if r["manual_recoverable"] and r["match_class"] in ("no_match", "unverifiable_identity")), "of": n},
        "employer_page_evidence": _count([r["employer_page_evidence"] for r in pos]),
        "negative_controls": {"n": len(neg), "matched": sum(1 for r in neg if r["match_class"] == "confirmed_exact"),
                              "would_reach_shortlist": sum(1 for r in neg if r["hypothetical_outcome"] in ("confirmed_eligible",)),
                              "actual_outcomes": _count([r["actual_outcome"] for r in neg if r["match_class"] != "no_match"])},
        "senior_group": {"n": sum(1 for r in rows if r["group"] == GROUP_SENIOR),
                         "hypothetical": _count([r["hypothetical_outcome"] for r in rows if r["group"] == GROUP_SENIOR])},
    }


def _count(values):
    d = defaultdict(int)
    for v in values:
        d[v] += 1
    return dict(sorted(d.items()))


def write_results_csv(rows, path):
    cols = ["benchmark_id", "group", "employer", "exact_title", "match_class", "match_strength", "canonical_job_ids", "match_evidence", "automatic_discovery", "automatic_sources", "manual_sources", "source_families",
            "first_observed_by_engine", "in_database_before_benchmark", "employer_sources", "collection_notes", "actual_outcome", "actual_score", "actual_family",
            "actual_location_class", "actual_experience", "actual_reason_codes", "actual_availability", "in_clean_top20", "in_current_session", "in_next_session_top20",
            "hypothetical_outcome", "hypothetical_score", "hypothetical_family", "hypothetical_location_class", "hypothetical_experience", "hypothetical_reasons",
            "hypothetical_would_reach_top20", "hypothetical_collected_outcome", "hypothetical_collected_score", "hypothetical_collected_would_reach_top20", "clean_top20_cutoff_score", "employer_page_evidence", "database_availability_class", "manual_recoverable", "manual_action", "manual_reason", "diagnosis"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = {k: (json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v) for k, v in r.items()}
            w.writerow(flat)
