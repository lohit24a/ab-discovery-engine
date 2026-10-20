"""Day 12: conservative location, role-family and experience rules."""
import copy

import pytest

from discovery import filters as f
from discovery import profile as prof


@pytest.fixture(scope="module")
def profile():
    return prof.load_profile()


@pytest.mark.parametrize("text,category,eligibility", [
    ("Bengaluru, Karnataka, India", "bengaluru", "confirmed"),
    ("Bangalore", "bengaluru", "confirmed"),
    ("Hybrid - Bengaluru", "bengaluru", "confirmed"),
    ("Remote - India", "india_remote_explicit", "confirmed"),
    ("India (Remote)", "india_remote_explicit", "confirmed"),
    ("Remote", "remote_unqualified", "unknown"),
    ("Anywhere", "remote_unqualified", "unknown"),
    ("Remote-US", "foreign_remote", "incompatible"),
    ("Remote, TX", "foreign_remote", "incompatible"),
    ("Remote - United States", "foreign_remote", "incompatible"),
    ("San Francisco, CA", "foreign_onsite", "incompatible"),
    ("London, United Kingdom", "foreign_onsite", "incompatible"),
    ("", "unknown", "unknown"),
    ("India", "india_country_only", "plausible"),
    ("Pune, Maharashtra, India", "india_other_city", "plausible"),
    ("Remote (United States or India)", "india_remote_explicit", "confirmed"),   # India is literally named as allowed
    ("Worldwide", "worldwide_remote", "plausible"),
    ("Remote - Banglore", "bengaluru", "confirmed"),
    ("United States | Remote", "foreign_remote", "incompatible"),
    ("Remote, APAC", "remote_regional_plausible", "plausible"),
])
def test_single_location_policy(profile, text, category, eligibility):
    r = f.classify_location([text], profile)
    assert (r.category, r.eligibility) == (category, eligibility)


def test_several_documented_locations_are_all_kept_and_the_best_counts(profile):
    r = f.classify_location(["Noida, India", "Bengaluru - GS, India"], profile)
    assert r.category == "bengaluru" and r.bengaluru and len(r.segments) == 2
    r = f.classify_location(["Bangalore, India ; Jakarta, Indonesia; Singapore"], profile)
    assert r.bengaluru and r.eligibility == "confirmed" and len(r.segments) == 3


def test_foreign_only_is_incompatible_but_unknown_or_unqualified_remote_keeps_it_open(profile):
    assert f.classify_location(["Remote, TX", "New York"], profile).eligibility == "incompatible"
    assert f.classify_location(["Remote, TX", "Remote"], profile).eligibility == "unknown"      # unqualified remote could be anywhere
    assert f.classify_location(["Remote, TX", ""], profile).eligibility == "incompatible"       # an empty entry is not evidence
    assert f.classify_location([], profile).eligibility == "unknown"                           # no location: unknown, not approved


def test_other_india_cities_follow_the_profile_decision(profile):
    raw = copy.deepcopy(profile.raw)
    raw["location"]["other_india_cities"] = "incompatible"
    strict = prof.build_profile(raw)
    assert f.classify_location(["Pune, India"], strict).eligibility == "incompatible"
    assert f.classify_location(["Pune, India"], profile).eligibility == "plausible"


@pytest.mark.parametrize("title,family,strength", [
    ("Associate Product Manager", "associate_product_ops", "strong"),
    ("Product Operations Associate", "associate_product_ops", "strong"),
    ("Forward Deployed Engineer - AI Agents", "forward_deployed_solutions", "strong"),
    ("Chief of Staff to the CEO", "founders_office", "strong"),
    ("Founding Associate, GTM Automation", None, None),
    ("AI Implementation Specialist", "ai_implementation", "strong"),
    ("Venture Builder", "venture_studio", "strong"),
    ("FP&A Analyst", "business_finance_ops", "strong"),
    ("Entrepreneur in Residence", "venture_studio", "strong"),
])
def test_unusual_but_relevant_titles_survive(profile, title, family, strength):
    m = f.match_roles(title, profile)
    assert m.has_signal
    if family:
        assert (m.best.family, m.best.strength) == (family, strength)


def test_generic_titles_are_weak_until_corroborated(profile):
    assert f.match_roles("Business Analyst", profile).best.strength == "weak"
    assert f.match_roles("Business Analyst - Finance", profile).best.strength == "weak_corroborated"
    assert f.match_roles("Product Manager", profile).best.strength == "weak"
    assert f.match_roles("Associate Product Manager", profile).best.strength == "strong"
    assert f.match_roles("Analyst", profile).best.strength == "weak"
    # company evidence can corroborate when the title alone cannot
    assert f.match_roles("Implementation Specialist", profile, company_text="An AI platform for support teams").best.strength == "weak_corroborated"


@pytest.mark.parametrize("title", ["Software Engineer", "AI Research Scientist", "Senior Data Engineer", "Account Executive", "Truck Driver", "Machine Learning Engineer"])
def test_ai_or_analyst_words_alone_do_not_make_a_role_relevant(profile, title):
    assert not f.match_roles(title, profile).has_signal


def test_negative_title_terms_are_recorded(profile):
    m = f.match_roles("Automation Engineer - Test Automation", profile)
    assert m.has_signal and m.best.negatives == ["test automation"]


@pytest.mark.parametrize("title,kwargs,category,flag", [
    ("Associate Product Manager", {}, "early_career", "TITLE_JUNIOR"),
    ("Product Analyst", {}, "unknown", "EXP_UNKNOWN"),
    ("Product Analyst", {"min_years": 1.0}, "early_career", "EXP_STATED_EARLY"),
    ("Product Analyst", {"min_years": 3.0}, "early_career", "EXP_STATED_EARLY"),
    ("Product Analyst", {"min_years": 4.0}, "mid", "EXP_STATED_MID"),
    ("Product Analyst", {"min_years": 6.0}, "senior", "EXP_STATED_SENIOR"),
    ("Senior Product Manager", {}, "senior", "TITLE_SENIOR"),
    ("Director of Operations", {}, "executive", "TITLE_EXECUTIVE"),
    ("Chief of Staff", {}, "unknown", "EXP_UNKNOWN"),                 # a target role, not an executive title
    ("Founding Designer", {"text": "Any (new grads ok)"}, "early_career", "EXP_TEXT_EARLY"),
    ("Senior Associate Analyst", {}, "senior", "TITLE_JUNIOR_BUT_SENIOR"),
])
def test_experience_classification(profile, title, kwargs, category, flag):
    r = f.classify_experience(title, profile, kwargs.get("min_years"), None, kwargs.get("text"))
    assert r.category == category and flag in r.flags


def test_missing_experience_is_never_senior(profile):
    r = f.classify_experience("Strategy Analyst", profile)
    assert r.category == "unknown" and "TITLE_SENIOR" not in r.flags and "EXP_STATED_SENIOR" not in r.flags
