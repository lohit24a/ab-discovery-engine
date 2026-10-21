"""Load and validate the search profile and the ranking weights (Day 12). Pure configuration; no database access.

Both files are editable YAML. The loaders refuse unknown keys, missing keys and wrong types so a typo is an error
rather than a silently different ranking. The weights are PROPOSALS until the operator approves them (status field).
Every key that is accepted is also USED by the code; test_settings_audit enforces that, so no setting can be ignored.
"""

import re
from dataclasses import dataclass, field

from . import database as db
from .yamlconfig import load_yaml

PROFILE_PATH = db.PROJECT_ROOT / "config" / "search_profile.yaml"
RANKING_PATH = db.PROJECT_ROOT / "config" / "ranking.yaml"

ROLE_TYPES = ("ai_implementation", "founders_office", "gtm_engineering", "forward_deployed_solutions",
              "associate_product_ops", "internal_ai_automation", "business_finance_ops", "venture_studio")
DATE_SEMANTICS = ("aggregator_first_seen", "provider_reported_creation", "user_supplied", "none")


class ConfigError(ValueError):
    """The profile or ranking file is invalid."""


def phrase_regex(phrases):
    """One case-insensitive regex matching any phrase on word boundaries (None for an empty list)."""
    parts = []
    for p in phrases or []:
        p = str(p).strip()
        if not p:
            continue
        esc = re.escape(p).replace(r"\ ", r"\s+")
        left = r"(?<![A-Za-z0-9])" if p[0].isalnum() else ""
        right = r"(?![A-Za-z0-9])" if p[-1].isalnum() else ""
        parts.append(left + esc + right)
    return re.compile("|".join(parts), re.I) if parts else None


@dataclass
class Family:
    name: str
    label: str
    meaning: str
    strong: list
    weak: list
    corroborating: list
    context: list
    negative: list
    signals: list
    re_strong: object = None
    re_weak: object = None
    re_corroborating: object = None
    re_context: object = None
    re_negative: object = None
    re_signals: object = None


@dataclass
class Profile:
    raw: dict
    families: dict = field(default_factory=dict)
    status: str = ""


_FAMILY_KEYS = {"label", "meaning", "strong_aliases", "weak_aliases", "corroborating_terms", "company_context_terms",
                "negative_title_terms", "responsibility_signals"}
_TOP_KEYS = {"version", "status", "role_types", "role_families", "location", "experience", "hard_exclusions", "role_screening",
             "company_interests", "generic_interest_categories", "unknown_handling", "verdicts", "shortlist"}
_LOCATION_KEYS = {"onsite_or_hybrid_cities", "remote_country", "india_remote_phrases", "generic_remote_terms", "worldwide_remote",
                  "worldwide_phrases", "india_inclusive_regions", "other_india_cities", "foreign_markers"}
_EXPERIENCE_KEYS = {"early_career_max_years", "senior_min_years", "unknown_treatment", "early_career_title_terms",
                    "early_career_text_signals", "senior_title_terms", "executive_title_terms", "executive_title_treatment",
                    "internship_treatment", "internship_title_terms", "mid_level_title_terms", "mid_level_title_treatment",
                    "source_reported_level_treatment"}
_EXCLUSION_KEYS = {"incompatible_location", "human_verdict_no", "closed_or_not_seen", "placeholder_company_label", "non_target_role"}
_SCREEN_KEYS = {"non_target_title_terms", "exploration_signals", "ai_engineering_terms", "ai_engineering_treatment"}
_UNKNOWN_KEYS = {"location", "remote_without_country", "experience", "availability", "weak_alias_without_corroboration", "missing_company_evidence"}
_VERDICT_KEYS = {"suppress_rejected", "never_modify_notes"}
_SHORTLIST_KEYS = {"jobs", "companies_only", "exploration_lane", "allow_fill_with_needs_review"}


def _check_keys(section, got, allowed, required=None):
    if not isinstance(got, dict):
        raise ConfigError(f"{section}: expected a mapping")
    unknown = sorted(set(got) - set(allowed))
    if unknown:
        raise ConfigError(f"{section}: unknown key(s) {unknown}")
    missing = sorted(set(required if required is not None else allowed) - set(got))
    if missing:
        raise ConfigError(f"{section}: missing key(s) {missing}")


def _strings(section, value, allow_empty=False):
    """A list of non-blank strings. Booleans and numbers are refused: a bare `no` or `on` in a hand-edited list must not pass silently."""
    if value is None and allow_empty:
        return []
    if not isinstance(value, list) or (not value and not allow_empty) or not all(isinstance(x, str) and x.strip() for x in value):
        raise ConfigError(f"{section}: expected a non-empty list of strings (quote words such as \"no\" or \"on\")")
    return [str(x) for x in value]


def _choice(section, value, options):
    if value not in options:
        raise ConfigError(f"{section} must be one of {list(options)}")


def build_profile(raw):
    _check_keys("profile", raw, _TOP_KEYS)
    if raw["version"] != 1:
        raise ConfigError("profile: unsupported version")
    if not isinstance(raw["status"], str) or not raw["status"]:
        raise ConfigError("profile: status must be a non-empty string")
    types = _strings("role_types", raw["role_types"])
    if list(types) != list(ROLE_TYPES):
        raise ConfigError(f"role_types must be exactly the eight agreed families in order: {list(ROLE_TYPES)}")
    fams = raw["role_families"]
    _check_keys("role_families", fams, ROLE_TYPES)
    families = {}
    for name in ROLE_TYPES:
        f = fams[name]
        _check_keys(f"role_families.{name}", f, _FAMILY_KEYS)
        fam = Family(name, str(f["label"]), str(f["meaning"]), _strings(f"{name}.strong_aliases", f["strong_aliases"]),
                     _strings(f"{name}.weak_aliases", f["weak_aliases"], True), _strings(f"{name}.corroborating_terms", f["corroborating_terms"], True),
                     _strings(f"{name}.company_context_terms", f["company_context_terms"], True),
                     _strings(f"{name}.negative_title_terms", f["negative_title_terms"], True),
                     _strings(f"{name}.responsibility_signals", f["responsibility_signals"], True))
        fam.re_strong, fam.re_weak = phrase_regex(fam.strong), phrase_regex(fam.weak)
        fam.re_corroborating, fam.re_context = phrase_regex(fam.corroborating), phrase_regex(fam.context)
        fam.re_negative, fam.re_signals = phrase_regex(fam.negative), phrase_regex(fam.signals)
        clash = {a.lower() for a in fam.strong} & {a.lower() for a in fam.weak}
        if clash:
            raise ConfigError(f"role_families.{name}: alias listed as both strong and weak: {sorted(clash)}")
        families[name] = fam
    seen = {}
    for name, fam in families.items():                         # an alias that is strong in two families is ambiguous
        for a in fam.strong:
            if a.lower() in seen and seen[a.lower()] != name:
                raise ConfigError(f"strong alias {a!r} appears in both {seen[a.lower()]} and {name}")
            seen[a.lower()] = name
    loc = raw["location"]
    _check_keys("location", loc, _LOCATION_KEYS)
    _choice("location.other_india_cities", loc["other_india_cities"], ("needs_review", "incompatible"))
    _choice("location.worldwide_remote", loc["worldwide_remote"], ("plausible", "confirmed"))
    if not isinstance(loc["remote_country"], str) or not loc["remote_country"].strip():
        raise ConfigError("location.remote_country must be a non-empty string")
    for k in ("onsite_or_hybrid_cities", "india_remote_phrases", "generic_remote_terms", "worldwide_phrases", "india_inclusive_regions", "foreign_markers"):
        _strings(f"location.{k}", loc[k])
    exp = raw["experience"]
    _check_keys("experience", exp, _EXPERIENCE_KEYS)
    if not all(isinstance(exp[k], (int, float)) and not isinstance(exp[k], bool) and exp[k] >= 0 for k in ("early_career_max_years", "senior_min_years")) \
            or exp["early_career_max_years"] >= exp["senior_min_years"]:
        raise ConfigError("experience: early_career_max_years must be below senior_min_years (both non-negative numbers)")
    _choice("experience.executive_title_treatment", exp["executive_title_treatment"], ("soft_penalty", "hard_exclude"))
    _choice("experience.internship_treatment", exp["internship_treatment"], ("flag_only", "needs_review"))
    _choice("experience.mid_level_title_treatment", exp["mid_level_title_treatment"], ("ignore", "soft_penalty"))
    _choice("experience.source_reported_level_treatment", exp["source_reported_level_treatment"], ("use", "ignore"))
    if exp["unknown_treatment"] != "neutral":
        raise ConfigError("experience.unknown_treatment must be neutral: missing experience never proves seniority")
    for k in ("early_career_title_terms", "early_career_text_signals", "senior_title_terms", "executive_title_terms", "internship_title_terms", "mid_level_title_terms"):
        _strings(f"experience.{k}", exp[k])
    _check_keys("hard_exclusions", raw["hard_exclusions"], _EXCLUSION_KEYS)
    for k, v in raw["hard_exclusions"].items():
        if not isinstance(v, bool):
            raise ConfigError(f"hard_exclusions.{k} must be true or false")
    scr = raw["role_screening"]
    _check_keys("role_screening", scr, _SCREEN_KEYS)
    for k in ("non_target_title_terms", "exploration_signals", "ai_engineering_terms"):
        _strings(f"role_screening.{k}", scr[k])
    _choice("role_screening.ai_engineering_treatment", scr["ai_engineering_treatment"], ("exploration", "non_target"))
    _check_keys("unknown_handling", raw["unknown_handling"], _UNKNOWN_KEYS)
    for k in ("location", "remote_without_country", "weak_alias_without_corroboration", "missing_company_evidence"):
        if raw["unknown_handling"][k] != "needs_review":
            raise ConfigError(f"unknown_handling.{k} must be needs_review: unknown evidence is never approval")
    if raw["unknown_handling"]["experience"] != "neutral" or raw["unknown_handling"]["availability"] != "neutral":
        raise ConfigError("unknown_handling.experience and .availability must be neutral")
    _check_keys("verdicts", raw["verdicts"], _VERDICT_KEYS)
    if raw["verdicts"]["never_modify_notes"] is not True:
        raise ConfigError("verdicts.never_modify_notes must stay true")
    if not isinstance(raw["verdicts"]["suppress_rejected"], bool):
        raise ConfigError("verdicts.suppress_rejected must be true or false")
    sl = raw["shortlist"]
    _check_keys("shortlist", sl, _SHORTLIST_KEYS)
    if not all(isinstance(sl[k], int) and not isinstance(sl[k], bool) and sl[k] > 0 for k in ("jobs", "companies_only")) \
            or not isinstance(sl["exploration_lane"], int) or isinstance(sl["exploration_lane"], bool) or sl["exploration_lane"] < 0:
        raise ConfigError("shortlist sizes must be positive integers (exploration_lane may be 0)")
    interests = raw["company_interests"]
    if not isinstance(interests, dict) or not interests:
        raise ConfigError("company_interests: expected a non-empty mapping")
    for k, v in interests.items():
        _strings(f"company_interests.{k}", v)
    generic = _strings("generic_interest_categories", raw["generic_interest_categories"], True)
    unknown = [g for g in generic if g not in interests]
    if unknown:
        raise ConfigError(f"generic_interest_categories names categories that do not exist: {unknown}")
    return Profile(raw=raw, families=families, status=raw["status"])


def load_profile(path=PROFILE_PATH):
    try:
        return build_profile(load_yaml(path))
    except OSError as exc:
        raise ConfigError(f"profile unreadable ({path}): {type(exc).__name__}") from exc


# ---------------------------------------------------------------------------- ranking weights

RANKING_SCHEMA = {
    "jobs": {
        "thresholds": ["confirmed_eligible_min_score", "needs_review_min_score"],
        "role_relevance": ["strong_alias", "weak_alias_corroborated", "weak_alias_uncorroborated", "secondary_family_bonus",
                           "negative_title_term_penalty", "responsibility_signal", "responsibility_signal_cap"],
        "experience": ["early_career_signal", "junior_title_term", "senior_title_penalty", "executive_title_penalty",
                       "stated_experience_within_early_career", "stated_experience_senior", "source_reported_senior_penalty",
                       "source_reported_entry_bonus", "recruiter_posted_penalty", "mid_level_title_penalty", "unknown"],
        "location": ["bengaluru", "india_remote_explicit", "india_country_only", "india_other_city", "worldwide_remote",
                     "remote_regional_plausible", "remote_unqualified", "unknown"],
        "exploration": ["exploration_signal", "ai_engineering_flag_penalty", "minimum_score"],
        "availability": ["employer_confirmed_open", "in_latest_check", "observed_earlier", "unknown", "historical"],
        "listing_age": ["fresh_days", "fresh_points", "recent_days", "recent_points", "stale_days", "stale_points"],
        "source_quality": ["ats_feed", "investor_board", "yc", "manual", "aggregator", "other"],
        "company": ["sector_interest_per_match", "sector_interest_cap", "startup_signal", "investor_board_listed", "investor_membership_cap",
                    "yc_company", "also_a_company_candidate"],
        "verdict": ["yes", "maybe"],
    },
    "companies": {
        "thresholds": ["eligible_min_score", "needs_review_min_score"],
        "product_relevance": ["sector_interest_per_match", "sector_interest_cap", "ai_native_bonus"],
        "startup_signals": ["yc_company", "yc_stage_or_batch_recorded", "small_team_recorded", "startup_signal_term", "startup_signal_cap"],
        "investor_evidence": ["per_investor_board_listing", "cap"],
        "india_connection": ["india_based", "potentially_relevant", "bengaluru_in_recorded_location", "unverified", "no_india_evidence_yet"],
        "source_confidence": ["description_recorded", "multiple_independent_sources", "single_source_name_only"],
        "jobs_presence": ["observed_job_listing", "observed_job_cap", "no_jobs"],
        "operating_status": ["inactive_penalty", "acquired_penalty"],
        "verdict": ["yes", "maybe"],
    },
    "session": {"": ["new_window_days"]},
    "shortlist": {"": ["max_per_employer", "minimum_score_to_list_jobs", "minimum_score_to_list_companies"]},
}
SESSION_SLOTS = ("new_discovery", "changed", "previously_unreviewed", "uncertain_valuable")
DATE_SOURCE_TYPES = ("ats_feed", "aggregator", "investor_board", "yc", "manual", "other")


def _number(where, v, non_negative=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (non_negative and v < 0):
        raise ConfigError(f"{where}: expected a {'non-negative ' if non_negative else ''}number")


def build_ranking(raw):
    _check_keys("ranking", raw, {"version", "status", "reference", "date_semantics", "jobs", "companies", "session", "shortlist"})
    if raw["version"] != 1:
        raise ConfigError("ranking: unsupported version")
    _check_keys("ranking.reference", raw["reference"], {"as_of"})
    _check_keys("ranking.date_semantics", raw["date_semantics"], DATE_SOURCE_TYPES)
    for k, v in raw["date_semantics"].items():
        _choice(f"ranking.date_semantics.{k}", v, DATE_SEMANTICS)
    for section in ("jobs", "companies"):
        schema = RANKING_SCHEMA[section]
        _check_keys(f"ranking.{section}", raw[section], schema.keys())
        for group, keys in schema.items():
            _check_keys(f"ranking.{section}.{group}", raw[section][group], keys)
            for k in keys:
                _number(f"ranking.{section}.{group}.{k}", raw[section][group][k])
    sess = raw["session"]
    _check_keys("ranking.session", sess, {"new_window_days", "slots"})
    _number("ranking.session.new_window_days", sess["new_window_days"], True)
    _check_keys("ranking.session.slots", sess["slots"], SESSION_SLOTS)
    for k, v in sess["slots"].items():
        _number(f"ranking.session.slots.{k}", v, True)
    _check_keys("ranking.shortlist", raw["shortlist"], RANKING_SCHEMA["shortlist"][""])
    for k, v in raw["shortlist"].items():
        _number(f"ranking.shortlist.{k}", v, True)
    j = raw["jobs"]["thresholds"]
    if j["confirmed_eligible_min_score"] < j["needs_review_min_score"]:
        raise ConfigError("ranking.jobs.thresholds: confirmed_eligible_min_score must not be below needs_review_min_score")
    c = raw["companies"]["thresholds"]
    if c["eligible_min_score"] < c["needs_review_min_score"]:
        raise ConfigError("ranking.companies.thresholds: eligible_min_score must not be below needs_review_min_score")
    return raw


def load_ranking(path=RANKING_PATH):
    try:
        return build_ranking(load_yaml(path))
    except OSError as exc:
        raise ConfigError(f"ranking unreadable ({path}): {type(exc).__name__}") from exc
