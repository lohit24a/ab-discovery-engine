"""Offline ranking preview (Day 12 checkpoint 1). READ-ONLY: it reads a database and writes nothing to it.

It scores every canonical job (Stream A) and company (Stream B) with the proposed profile and weights, then reports counts
and a shortlist preview. The preview is a design aid: weights are proposals, nothing is published to Sheets, and no
posting is described as currently open (availability is always shown as observed-on-source, not employer-verified).
"""

import collections

from . import candidates as cand_mod
from .scoring import score_company, score_job, select_exploration, select_shortlist

REGRESSION_EMPLOYERS = ("mindtickle", "fiserv", "rsm", "xendit")
NOTES = [
    "Weights and role terms are PROPOSALS awaiting operator review (config/ranking.yaml, config/search_profile.yaml).",
    "Job descriptions are not stored for the automated sources, so responsibilities and skills cannot be scored for them; only titles, "
    "locations, recorded experience and company descriptions are used.",
    "Experience years exist for only a handful of jobs; unknown experience is neutral, never senior.",
    "'Observed on source' is not 'open': no listing here is employer-confirmed, and a recent investor-board observation proves nothing about a vacancy.",
    "Lifecycle states are never changed by ranking; automatic Closed/Not Seen policy remains pending the operator's decision.",
]


def _counts(items, key):
    return dict(sorted(collections.Counter(key(i) for i in items).items(), key=lambda kv: (-kv[1], str(kv[0]))))


def _slim(r, with_components=True):
    out = {k: r.get(k) for k in ("id", "company", "title", "classification", "score", "family", "role_strength", "location_category", "availability",
                                 "experience", "sources", "verdict", "explanation", "hints", "review_reasons", "reason_codes", "n_jobs", "missing_evidence", "posting_date")
           if k in r}
    if with_components:
        out["components"] = r.get("components")
    return out


def build_preview(conn, profile, ranking, as_of=None, with_hints=True, top=None, case_job_ids=(), upstream=None):
    jobs, companies, ctx = cand_mod.load_candidates(conn, with_hints=with_hints, upstream=upstream)
    as_of = as_of or (cand_mod.parse_when(ranking["reference"]["as_of"]) if ranking["reference"]["as_of"] else None) or cand_mod.database_as_of(conn)
    sl = profile.raw["shortlist"]
    n_jobs_top, n_co_top = (top or {}).get("jobs", sl["jobs"]), (top or {}).get("companies", sl["companies_only"])

    cscored = {c["company_id"]: score_company(c, profile, ranking) for c in companies}
    by_co = {c["company_id"]: c for c in companies}
    for j in jobs:
        co = by_co[j["company_id"]]
        j["company_text"] = co["evidence"]["text"] + " " + " ".join(co["evidence"]["industries"])
        j["company_evidence"] = co["evidence"]
        j["also_company_candidate"] = cscored[j["company_id"]]["classification"] == "confirmed_eligible"
    jscored = [score_job(j, profile, ranking, as_of) for j in jobs]

    raw_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    raw_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    clist = list(cscored.values())
    live = [r for r in jscored if r["classification"] != "excluded"]

    sj, qj, dj = select_shortlist(jscored, n_jobs_top, ranking["shortlist"]["minimum_score_to_list_jobs"], ranking["shortlist"]["max_per_employer"],
                                  sl["allow_fill_with_needs_review"])
    sc, qc, dc = select_shortlist(clist, n_co_top, ranking["shortlist"]["minimum_score_to_list_companies"], ranking["shortlist"]["max_per_employer"], False)
    pool = [r for r in jscored if r["classification"] == "confirmed_eligible"]
    src_of = lambda rs: collections.Counter(s for r in rs for s in r["sources"])
    shortlist_sources = src_of(sj)
    pool_sources = src_of(pool)
    explore = select_exploration(jscored, sl["exploration_lane"], ranking["shortlist"]["max_per_employer"])
    near_misses = sorted((r for r in jscored if r["classification"] == "excluded" and r["role_strength"] == "strong" and r["hard_exclusion"] == "LOCATION_INCOMPATIBLE"),
                         key=lambda r: r["id"])[:5]

    def regression(names):
        out = {}
        for n in names:
            rows = [r for r in jscored if r["employer_key"] == n]
            out[n] = [{"id": r["id"][:8], "title": r["title"], "locations": r["locations"], "location_category": r["location_category"],
                       "classification": r["classification"], "score": r["score"], "family": r["family"], "hints": r["hints"][:1]} for r in rows[:6]]
        return out

    return {
        "as_of": as_of.isoformat(), "profile_status": profile.status, "ranking_status": ranking["status"], "notes": NOTES,
        "considered": {"job_rows_in_database": raw_jobs, "distinct_canonical_jobs": len(jobs), "company_rows_in_database": raw_companies,
                       "distinct_canonical_companies": len(companies)},
        "jobs": {
            "by_classification": _counts(jscored, lambda r: r["classification"]),
            "hard_excluded_by_primary_reason": _counts([r for r in jscored if r["classification"] == "excluded"], lambda r: r["hard_exclusion"]),
            "hard_exclusion_reasons_applicable_including_overlaps": dict(collections.Counter(x for r in jscored for x in r["all_exclusions"])),
            "needs_review_reasons": dict(sorted(collections.Counter(x for r in jscored if r["classification"] == "needs_review" for x in r["review_reasons"]).items(), key=lambda kv: -kv[1])),
            "by_role_family_not_excluded": _counts(live, lambda r: r["family"]),
            "by_role_family_confirmed": _counts(pool, lambda r: r["family"]),
            "by_location_category_all": _counts(jscored, lambda r: r["location_category"]),
            "by_location_eligibility_all": _counts(jscored, lambda r: r["location_eligibility"]),
            "by_availability_all": _counts(jscored, lambda r: r["availability"]),
            "by_availability_confirmed": _counts(pool, lambda r: r["availability"]),
            "with_unresolved_identity_hint": sum(1 for r in jscored if r["hints"]),
            "shortlist": [_slim(r) for r in sj],
            "exploration_lane": [_slim(r, False) for r in explore],
            "exploration_pool_size": sum(1 for r in jscored if r["classification"] == "exploration"),
            "missing_evidence_counts": dict(collections.Counter(x for r in live for x in r["missing_evidence"])),
            "missing_evidence_in_confirmed": dict(collections.Counter(x for r in pool for x in r["missing_evidence"])),
            "review_queue": [_slim(r, False) for r in qj],
            "deferred_close_alternatives": dj,
            "excluded_near_misses_strong_role_but_foreign_only": [_slim(r, False) for r in near_misses],
            "shortlist_size_returned": len(sj), "shortlist_size_target": n_jobs_top,
            "unique_employers_in_shortlist": len({r["employer_key"] for r in sj}),
            "source_share_of_shortlist": dict(shortlist_sources), "source_share_of_confirmed_pool": dict(pool_sources),
            "confirmed_pool_size": len(pool), "unique_employers_in_confirmed_pool": len({r["employer_key"] for r in pool}),
        },
        "companies": {
            "by_classification": _counts(clist, lambda r: r["classification"]),
            "hard_excluded_by_primary_reason": _counts([r for r in clist if r["classification"] == "excluded"], lambda r: r["hard_exclusion"]),
            "needs_review_reasons": dict(sorted(collections.Counter(x for r in clist if r["classification"] == "needs_review" for x in r["review_reasons"]).items(), key=lambda kv: -kv[1])),
            "with_no_jobs_but_qualified": sum(1 for r in clist if r["classification"] == "confirmed_eligible" and r["n_jobs"] == 0),
            "shortlist": [_slim(r) for r in sc], "review_queue": [_slim(r, False) for r in qc[:10]], "deferred_close_alternatives": dc,
            "shortlist_size_returned": len(sc), "shortlist_size_target": n_co_top,
            "with_unresolved_identity_hint": sum(1 for r in clist if r["hints"]),
        },
        "regression_cases": regression(REGRESSION_EMPLOYERS),
        "cases_by_job_id": [_slim(r, False) | {"locations": r["locations"], "location_category": r["location_category"]}
                            for r in jscored if any(r["id"].startswith(x) for x in case_job_ids)],
        "determinism_fingerprint": _fingerprint(jscored, clist),
    }


def _fingerprint(jscored, clist):
    import hashlib
    h = hashlib.sha256()
    for r in sorted(jscored, key=lambda r: r["id"]):
        h.update(f"{r['id']}|{r['classification']}|{r['score']}|{r['hard_exclusion']}".encode())
    for r in sorted(clist, key=lambda r: r["id"]):
        h.update(f"{r['id']}|{r['classification']}|{r['score']}".encode())
    return h.hexdigest()[:16]


def render_text(p):
    c, j, co = p["considered"], p["jobs"], p["companies"]
    lines = [f"Ranking preview (read-only; weights {p['ranking_status']}; reference time {p['as_of']})", ""]
    lines += [f"Considered: {c['job_rows_in_database']} job rows -> {c['distinct_canonical_jobs']} canonical jobs; "
              f"{c['company_rows_in_database']} company rows -> {c['distinct_canonical_companies']} canonical companies"]
    lines += ["", "JOBS", "  classification: " + str(j["by_classification"]), "  hard-excluded (primary reason): " + str(j["hard_excluded_by_primary_reason"]),
              "  needs-review reasons: " + str(j["needs_review_reasons"]), "  role family (not excluded): " + str(j["by_role_family_not_excluded"]),
              "  role family (confirmed): " + str(j["by_role_family_confirmed"]), "  location category: " + str(j["by_location_category_all"]),
              "  availability evidence: " + str(j["by_availability_all"]),
              f"  shortlist: {j['shortlist_size_returned']} of {j['shortlist_size_target']} (never padded); unique employers {j['unique_employers_in_shortlist']}; "
              f"confirmed pool {j['confirmed_pool_size']} jobs / {j['unique_employers_in_confirmed_pool']} employers",
              "  source share of shortlist: " + str(j["source_share_of_shortlist"]), "  source share of confirmed pool: " + str(j["source_share_of_confirmed_pool"])]
    for i, r in enumerate(j["shortlist"], 1):
        lines.append(f"  {i:2}. [{r['score']}] {r['company']} | {r['title']} | {r['family']}\n      {r['explanation']}")
    lines += [f"  exploration lane: {len(j['exploration_lane'])} shown of {j['exploration_pool_size']} (never qualified)"]
    for i, r in enumerate(j["exploration_lane"], 1):
        lines.append(f"  E{i:2}. [{r['score']}] {r['company']} | {r['title']}\n      {r['explanation']}")
    lines += ["", "COMPANIES", "  classification: " + str(co["by_classification"]), "  needs-review reasons: " + str(co["needs_review_reasons"]),
              f"  shortlist: {co['shortlist_size_returned']} of {co['shortlist_size_target']}; qualified with no jobs: {co['with_no_jobs_but_qualified']}"]
    for i, r in enumerate(co["shortlist"], 1):
        lines.append(f"  {i}. [{r['score']}] {r['company']}\n      {r['explanation']}")
    lines += ["", "Notes:"] + ["  - " + n for n in p["notes"]]
    return lines
