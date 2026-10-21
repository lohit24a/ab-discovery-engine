"""Deterministic, explainable scoring for jobs (Stream A) and companies (Stream B) (Day 12). Pure functions, no database.

Every point comes from a recorded field and is listed in `components`; every candidate gets reason codes, a list of
MISSING EVIDENCE and a concise explanation built only from those facts. No model call, no randomness, no wall-clock: the
reference time is passed in. Scoring never writes a lifecycle state and never closes, hides or merges anything; hard
exclusions only keep a record out of the normal suggestions (it stays in SQLite).

Classification
  confirmed_eligible  strong (or corroborated) role evidence AND confirmed India/Bengaluru location, not senior, not an
                      internship (unless the profile says so), not recruiter-posted, not historical / seen-earlier
  needs_review        something is unknown, weakly supported or capped (see `review_reasons`)
  exploration         no role family matched but the title is worth a look; NEVER qualified automatically
  low_priority        retained; below the review threshold or no role evidence at all
  excluded            a hard exclusion applies (primary reason in `hard_exclusion`)

Dates are never conflated: an aggregator's first-seen date is not a posting date and earns nothing; only a date a
provider reports as the listing's creation can earn the listing-age points (ranking.yaml -> date_semantics).
"""

from datetime import timezone

from .filters import classify_experience, classify_location, match_roles, screen_title
from .identity import is_placeholder_name, name_key
from .profile import phrase_regex

EXCLUSION_ORDER = ("HUMAN_NO", "CLOSED_OR_NOT_SEEN", "PLACEHOLDER_COMPANY", "LOCATION_INCOMPATIBLE", "EXECUTIVE_TITLE", "NON_TARGET_ROLE")

AVAILABILITY_TEXT = {
    "employer_confirmed_open": "confirmed open by an employer-direct source",
    "in_latest_check": "listed in the latest {src} check (observed on source, not employer-verified)",
    "observed_earlier": "seen earlier on {src} but not in its latest check",
    "unknown": "no successful source check on record, so availability is unknown",
    "historical": "provider-retained or historical listing, not confirmed open",
}
LOCATION_TEXT = {
    "bengaluru": "a documented location is Bengaluru",
    "india_remote_explicit": "India remote is stated explicitly",
    "india_country_only": "the location says India without a city",
    "india_other_city": "the location is an Indian city other than Bengaluru",
    "worldwide_remote": "worldwide remote is stated (India is not named; employers often restrict it)",
    "remote_regional_plausible": "remote within a region that includes India (India is not named)",
    "remote_unqualified": "remote with no country stated (not assumed India-eligible)",
    "unknown": "location missing or unparseable (eligibility unknown)",
    "foreign_remote": "remote tied to a non-India region",
    "foreign_onsite": "only non-India locations are documented",
}


def _interest_regexes(profile):
    cache = getattr(profile, "_interest_cache", None)
    if cache is None:
        cache = {name: phrase_regex(terms) for name, terms in profile.raw["company_interests"].items()}
        profile._interest_cache = cache
    return cache


def sector_matches(text, profile):
    """Interest categories (and the matched phrase) found in a company's recorded description text."""
    found = {}
    for name, rx in _interest_regexes(profile).items():
        if name == "startup_signals" or rx is None:
            continue
        m = rx.search(text or "")
        if m:
            found[name] = m.group(0).lower()
    return found


def startup_terms(text, profile):
    rx = _interest_regexes(profile).get("startup_signals")
    return sorted({m.group(0).lower() for m in rx.finditer(text or "")}) if rx else []


def _band(age_days, w):
    if age_days is None:
        return 0, None
    if age_days <= w["fresh_days"]:
        return w["fresh_points"], "AGE_FRESH"
    if age_days <= w["recent_days"]:
        return w["recent_points"], "AGE_RECENT"
    if age_days >= w["stale_days"]:
        return w["stale_points"], "AGE_STALE"
    return 0, "AGE_MIDDLE"


def _hint(hints):
    return (" Review hint: " + hints[0] + ".") if hints else ""


def posting_date(c, ranking):
    """-> (date or None, codes). Only a provider-reported CREATION date counts as a posting date; an aggregator's first-seen
    date, a local first-seen date and a last observation are different facts and never stand in for it."""
    sem = ranking["date_semantics"]
    reported, aggregator = [], False
    for d in c.get("dated", []):
        kind = sem.get(d["source_type"], sem["other"])
        if kind == "provider_reported_creation" and d.get("when"):
            reported.append(d["when"])
        elif kind == "aggregator_first_seen":
            aggregator = True
    if reported:
        return min(reported), ["DATE_PROVIDER_REPORTED"]
    return None, (["DATE_AGGREGATOR_FIRST_SEEN_NO_BONUS"] if aggregator else []) + ["POSTING_AGE_UNKNOWN"]


def missing_evidence(c, loc, exp, has_posting_date):
    out = []
    if not (c.get("description") or "").strip():
        out.append("NO_DESCRIPTION")
    if exp.category == "unknown" and not c.get("source_level"):
        out.append("EXPERIENCE_UNKNOWN")
    if not has_posting_date:
        out.append("POSTING_DATE_UNKNOWN")
    if c.get("availability") != "employer_confirmed_open":
        out.append("AVAILABILITY_UNVERIFIED")
    if loc.confidence != "literal":
        out.append("LOCATION_NOT_LITERAL")
    if c.get("scan_partial"):
        out.append("SCAN_PARTIAL")
    return out


# ---------------------------------------------------------------------------------- jobs

def score_job(c, profile, ranking, as_of):
    """c: candidate dict (see candidates.load_candidates). Returns a result dict; never mutates c."""
    W = ranking["jobs"]
    hx = profile.raw["hard_exclusions"]
    ex = profile.raw["experience"]
    verdict = c.get("verdict")
    loc = classify_location(c["locations"], profile)
    ctext = c.get("company_text") or ""
    role = match_roles(c["title"], profile, ctext, c.get("description", ""))
    exp = classify_experience(c["title"], profile, c.get("exp_min"), c.get("exp_max"),
                              " ".join(x for x in (c.get("exp_text"), c.get("description")) if x) or None, employer_stated=bool(c.get("exp_reliable")))
    screen = screen_title(c["title"], profile) if not role.has_signal else None
    when, date_codes = posting_date(c, ranking)
    excl = []
    if hx["human_verdict_no"] and verdict and verdict.get("decision") == "No" and profile.raw["verdicts"]["suppress_rejected"]:
        excl.append("HUMAN_NO")
    if hx["closed_or_not_seen"] and c.get("state") in ("Closed", "Not Seen"):
        excl.append("CLOSED_OR_NOT_SEEN")
    if hx["placeholder_company_label"] and is_placeholder_name(c["company_name"]):
        excl.append("PLACEHOLDER_COMPANY")
    if hx["incompatible_location"] and loc.eligibility == "incompatible":
        excl.append("LOCATION_INCOMPATIBLE")
    if exp.category == "executive" and ex["executive_title_treatment"] == "hard_exclude":
        excl.append("EXECUTIVE_TITLE")
    if hx["non_target_role"] and screen is not None and screen.kind == "non_target":
        excl.append("NON_TARGET_ROLE")
    missing = missing_evidence(c, loc, exp, when is not None)
    base = {"kind": "job", "id": c["job_id"], "company_id": c["company_id"], "company": c["company_name"], "title": c["title"],
            "location_category": loc.category, "location_eligibility": loc.eligibility, "location_confidence": loc.confidence,
            "locations": [s for s, _ in loc.segments], "location_evidence": c.get("location_evidence", []),
            "availability": c.get("availability", "unknown"), "family": role.best.family if role.best else None,
            "role_strength": role.best.strength if role.best else None, "experience": exp.category, "hints": list(c.get("hints", [])),
            "sources": list(c.get("source_ids", [])), "employer_key": name_key(c["company_name"]), "verdict": (verdict or {}).get("decision"),
            "missing_evidence": missing, "local_first_seen": c.get("local_first_seen"), "last_seen": c.get("last_seen"),
            "state": c.get("state"), "merged_records": c.get("merged_records", 0), "user_supplied": bool(c.get("user_supplied")), "link": c.get("link") or c.get("url") or "", "posting_date": when.isoformat() if when else None,
            "families": [h.family for h in role.hits]}
    if excl:
        reason = next(r for r in EXCLUSION_ORDER if r in excl)
        text = {"HUMAN_NO": "a stored human verdict is No", "CLOSED_OR_NOT_SEEN": f"the lifecycle already says {c.get('state')}",
                "PLACEHOLDER_COMPANY": "the company label is a source placeholder", "EXECUTIVE_TITLE": "executive-level title (excluded by the profile)",
                "LOCATION_INCOMPATIBLE": "every documented location is outside India (" + "; ".join(s for s, _ in loc.segments[:3]) + ")",
                "NON_TARGET_ROLE": "the title names a different kind of job (" + ", ".join(screen.terms[:2] if screen else []) + ") and matches none of the eight role families"}[reason]
        return {**base, "classification": "excluded", "score": None, "hard_exclusion": reason, "all_exclusions": excl, "components": {},
                "reason_codes": ["EXCLUDED_" + reason], "review_reasons": [],
                "explanation": f"Excluded ({reason}): {text}. The record is kept in SQLite and not suggested."}

    comp, codes, review, facts = {}, list(date_codes), [], []
    L, A, E = W["location"], W["availability"], W["experience"]
    # ------------------------------------------------ no role family: exploration lane or low priority, never qualified
    if not role.has_signal:
        if screen.kind == "exploration":
            X = W["exploration"]
            comp["exploration_signal"] = X["exploration_signal"]
            codes.append("EXPLORATION_SIGNAL")
            facts.append(f"no role family matched; the title contains '{', '.join(screen.terms[:3])}', which is worth a look")
            if screen.ai_engineering:
                comp["ai_engineering_flag"] = X["ai_engineering_flag_penalty"]
                codes.append("AI_ENGINEERING_UNCLEAR")
                facts.append("reads as AI/ML engineering (building) rather than AI implementation (deploying); your call")
            comp["location"] = L.get(loc.category, 0)
            codes.append("LOC_" + loc.category.upper())
            facts.append(LOCATION_TEXT[loc.category] + ("" if not loc.segments else " (" + "; ".join(f"'{s}'" for s, _ in loc.segments[:2]) + ")"))
            comp["availability"] = A[c.get("availability", "unknown")]
            codes.append("AVAIL_" + c.get("availability", "unknown").upper())
            if exp.category in ("senior", "executive"):
                comp["experience"] = E["executive_title_penalty"] if exp.category == "executive" else E["senior_title_penalty"]
                facts.append(exp.detail)
            if verdict and verdict.get("decision") in ("Yes", "Maybe"):
                comp["verdict"] = W["verdict"][verdict["decision"].lower()]
            score = sum(comp.values())
            cls = "exploration" if score >= X["minimum_score"] and loc.eligibility != "incompatible" else "low_priority"
            expl = (f"Exploration (score {score}): " if cls == "exploration" else f"Low priority (score {score}): ") + "; ".join(facts) + \
                   ". Not qualified: nothing here establishes the role." + _hint(c.get("hints"))
            return {**base, "classification": cls, "score": score, "hard_exclusion": None, "all_exclusions": [], "components": comp, "reason_codes": codes,
                    "review_reasons": ["no role family matched (exploration only)"], "explanation": expl}
        codes.append("NO_ROLE_EVIDENCE")
        return {**base, "classification": "low_priority", "score": 0, "hard_exclusion": None, "all_exclusions": [], "components": {}, "reason_codes": codes,
                "review_reasons": ["no role evidence in the title"],
                "explanation": "Low priority (score 0): the title gives no evidence of any of the eight role families. Kept in SQLite." + _hint(c.get("hints"))}

    fam = profile.families[role.best.family]
    R = W["role_relevance"]
    if role.best.strength == "strong":
        comp["role"] = R["strong_alias"]
        codes.append("ROLE_STRONG")
    elif role.best.strength == "weak_corroborated":
        comp["role"] = R["weak_alias_corroborated"]
        codes.append("ROLE_WEAK_CORROBORATED")
    else:
        comp["role"] = R["weak_alias_uncorroborated"]
        codes.append("ROLE_WEAK_UNCORROBORATED")
        review.append("weak role alias without corroboration")
    if len(role.hits) > 1:
        comp["role_secondary"] = R["secondary_family_bonus"]
        codes.append("ROLE_SECONDARY_FAMILY")
    if role.best.negatives:
        comp["role_negative_terms"] = R["negative_title_term_penalty"]
        codes.append("ROLE_NEGATIVE_TERM")
    if role.best.signals:
        comp["responsibility_signals"] = min(R["responsibility_signal_cap"], R["responsibility_signal"] * len(role.best.signals))
        codes.append("ROLE_RESPONSIBILITY_SIGNAL")
    facts.append(f"{fam.label} ({role.best.strength.replace('_', ' ')}): title matches '{', '.join(role.best.aliases)}'"
                 + (f"; corroborated by {', '.join(role.best.corroboration[:2])}" if role.best.strength == "weak_corroborated" else ""))

    early = 0
    if "EXP_TEXT_EARLY" in exp.flags:
        early = max(early, E["early_career_signal"])
    if "EXP_STATED_EARLY" in exp.flags:
        early = max(early, E["stated_experience_within_early_career"])
    if "TITLE_JUNIOR" in exp.flags:
        early = max(early, E["junior_title_term"])
    pen = 0
    if "TITLE_EXECUTIVE" in exp.flags:
        pen = min(pen, E["executive_title_penalty"])
    if "TITLE_SENIOR" in exp.flags:
        pen = min(pen, E["senior_title_penalty"])
    if "EXP_STATED_SENIOR" in exp.flags:
        pen = min(pen, E["stated_experience_senior"])
    if "TITLE_MID_LEVEL" in exp.flags and ex["mid_level_title_treatment"] == "soft_penalty":
        pen = min(pen, E["mid_level_title_penalty"])
        codes.append("TITLE_MID_LEVEL_PENALTY")
    comp["experience"] = early + pen if (early or pen) else E["unknown"]
    codes += [f for f in exp.flags if f != "TITLE_JUNIOR_BUT_SENIOR"]
    facts.append(exp.detail if exp.detail else "experience not stated (unknown, not treated as senior)")
    if "TITLE_MID_LEVEL" in exp.flags and ex["mid_level_title_treatment"] != "soft_penalty":
        facts.append("the title has a mid-level word, which your settings do not penalise")
    senior_signal = exp.category in ("senior", "executive")
    if senior_signal:
        review.append(f"{exp.category} experience signal in the title/requirements")
    level = c.get("source_level")
    employer_years = c.get("exp_min") if c.get("exp_reliable") else None
    if level and employer_years is not None:                       # a noisy aggregator label never overrides the employer's own figure; the disagreement stays visible
        codes.append("SRC_LEVEL_IGNORED_EMPLOYER_EXPERIENCE_STATED")
        facts.append(f"the source labels the level '{level}' but the employer states a minimum of {employer_years:g} years, which takes precedence")
    elif level and ex["source_reported_level_treatment"] == "use":
        if level == "senior":
            comp["source_reported_level"] = E["source_reported_senior_penalty"]
            senior_signal = True
            review.append("the source labels this listing senior (source-reported, machine-derived)")
            codes.append("SRC_LEVEL_SENIOR")
        elif level in ("entry", "intern"):
            comp["source_reported_level"] = E["source_reported_entry_bonus"]
            codes.append("SRC_LEVEL_" + level.upper())
        facts.append(f"the source labels the level '{level}' (source-reported)")
    if c.get("recruiter_posted"):
        comp["recruiter_posted"] = E["recruiter_posted_penalty"]
        review.append("posted by a recruiter/agency, so the employer is not the poster")
        codes.append("RECRUITER_POSTED")
    held_intern = "INTERNSHIP" in exp.flags and ex["internship_treatment"] == "needs_review"
    if held_intern:
        review.append("internship title (employment type needs your decision)")
        codes.append("INTERNSHIP_HELD")

    comp["location"] = L.get(loc.category, 0)
    codes.append("LOC_" + loc.category.upper())
    if loc.confidence == "inferred":
        codes.append("LOC_INFERRED")
    seg = "; ".join(f"'{s}'" for s, _ in loc.segments[:3])
    facts.append(LOCATION_TEXT[loc.category] + (f" ({seg})" if seg else ""))
    if loc.eligibility in ("plausible", "unknown"):
        review.append("location eligibility not confirmed")

    av = c.get("availability", "unknown")
    comp["availability"] = A[av]
    codes.append("AVAIL_" + av.upper())
    if c.get("scan_partial"):
        codes.append("SCAN_PARTIAL")
    src = ", ".join(c.get("source_ids", [])[:2]) or "source"
    caveat = ""
    if av == "in_latest_check" and c.get("newest_attempt_failed"):
        codes.append("SOURCE_NEWEST_ATTEMPT_FAILED")
        caveat = f" [the source's newest attempt ({str(c['newest_attempt_failed'])[:10]}) failed; this listing is from its earlier successful check]"
    facts.append(AVAILABILITY_TEXT[av].format(src=src) + (" [partial scan]" if c.get("scan_partial") and av == "in_latest_check" else "") + caveat)
    if av == "historical":
        review.append("historical or provider-retained listing")
    elif av == "observed_earlier":
        review.append("seen earlier but not in its source's latest check")

    age_days = None
    if when is not None:
        age_days = max(0, (as_of - when.astimezone(timezone.utc)).days)
        pts, code = _band(age_days, W["listing_age"])
        comp["listing_age"] = pts
        if code:
            codes.append(code)
        facts.append(f"the provider reports it created {age_days} day(s) before the reference date (provider-reported, unverified)")
    else:
        facts.append("posting age unknown" + (" (the aggregator's first-seen date is not a posting date)" if "DATE_AGGREGATOR_FIRST_SEEN_NO_BONUS" in codes else ""))

    Sq = W["source_quality"]
    types = c.get("source_types", ["other"])
    comp["source_quality"] = max((Sq.get(t, Sq["other"]) for t in types if t != "investor_board"), default=0)      # independent sourcing only
    codes += ["SRC_" + t.upper() for t in types]

    C = W["company"]
    matches = sector_matches(ctext, profile)
    if matches:
        comp["company_sector"] = min(C["sector_interest_cap"], C["sector_interest_per_match"] * len(matches))
        codes.append("CO_SECTOR")
        facts.append("employer sector evidence: " + ", ".join(f"{k} ('{v}')" for k, v in sorted(matches.items())[:2]))
    cev = c.get("company_evidence") or {}
    if cev.get("yc"):
        comp["company_yc"] = C["yc_company"]
        codes.append("CO_YC")
    boards = cev.get("investor_boards") or []
    sighted_on_board = "investor_board" in types
    membership = C["investor_board_listed"] * len(boards) + (Sq["investor_board"] if sighted_on_board else 0)
    if boards or sighted_on_board:
        comp["investor_membership"] = min(C["investor_membership_cap"], membership)
        if boards:
            codes.append("CO_INVESTOR_LISTED")
        if membership > C["investor_membership_cap"]:
            codes.append("INVESTOR_MEMBERSHIP_CAPPED")
            facts.append(f"investor-board membership is worth at most {C['investor_membership_cap']} points per job (the cap, however many boards carry it)")
    if startup_terms(ctext, profile) or cev.get("yc"):
        comp["company_startup"] = C["startup_signal"]
    if c.get("also_company_candidate"):
        comp["also_company_candidate"] = C["also_a_company_candidate"]
        codes.append("CO_ALSO_STREAM_B")

    V = W["verdict"]
    if verdict and verdict.get("decision") == "Yes":
        comp["verdict"] = V["yes"]
        codes.append("VERDICT_YES")
        facts.insert(0, f"your stored verdict is Yes (+{V['yes']} points)")      # first, so a long explanation never hides it
    elif verdict and verdict.get("decision") == "Maybe":
        comp["verdict"] = V["maybe"]
        codes.append("VERDICT_MAYBE")
        facts.insert(0, f"your stored verdict is Maybe (+{V['maybe']} points)")
    if c.get("hints"):
        codes.append("HINT_UNRESOLVED_IDENTITY")
    if c.get("merged_records"):
        codes.append("MERGED_RECORDS")
        facts.append(f"combines {c['merged_records']} duplicate record(s) confirmed as the same posting; every recorded location is shown")
    if c.get("user_supplied"):
        codes.append("USER_SUPPLIED")
        facts.append("details supplied by you" + (" including a description" if c.get("description") else "") + ", not independently verified")

    score = sum(comp.values())
    T = W["thresholds"]
    confirmed_ok = (loc.eligibility == "confirmed" and role.best.strength in ("strong", "weak_corroborated") and not senior_signal
                    and av not in ("historical", "observed_earlier") and not c.get("recruiter_posted") and not held_intern)
    if confirmed_ok and score >= T["confirmed_eligible_min_score"]:
        cls = "confirmed_eligible"
    elif score >= T["needs_review_min_score"]:
        cls = "needs_review"
        if confirmed_ok:
            review.append(f"score {score} is below the confirmed threshold {T['confirmed_eligible_min_score']}")
    else:
        cls = "low_priority"
    label = {"confirmed_eligible": "Qualified", "needs_review": "Held for review", "low_priority": "Low priority"}[cls]
    tail = (" Reasons: " + "; ".join(sorted(set(review))) + ".") if cls == "needs_review" and review else ""
    gaps = (" Missing evidence: " + ", ".join(missing) + ".") if missing else ""
    explanation = f"{label} (score {score}). " + "; ".join(f for f in facts if f) + "." + tail + gaps + _hint(c.get("hints"))
    return {**base, "classification": cls, "score": score, "hard_exclusion": None, "all_exclusions": [], "components": comp,
            "reason_codes": codes, "review_reasons": sorted(set(review)), "explanation": explanation, "age_days": age_days}


# ------------------------------------------------------------------------------ companies

def score_company(c, profile, ranking):
    W = ranking["companies"]
    hx = profile.raw["hard_exclusions"]
    ev = c["evidence"]
    verdict = c.get("verdict")
    base = {"kind": "company", "id": c["company_id"], "company": c["name"], "employer_key": name_key(c["name"]), "hints": list(c.get("hints", [])),
            "verdict": (verdict or {}).get("decision"), "n_jobs": c.get("n_jobs", 0), "local_first_seen": c.get("first_seen"), "sources": sorted(c.get("sources", [])),
            "missing_evidence": ["FUNDING_NOT_RECORDED", "OPERATING_STATUS_" + ("SOURCE_REPORTED" if ev.get("yc_status") else "UNKNOWN"),
                                 "FACTS_ARE_SOURCE_REPORTED"]}
    if hx["human_verdict_no"] and verdict and verdict.get("decision") == "No" and profile.raw["verdicts"]["suppress_rejected"]:
        return {**base, "classification": "excluded", "score": None, "hard_exclusion": "HUMAN_NO", "components": {}, "reason_codes": ["EXCLUDED_HUMAN_NO"],
                "review_reasons": [], "explanation": "Excluded (HUMAN_NO): a stored human verdict is No. The record is kept in SQLite and not suggested."}
    if hx["placeholder_company_label"] and is_placeholder_name(c["name"]):
        return {**base, "classification": "excluded", "score": None, "hard_exclusion": "PLACEHOLDER_COMPANY", "components": {},
                "reason_codes": ["EXCLUDED_PLACEHOLDER_COMPANY"], "review_reasons": [],
                "explanation": "Excluded (PLACEHOLDER_COMPANY): the label is a source placeholder, not a company."}
    comp, codes, review, facts = {}, [], [], []
    text = ev["text"]
    matches = sector_matches(text + " " + " ".join(ev["industries"]), profile)
    generic = set(profile.raw["generic_interest_categories"])
    core = {k: v for k, v in matches.items() if k not in generic}
    P = W["product_relevance"]
    if matches:
        comp["product_relevance"] = min(P["sector_interest_cap"], P["sector_interest_per_match"] * len(matches))
        codes.append("CO_SECTOR")
        facts.append("the recorded description matches " + ", ".join(f"{k} ('{v}')" for k, v in sorted(matches.items())[:3]) + " (source-reported text)")
        if "ai_native" in matches:
            comp["ai_native"] = P["ai_native_bonus"]
            codes.append("CO_AI_NATIVE")
        if not core:
            codes.append("CO_SECTOR_GENERIC_ONLY")
            review.append("sector evidence is only generic wording (b2b/saas)")
    S = W["startup_signals"]
    if ev["yc"]:
        comp["yc"] = S["yc_company"]
        codes.append("CO_YC")
        facts.append(f"listed in the YC directory ({ev['yc_batch'] or 'batch not recorded'}; source-reported)")
        if ev["yc_batch"] or ev["yc_stage"]:
            comp["yc_recorded"] = S["yc_stage_or_batch_recorded"]
    status = (ev.get("yc_status") or "").lower()
    O = W["operating_status"]
    if status == "inactive":
        comp["operating_status"] = O["inactive_penalty"]
        codes.append("CO_STATUS_INACTIVE_REPORTED")
        review.append("the YC directory reports the company Inactive (source-reported, may be outdated)")
        facts.append("YC directory status: Inactive")
    elif status == "acquired":
        comp["operating_status"] = O["acquired_penalty"]
        codes.append("CO_STATUS_ACQUIRED_REPORTED")
        review.append("the YC directory reports the company Acquired (source-reported, may be outdated)")
        facts.append("YC directory status: Acquired")
    elif status:
        codes.append("CO_STATUS_" + status.upper() + "_REPORTED")
    team = ev["yc_team_size"]
    if isinstance(team, (int, float)) and 0 < team <= 50:
        comp["small_team"] = S["small_team_recorded"]
        codes.append("CO_SMALL_TEAM")
    terms = startup_terms(text, profile)
    if terms:
        comp["startup_terms"] = min(S["startup_signal_cap"], S["startup_signal_term"] * len(terms))
    I = W["investor_evidence"]
    if ev["investor_boards"]:
        comp["investor_listing"] = min(I["cap"], I["per_investor_board_listing"] * len(ev["investor_boards"]))
        codes.append("CO_INVESTOR_LISTED")
        facts.append("appears on investor jobs board(s): " + ", ".join(ev["investor_boards"]) + " (a board listing, not a funding fact)")
    N = W["india_connection"]
    cls_in = ev["india_relevance"] or "unverified"
    india_points = N.get(cls_in, N["unverified"])
    loc_text = " ".join(ev["locations"])
    bengaluru = phrase_regex(profile.raw["location"]["onsite_or_hybrid_cities"]).search(loc_text) is not None
    if india_points:
        comp["india_connection"] = india_points
    if bengaluru:
        comp["bengaluru_location"] = N["bengaluru_in_recorded_location"]
        codes.append("CO_BENGALURU_RECORDED")
        facts.append("a recorded company location is Bengaluru (source-reported)")
    codes.append("INDIA_" + cls_in.upper())
    if cls_in in ("india_based", "potentially_relevant"):
        facts.append(f"India connection recorded as {cls_in.replace('_', ' ')}")
    has_india = bool(india_points) or bengaluru
    if not has_india:
        review.append("no India connection recorded yet")
    F = W["source_confidence"]
    if text.strip():
        comp["description"] = F["description_recorded"]
    else:
        comp["single_source_name_only"] = F["single_source_name_only"]
        codes.append("CO_NAME_ONLY")
        review.append("no product description recorded")
    if len(c.get("sources", [])) > 1:
        comp["multiple_sources"] = F["multiple_independent_sources"]
        codes.append("CO_MULTI_SOURCE")
    J = W["jobs_presence"]
    n = c.get("n_jobs", 0)
    comp["jobs_presence"] = min(J["observed_job_cap"], J["observed_job_listing"] * n) if n else J["no_jobs"]
    facts.append(f"{n} observed job listing(s) (not verified open)" if n else "no job listings observed (not a penalty)")
    V = W["verdict"]
    if verdict and verdict.get("decision") == "Yes":
        comp["verdict"] = V["yes"]
        codes.append("VERDICT_YES")
        facts.insert(0, f"your stored verdict is Yes (+{V['yes']} points)")
    elif verdict and verdict.get("decision") == "Maybe":
        comp["verdict"] = V["maybe"]
        codes.append("VERDICT_MAYBE")
        facts.insert(0, f"your stored verdict is Maybe (+{V['maybe']} points)")
    if c.get("hints"):
        codes.append("HINT_UNRESOLVED_IDENTITY")
    score = sum(comp.values())
    T = W["thresholds"]
    product_ok = bool(matches) or ev["yc"] or bool(ev["investor_boards"])
    qualifies = score >= T["eligible_min_score"] and has_india and bool(core) and status not in ("inactive", "acquired")
    if qualifies:
        cls = "confirmed_eligible"
    elif score >= T["needs_review_min_score"] and product_ok:
        cls = "needs_review"
    else:
        cls = "low_priority"
    label = {"confirmed_eligible": "Qualified", "needs_review": "Held for review", "low_priority": "Low priority"}[cls]
    tail = (" Reasons: " + "; ".join(sorted(set(review))) + ".") if cls == "needs_review" and review else ""
    return {**base, "classification": cls, "score": score, "hard_exclusion": None, "components": comp, "reason_codes": codes,
            "review_reasons": sorted(set(review)), "explanation": f"{label} (score {score}). " + "; ".join(facts) + "." + tail + _hint(c.get("hints"))}


# ------------------------------------------------------------------------------ selection

def _key(r):
    return (-r["score"], r["id"])


def select_shortlist(results, size, min_score, max_per_employer, allow_fill_with_needs_review=False):
    """Deterministic shortlist. Never pads: fewer items are returned when fewer qualify.

    Returns (selected, review_queue, deferred); deferred records why a close alternative was not listed."""
    qualified = sorted((r for r in results if r["classification"] == "confirmed_eligible" and r["score"] >= min_score), key=_key)
    selected, per_employer, deferred = [], {}, []
    for r in qualified:
        n = per_employer.get(r["employer_key"], 0)
        if len(selected) >= size:
            deferred.append({"id": r["id"], "company": r["company"], "title": r.get("title"), "score": r["score"], "why": "beyond the shortlist size"})
        elif n >= max_per_employer:
            deferred.append({"id": r["id"], "company": r["company"], "title": r.get("title"), "score": r["score"],
                             "why": f"employer already has {max_per_employer} item(s) on the list (generic per-employer cap)"})
        else:
            selected.append(r)
            per_employer[r["employer_key"]] = n + 1
    queue = sorted((r for r in results if r["classification"] == "needs_review"), key=_key)[:size]
    if allow_fill_with_needs_review and len(selected) < size:
        selected += [r for r in queue if r not in selected][: size - len(selected)]
    return selected, queue, deferred[:15]


def select_exploration(results, size, max_per_employer):
    """The low-cost exploration lane: best-scoring exploration records, capped per employer. Never padded."""
    pool = sorted((r for r in results if r["classification"] == "exploration"), key=_key)
    out, per = [], {}
    for r in pool:
        if len(out) >= size:
            break
        if per.get(r["employer_key"], 0) >= max_per_employer:
            continue
        out.append(r)
        per[r["employer_key"]] = per.get(r["employer_key"], 0) + 1
    return out
