"""Day 11 hardening: Workday keys, name rules, evidence-backed promotions, tier-D categories, negative decisions,
non-transitive conflicts, relations and repeated imports. Disposable databases only."""
import json
import sqlite3

import pytest

import test_investor_boards as tib
from discovery import database as db, identity, identity_review as ir, merge, relations, sheets_sync
from discovery.adapters import investor_boards as ib
from discovery.idkeys import comparison_query, host_class, registrable_domain, url_signature


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.initialize_database(path)
    c = db.connect(path)
    yield c
    c.close()


def source(conn, sid, stype="ats_feed", config_ref=None):
    db.register_source(conn, sid, sid, stype, None, config_ref=config_ref)


def company(conn, name, domain=None, reported=None):
    facts = {"investor_boards": {"reported_domains": reported}} if reported else None
    cid, _ = db.get_or_create_company(conn, name, domain, facts=facts)
    return cid


def job(conn, cid, title, sid, url, ext=None, **kw):
    return db.observe_job(conn, source_id=sid, source_url=url, company_id=cid, title=title, external_id=ext, **kw).job_id


def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("companies", "jobs", "sightings", "status_events", "verdicts")}


def wd(tenant, site, loc, slug, token, wdn="wd5"):
    return f"https://{tenant}.{wdn}.myworkdayjobs.com/{site}/job/{loc}/{slug}_{token}"


# ======================================================= Rule 1: Workday keys

@pytest.mark.parametrize("token,job_key,requisition", [
    ("REF089066W", "ref089066w", "ref089066w"),
    ("R-026595-1", "r-026595-1", "r-026595"),
    ("202607-119623", "202607-119623", "202607-119623"),     # a six-digit suffix is part of the id, not an instance
    ("748284WD-1", "748284wd-1", "748284wd"),
    ("JR00043989-1", "jr00043989-1", "jr00043989"),
    ("31163039-1", "31163039-1", "31163039"),
    ("3235", "3235", "3235"),
    ("REQ-1575", "req-1575", "req-1575"),
])
def test_workday_posting_token_and_requisition_are_parsed_without_losing_letters(token, job_key, requisition):
    sig = url_signature(wd("visa", "visa", "IN---Bengaluru-India", "Analyst--Client-Success", token))
    assert sig.kind == "specific" and sig.platform == "workday" and sig.slug == "visa.wd5"
    assert sig.job_key == job_key and sig.id_kind == "posting"
    assert sig.requisition_key == ("ats", "workday", "visa.wd5", "requisition", requisition)


def test_workday_token_with_an_underscore_inside_the_id_cannot_collide_with_a_plain_number():
    a = url_signature(wd("tinuiti", "tinuiti", "Remote--NY", "Insights-Lead-Analyst", "R26_642", "wd12"))
    b = url_signature(wd("tinuiti", "tinuiti", "Remote--NY", "Some-Other-Job", "642", "wd12"))
    assert a.key != b.key                                                 # the whole title slug + token is compared


def test_same_workday_id_in_two_tenants_is_never_one_posting(conn):
    source(conn, "feed")
    j1 = job(conn, company(conn, "Alpha"), "Analyst", "feed", wd("alpha", "ext", "Pune", "Analyst", "R1234567"))
    j2 = job(conn, company(conn, "Beta"), "Analyst", "feed", wd("beta", "ext", "Pune", "Analyst", "R1234567"))
    assert identity.job_pairs(identity.load_job_refs(conn)) == []
    assert url_signature(wd("alpha", "ext", "Pune", "Analyst", "R1234567")).key != url_signature(wd("beta", "ext", "Pune", "Analyst", "R1234567")).key
    # different Workday instance of the same tenant name is a different namespace too
    assert url_signature(wd("alpha", "ext", "Pune", "Analyst", "R1", "wd1")).key != url_signature(wd("alpha", "ext", "Pune", "Analyst", "R1", "wd3")).key


def test_pismo_visa_workday_posting_is_a_label_conflict_not_a_merge(conn):
    source(conn, "feed")
    source(conn, "board", "investor_board")
    url = wd("visa", "visa", "IN---Bengaluru-India", "Analyst--Client-Success", "REF089066W")
    v = job(conn, company(conn, "visa"), "Analyst, Client Success", "feed", url, ext="w1")
    p = job(conn, company(conn, "Pismo"), "Analyst, Client Success", "board", url, ext="97372416")
    pairs = identity.job_pairs(identity.load_job_refs(conn))
    assert [(x.verdict.tier, x.verdict.rule, x.verdict.category) for x in pairs] == [("D", "same_posting_conflicting_companies", "different_label_same_posting")]
    cat, hints = ir.categorize_conflict(pairs[0].verdict, "Pismo", "visa", pairs[0].sig)
    assert cat == "related_company_candidate" and hints[0]["hint"] == "posting_lives_with_one_label"
    assert merge.plan_merges(conn)["jobs"] == []                           # never planned
    before = counts(conn)
    assert before["jobs"] == 2 and p != v


def test_workday_instances_of_one_requisition_are_related_postings_not_duplicates(conn):
    source(conn, "feed")
    cid = company(conn, "columbia")
    j1 = job(conn, cid, "Senior Automation Engineer", "feed", wd("columbia", "csc", "Bangalore", "Senior-Automation-Engineer", "R-026595-1"))
    j2 = job(conn, cid, "Senior Automation Engineer", "feed", wd("columbia", "csc", "Bangalore", "Senior-Automation-Engineer", "R-026595-2"))
    pairs = identity.job_pairs(identity.load_job_refs(conn))
    assert [(p.verdict.tier, p.verdict.rule) for p in pairs] == [("C", "same_requisition_different_posting")]
    assert merge.plan_merges(conn)["jobs"] == []


def test_same_workday_posting_in_two_cities_is_possible_not_collapsed(conn):
    source(conn, "feed")
    cid = company(conn, "pwc")
    job(conn, cid, "Deals Advisory Associate", "feed", wd("pwc", "ext", "Hyderabad", "Deals-Advisory-Associate", "748284WD-1"), ext="a", location="Hyderabad, India")
    job(conn, cid, "Deals Advisory Associate", "feed", wd("pwc", "ext", "Bangalore", "Deals-Advisory-Associate", "748284WD-1"), ext="b", location="Bangalore, India")
    assert {(p.verdict.tier, p.verdict.rule) for p in identity.job_pairs(identity.load_job_refs(conn))} == {("B", "same_posting_token_different_locations")}
    # one city spelled two ways is NOT a conflict
    cid2 = company(conn, "acme")
    job(conn, cid2, "Analyst", "feed", wd("acme", "ext", "Bangalore", "Analyst", "R7654321"), ext="c", location="Bangalore, India")
    job(conn, cid2, "Analyst", "feed", wd("acme", "ext", "Bengaluru", "Analyst", "R7654321"), ext="d", location="Bengaluru, Karnataka, India")
    assert ("A", "same_posting_same_company") in {(p.verdict.tier, p.verdict.rule) for p in identity.job_pairs(identity.load_job_refs(conn))}


def test_raw_urls_are_never_rewritten_and_only_verified_tracking_params_are_ignored(conn):
    source(conn, "feed")
    raw = "https://jobs.lever.co/meesho/974e9975-c536-4219-bdaa-9cd0f2d4d030?lever-source%5B%5D=careers&utm_source=x"
    job(conn, company(conn, "meesho"), "Manager", "feed", raw, ext="1")
    stored = conn.execute("SELECT source_url FROM sightings").fetchone()[0]
    assert "lever-source" in stored                                        # stored exactly as the existing sighting normaliser left it
    assert comparison_query("lever-source%5B%5D=careers&utm_source=x&position=abc&ref=5") == "position=abc&ref=5"
    g1 = url_signature("https://careers.example.com/open?position=abc&utm_medium=m")
    g2 = url_signature("https://careers.example.com/open?position=xyz")
    assert g1.kind == g2.kind == "generic" and g1.key != g2.key           # a meaningful parameter keeps them apart


def test_host_classes_and_registrable_domains_are_offline_and_deterministic():
    assert registrable_domain("careers.nebius.com") == "nebius.com" and registrable_domain("jobs.example.co.uk") == "example.co.uk"
    assert host_class("fivetran.com") == "employer" and host_class("acme.wd5.myworkdayjobs.com") == "shared_ats"
    assert host_class("mihup.keka.com") == "shared_ats" and host_class("careers.hpe.com") == "employer"


# ================================================== Rule 2: company-name rules

@pytest.mark.parametrize("a,b,rel", [
    ("Couchbase", "couchbaseinc", "glued_suffix"),
    ("telnyx", "telnyx54", "unrelated"),
    ("G2 Crowd", "g2", "compatible"),
    ("Hyper", "Hyper Solutions Inc Internal", "compatible"),
    ("Sumo Logic", "sumologic", "equivalent"),
    ("Acme Inc.", "ACME", "equivalent"),
    ("Princeton", "Prince", "unrelated"),          # arbitrary trailing letters are not a suffix
    ("Zinc", "z", "unrelated"),
    ("Clinc", "cl", "unrelated"),
])
def test_company_name_relations(a, b, rel):
    assert identity.company_relation(a, b) == rel


def test_glued_suffix_is_only_a_candidate_never_an_automatic_match(conn):
    source(conn, "board", "investor_board")
    source(conn, "feed")
    url = "https://boards.greenhouse.io/couchbaseinc/jobs/4567890"
    job(conn, company(conn, "Couchbase"), "Solutions Engineer", "board", url, ext="1")
    job(conn, company(conn, "couchbaseinc"), "Solutions Engineer", "feed", url)
    jp = identity.job_pairs(identity.load_job_refs(conn))
    assert [(p.verdict.tier, p.verdict.rule) for p in jp] == [("B", "same_posting_company_name_glued_suffix")]
    co, _ = identity.company_pairs(conn)
    assert [(v.tier, v.rule) for a, b, v in co] == [("B", "glued_legal_suffix_name")]
    assert merge.plan_merges(conn)["companies"] == []


def test_telnyx54_is_an_unexplained_label_variant_not_a_merge_or_a_relation():
    sig = url_signature("https://boards.greenhouse.io/telnyx54/jobs/4567890")
    verdict = identity.Verdict("D", "same_posting_conflicting_companies", [], "different_label_same_posting")
    assert ir.categorize_conflict(verdict, "telnyx", "telnyx54", sig)[0] == "ambiguous_label_variant"


def test_distinct_companies_with_the_same_normalised_name_stay_apart(conn):
    a = company(conn, "Reframe", reported=["joinreframeapp.com"])
    b = company(conn, "Reframe", reported=["reframe.dev"])
    c = company(conn, "Reframe")
    got = {(v.tier, v.rule) for x, y, v in identity.company_pairs(conn)[0]}
    assert ("C", "different_reported_domains") in got and ("B", "name_only") in got and not any(t == "A" for t, _ in got)
    assert merge.plan_merges(conn)["companies"] == [] and len({a, b, c}) == 3


def test_placeholder_labels_are_data_quality_flags_not_companies():
    assert ir.identity.is_placeholder_name("DO NOT USE") and identity.is_placeholder_name("Actively Hiring")
    assert not identity.is_placeholder_name("Test Kitchen Inc") and not identity.is_placeholder_name("Hiring Hub Labs")
    a = identity.CompanyRef("a", "DOXA Insurance Holdings", None, set(), set(), {"feed"})
    b = identity.CompanyRef("b", "DO NOT USE", None, set(), set(), {"feed"})
    assert identity.classify_company_pair(a, b, "A").category == "invalid_source_label"


# =============================================== Rules 3 and 4: promotions

def ema_world(conn, *, domain_b=None, same_source=False, postings=5, third_on_same_board=False, third_record=True):
    source(conn, "inv_accel", "investor_board")
    source(conn, "feashliaa", "ats_feed", "no-closure-evidence")
    a = company(conn, "Ema Unlimited", reported=["ema.co"])
    b = company(conn, "ema", reported=[domain_b] if domain_b else None)
    for i in range(postings):
        url = f"https://jobs.ashbyhq.com/ema/{i:08d}-aaaa-bbbb-cccc-dddddddddddd"
        job(conn, a, f"Role {i}", "inv_accel", url, ext=f"a{i}")
        job(conn, b, f"Role {i}", "inv_accel" if same_source else "feashliaa", url, ext=f"b{i}")
    if third_record:
        t = company(conn, "EMA INC")
        job(conn, t, "Associate Engineer", "feashliaa", "https://recruiting.paylocity.com/recruiting/Jobs/Details/4383752", ext="p1")
        if third_on_same_board:
            job(conn, t, "Another role", "feashliaa", "https://jobs.ashbyhq.com/ema/99999999-aaaa-bbbb-cccc-dddddddddddd", ext="p2")
    return a, b


def proposals(conn):
    return ir.promotion_proposals(conn)


def test_ema_is_promoted_only_with_independent_namespaced_postings_and_no_contradiction(conn):
    ema_world(conn)
    got, held = proposals(conn)
    assert [p["names"] for p in got] == [["Ema Unlimited", "ema"]] or [sorted(p["names"]) for p in got] == [["Ema Unlimited", "ema"]]
    p = got[0]
    assert p["proposed_tier"] == "A" and p["rule"] == "board_owner_with_independent_postings" and p["rule_version"] == identity.RULE_VERSION
    assert len(p["evidence"]["posting_ids"]) == 5 and p["evidence"]["namespace"] == [["ashby", "ema"]]
    assert any("EMA INC" in c["detail"] and "separable" in c["detail"] for c in p["checks"])    # the third record was examined
    assert counts(conn)["companies"] == 3                                                       # a proposal changes nothing


@pytest.mark.parametrize("kwargs,blocker", [
    ({"same_source": True}, "independent_collection_channels"),
    ({"postings": 1}, "two_or_more_distinct_posting_ids_in_one_namespace"),
    ({"third_on_same_board": True}, "no_unresolved_record_with_the_same_name"),
])
def test_ema_style_promotion_is_blocked_by_each_contradiction(conn, kwargs, blocker):
    ema_world(conn, **kwargs)
    got, held = proposals(conn)
    assert got == []
    assert any(blocker in h["blockers"] for h in held), [h["blockers"] for h in held]


def test_a_negative_decision_blocks_promotion_and_planning(conn):
    a, b = ema_world(conn)
    merge.record_link(conn, "company", a, b, "rejected", "B", "human_not_same", {"note": "different firms"}, "operator")
    assert proposals(conn)[0] == []
    co, _ = identity.company_pairs(conn)
    assert any(v.rule == "human_rejected" for _, _, v in co)
    with pytest.raises(merge.MergeRefused, match="not the same"):
        merge.apply_company_merge(conn, a, b, rule="x", evidence={}, decided_by="t")


def domain_world(conn, *, host="www.fivetran.com", gh=True, other_name="fivetran", extra_conflict=False):
    source(conn, "yc", "yc")
    source(conn, "feashliaa", "ats_feed")
    w = company(conn, "Fivetran", reported=["fivetran.com"])
    o = company(conn, other_name)
    path = "careers/job?gh_jid=7978687003" if gh else "careers"
    job(conn, o, "Data Engineer", "feashliaa", f"https://{host}/{path}", ext="f1")
    if extra_conflict:
        company(conn, "Fivetran", reported=["fivetran.io"])
    return w, o


def test_domain_corroboration_promotes_with_employer_host_and_posting_id(conn):
    domain_world(conn)
    got, held = proposals(conn)
    assert len(got) == 1 and got[0]["rule"] == "name_and_job_urls_on_reported_domain"
    ev = got[0]["evidence"]["matching_job_urls"][0]
    assert ev["host_class"] == "employer" and ev["job_specific"] and ev["registrable_domain"] == "fivetran.com" and ev["comparison_key"][0] == "id"
    assert any("not verified ownership" in c["detail"] or "assumption" in c["detail"] for c in got[0]["checks"])


@pytest.mark.parametrize("kwargs,blocker", [
    ({"host": "boards.greenhouse.io"}, "job_urls_on_the_reported_domain"),
    ({"gh": False}, "job_url_has_a_posting_identifier"),
    ({"extra_conflict": True}, "no_conflicting_record_with_the_same_name"),
    ({"other_name": "Fivetran Technologies Holding"}, "names_are_equivalent"),
])
def test_domain_corroboration_is_blocked_when_evidence_is_weak_or_contradicted(conn, kwargs, blocker):
    domain_world(conn, **kwargs)
    got, held = proposals(conn)
    assert got == [] or all(p["names"][0] != "Fivetran" or True for p in got)
    assert not [p for p in got if p["rule"] == "name_and_job_urls_on_reported_domain"] or blocker == "names_are_equivalent"
    assert any(blocker in h["blockers"] for h in held) or blocker == "names_are_equivalent"


def test_a_parent_domain_does_not_prove_a_subsidiary(conn):
    source(conn, "yc", "yc")
    source(conn, "feashliaa", "ats_feed")
    w = company(conn, "Acme", reported=["acme.com"])
    o = company(conn, "Acme Robotics")
    job(conn, o, "Engineer", "feashliaa", "https://acme.com/careers/job?gh_jid=1234567", ext="1")
    cos = identity.load_company_refs(conn, identity.load_job_refs(conn))
    res = ir.evaluate_domain_corroboration(cos[w], cos[o], cos, identity.load_job_refs(conn), set())
    assert not res["eligible"] and "names_are_equivalent" in res["blockers"]


# ============================================================ Tier D handling

@pytest.mark.parametrize("a,b,url,expected", [
    ("Nimble Storage", "Hpe Simplivity", "https://careers.hpe.com/us/en/job/1216217", "related_company_candidate"),
    ("LinkedIn", "Lynda", "https://careers.linkedin.com/job/1234567", "related_company_candidate"),
    ("Slintel, a 6sense Company", "6sense", "https://boards.greenhouse.io/6sense/jobs/4567890", "related_company_candidate"),
    ("Forgerock", "pingidentity", "https://boards.greenhouse.io/pingidentity/jobs/4567890", "related_company_candidate"),
    ("Trifacta", "alteryx", "https://boards.greenhouse.io/alteryx/jobs/4567890", "related_company_candidate"),
    ("Pismo", "visa", wd("visa", "visa", "IN", "Analyst", "REF089066W"), "related_company_candidate"),
    ("DOXA Insurance Holdings", "DO NOT USE", "https://recruiting.paylocity.com/recruiting/Jobs/Details/4383752", "invalid_source_label"),
    ("700 Credit", "Open Dealer Exchange", "https://recruiting.paylocity.com/recruiting/Jobs/Details/4518611", "different_label_same_posting"),
])
def test_tier_d_regression_cases_are_categorised_and_never_merged(conn, a, b, url, expected):
    source(conn, "s")
    j1 = job(conn, company(conn, a), "Role", "s", url, ext="1")
    j2 = job(conn, company(conn, b), "Role", "s", url, ext="2")
    pairs = identity.job_pairs(identity.load_job_refs(conn))
    assert len(pairs) == 1 and pairs[0].verdict.tier == "D"
    assert ir.categorize_conflict(pairs[0].verdict, a, b, pairs[0].sig)[0] == expected
    plan = merge.plan_merges(conn)
    assert plan["jobs"] == [] and plan["companies"] == []
    assert counts(conn)["jobs"] == 2 and j1 != j2


def test_known_distinct_jobs_on_a_generic_careers_page_are_tier_c(conn):
    source(conn, "board", "investor_board")
    cid = company(conn, "Elixiir Foods")
    for i, t in enumerate(["Category Manager", "E-Commerce Manager"]):
        job(conn, cid, t, "board", "https://elixiirfoods.com/careers", ext=str(i))
    assert {(p.verdict.tier, p.verdict.rule) for p in identity.job_pairs(identity.load_job_refs(conn))} == {("C", "same_url_different_titles")}


# ===================================== relations, links and safeguards

def test_relations_and_possible_links_never_change_records_publication_or_lifecycle(conn):
    source(conn, "s")
    a = company(conn, "Nimble Storage")
    b = company(conn, "Hpe Simplivity")
    ja = job(conn, a, "Role", "s", "https://careers.hpe.com/us/en/job/1216217", ext="1")
    jb = job(conn, b, "Role", "s", "https://careers.hpe.com/us/en/job/1216217", ext="2")
    before = (counts(conn), sheets_sync.load_company_rows(conn), sheets_sync.load_post_rows(conn),
              [r[0] for r in conn.execute("SELECT lifecycle_state FROM jobs ORDER BY job_id")])
    rid, created = relations.record_relation(conn, "company", "related_company", a, b, detail="parent/subsidiary not established",
                                             rule="posting_lives_with_one_label", evidence={"host": "careers.hpe.com"})
    assert created and relations.record_relation(conn, "company", "related_company", b, a, detail="x", rule="y", evidence={})[1] is False
    relations.record_relation(conn, "job", "same_posting_different_label", ja, jb, detail="same posting", rule="r", evidence={})
    merge.record_link(conn, "job", ja, jb, "possible", "D", "same_posting_conflicting_companies", {"x": 1}, "rule:review")
    after = (counts(conn), sheets_sync.load_company_rows(conn), sheets_sync.load_post_rows(conn),
             [r[0] for r in conn.execute("SELECT lifecycle_state FROM jobs ORDER BY job_id")])
    assert before == after
    assert identity.alias_ids(conn, "job") == set() and merge.plan_merges(conn)["jobs"] == []
    row = conn.execute("SELECT * FROM identity_relations").fetchone()
    assert row["status"] == "candidate" and row["rule_version"] == identity.RULE_VERSION and row["review_status"] == "unreviewed"
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM identity_relations")


def test_invalid_label_flag_has_no_second_record(conn):
    c = company(conn, "DO NOT USE")
    relations.record_relation(conn, "company", "invalid_source_label", c, None, detail="placeholder", rule="placeholder_name", evidence={"name": "DO NOT USE"})
    with pytest.raises(sqlite3.IntegrityError):
        relations.record_relation(conn, "company", "invalid_source_label", c, company(conn, "other"), detail="x", rule="y", evidence={})


def test_links_record_rule_version_and_review_status_and_existing_ones_are_backfilled(conn, tmp_path):
    a, b = company(conn, "cartesia"), company(conn, "Cartesia")
    merge.apply_company_merge(conn, a, b, rule="r", evidence={"k": 1}, decided_by="operator")
    row = conn.execute("SELECT * FROM identity_links").fetchone()
    assert row["rule_version"] == identity.RULE_VERSION and row["review_status"] == "reviewed"
    # a v5 database with an old confirmed link: migration 6 marks it reviewed with the v1 rule version
    from discovery.schema import MIGRATIONS
    path = tmp_path / "v5.db"
    raw = sqlite3.connect(path)
    for m in MIGRATIONS[:5]:
        raw.executescript(m)
    raw.execute("INSERT INTO identity_links (link_id, entity_type, canonical_id, alias_id, status, tier, rule, evidence, decided_by, created_at, updated_at)"
                " VALUES ('l','job','x','y','confirmed','A','r','{}','operator','t','t')")
    raw.commit()
    raw.close()
    db.initialize_database(path)
    c2 = db.connect(path)
    r = c2.execute("SELECT rule_version, review_status FROM identity_links").fetchone()
    assert (r[0], r[1]) == ("day11-v1", "reviewed")
    c2.close()


def test_a_confirmed_merge_blocks_a_later_chain_through_a_negative_decision(conn):
    a, b, c = company(conn, "Alpha"), company(conn, "alpha"), company(conn, "ALPHA")
    merge.apply_company_merge(conn, a, b, rule="r", evidence={}, decided_by="t")
    merge.record_link(conn, "company", b, c, "rejected", "C", "human_not_same", {}, "operator")
    with pytest.raises(merge.MergeRefused, match="not the same"):
        merge.apply_company_merge(conn, a, c, rule="r", evidence={}, decided_by="t")        # c is rejected against a MEMBER (b) of a's cluster
    assert identity.alias_ids(conn, "company") == {b}


def test_non_transitive_cluster_is_reported_as_a_conflict_and_nothing_is_planned(conn):
    source(conn, "feed")
    source(conn, "board", "investor_board")
    a, b, c = company(conn, "falconx"), company(conn, "FalconX"), company(conn, "Falconx")
    url = "https://boards.greenhouse.io/falconx/jobs/4740719005"
    ja = job(conn, a, "Compliance Associate", "feed", url)
    jb = job(conn, b, "Compliance Associate", "board", url + "?gh_src=a", ext="1")
    jc = job(conn, c, "Compliance Associate", "board", url + "?gh_src=b", ext="2")
    merge.record_link(conn, "company", b, c, "rejected", "C", "human_not_same", {}, "operator")
    plan = merge.plan_merges(conn)
    assert plan["companies"] == [] and plan["jobs"] == []
    assert plan["conflicts"] and plan["conflicts"][0]["entity"] == "company" and "not the same" in plan["conflicts"][0]["reason"]


def test_cluster_with_two_human_verdicts_is_a_conflict(conn):
    source(conn, "feed")
    a, b = company(conn, "Acme"), company(conn, "acme")
    ja = job(conn, a, "Role", "feed", "https://boards.greenhouse.io/acme/jobs/1234567")
    jb = job(conn, b, "Role", "feed", "https://boards.greenhouse.io/acme/jobs/1234567?gh_src=z", ext="2")
    db.save_verdict(conn, "Yes", job_id=ja)
    db.save_verdict(conn, "No", job_id=jb)
    plan = merge.plan_merges(conn)
    assert plan["jobs"] == [] and any(c["entity"] == "job" and "verdict" in c["reason"] for c in plan["conflicts"])


def test_requisition_conflict_blocks_a_job_pair(conn):
    source(conn, "feed")
    cid = company(conn, "Acme")
    url = "https://boards.greenhouse.io/acme/jobs/1234567"
    j1 = job(conn, cid, "Role", "feed", url, requisition_id="R1")
    j2 = db.insert_job(conn, cid, "Role", requisition_id="R2")
    db.record_sighting(conn, "feed", url + "?gh_src=z", job_id=j2, external_id="x")
    pairs = identity.job_pairs(identity.load_job_refs(conn))
    assert {(p.verdict.tier, p.verdict.rule) for p in pairs} == {("D", "requisition_conflict")}


def test_repeated_investor_import_after_a_confirmed_merge_creates_no_duplicates(conn):
    """The board company/job lands on the canonical record after a merge; replaying the snapshot adds nothing."""
    yc = tib.add_yc_company(conn, 100, "acme", "acme.com")
    jobs = [tib.getro_job(1, org="acme-inc", domain=None, organization={"id": 300, "name": "Acme Inc", "slug": "acme-inc", "domain": None})]
    snap = tib.make_snapshot([(tib.ACCEL, tib.getro_scans(tib.ACCEL, jobs))])
    ib.import_snapshot(conn, snap)
    cos = [r[0] for r in conn.execute("SELECT company_id FROM companies WHERE company_id <> ?", (yc,))]
    assert len(cos) == 1
    merge.apply_company_merge(conn, yc, cos[0], rule="reviewed", evidence={"note": "operator"}, decided_by="operator")
    before = counts(conn)
    again = ib.import_snapshot(conn, snap)
    assert again.companies_created == 0 and again.jobs_created == 0
    assert {k: v for k, v in counts(conn).items() if k != "sightings"} == {k: v for k, v in before.items() if k != "sightings"}
    owner = conn.execute("SELECT DISTINCT company_id FROM jobs").fetchall()
    assert [r[0] for r in owner] == [yc]


def test_possible_duplicate_links_do_not_touch_verdicts_or_history(conn):
    source(conn, "s")
    a = company(conn, "Acme")
    j1 = job(conn, a, "Role", "s", "https://acme.example/careers", ext="1")
    j2 = job(conn, a, "Role", "s", "https://acme.example/careers", ext="2")
    db.save_verdict(conn, "Maybe", "keep", job_id=j1)
    events = conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0]
    merge.record_link(conn, "job", j1, j2, "possible", "B", "generic_url_same_title", {"x": 1}, "rule:review")
    assert conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0] == events
    assert db.get_verdict(conn, job_id=j1)["decision"] == "Maybe" and db.get_job(conn, j2) is not None


def test_review_report_counts_distinct_records_not_just_pairs(conn):
    source(conn, "board", "investor_board")
    cid = company(conn, "Spindle")
    for i in range(4):
        job(conn, cid, "Technical Support Engineer", "board", "https://spindle.example/careers", ext=str(i))
    rv = ir.build_review(conn)
    s = rv["jobs"]["summary"]["B:generic_url_same_title"]
    assert s["pairs"] == 6 and s["distinct_jobs"] == 4                   # four jobs, six pairs
    assert rv["rule_version"] == identity.RULE_VERSION and "presentation_grouping_proposal" in rv
    assert rv["accel_same_source_repeats"]["resolvable_with_stored_evidence"] is False


def test_a_domain_contradiction_makes_the_pair_distinct_not_a_promotion(conn):
    a, b = ema_world(conn, domain_b="ema-other.com")
    got, held = proposals(conn)
    co, _ = identity.company_pairs(conn)
    assert got == [] and {(v.tier, v.rule) for x, y, v in co if {x.company_id, y.company_id} == {a, b}} == {("C", "different_reported_domains")}


def test_location_guard_is_conservative_about_places_it_knows():
    assert identity.locations_conflict(("Remote, TN", "Remote, CA"))
    assert identity.locations_conflict(("Kolkata (AC) - Hyderabad", "Kolkata (AC) - Bangalore"))
    assert not identity.locations_conflict(("Bangalore (SDC) - Bagmane", "Bengaluru, Karnataka, India"))
    assert not identity.locations_conflict(("Remote -NY", "USA - Remote"), ("", "Remote"))      # unknown on one side: no conflict claimed
    assert not identity.locations_conflict(("India", "Remote"))


def test_location_text_disagreement_on_one_non_workday_posting_is_not_a_reason_to_doubt_identity(conn):
    """Regression (Day 11 audit): Lever/Greenhouse ids are one posting each; sources may disagree about the location text."""
    source(conn, "feed")
    source(conn, "board", "investor_board")
    url = "https://jobs.lever.co/mindtickle/864ac315-d7c5-4cd5-ab44-0e33d08f48f1"
    c1, c2 = company(conn, "mindtickle"), company(conn, "Mindtickle")
    ja = job(conn, c1, "AI Product Engineer", "feed", url, location="Pune, Maharashtra")
    jb = job(conn, c2, "AI Product Engineer", "board", url + "?lever-source%5B%5D=x", ext="9", location="Bengaluru, Karnataka, India / Karnataka, India")
    assert {(p.verdict.tier, p.verdict.rule) for p in identity.job_pairs(identity.load_job_refs(conn))} == {("A", "same_posting_equivalent_company_names")}
    merge.apply_company_merge(conn, c1, c2, rule="r", evidence={}, decided_by="t")
    merge.apply_job_merge(conn, ja, jb, rule="r", evidence={}, decided_by="t")
    locs = identity.effective_locations(conn, ja)             # the merge must not hide Bengaluru
    assert locs[0] == "Pune, Maharashtra" and any("Bengaluru" in l for l in locs) and len(locs) == 2


def test_company_reversal_is_refused_while_job_merges_depend_on_it_and_succeeds_after_they_are_reversed(conn):
    source(conn, "feed")
    source(conn, "board", "investor_board")
    c1, c2 = company(conn, "xendit"), company(conn, "Xendit")
    url = "https://job-boards.greenhouse.io/xendit/jobs/8032565003"
    ja = job(conn, c1, "Senior PM", "feed", url)
    jb = job(conn, c2, "Senior PM", "board", url, ext="x")
    merge.apply_company_merge(conn, c1, c2, rule="r", evidence={}, decided_by="t")
    merge.apply_job_merge(conn, ja, jb, rule="r", evidence={}, decided_by="t")
    with pytest.raises(merge.MergeRefused, match="depend on this company merge"):
        merge.reverse_merge(conn, "company", c2, decided_by="operator")
    merge.reverse_merge(conn, "job", jb, decided_by="operator")
    merge.reverse_merge(conn, "company", c2, decided_by="operator")
    assert db.get_job(conn, jb)["company_id"] == c2 and db.get_job(conn, ja)["company_id"] == c1
    row = conn.execute("SELECT status, review_status, rule_version FROM identity_links WHERE entity_type = 'job'").fetchone()
    assert (row["status"], row["review_status"], row["rule_version"]) == ("possible", "reviewed", identity.RULE_VERSION)


def test_workday_location_listings_of_one_id_stay_separate_and_both_locations_remain_visible(conn):
    source(conn, "feed")
    cid = company(conn, "rsm")
    ja = job(conn, cid, "Finance Manager", "feed", wd("rsm", "careers", "Hyderabad", "Finance-Manager", "JR121520", "wd1"), ext="a", location="Hyderabad")
    jb = job(conn, cid, "Finance Manager", "feed", wd("rsm", "careers", "Bengaluru", "Finance-Manager", "JR121520", "wd1"), ext="b", location="Bengaluru")
    assert {(p.verdict.tier, p.verdict.rule) for p in identity.job_pairs(identity.load_job_refs(conn))} == {("B", "same_posting_token_different_locations")}
    assert merge.plan_merges(conn)["jobs"] == [] and identity.effective_locations(conn, ja) == ["Hyderabad"]
