"""Evidence review for identity candidates (Day 11 hardening). Read-only: proposes, applies nothing.

Conservative identity model used throughout (the four concepts are never mixed):

  same entity          strong evidence two records are one job/company        -> tier A (merge only after operator approval)
  related entities     parent / subsidiary / acquired / legacy brand           -> a RELATION candidate (relations.py), never a merge
  possible duplicate   overlap suspected, identity unresolved                  -> tier B
  confirmed distinct   supported evidence they differ (or a human said so)     -> tier C
  conflict             contradictory evidence needing a human                  -> tier D, refined into the categories below

Tier D categories: invalid_source_label (placeholder labels), related_company_candidate (a hint of a corporate relationship),
different_label_same_posting (same posting, unexplained labels), ambiguous_label_variant (labels differing by trailing digits),
conflicting_domains, requisition_conflict.

Promotions (B -> A) are NEVER applied by classification. `promotion_proposals` returns specific, evidence-backed proposals
that the operator may approve one by one; each lists the checks that passed and the contradictions that block it.
"""

import collections
import json
from urllib.parse import urlsplit

from . import identity
from .idkeys import host_class, host_of, registrable_domain, url_signature

NAMESPACED_PLATFORMS = {"greenhouse", "lever", "ashby", "workable", "smartrecruiters", "workday"}

DISPOSITION = {"A": "same_entity_candidate", "B": "possible_duplicate", "C": "distinct", "D": "conflict"}


def source_family(source_id):
    if source_id.startswith("inv_"):
        return "investor_board"
    if source_id.startswith("yc"):
        return "yc"
    if source_id.startswith("jobspy"):
        return "aggregator"
    return "ats_feed" if source_id == "feashliaa" else source_id


def _label(host_or_domain):
    """First label of a registrable domain ('hpe.com' -> 'hpe')."""
    return registrable_domain(host_or_domain).split(".")[0]


def namespace_label(sig):
    """The employer-side label of a posting's namespace: Workday tenant ('visa.wd5' -> 'visa'), ATS board slug, or the
    registrable label of an employer-controlled host."""
    if sig.platform == "workday":
        return sig.slug.split(".")[0]
    if sig.slug:
        return sig.slug
    return _label(sig.host) if host_class(sig.host) == "employer" else ""


# ------------------------------------------------------------ relation hints

def relation_hints(name_a, name_b, sig):
    """Evidence that two differently-labelled companies are RELATED (not that they are the same). Each hint is a
    pointer for a human; a hint is never verification of a corporate relationship."""
    hints = []
    ka, kb = identity.name_key(name_a), identity.name_key(name_b)
    ta, tb = ka.split(), kb.split()

    def contains(long_tokens, short_tokens):
        n = len(short_tokens)
        return n > 0 and len(short_key := " ".join(short_tokens)) >= 3 and any(long_tokens[i:i + n] == short_tokens for i in range(len(long_tokens) - n + 1))

    if contains(ta, tb) and ta != tb:
        hints.append({"hint": "affiliation_in_name", "detail": f"'{name_a}' contains '{name_b}'"})
    elif contains(tb, ta) and ta != tb:
        hints.append({"hint": "affiliation_in_name", "detail": f"'{name_b}' contains '{name_a}'"})
    strip_digits = lambda k: k.replace(" ", "").rstrip("0123456789")
    if ka and kb and ka != kb and strip_digits(ka) == strip_digits(kb) and strip_digits(ka):
        hints.append({"hint": "trailing_digits_variant", "detail": f"'{name_a}' and '{name_b}' differ only by trailing digits (an ATS board suffix?); unexplained"})
        return hints
    label = namespace_label(sig)
    if label:
        owners = [n for n, k in ((name_a, ka), (name_b, kb)) if k.replace(" ", "") == label.lower() or label.lower() in k.split()]
        if len(owners) == 1:
            kind = "ATS tenant/board" if sig.slug else "employer host"
            other = name_b if owners[0] == name_a else name_a
            hints.append({"hint": "posting_lives_with_one_label",
                          "detail": f"the posting sits on the {kind} '{label}', which matches '{owners[0]}' and not '{other}'"})
    return hints


def categorize_conflict(verdict, name_a, name_b, sig):
    """Refined tier-D category plus relation hints."""
    if verdict.category in ("invalid_source_label", "conflicting_domains", "requisition_conflict"):
        return verdict.category, []
    hints = relation_hints(name_a, name_b, sig) if sig is not None else []
    if any(h["hint"] == "trailing_digits_variant" for h in hints):
        return "ambiguous_label_variant", hints
    return ("related_company_candidate" if hints else "different_label_same_posting"), hints


# ----------------------------------------------------- Rule 3: board owner

def evaluate_slug_owner(a, b, cos, shared_pairs, jobs, rejected):
    """Is this compatible-name company pair a specific, evidence-backed Tier-A candidate?

    Not a broad rule: it needs several independent posting ids in ONE ATS namespace whose slug is exactly the shorter
    name, independent collection channels, no domain contradiction, no negative decision and no other record that would
    make the pairing ambiguous. Anything else stays tier B."""
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    rel = identity.company_relation(a.name, b.name)
    check("names_are_compatible", rel == "compatible", f"{a.name!r} / {b.name!r} -> {rel}")
    short, long_ = (a, b) if len(identity.name_key(a.name)) <= len(identity.name_key(b.name)) else (b, a)
    postings = {}
    for p in shared_pairs:
        if p.sig is not None and p.sig.kind == "specific" and p.sig.platform in NAMESPACED_PLATFORMS and p.sig.slug:
            postings[p.key] = p.sig
    spaces = {(s.platform, s.slug) for s in postings.values()}
    check("two_or_more_distinct_posting_ids_in_one_namespace", len(postings) >= 2 and len(spaces) == 1,
          f"{len(postings)} distinct stable posting ids in namespace(s) {sorted(spaces)}")
    owner = bool(spaces) and all(namespace_label(s).lower() == identity.compact_key(short.name) for s in postings.values())
    check("shorter_name_is_the_ats_board_owner", owner, f"namespace label(s) {sorted({namespace_label(s) for s in postings.values()})} vs {identity.compact_key(short.name)!r}")
    fa, fb = {source_family(s) for s in a.sources}, {source_family(s) for s in b.sources}
    check("independent_collection_channels", bool(fa) and bool(fb) and not (fa & fb) and not (a.sources & b.sources),
          f"{sorted(a.sources)} ({sorted(fa)}) vs {sorted(b.sources)} ({sorted(fb)}); both read the same employer-owned board, collected by different channels")
    da, db_ = a.reported_domains, b.reported_domains
    check("no_domain_contradiction", not (da and db_ and not (da & db_)), f"reported domains {sorted(da)} / {sorted(db_)}")
    check("no_negative_decision", frozenset((a.company_id, b.company_id)) not in rejected, "no 'not the same' decision between them")
    others = [c for c in cos.values() if c.company_id not in (a.company_id, b.company_id)
              and identity.compact_key(c.name) in {identity.compact_key(a.name), identity.compact_key(b.name)}]
    pair_hosts = {registrable_domain(host_of(u)) for c in (a, b) for j in jobs.values() if j.company_id == c.company_id for u in j.urls}
    unseparated, separated = [], []
    for o in others:
        o_sigs = [url_signature(u) for j in jobs.values() if j.company_id == o.company_id for u in j.urls]
        o_spaces = {(g.platform, g.slug) for g in o_sigs if g.kind == "specific" and g.slug}
        o_hosts = {registrable_domain(g.host) for g in o_sigs}
        # positively separable: it has postings, none in the pair's ATS namespace, none on a host the pair uses,
        # and it reports no domain that the pair reports
        if o_sigs and not (o_spaces & spaces) and not (o_hosts & pair_hosts) and not (o.reported_domains & (a.reported_domains | b.reported_domains)):
            separated.append(o)
        else:
            unseparated.append(o)
    detail = f"{len(others)} other record(s) share the normalised name"
    if separated:
        detail += f"; {len(separated)} is positively separable (postings only on other platforms/hosts): {[o.name for o in separated][:2]}"
    if unseparated:
        detail += f"; unresolved: {[o.name for o in unseparated][:3]}"
    check("no_unresolved_record_with_the_same_name", not unseparated, detail)
    eligible = all(c["passed"] for c in checks)
    return {"eligible": eligible, "checks": checks,
            "evidence": {"namespace": [list(x) for x in sorted(spaces)], "posting_ids": sorted({str(k[3]) for k in postings}),
                         "records": [{"id": c.company_id, "name": c.name, "sources": sorted(c.sources), "reported_domains": sorted(c.reported_domains)} for c in (a, b)]},
            "blockers": [c["check"] for c in checks if not c["passed"]]}


# ------------------------------------------------ Rule 4: domain corroboration

def job_url_rows(company_id, jobs):
    rows = []
    for j in jobs.values():
        if j.company_id != company_id:
            continue
        for u in sorted(j.urls):
            sig = url_signature(u)
            host = host_of(u)
            rows.append({"url": u, "comparison_key": [str(x) for x in sig.key[:4]], "job_specific": sig.kind == "specific",
                         "id_reason": sig.reason, "host": host, "registrable_domain": registrable_domain(host),
                         "host_class": host_class(host)})
    return rows


def evaluate_domain_corroboration(w, o, cos, jobs, rejected):
    """w reports a website domain; o (equal name) reports none. Can o's own job URLs corroborate the pairing?

    Corroboration is NOT verified ownership: registrable-domain equality of a job URL host and a reported domain, plus an
    equal name and a job-specific identifier, is promoted to a Tier-A PROPOSAL only when nothing contradicts it."""
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    rel = identity.company_relation(w.name, o.name)
    check("names_are_equivalent", rel == "equivalent", f"{w.name!r} / {o.name!r} -> {rel} (a parent's domain would not prove a subsidiary, so equal names are required)")
    reported_regs = {registrable_domain(d) for d in w.reported_domains}
    rows = job_url_rows(o.company_id, jobs)
    match = [r for r in rows if r["host_class"] == "employer" and r["registrable_domain"] in reported_regs]
    check("job_urls_on_the_reported_domain", bool(match), f"{len(match)} of {len(rows)} job URL(s) of the other record are on {sorted(reported_regs)}")
    spec = [r for r in match if r["job_specific"]]
    check("job_url_has_a_posting_identifier", bool(spec), f"{len(spec)} matching URL(s) carry a job-specific identifier")
    check("not_a_shared_ats_host", bool(match) and all(r["host_class"] == "employer" for r in match),
          f"hosts {sorted({r['host'] for r in match})} are not on the shared-ATS list (host class is an assumption, not verified ownership)")
    contra = [c for c in cos.values() if c.company_id not in (w.company_id, o.company_id)
              and identity.name_key(c.name) == identity.name_key(w.name)
              and c.reported_domains and not ({registrable_domain(d) for d in c.reported_domains} & reported_regs)]
    check("no_conflicting_record_with_the_same_name", not contra,
          f"{len(contra)} other record(s) with this name report a different domain" + (f": {[(c.name, sorted(c.reported_domains)) for c in contra[:2]]}" if contra else ""))
    check("no_domain_contradiction", not (o.reported_domains and not (o.reported_domains & w.reported_domains)), f"other record reports {sorted(o.reported_domains)}")
    check("no_negative_decision", frozenset((w.company_id, o.company_id)) not in rejected, "no 'not the same' decision between them")
    return {"eligible": all(c["passed"] for c in checks), "checks": checks,
            "evidence": {"reported_domains": sorted(w.reported_domains), "records": [
                {"id": c.company_id, "name": c.name, "sources": sorted(c.sources)} for c in (w, o)],
                "matching_job_urls": match[:3], "other_job_url_count": len(rows)},
            "blockers": [c["check"] for c in checks if not c["passed"]]}


def promotion_proposals(conn, jobs=None, cos=None, co_pairs=None, job_pair_list=None):
    """Specific Tier-A proposals (each needs operator approval) plus the candidates that stay at tier B with the reason."""
    jobs = jobs if jobs is not None else identity.load_job_refs(conn)
    cos = cos if cos is not None else identity.load_company_refs(conn, jobs)
    jp = job_pair_list if job_pair_list is not None else identity.job_pairs(jobs, cos, identity.rejected_pairs(conn, "job"))
    if co_pairs is None:
        co_pairs, _ = identity.company_pairs(conn, jobs)
    rejected = identity.rejected_pairs(conn, "company")
    between = collections.defaultdict(list)
    for p in jp:
        ca, cb = jobs[p.a].company_id, jobs[p.b].company_id
        if ca != cb and p.verdict.rule == "same_posting_company_names_compatible":
            between[tuple(sorted((ca, cb)))].append(p)
    proposals, held = [], []
    for a, b, v in co_pairs:
        if v.tier != "B":
            continue
        k = tuple(sorted((a.company_id, b.company_id)))
        if v.rule in ("compatible_name_and_shared_posting", "compatible_names"):
            res = evaluate_slug_owner(a, b, cos, between.get(k, []), jobs, rejected)
            rule = "board_owner_with_independent_postings"
        elif v.rule == "name_only" and bool(a.reported_domains) != bool(b.reported_domains):
            w, o = (a, b) if a.reported_domains else (b, a)
            res = evaluate_domain_corroboration(w, o, cos, jobs, rejected)
            rule = "name_and_job_urls_on_reported_domain"
        else:
            continue
        entry = {"rule": rule, "rule_version": identity.RULE_VERSION, "a": a.company_id, "b": b.company_id,
                 "names": [a.name, b.name], "current_tier": "B", "proposed_tier": "A" if res["eligible"] else "B",
                 "checks": res["checks"], "evidence": res["evidence"], "blockers": res["blockers"],
                 "reasons": [c["detail"] for c in res["checks"] if c["passed"]]}
        (proposals if res["eligible"] else held).append(entry)
    return proposals, held


# ------------------------------------------------------------ review report

def _affected(pairs):
    ids = set()
    for p in pairs:
        ids |= {p.a, p.b}
    return len(ids)


def build_review(conn, baseline=None):
    """Detailed, machine-readable review of every tier B/D candidate. Read-only."""
    jobs = identity.load_job_refs(conn)
    cos = identity.load_company_refs(conn, jobs)
    jrej = identity.rejected_pairs(conn, "job")
    pairs = identity.job_pairs(jobs, cos, jrej)
    co_pairs, _ = identity.company_pairs(conn, jobs)
    proposals, held = promotion_proposals(conn, jobs, cos, co_pairs, pairs)

    by_rule = collections.defaultdict(list)
    for p in pairs:
        by_rule[(p.verdict.tier, p.verdict.rule)].append(p)
    job_summary = {f"{t}:{r}": {"pairs": len(v), "distinct_jobs": _affected(v),
                                "same_source": sum(1 for p in v if p.same_source), "cross_source": sum(1 for p in v if p.cross_source)}
                   for (t, r), v in sorted(by_rule.items())}
    tier_counts = collections.Counter(p.verdict.tier for p in pairs)
    id_kinds = collections.Counter()
    for p in pairs:
        if p.sig is not None and p.sig.kind == "specific":
            id_kinds[p.sig.id_kind or "unscoped"] += 1

    # Tier D redesign
    d_pairs = [p for p in pairs if p.verdict.tier == "D"]
    d_cat = collections.defaultdict(list)
    for p in d_pairs:
        cat, hints = categorize_conflict(p.verdict, jobs[p.a].company_name, jobs[p.b].company_name, p.sig)
        d_cat[cat].append((p, hints))
    d_summary = {}
    for cat, items in sorted(d_cat.items()):
        label_pairs = collections.Counter(tuple(sorted((jobs[p.a].company_name, jobs[p.b].company_name))) for p, _ in items)
        d_summary[cat] = {"job_pairs": len(items), "distinct_jobs": _affected([p for p, _ in items]),
                          "company_label_pairs": len(label_pairs),
                          "top": [{"labels": list(k), "job_pairs": n, "hints": [h["hint"] for p, hs in items
                                  if tuple(sorted((jobs[p.a].company_name, jobs[p.b].company_name))) == k for h in hs][:2]}
                                  for k, n in label_pairs.most_common(10)]}
    co_tier = collections.Counter(v.tier for _, _, v in co_pairs)
    co_rule = collections.Counter(f"{v.tier}:{v.rule}" for _, _, v in co_pairs)
    co_d = []
    for a, b, v in co_pairs:
        if v.tier == "D":
            sig = None
            for p in pairs:
                if {jobs[p.a].company_id, jobs[p.b].company_id} == {a.company_id, b.company_id}:
                    sig = p.sig
                    break
            cat, hints = categorize_conflict(v, a.name, b.name, sig)
            co_d.append({"names": [a.name, b.name], "sources": [sorted(a.sources), sorted(b.sources)], "category": cat,
                         "hints": hints, "jobs": [a.n_jobs, b.n_jobs]})

    # same-source Accel repeats
    ex = {}
    for r in conn.execute("SELECT job_id, external_id, provider_first_seen_at pfs FROM sightings WHERE source_id = 'inv_accel' AND job_id IS NOT NULL"):
        ex[r["job_id"]] = r
    rep = [p for p in by_rule.get(("B", "generic_url_same_title"), []) if p.same_source and "inv_accel" in jobs[p.a].sources and "inv_accel" in jobs[p.b].sources]
    groups = collections.defaultdict(set)
    for p in rep:
        groups[(jobs[p.a].company_id, identity.title_key(jobs[p.a].title), p.key)] |= {p.a, p.b}
    day = lambda i: (ex[i]["pfs"] or "")[:10] if i in ex else ""
    same_day_groups = sum(1 for g in groups.values() if len({day(i) for i in g}) == 1)
    accel = {"pairs": len(rep), "distinct_jobs": len({x for p in rep for x in (p.a, p.b)}), "groups": len(groups),
             "group_sizes": dict(collections.Counter(len(g) for g in groups.values())),
             "groups_created_same_day": same_day_groups, "groups_created_on_different_days": len(groups) - same_day_groups,
             "provider_ids_distinct_in_every_pair": all(ex[p.a]["external_id"] != ex[p.b]["external_id"] for p in rep if p.a in ex and p.b in ex),
             "resolvable_with_stored_evidence": False,
             "what_would_resolve_it": "a posting-specific URL, an employer requisition id or the job description; none is stored for these records",
             "disposition": "keep every provider record separate (possible duplicate or multiple openings); no merge, no suppression"}

    titles = []
    for p in by_rule.get(("B", "same_posting_id_different_titles"), []):
        row = []
        for jid in (p.a, p.b):
            sights = conn.execute("SELECT source_id, external_id, source_url, provider_first_seen_at, first_seen_at FROM sightings WHERE job_id = ?", (jid,)).fetchall()
            row.append({"employer": jobs[jid].company_name, "title": jobs[jid].title,
                        "sightings": [{"source": s["source_id"], "provider_id": s["external_id"], "url": s["source_url"],
                                       "provider_first_seen": (s["provider_first_seen_at"] or "")[:10], "our_first_seen": s["first_seen_at"][:10]} for s in sights]})
        titles.append({"key": [str(x) for x in p.key[:4]], "id_kind": p.sig.id_kind, "a": row[0], "b": row[1]})
    named = {"inspirebrands", "ionq", "santander"}
    manual = [t for t in titles if identity.name_key(t["a"]["employer"]) in named]
    other = collections.Counter(identity.name_key(t["a"]["employer"]) for t in titles if identity.name_key(t["a"]["employer"]) not in named)
    prefix = sum(1 for t in titles if identity.title_key(t["a"]["title"])[:20] == identity.title_key(t["b"]["title"])[:20]
                 or identity.title_key(t["a"]["title"]) in identity.title_key(t["b"]["title"]) or identity.title_key(t["b"]["title"]) in identity.title_key(t["a"]["title"]))

    # regression cases
    cases = {}
    wanted = {"hpe_simplivity_vs_nimble": ("hpe simplivity", "nimble storage"), "linkedin_vs_lynda": ("linkedin", "lynda"),
              "6sense_vs_slintel": ("6sense", "slintel a 6sense company"), "forgerock_vs_ping": ("forgerock", "pingidentity"),
              "trifacta_vs_alteryx": ("trifacta", "alteryx"), "pismo_vs_visa": ("pismo", "visa"),
              "couchbase_vs_couchbaseinc": ("couchbase", "couchbaseinc"), "telnyx_vs_telnyx54": ("telnyx", "telnyx54")}
    for label, (x, y) in wanted.items():
        hit = None
        for a, b, v in co_pairs:
            if {identity.name_key(a.name), identity.name_key(b.name)} == {x, y}:
                hit = (a, b, v)
                break
        jp_hit = [p for p in pairs if {identity.name_key(jobs[p.a].company_name), identity.name_key(jobs[p.b].company_name)} == {x, y}]
        cat = None
        if hit:
            sig = jp_hit[0].sig if jp_hit else None
            cat = categorize_conflict(hit[2], hit[0].name, hit[1].name, sig)[0] if hit[2].tier == "D" else DISPOSITION[hit[2].tier]
        cases[label] = {"company_pair": ({"tier": hit[2].tier, "rule": hit[2].rule, "category": cat} if hit else None),
                        "job_pairs": dict(collections.Counter(f"{p.verdict.tier}:{p.verdict.rule}" for p in jp_hit)),
                        "merged": False}

    return {
        "rule_version": identity.RULE_VERSION, "baseline": baseline,
        "jobs": {"rows": len(jobs), "pairs": len(pairs), "by_tier": dict(tier_counts), "distinct_jobs_in_any_pair": _affected(pairs),
                 "summary": job_summary, "posting_id_kinds_in_specific_pairs": dict(id_kinds),
                 "dispositions": {DISPOSITION[t]: n for t, n in tier_counts.items()}},
        "tier_d_jobs": d_summary,
        "companies": {"rows": len(cos), "pairs": len(co_pairs), "by_tier": dict(co_tier), "by_rule": dict(sorted(co_rule.items())),
                      "distinct_companies_in_any_pair": len({x for a, b, _ in co_pairs for x in (a.company_id, b.company_id)}),
                      "tier_d": co_d},
        "promotion_proposals": proposals, "held_at_tier_b": held,
        "accel_same_source_repeats": accel, "title_conflicts_manual_review": manual, "title_conflicts_total": len(titles),
        "title_conflicts_other_by_employer": dict(other.most_common(10)), "title_conflicts_one_title_extends_the_other": prefix, "regression_cases": cases,
        "presentation_grouping_proposal": (
            "Display-only and reversible: group key = (platform, namespace, posting key) for 'same posting under different labels' and "
            "(platform, namespace, requisition) for reposts; group id = a hash of that key; recorded as identity_relations (kind "
            "same_posting_different_label / same_requisition_different_posting) so a Day 12 view can show one row with the other labels "
            "attached. No record is merged, hidden, closed or re-attributed; deleting the relation rows restores today's view."),
    }
