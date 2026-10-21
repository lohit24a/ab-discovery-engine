"""Conservative classification rules (Day 12): location eligibility, role-family matching, role screening and experience.
Pure functions, no database.

Principles
  * Unknown is unknown. Missing location is never approval; missing experience is never "senior" and never "junior".
  * "Remote" alone is not India-eligible; Remote-US / Remote-TX is foreign evidence, not an assumption of India.
  * A literal statement that allows India ("Remote - US or India", "India, Philippines (Remote)") is a literal statement:
    it is not downgraded to unknown just because another country is also allowed.
  * Employer headquarters is never location evidence. A foreign-only POSTING is incompatible; that says nothing about the
    COMPANY (an India-headquartered company can advertise a US-only role). These functions judge one posting at a time.
  * Geographic compatibility and the CONFIDENCE in that evidence are separate: literal / inferred / absent.
  * Every result carries the evidence (matched phrases, location segments) so explanations can quote it.
"""

import re
from dataclasses import dataclass, field

from .adapters.feashliaa import INDIA_CITIES, INDIA_STATES
from .identity import _US_STATES
from .profile import phrase_regex

# Best-first order used to combine several documented locations of one posting. A bare "India" says less than a named
# Indian city, so a documented city outranks a country-only entry (boards add a country-level fallback to every location list).
LOCATION_PRIORITY = ("bengaluru", "india_remote_explicit", "india_other_city", "india_country_only", "worldwide_remote",
                     "remote_regional_plausible", "remote_unqualified", "unknown", "foreign_remote", "foreign_onsite")
_SPLIT_LOCATIONS = re.compile(r"\s*(?:/|;|\|)\s*")
_TOKEN_SPLIT = re.compile(r"[,;/|()\-–—]")
_US_CODE = re.compile(r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])")
_US_WORD = re.compile(r"(?<![A-Za-z])(US|USA|U\.S\.A?\.?)(?![A-Za-z])")        # capitals only: "Remote-US", not the pronoun


@dataclass
class LocationResult:
    category: str                      # best category among the documented locations
    eligibility: str                   # confirmed | plausible | unknown | incompatible
    segments: list = field(default_factory=list)      # [(text, category)]
    bengaluru: bool = False
    confidence: str = "absent"         # literal | inferred | absent  (how directly the text states it)
    note: str = ""


def _compile_location(profile):
    cached = getattr(profile, "_loc_cache", None)
    if cached:
        return cached
    loc = profile.raw["location"]
    country = loc["remote_country"].strip()
    cache = {
        "bengaluru": phrase_regex(loc["onsite_or_hybrid_cities"]),
        "india_remote": phrase_regex(loc["india_remote_phrases"]),
        "remote": phrase_regex(loc["generic_remote_terms"]),
        "foreign": phrase_regex(loc["foreign_markers"]),
        "country": phrase_regex([country, "bharat"] if country.lower() == "india" else [country]),
        "worldwide": phrase_regex(loc["worldwide_phrases"]),
        "regions": phrase_regex(loc["india_inclusive_regions"]),
    }
    profile._loc_cache = cache
    return cache


def _foreign_evidence(text, rx):
    if rx["foreign"].search(text) or _US_WORD.search(text):
        return True
    return any(code in _US_STATES for code in _US_CODE.findall(text))


def _city_tokens(text):
    tokens = {t.strip().casefold() for t in _TOKEN_SPLIT.split(text)}
    return bool(tokens & INDIA_CITIES or tokens & INDIA_STATES)


def classify_segment(text, profile):
    """One documented location -> (category, confidence)."""
    rx = _compile_location(profile)
    s = (text or "").strip()
    if not s:
        return "unknown", "absent"
    india = bool(rx["country"].search(s))
    foreign = _foreign_evidence(s, rx)
    remote = bool(rx["remote"].search(s))
    if rx["bengaluru"].search(s):
        return "bengaluru", "literal"
    if rx["india_remote"].search(s) or (india and remote):
        return "india_remote_explicit", "literal"                  # India is literally allowed, even if other countries are too
    if india:
        return ("india_other_city" if _city_tokens(s) else "india_country_only"), "literal"
    if not foreign and _city_tokens(s):
        return "india_other_city", "inferred"                       # an Indian city/state with the country unstated
    if not foreign and rx["worldwide"].search(s):
        return "worldwide_remote", "literal"
    if remote or rx["regions"].search(s):
        if foreign:
            return "foreign_remote", "literal"
        if rx["regions"].search(s):
            return "remote_regional_plausible", "literal"           # for example "Remote - APAC": the region contains India
        return "remote_unqualified", "absent"
    return ("foreign_onsite", "literal") if foreign else ("unknown", "absent")


def classify_location_segment(text, profile):
    return classify_segment(text, profile)[0]


def _split(text, profile):
    parts = [p.strip() for p in _SPLIT_LOCATIONS.split(text or "") if p.strip()]
    cats = [classify_segment(p, profile) for p in parts]
    # "United States | Remote": a bare Remote beside only foreign places is remote WITHIN them, not remote anywhere
    foreign_only = [c for c, _ in cats if c != "remote_unqualified"]
    if len(cats) > 1 and foreign_only and all(c in ("foreign_onsite", "foreign_remote") for c in foreign_only):
        cats = [("foreign_remote", "literal") if c == "remote_unqualified" else (c, conf) for c, conf in cats]
    return list(zip(parts, cats))


def classify_location(texts, profile):
    """Combine every documented location of a posting (for a merged job pass effective_locations)."""
    segments = []
    for t in texts or []:
        for part, (cat, conf) in _split(t, profile):
            segments.append((part, cat, conf))
    if not segments:
        return LocationResult("unknown", "unknown", [], False, "absent", "no location recorded")
    best = min((c for _, c, _ in segments), key=LOCATION_PRIORITY.index)
    conf = next(cf for _, c, cf in segments if c == best)
    treat_other = profile.raw["location"]["other_india_cities"]
    if best in ("bengaluru", "india_remote_explicit"):
        elig = "confirmed"
    elif best in ("india_country_only", "remote_regional_plausible"):
        elig = "plausible"
    elif best == "worldwide_remote":
        elig = "confirmed" if profile.raw["location"]["worldwide_remote"] == "confirmed" else "plausible"
    elif best == "india_other_city":
        elig = "plausible" if treat_other == "needs_review" else "incompatible"
    elif best in ("foreign_remote", "foreign_onsite"):
        elig = "incompatible"
    else:
        elig = "unknown"
    return LocationResult(best, elig, [(t, c) for t, c, _ in segments], any(c == "bengaluru" for _, c, _ in segments), conf, "")


# ------------------------------------------------------------------------------ role families

RANK = {"strong": 3, "weak_corroborated": 2, "weak": 1}


@dataclass
class FamilyHit:
    family: str
    strength: str                      # strong | weak_corroborated | weak
    aliases: list
    corroboration: list
    negatives: list
    signals: list


@dataclass
class RoleMatch:
    hits: list = field(default_factory=list)          # best first
    best: object = None

    @property
    def has_signal(self):
        return self.best is not None


def _find(rx, text):
    return sorted({m.group(0).lower() for m in rx.finditer(text)}) if rx and text else []


def _spans(rx, text):
    return [(m.start(), m.end(), m.group(0).lower()) for m in rx.finditer(text)] if rx and text else []


def _overlaps(a, bs):
    return any(a[0] < b[1] and b[0] < a[1] for b in bs)


def match_roles(title, profile, company_text="", description=""):
    """Role-family evidence for a title (plus optional company description and saved job-description text).

    A negative term that OVERLAPS an alias vetoes that alias ("Associate Product Marketing Manager" is not "associate
    product"). A weak alias such as "business analyst" is corroborated only by a corroborating term elsewhere in the TITLE
    or by company_context_terms in the company's own recorded description; otherwise it stays 'weak' (needs review)."""
    hits = []
    t = title or ""
    for name, fam in profile.families.items():
        neg = _spans(fam.re_negative, t)
        strong_sp = [s for s in _spans(fam.re_strong, t) if not _overlaps(s, neg)]
        weak_sp = [s for s in _spans(fam.re_weak, t) if not _overlaps(s, neg) and not any(s[2] in x[2] for x in strong_sp)]
        strong, weak = sorted({s[2] for s in strong_sp}), sorted({s[2] for s in weak_sp})
        if not strong and not weak:
            continue
        rest = t
        for rx_alias in (fam.re_strong, fam.re_weak):
            if rx_alias:
                rest = rx_alias.sub(" ", rest)
        corroboration = _find(fam.re_corroborating, rest)
        if fam.re_context and company_text and fam.re_context.search(company_text):
            corroboration = corroboration + ["company:" + x for x in _find(fam.re_context, company_text)]
        strength = "strong" if strong else ("weak_corroborated" if corroboration else "weak")
        alias_spans = strong_sp + weak_sp
        negatives = sorted({n[2] for n in neg if not _overlaps(n, [(a[0], a[1]) for a in alias_spans])})
        hits.append(FamilyHit(name, strength, strong or weak, corroboration, negatives, _find(fam.re_signals, description)))
    order = {n: i for i, n in enumerate(profile.families)}
    hits.sort(key=lambda h: (-RANK[h.strength], -len(h.aliases), order[h.family]))
    return RoleMatch(hits, hits[0] if hits else None)


# ------------------------------------------------------------------------------ role screening

@dataclass
class Screen:
    kind: str                          # non_target | exploration | none
    terms: list = field(default_factory=list)
    ai_engineering: bool = False


def screen_title(title, profile):
    """For a title that matched NO role family: is it explicitly a different job (non_target), plausible enough to look at
    (exploration), or neither (none)? Aliases always win, so this never removes 'Solutions Engineer' for the word 'engineer'."""
    cache = getattr(profile, "_screen_cache", None)
    if cache is None:
        sc = profile.raw["role_screening"]
        cache = {"nt": phrase_regex(sc["non_target_title_terms"]), "ex": phrase_regex(sc["exploration_signals"]),
                 "ai": phrase_regex(sc["ai_engineering_terms"])}
        profile._screen_cache = cache
    t = title or ""
    ai = _find(cache["ai"], t)
    if ai:
        if profile.raw["role_screening"]["ai_engineering_treatment"] == "non_target":
            return Screen("non_target", ai, True)
        return Screen("exploration", ai, True)
    nt = _find(cache["nt"], t)
    if nt:
        return Screen("non_target", nt)
    ex = _find(cache["ex"], t)
    return Screen("exploration", ex) if ex else Screen("none")


# ------------------------------------------------------------------------------ experience

@dataclass
class ExperienceResult:
    category: str                      # early_career | mid | senior | executive | unknown
    flags: list = field(default_factory=list)       # reason-code fragments
    detail: str = ""


# "Head" as the role itself is a department-head title: "Head of Product", "Head - Business Finance", "Business Head", "Department
# Head". "head" inside a reporting line is not: "Founder's Office Associate, reporting to the Head of Growth", "Chief of Staff to the
# Head of Sales", "Assistant to Department Head". Whole words only, so headquarters / headcount / head office never count. Title-only
# evidence, like every other title term; a description that mentions a head of department changes nothing.
_HEAD_REPORTING = re.compile(r"\b(?:reporting\s+to|reports\s+to|assistant\s+to|office\s+of|to|for|under|supporting)\s+(?:the\s+|a\s+)?"
                             r"[^,;|()\[\]]*?\bhead\b[^,;|()\[\]]*", re.I)
_HEAD_UNITS = (r"department|departmental|division|divisional|business|function|functional|vertical|regional|country|zonal|zone|practice|unit|"
               r"category|cluster|branch|city|state|area|circle|centre|center|product|sales|marketing|finance|operations|ops|growth|content|"
               r"service|services|stores|supply|procurement|hr|people|acquisition|engineering|technology|design|brand|partnerships|strategy|"
               r"program|programme|project|plant|site|delivery|group")
_HEAD_ROLE = re.compile(r"^\W*head\b(?![-\s]*(?:office|quarters?|count|hunt))"                 # Head ..., at the start
                        r"|\bhead\s*(?:of\b|[-–—:,|](?!\s*hunt))"                      # Head of X / Head - X / Head, X
                        r"|\b(?:" + _HEAD_UNITS + r")\s+head\b", re.I)                            # Product Head, Department Head, Regional Head


def _compile_experience(profile):
    cached = getattr(profile, "_exp_cache", None)
    if cached:
        return cached
    e = profile.raw["experience"]
    cache = {"early_title": phrase_regex(e["early_career_title_terms"]), "early_text": phrase_regex(e["early_career_text_signals"]),
             "senior": phrase_regex(e["senior_title_terms"]), "exec": phrase_regex(e["executive_title_terms"]),
             "intern": phrase_regex(e["internship_title_terms"]), "mid": phrase_regex(e["mid_level_title_terms"])}
    profile._exp_cache = cache
    return cache


def classify_experience(title, profile, min_years=None, max_years=None, text=None, employer_stated=False):
    """Early-career / senior evidence. Absent evidence is 'unknown', never 'senior'.

    PRECEDENCE POLICY (operator-approved, Day 14): a reliable, explicit, EMPLOYER-provided minimum (`employer_stated=True`, which the candidate builder sets only
    for sources that parse the employer's own posting, never for user-supplied hints or aggregator labels) outranks seniority inferred solely from a senior TITLE
    term ('Lead', 'Senior', ...). The conflict stays visible (EXP_TITLE_CONFLICT and a sentence in the detail). A stated minimum is not proof that a role is junior,
    so leadership duties are not assessed here; executive titles are not overridden; missing evidence stays 'unknown'."""
    rx = _compile_experience(profile)
    e = profile.raw["experience"]
    flags, detail = [], []
    t = title or ""
    scrubbed = re.sub(r"chief of staff", " ", t, flags=re.I)           # "chief of staff" is a target role, not an executive title
    scrubbed = _HEAD_REPORTING.sub(" ", scrubbed)                       # a head named in a reporting line is not this role's level
    exec_hits = _find(rx["exec"], scrubbed)
    if _HEAD_ROLE.search(scrubbed) and not any(h.startswith("head") for h in exec_hits):
        exec_hits = exec_hits + ["head (department head)"]
    senior_hits = _find(rx["senior"], scrubbed)
    early_title = _find(rx["early_title"], t)
    early_text = _find(rx["early_text"], text or "")
    category = "unknown"
    if min_years is not None:
        if min_years <= e["early_career_max_years"]:
            category, flags = "early_career", flags + ["EXP_STATED_EARLY"]
        elif min_years >= e["senior_min_years"]:
            category, flags = "senior", flags + ["EXP_STATED_SENIOR"]
        else:
            category, flags = "mid", flags + ["EXP_STATED_MID"]
        detail.append(f"stated minimum {min_years:g} years")
    if early_text:
        flags.append("EXP_TEXT_EARLY")
        detail.append("experience text: " + ", ".join(early_text))
        if category in ("unknown", "mid"):
            category = "early_career"
    if exec_hits:
        category = "executive"
        flags.append("TITLE_EXECUTIVE")
        detail.append("executive title term: " + ", ".join(exec_hits))
    elif senior_hits:
        if employer_stated and min_years is not None and min_years < e["senior_min_years"]:
            flags += ["TITLE_SENIOR_OVERRIDDEN", "EXP_TITLE_CONFLICT"]
            detail.append(f"title says '{', '.join(senior_hits)}' but the employer states a minimum of {min_years:g} years: the employer's figure takes precedence "
                          "(a stated minimum is not proof the role is junior, and leadership duties are not assessed)")
        else:
            if category in ("unknown", "early_career", "mid"):
                category = "senior"
            flags.append("TITLE_SENIOR")
            detail.append("senior title term: " + ", ".join(senior_hits))
    if early_title and not (exec_hits or senior_hits):
        flags.append("TITLE_JUNIOR")
        detail.append("early-career title term: " + ", ".join(early_title))
        if category == "unknown":
            category = "early_career"
    elif early_title:
        flags.append("TITLE_JUNIOR_BUT_SENIOR")
    if _find(rx["intern"], t):
        flags.append("INTERNSHIP")
        detail.append("internship title: employment type is not a regular role")
    mid_hits = _find(rx["mid"], scrubbed)
    if mid_hits and not (exec_hits or senior_hits):
        flags.append("TITLE_MID_LEVEL")
    if category == "unknown":
        flags.append("EXP_UNKNOWN")
    return ExperienceResult(category, flags, "; ".join(detail))
