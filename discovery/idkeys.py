"""Platform- and tenant-scoped identity keys for job URLs (Day 11, rule version in identity.RULE_VERSION).

Pure functions, no database, no network. A key says WHICH posting a URL names, scoped to the ATS platform and the
employer's tenant/board, so the same numeric id on two different tenants is never treated as one posting.

* Raw URLs are never rewritten; keys exist only for comparison.
* A tracking parameter is ignored only if it is on the verified list below. Every other query parameter stays
  part of a generic URL's comparison key.
* A key is 'specific' only when the URL carries a posting identifier. Generic careers pages never identify a job.
* Posting identity and requisition identity are kept apart where the platform shows both (Workday: the token
  `R123-1` is posting 1 of requisition `R123`).
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

# Query parameters verified as pure tracking (campaign/referrer markers). Anything else is kept in generic keys.
VERIFIED_TRACKING = {"gclid", "fbclid", "mc_cid", "mc_eid", "gh_src", "lever-source", "lever-origin", "lever-via",
                     "igshid", "msclkid"}

# Multi-tenant applicant-tracking hosts: many unrelated employers share these registrable domains.
SHARED_ATS_DOMAINS = {"greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "myworkdayjobs.com", "smartrecruiters.com",
                      "paylocity.com", "keka.com", "mynexthire.com", "jobvite.com", "icims.com", "bamboohr.com",
                      "recruitee.com", "breezy.hr", "teamtailor.com", "zohorecruit.com", "darwinbox.in", "darwinbox.com",
                      "freshteam.com", "oraclecloud.com", "successfactors.com", "taleo.net", "ultipro.com",
                      "dayforcehiring.com", "jazzhr.com", "applytojob.com", "pinpointhq.com", "workday.com",
                      "myworkdaysite.com", "eightfold.ai", "phenompeople.com", "avature.net", "csod.com"}

# Multi-part public suffixes that matter for the hosts we see. Deterministic and offline: this is a subset of the
# Public Suffix List, not the whole list; an unlisted multi-part suffix would be read as its last two labels.
_MULTI_PART_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz", "co.in", "net.in",
                        "org.in", "ac.in", "gov.in", "com.br", "com.mx", "co.jp", "co.kr", "com.sg", "com.hk", "com.cn",
                        "co.za", "com.tr", "com.ar", "co.il", "com.my", "com.ph", "com.pk", "co.id"}

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_WORKDAY_HOST = re.compile(r"^(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com$")
# Workday: the final path segment is `<title-slug>_<token>`. The token (after the LAST underscore) is shown as job_key, but the
# comparison key uses the WHOLE segment, so two URLs only match when title slug and token both match; tokens such as
# `R26_642` (an underscore inside the id) therefore cannot collide with an unrelated `642`. The `-N` instance suffix
# (`R-026595-1`, `-2`) is split off ONLY when the base has >= 4 digits and the suffix is 1-2 digits, so `202607-119623`
# stays whole. Examples seen in stored URLs: 3235, 31163039-1, R283, R221633, JR00043989-1, REQ-1575, R-123456,
# R0019832-1, REF089066W, 748284WD-1, 202607-119623, R26_642.
_WORKDAY_TOKEN = re.compile(r"_(?P<tail>(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]+)$")
_WORKDAY_INSTANCE = re.compile(r"(?P<base>[a-z]{0,6}-?\d{4,}[a-z]{0,2})-(?P<n>\d{1,2})$")
_ID_QUERY_KEYS = ("gh_jid", "jid", "job_id", "jobid", "requisitionid", "reqid", "req_id", "id")


@dataclass(frozen=True)
class UrlSignature:
    kind: str                 # 'specific' (names one posting) or 'generic'
    key: tuple                # comparison key (scoped); never used to rewrite a stored URL
    platform: str = ""
    slug: str = ""            # employer namespace on the platform (board slug / tenant); '' when the host itself scopes it
    job_key: str = ""         # the posting identifier as it appears (lowercased)
    reason: str = ""
    id_kind: str = ""         # 'posting', 'requisition' or '' (not stated by the URL)
    requisition_key: tuple = ()   # underlying requisition when the platform states one (Workday), else ()
    host: str = ""


def host_of(url):
    return urlsplit((url or "").strip()).netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")


def registrable_domain(host):
    """eTLD+1 using a small embedded subset of the Public Suffix List (offline and deterministic)."""
    labels = [l for l in (host or "").lower().strip(".").split(".") if l]
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _MULTI_PART_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def host_class(host):
    """'shared_ats' for a known multi-tenant hiring platform, else 'employer' (assumed employer-controlled: this is a
    classification of the host, not a verification of who owns it)."""
    return "shared_ats" if registrable_domain(host) in SHARED_ATS_DOMAINS else "employer"


def _tracking(name):
    n = name.lower().rstrip("[]")
    return n.startswith("utm_") or n in VERIFIED_TRACKING


def comparison_query(query):
    """Query string for a GENERIC comparison key: only verified tracking parameters are dropped."""
    kept = sorted((k.lower().rstrip("[]"), v) for k, v in parse_qsl(query, keep_blank_values=True) if not _tracking(k))
    return "&".join(f"{k}={v}" for k, v in kept)


def url_signature(url):
    parts = urlsplit((url or "").strip())
    host = host_of(url)
    segs = [s for s in parts.path.split("/") if s]
    low = [s.lower() for s in segs]
    query = {k.lower(): v for k, v in parse_qsl(parts.query, keep_blank_values=True)}

    def ats(platform, slug, job_key, id_kind="posting", reason="ats posting identifier", req=()):
        return UrlSignature("specific", ("ats", platform, slug.lower(), job_key.lower()), platform, slug.lower(),
                            job_key.lower(), reason, id_kind, req, host)

    wd = _WORKDAY_HOST.match(host)
    if wd and "job" in low and low.index("job") < len(segs) - 1:
        m = _WORKDAY_TOKEN.search(segs[-1])
        if m:
            tenant = f"{wd.group('tenant')}.{wd.group('wd')}"                 # the tenant namespace, instance included
            tail = m.group("tail").lower()
            inst = _WORKDAY_INSTANCE.match(tail)
            base = inst.group("base") if inst else tail
            return UrlSignature("specific", ("ats", "workday", tenant, segs[-1].lower()), "workday", tenant, tail,
                                "workday posting (title slug + token)", "posting", ("ats", "workday", tenant, "requisition", base), host)
        return UrlSignature("generic", ("url", host, "/".join(low), comparison_query(parts.query)),
                            reason="workday path without a posting token", host=host)
    if host.endswith("greenhouse.io") and "jobs" in low:
        i = low.index("jobs")
        if i + 1 < len(segs) and segs[i + 1].isdigit() and i >= 1:
            return ats("greenhouse", segs[0], segs[i + 1])
    if host == "jobs.lever.co" and len(segs) >= 2 and _UUID.match(segs[1]):
        return ats("lever", segs[0], segs[1])
    if host == "jobs.ashbyhq.com" and len(segs) >= 2 and _UUID.match(segs[1]):
        return ats("ashby", segs[0], segs[1])
    if host == "apply.workable.com" and len(segs) >= 3 and low[1] == "j":
        return ats("workable", segs[0], segs[2])
    if host == "jobs.smartrecruiters.com" and len(segs) >= 2 and re.match(r"^\d{6,}", segs[1]):
        return ats("smartrecruiters", segs[0], re.match(r"^\d+", segs[1]).group(0))
    if host == "recruiting.paylocity.com" and segs and segs[-1].isdigit() and "details" in low:
        return ats("paylocity", "", segs[-1])
    if "gh_jid" in query and query["gh_jid"].isdigit():
        return UrlSignature("specific", ("id", host, "gh_jid", query["gh_jid"]), "greenhouse-embed", "", query["gh_jid"],
                            "gh_jid parameter", "posting", (), host)
    for k in _ID_QUERY_KEYS:
        v = query.get(k, "")
        if v and re.search(r"\d{4,}", v):
            return UrlSignature("specific", ("id", host, k, v.lower()), "query-id", "", v.lower(), f"{k} parameter", "", (), host)
    if segs:
        last = segs[-1]
        if _UUID.match(last) or re.fullmatch(r"\d{5,}", last) or re.search(r"[_-][A-Za-z]{0,6}-?\d{4,}[A-Za-z]{0,2}(?:-\d+)?$", last):
            return UrlSignature("specific", ("id", host, "path", last.lower()), "path-id", "", last.lower(),
                                "identifier in last path segment", "", (), host)
    reason = "no path" if not segs else "path without a posting identifier"
    return UrlSignature("generic", ("url", host, "/".join(low), comparison_query(parts.query)), reason=reason, host=host)
