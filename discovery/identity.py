"""Cross-source identity evidence for jobs and companies (Day 11). Read-only: nothing here changes the database.

Four separate ideas are kept apart (see identity_review for the full model):

  same entity        strong evidence two records are one real job / company          -> evidence tier A
  related entities   parent, subsidiary, acquired or legacy brand: NOT the same entity -> relation candidate, never a merge
  possible duplicate some evidence of overlap, identity unresolved                    -> evidence tier B
  distinct           the records are different (shared name/URL only, or a human said so) -> evidence tier C
  conflict           contradictory evidence that needs a human                        -> evidence tier D

Never used as evidence on their own: company name, job title, location, a generic careers-page URL. A job-specific URL
is evidence only while it identifies ONE posting; a URL shared by rows with different titles is treated as non-unique.
Every decision records the rule version so a changed rule can be audited.
"""

import json
import re
from urllib.parse import urlsplit
from collections import defaultdict
from dataclasses import dataclass, field

from .idkeys import (SHARED_ATS_DOMAINS, UrlSignature, host_class, host_of, path_uuid, registrable_domain, workable_shortcode,  # noqa: F401 (re-exported)
                     url_signature)

RULE_VERSION = "2026-10-21.2"
LOCATION_SCOPED_PLATFORMS = {"workday"}
TIERS = ("A", "B", "C", "D")

# Hosts that many unrelated companies share; a "domain" on one of them is never company identity.
SHARED_HOSTS = {"ycombinator.com", "producthunt.com", "linkedin.com", "github.com", "linktr.ee", "notion.site",
                "notion.so", "medium.com", "google.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
                "wellfound.com", "angel.co", "crunchbase.com", "getro.com", "considerhq.com"} | set(SHARED_ATS_DOMAINS)

_LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "llp", "ltd", "limited", "pvt", "private", "plc", "corp",
                   "corporation", "gmbh", "ag", "bv", "pte", "sa", "sas"}
# Known places used ONLY to detect that two otherwise identical postings are for different locations (so a multi-location
# posting is never collapsed into one). Aliases map to one canonical name. A place not listed here is simply not compared.
_CITY_ALIASES = {"bangalore": "bengaluru", "bengaluru": "bengaluru", "bombay": "mumbai", "mumbai": "mumbai", "gurgaon": "gurugram",
                 "gurugram": "gurugram", "noida": "noida", "delhi": "delhi", "new delhi": "delhi", "hyderabad": "hyderabad",
                 "pune": "pune", "chennai": "chennai", "madras": "chennai", "kolkata": "kolkata", "calcutta": "kolkata",
                 "ahmedabad": "ahmedabad", "jaipur": "jaipur", "kochi": "kochi", "cochin": "kochi", "coimbatore": "coimbatore",
                 "chandigarh": "chandigarh", "indore": "indore", "nagpur": "nagpur", "lucknow": "lucknow", "mysore": "mysuru",
                 "mysuru": "mysuru", "london": "london", "new york": "new york", "san francisco": "san francisco",
                 "singapore": "singapore", "dubai": "dubai", "tokyo": "tokyo", "sydney": "sydney", "toronto": "toronto",
                 "berlin": "berlin", "dublin": "dublin", "seattle": "seattle", "austin": "austin", "boston": "boston"}
# Complete legal suffixes that may appear glued to a slug-like single-word name ('couchbaseinc'). Candidate evidence only.
_GLUEABLE_SUFFIXES = ("incorporated", "corporation", "limited", "inc", "llc", "ltd", "corp", "gmbh", "plc", "pvt")
# Whole-label source placeholders that are data-quality problems, not companies. Exact normalised matches only.
_PLACEHOLDER_NAMES = {"do not use", "donotuse", "dnu", "actively hiring", "now hiring", "hiring now", "confidential",
                      "stealth", "stealth startup", "stealth company", "unknown", "unknown company", "test", "test company",
                      "n a", "tbd", "various", "multiple companies", "company name"}


# ------------------------------------------------------------------ names

def name_key(name):
    """Comparison key for a company name: case, punctuation, a leading 'the' and trailing legal suffix tokens ignored."""
    tokens = re.sub(r"[^a-z0-9]+", " ", (name or "").casefold().replace("&", " and ")).split()
    if tokens and tokens[0] == "the" and len(tokens) > 1:
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def title_key(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").casefold()).strip()


def compact_key(name):
    return name_key(name).replace(" ", "")


_US_STATES = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "IL", "IA", "KS", "KY", "LA", "MD", "MA", "MI", "MN", "MS", "MO",
              "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
              "WI", "WY", "DC"}          # IN, OR, ME, OK, HI, ID, AS are left out: too easily ordinary words or country prefixes


def cities_in(text):
    """Known places mentioned in a location string or URL location segment: canonical cities plus US state codes written
    in capitals ('Remote, TN'). Empty when nothing is recognised."""
    raw = text or ""
    s = " " + re.sub(r"[^a-z0-9]+", " ", raw.casefold()).strip() + " "
    places = {canon for alias, canon in _CITY_ALIASES.items() if f" {alias} " in s}
    places |= {f"us-{m}" for m in re.findall(r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])", raw) if m in _US_STATES}
    return places


def locations_conflict(*pairs):
    """True when, for any (x, y) of location texts, both name known places and the sets are not identical. Conservative:
    'Kolkata + Hyderabad' vs 'Kolkata + Bengaluru' is a conflict. A text that names no known place never creates or hides one."""
    for x, y in pairs:
        cx, cy = cities_in(x), cities_in(y)
        if cx and cy and cx != cy:
            return True
    return False


def is_placeholder_name(name):
    return re.sub(r"[^a-z0-9]+", " ", (name or "").casefold()).strip() in _PLACEHOLDER_NAMES


def glued_suffix_stem(name):
    """If the name is ONE slug-like word ending in a complete legal suffix ('couchbaseinc'), return the stem
    ('couchbase'), else None. Trailing digits or arbitrary letters are never stripped ('telnyx54' stays as it is)."""
    k = name_key(name)
    if " " in k or not k.isalpha():
        return None
    for suf in _GLUEABLE_SUFFIXES:
        if k.endswith(suf) and len(k) - len(suf) >= 4:
            return k[:-len(suf)]
    return None


def company_relation(name_a, name_b):
    """'equivalent' (same name key, spaces ignored), 'glued_suffix' (one name is the other plus a glued legal suffix),
    'compatible' (one is a whole-word prefix of the other, e.g. 'ema' / 'ema unlimited'), or 'unrelated'.
    Only 'equivalent' ever counts toward identity; the others are hints for a human."""
    a, b = name_key(name_a), name_key(name_b)
    if not a or not b:
        return "unrelated"
    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return "equivalent"
    sa, sb = glued_suffix_stem(name_a), glued_suffix_stem(name_b)
    if (sa and sa == b.replace(" ", "")) or (sb and sb == a.replace(" ", "")):
        return "glued_suffix"
    ta, tb = a.split(), b.split()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return "compatible" if long_[:len(short)] == short else "unrelated"


# ------------------------------------------------------------- data shapes

@dataclass
class JobRef:
    job_id: str
    company_id: str
    company_name: str
    title: str
    state: str
    sources: set = field(default_factory=set)
    external_ids: dict = field(default_factory=dict)      # source_id -> provider id
    urls: set = field(default_factory=set)
    first_seen_at: str = ""
    requisition_id: str = None
    location: str = ""


@dataclass
class Verdict:
    tier: str
    rule: str
    reasons: list
    category: str = ""            # refined label for tier D (see identity_review)
    rule_version: str = RULE_VERSION

    def as_dict(self):
        return {"tier": self.tier, "rule": self.rule, "reasons": self.reasons, "category": self.category,
                "rule_version": self.rule_version}


@dataclass
class JobPair:
    a: str
    b: str
    key: tuple
    kind: str
    verdict: Verdict
    relation: str
    cross_source: bool
    same_source: bool
    sig: UrlSignature = None


# -------------------------------------------------------------------- jobs

def load_job_refs(conn):
    refs = {}
    merged_away = alias_ids(conn, "job")       # already merged into another job: no longer a separate candidate
    for r in conn.execute("""SELECT j.job_id, j.company_id, c.name AS cname, j.title, j.lifecycle_state, j.first_seen_at,
                                    j.requisition_id, j.posting_url, j.location
                             FROM jobs j JOIN companies c USING (company_id)"""):
        if r["job_id"] in merged_away:
            continue
        ref = JobRef(r["job_id"], r["company_id"], r["cname"], r["title"], r["lifecycle_state"],
                     first_seen_at=r["first_seen_at"], requisition_id=r["requisition_id"], location=r["location"] or "")
        if r["posting_url"]:
            ref.urls.add(r["posting_url"])
        refs[r["job_id"]] = ref
    for r in conn.execute("SELECT job_id, source_id, source_url, external_id FROM sightings WHERE job_id IS NOT NULL"):
        ref = refs.get(r["job_id"])
        if ref:
            ref.sources.add(r["source_id"])
            ref.urls.add(r["source_url"])
            if r["external_id"]:
                ref.external_ids[r["source_id"]] = r["external_id"]
    return refs


def classify_job_pair(a, b, sig, url_is_unique, relation, locs=()):
    """Tier for two DIFFERENT job rows that share URL signature `sig`.

    url_is_unique: every row sharing this URL has the same title (the URL demonstrably names one posting).
    locs: (x, y) location texts to compare for a conflict (stored locations and URL location paths)."""
    reasons = []
    if is_placeholder_name(a.company_name) or is_placeholder_name(b.company_name):
        return Verdict("D", "placeholder_company_label", ["a company label is a source placeholder, not a company"],
                       "invalid_source_label")
    same_title = title_key(a.title) == title_key(b.title)
    if not same_title:
        if sig.kind == "specific":
            return Verdict("B", "same_posting_id_different_titles",
                           [f"identical {sig.reason} but different titles: a renamed posting or a non-unique identifier; not decidable from the URL"])
        return Verdict("C", "same_url_different_titles", ["a generic URL shared by postings with different titles does not identify one job"])
    if sig.kind != "specific":
        return Verdict("B", "generic_url_same_title", ["same title on a generic careers URL: a repost, a second opening or a duplicate; not decidable from the URL"])
    scope = f"{sig.platform}/{sig.slug}" if sig.slug else sig.platform
    reasons.append(f"identical {sig.reason} in the same namespace ({scope}) and identical title")
    if not url_is_unique:
        return Verdict("B", "specific_url_not_unique", reasons + ["the URL is also shared by rows with other titles"])
    if a.requisition_id and b.requisition_id and a.requisition_id != b.requisition_id:
        return Verdict("D", "requisition_conflict", reasons + [f"different employer requisition ids ({a.requisition_id} / {b.requisition_id})"],
                       "requisition_conflict")
    if relation in ("same", "equivalent") and locations_conflict(*locs):
        return Verdict("B", "same_posting_token_different_locations",
                       reasons + ["the stored locations or the URL location paths name different cities: a multi-location posting must not be collapsed into one"])
    if relation == "same":
        return Verdict("A", "same_posting_same_company", reasons + ["both rows belong to one company record"])
    if relation == "equivalent":
        return Verdict("A", "same_posting_equivalent_company_names", reasons + ["company names are equal after normalisation"])
    if relation == "glued_suffix":
        return Verdict("B", "same_posting_company_name_glued_suffix",
                       reasons + ["the names differ only by a glued legal suffix; a human should confirm the company"])
    if relation == "compatible":
        return Verdict("B", "same_posting_company_names_compatible", reasons + ["company names are related but not equal; a human should confirm the company"])
    return Verdict("D", "same_posting_conflicting_companies", reasons + ["the same posting is attributed to unrelated company names"],
                   "different_label_same_posting")


def _url_place(ref, key):
    """Location path segment of the job's Workday URL for this key, if any ('.../job/Bangalore/Title_R1' -> 'Bangalore')."""
    for u in ref.urls:
        if url_signature(u).key == key:
            segs = [s for s in urlsplit(u).path.split("/") if s]
            low = [s.lower() for s in segs]
            if "job" in low and low.index("job") + 1 < len(segs) - 1:
                return segs[low.index("job") + 1].replace("-", " ")
    return ""


def effective_locations(conn, job_id):
    """Every location text known for a job: its own plus those of the jobs merged into it, canonical first, de-duplicated.
    Read-only; this is what a view should show for a multi-location job so a merge never hides a location (Bengaluru included)."""
    texts = []
    ids = [job_id]
    if table_exists(conn, "identity_links"):
        ids += [r[0] for r in conn.execute("SELECT alias_id FROM identity_links WHERE entity_type = 'job' AND canonical_id = ? AND status = 'confirmed' ORDER BY created_at",
                                           (job_id,))]
    for jid in ids:
        row = conn.execute("SELECT location FROM jobs WHERE job_id = ?", (jid,)).fetchone()
        if row and row[0] and row[0].strip() and row[0].strip().lower() not in {t.lower() for t in texts}:
            texts.append(row[0].strip())
    return texts


def rejected_pairs(conn, entity_type):
    """Pairs a human marked as 'not the same'. Empty before migration 5."""
    if not table_exists(conn, "identity_links"):
        return set()
    return {frozenset((r[0], r[1])) for r in conn.execute(
        "SELECT canonical_id, alias_id FROM identity_links WHERE entity_type = ? AND status = 'rejected'", (entity_type,))}


def job_pairs(refs, companies=None, rejected=None):
    """All candidate pairs of distinct job rows that share a posting key (or, for Workday, an underlying requisition)."""
    rejected = rejected or set()
    by_key, by_req = defaultdict(set), defaultdict(set)
    sigs = {}
    for ref in refs.values():
        for u in ref.urls:
            if not u:
                continue
            sig = url_signature(u)
            by_key[sig.key].add(ref.job_id)
            sigs[sig.key] = sig
            if sig.requisition_key:
                by_req[sig.requisition_key].add((ref.job_id, sig.key))
    pairs, seen = [], set()

    def add(a, b, key, sig, verdict_fn):
        if (a.job_id, b.job_id, key) in seen:
            return
        seen.add((a.job_id, b.job_id, key))
        rel = "same" if a.company_id == b.company_id else company_relation(a.company_name, b.company_name)
        v = verdict_fn(rel)
        if frozenset((a.job_id, b.job_id)) in rejected:
            v = Verdict("C", "human_rejected", ["a person marked these two jobs as not the same"], "distinct_confirmed")
        elif v.tier == "A" and rel != "same" and companies:
            ca, cb = companies.get(a.company_id), companies.get(b.company_id)
            if ca and cb and ca.reported_domains and cb.reported_domains and not (ca.reported_domains & cb.reported_domains):
                v = Verdict("D", "same_posting_conflicting_company_domains",
                            v.reasons + ["the two company records report different domains, so the shared posting does not make them one company"],
                            "conflicting_domains")
        pairs.append(JobPair(a.job_id, b.job_id, key, sig.kind, v, rel,
                             cross_source=not (a.sources & b.sources), same_source=bool(a.sources & b.sources), sig=sig))

    for key, ids in by_key.items():
        if len(ids) < 2:
            continue
        sig = sigs[key]
        members = sorted(ids)
        unique = len({title_key(refs[i].title) for i in members}) == 1
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = refs[members[i]], refs[members[j]]
                # Only Workday URLs carry a per-location path, so only there can one id sit on two location listings.
                # On Lever/Greenhouse/Ashby one posting id is one posting; differing location TEXT is source disagreement,
                # to be kept as several locations, not a reason to doubt the identity.
                locs = (((a.location, b.location), (_url_place(a, key), _url_place(b, key))) if sig.platform in LOCATION_SCOPED_PLATFORMS else ())
                add(a, b, key, sig, lambda rel, a=a, b=b, locs=locs: classify_job_pair(a, b, sig, unique, rel, locs))
    # One underlying requisition posted more than once (Workday `R123` and `R123-1`): related, never the same posting.
    for req, members in by_req.items():
        posting_keys = {k for _, k in members}
        if len(posting_keys) < 2:
            continue
        ordered = sorted(members)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                (ja, ka), (jb, kb) = ordered[i], ordered[j]
                if ka == kb or ja == jb:
                    continue
                a, b = refs[ja], refs[jb]
                key = ("requisition",) + req[2:]

                def fn(rel):
                    if rel in ("same", "equivalent"):
                        return Verdict("C", "same_requisition_different_posting",
                                       ["different postings of one underlying requisition (a repost or an additional location); related, not the same posting"],
                                       "related_postings")
                    return Verdict("D", "same_requisition_conflicting_companies", ["one requisition is attributed to unrelated company names"],
                                   "different_label_same_posting")
                add(a, b, key, sigs[ka], fn)
    _cross_form_pairs(refs, rejected, add, sigs)
    return pairs


def _cross_form_pairs(refs, rejected, add, sigs):
    """Candidates the exact-key grouping cannot see because the SAME posting identifier is written two ways:
    * Workable: `apply.workable.com/j/<code>` (no tenant) next to `apply.workable.com/<tenant>/j/<code>`;
    * a UUID issued by an ATS embedded in another host's path (an employer careers page quoting the Ashby/Lever posting id).
    Both are Tier B at best (a human confirms) because neither namespace is documented; a conflict (one code under two tenants, or unrelated
    companies) is Tier D evidence that the identifier is not unique."""
    wk = defaultdict(lambda: defaultdict(set))          # code -> tenant -> job ids
    uu = defaultdict(lambda: defaultdict(set))          # uuid -> host -> job ids
    for ref in refs.values():
        for u in ref.urls:
            w = workable_shortcode(u)
            if w:
                wk[w[1]][w[0]].add(ref.job_id)
            x = path_uuid(u)
            if x:
                uu[x[1]][x[0]].add(ref.job_id)

    def pair_up(groups_a, groups_b, key, reason, rule):
        sig = UrlSignature("specific", key, "candidate", "", key[-1].lower(), reason, "posting", (), "")
        for a_id in sorted(groups_a):
            for b_id in sorted(groups_b):
                if a_id == b_id:
                    continue
                a, b = refs[a_id], refs[b_id]
                if a_id > b_id:
                    a, b = b, a

                def fn(rel, a=a, b=b):
                    if rel not in ("same", "equivalent", "glued_suffix", "compatible"):
                        return Verdict("D", "same_identifier_conflicting_companies", [reason, "the identifier is attributed to unrelated company names"], "different_label_same_posting")
                    if title_key(a.title) != title_key(b.title):
                        return Verdict("B", "same_identifier_different_titles", [reason, "different titles: a renamed posting or a non-unique identifier"])
                    return Verdict("B", rule, [reason, "same company and identical title, but the identifier's namespace is not documented: a human should confirm"])
                add(a, b, key, sig, fn)

    for code, tenants in wk.items():
        named = {t for t in tenants if t}
        if len(named) > 1:                                                    # one code under two tenants: the identifier is NOT unique
            ids = sorted({j for t in named for j in tenants[t]})
            sig = UrlSignature("specific", ("workable-shortcode", code), "workable", "", code.lower(), "workable shortcode under several tenants", "posting", (), "")
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = refs[ids[i]], refs[ids[j]]
                    add(a, b, ("workable-shortcode", code), sig, lambda rel: Verdict("D", "workable_shortcode_tenant_conflict",
                                                                                     [f"shortcode {code} appears under tenants {sorted(named)}"], "identifier_not_unique"))
            continue
        if "" in tenants and named:
            pair_up(tenants[""], {j for t in named for j in tenants[t]}, ("workable-shortcode", code),
                    f"identical Workable shortcode {code}, written without and with the tenant ({sorted(named)[0]})", "workable_shortcode_tenantless_vs_tenant")
    for uuid, hosts in uu.items():
        if len(hosts) < 2:
            continue
        names = sorted(hosts)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                pair_up(hosts[names[i]], hosts[names[j]], ("uuid", uuid), f"identical posting UUID {uuid} on {names[i]} and {names[j]}", "same_uuid_different_hosts")


# --------------------------------------------------------------- companies

@dataclass
class CompanyRef:
    company_id: str
    name: str
    domain: str
    reported_domains: set
    ats_slugs: set
    sources: set
    n_jobs: int = 0
    created_at: str = ""


def _clean_domain(value):
    d = (value or "").strip().lower()
    d = re.sub(r"^[a-z][a-z0-9+.-]*://", "", d).split("/", 1)[0].split(":", 1)[0].removeprefix("www.").strip(".")
    if not d or "." not in d or d in SHARED_HOSTS or any(d.endswith("." + h) for h in SHARED_HOSTS):
        return None
    return d


def load_company_refs(conn, job_refs=None):
    refs = {}
    merged_away = alias_ids(conn, "company")
    for r in conn.execute("SELECT company_id, name, domain, facts, created_at FROM companies"):
        if r["company_id"] in merged_away:
            continue
        facts = json.loads(r["facts"])
        reported = set()
        for d in [r["domain"], (facts.get("yc") or {}).get("website_domain_reported"),
                  *((facts.get("investor_boards") or {}).get("reported_domains") or [])]:
            d = _clean_domain(d)
            if d:
                reported.add(d)
        refs[r["company_id"]] = CompanyRef(r["company_id"], r["name"], r["domain"], reported, set(), set(), 0, r["created_at"])
    for r in conn.execute("SELECT company_id, source_id FROM sightings WHERE company_id IS NOT NULL"):
        if r["company_id"] in refs:
            refs[r["company_id"]].sources.add(r["source_id"])
    if job_refs is not None:
        for j in job_refs.values():
            c = refs.get(j.company_id)
            if c is None:
                continue
            c.n_jobs += 1
            c.sources |= j.sources                  # a record is also known through the sources that listed its jobs
            for u in j.urls:
                sig = url_signature(u)
                if sig.kind == "specific" and sig.slug:
                    c.ats_slugs.add(sig.slug)
    return refs


def classify_company_pair(a, b, linked_job_tier=None):
    """Tier for two company rows. linked_job_tier: best tier among job pairs that join the two companies."""
    if is_placeholder_name(a.name) or is_placeholder_name(b.name):
        return Verdict("D", "placeholder_company_label", ["a company label is a source placeholder, not a company"], "invalid_source_label")
    rel = company_relation(a.name, b.name)
    if a.domain and b.domain:
        if a.domain != b.domain:
            return Verdict("C", "different_verified_domains", [f"verified domains differ ({a.domain} / {b.domain})"])
        return Verdict("A", "same_verified_domain", ["identical verified domain"])
    shared = a.reported_domains & b.reported_domains
    if shared:
        return Verdict("A", "shared_reported_domain", [f"both report the domain {sorted(shared)[0]}"])
    if a.reported_domains and b.reported_domains:
        return Verdict("C", "different_reported_domains", ["each record reports a different domain"])
    if linked_job_tier == "A" and rel == "equivalent":
        return Verdict("A", "same_name_and_shared_posting", ["equal names and a shared job-specific posting (tier A job pair)"])
    if linked_job_tier in ("A", "B") and rel == "glued_suffix":
        return Verdict("B", "glued_legal_suffix_name", ["names differ only by a glued legal suffix and a posting is shared; needs corroboration"])
    if linked_job_tier == "A" and rel == "compatible":
        return Verdict("B", "compatible_name_and_shared_posting", ["related names and a shared job-specific posting"])
    if linked_job_tier in ("A", "D") and rel == "unrelated":
        return Verdict("D", "unrelated_names_share_posting", ["the same job-specific posting is attributed to unrelated company names"],
                       "different_label_same_posting")
    slug = {compact_key(a.name), compact_key(b.name)} & (a.ats_slugs | b.ats_slugs)
    if rel == "equivalent":
        why = ["equal names after normalisation (a name alone is not identity evidence)"]
        if slug:
            why.append(f"one record's postings sit on the ATS board '{sorted(slug)[0]}', matching the name")
        return Verdict("B", "name_only", why)
    if rel == "glued_suffix":
        return Verdict("B", "glued_legal_suffix_name", ["names differ only by a glued legal suffix; needs corroboration"])
    if rel == "compatible":
        return Verdict("B", "compatible_names", ["related names; needs more evidence"])
    if linked_job_tier:
        return Verdict("B", "linked_by_possible_job_pair", ["connected only through a possible (tier B) job pair"])
    return Verdict("C", "no_evidence", ["no usable identity evidence"])


def company_pairs(conn, jobs=None, rejected=None):
    jobs = jobs if jobs is not None else load_job_refs(conn)
    cos = load_company_refs(conn, jobs)
    rejected = rejected if rejected is not None else rejected_pairs(conn, "company")
    best = {}
    order = {"A": 0, "D": 1, "B": 2, "C": 3}
    for p in job_pairs(jobs, cos, rejected_pairs(conn, "job")):
        ca, cb = jobs[p.a].company_id, jobs[p.b].company_id
        if ca == cb:
            continue
        k = tuple(sorted((ca, cb)))
        if k not in best or order[p.verdict.tier] < order[best[k]]:
            best[k] = p.verdict.tier
    groups = defaultdict(list)
    for c in cos.values():
        groups[name_key(c.name)].append(c.company_id)
    candidates = set(best)
    for ids in groups.values():
        ids = sorted(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                candidates.add((ids[i], ids[j]))
    # a glued legal suffix ('couchbaseinc') is candidate evidence: pair the slug-like name with its stem's records
    stems = {}
    for c in cos.values():
        stems.setdefault(compact_key(c.name), []).append(c.company_id)
    for c in cos.values():
        stem = glued_suffix_stem(c.name)
        for other in stems.get(stem, []) if stem else []:
            if other != c.company_id:
                candidates.add(tuple(sorted((c.company_id, other))))
    out = []
    for k in sorted(candidates):
        a, b = cos[k[0]], cos[k[1]]
        v = classify_company_pair(a, b, best.get(k))
        if frozenset(k) in rejected:
            v = Verdict("C", "human_rejected", ["a person marked these two companies as not the same"], "distinct_confirmed")
        out.append((a, b, v))
    return out, cos


# ----------------------------------------------------------------- report

def _group_summary(pairs):
    by_rule = defaultdict(int)
    by_tier = defaultdict(int)
    for p in pairs:
        by_rule[(p.verdict.tier, p.verdict.rule)] += 1
        by_tier[p.verdict.tier] += 1
    return dict(by_tier), {f"{t}:{r}": n for (t, r), n in sorted(by_rule.items())}


def build_report(conn, examples=2):
    """Read-only reconciliation report over the whole database."""
    jobs = load_job_refs(conn)
    cos = load_company_refs(conn, jobs)
    pairs = job_pairs(jobs, cos, rejected_pairs(conn, "job"))
    j_tier, j_rule = _group_summary(pairs)
    co_pairs, cos = company_pairs(conn, jobs)
    c_tier, c_rule = defaultdict(int), defaultdict(int)
    for a, b, v in co_pairs:
        c_tier[v.tier] += 1
        c_rule[f"{v.tier}:{v.rule}"] += 1
    kinds = defaultdict(int)
    for p in pairs:
        kinds[("cross_source" if p.cross_source else "within_source", p.verdict.tier)] += 1

    def job_example(p):
        a, b = jobs[p.a], jobs[p.b]
        return {"tier": p.verdict.tier, "rule": p.verdict.rule, "reasons": p.verdict.reasons,
                "key": [str(x) for x in p.key[:4]], "a": {"company": a.company_name, "title": a.title[:60], "sources": sorted(a.sources)},
                "b": {"company": b.company_name, "title": b.title[:60], "sources": sorted(b.sources)}}

    def company_example(a, b, v):
        return {"tier": v.tier, "rule": v.rule, "reasons": v.reasons,
                "a": {"name": a.name, "domain": a.domain, "reported": sorted(a.reported_domains), "sources": sorted(a.sources), "jobs": a.n_jobs},
                "b": {"name": b.name, "domain": b.domain, "reported": sorted(b.reported_domains), "sources": sorted(b.sources), "jobs": b.n_jobs}}

    job_ex, co_ex = {}, {}
    for p in pairs:
        job_ex.setdefault(f"{p.verdict.tier}:{p.verdict.rule}", [])
        if len(job_ex[f"{p.verdict.tier}:{p.verdict.rule}"]) < examples:
            job_ex[f"{p.verdict.tier}:{p.verdict.rule}"].append(job_example(p))
    for a, b, v in co_pairs:
        co_ex.setdefault(f"{v.tier}:{v.rule}", [])
        if len(co_ex[f"{v.tier}:{v.rule}"]) < examples:
            co_ex[f"{v.tier}:{v.rule}"].append(company_example(a, b, v))
    return {"rule_version": RULE_VERSION,
            "jobs": {"rows": len(jobs), "candidate_pairs": len(pairs), "by_tier": j_tier, "by_rule": j_rule,
                     "by_scope_and_tier": {f"{k[0]}:{k[1]}": n for k, n in sorted(kinds.items())}, "examples": job_ex},
            "companies": {"rows": len(cos), "candidate_pairs": len(co_pairs), "by_tier": dict(c_tier),
                          "by_rule": dict(sorted(c_rule.items())), "examples": co_ex}}


def render_report(report):
    lines = [f"Identity reconciliation report (read-only; nothing was changed; rule version {report.get('rule_version', '?')})", ""]
    j = report["jobs"]
    lines.append(f"Jobs: {j['rows']} rows, {j['candidate_pairs']} candidate pairs sharing an identity key. Tiers: "
                 + ", ".join(f"{t}={j['by_tier'].get(t, 0)}" for t in TIERS))
    for k, n in j["by_rule"].items():
        lines.append(f"  {k}: {n}")
    lines.append("  by scope: " + ", ".join(f"{k}={n}" for k, n in j["by_scope_and_tier"].items()))
    c = report["companies"]
    lines.append(f"Companies: {c['rows']} rows, {c['candidate_pairs']} candidate pairs. Tiers: "
                 + ", ".join(f"{t}={c['by_tier'].get(t, 0)}" for t in TIERS))
    for k, n in c["by_rule"].items():
        lines.append(f"  {k}: {n}")
    lines.append("")
    lines.append("Tier A = strong evidence (merge candidate after approval); B = possible, needs more evidence; "
                 "C = distinct records sharing a URL or name; D = conflicting identities, manual review.")
    return lines


def table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def alias_ids(conn, entity_type):
    """Ids of records already merged into another record (empty before migration 5)."""
    if not table_exists(conn, "identity_links"):
        return set()
    return {r[0] for r in conn.execute(
        "SELECT alias_id FROM identity_links WHERE entity_type = ? AND status = 'confirmed'", (entity_type,))}
