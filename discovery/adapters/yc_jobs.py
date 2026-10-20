"""Stream A source adapter: public job listings on Y Combinator company pages (source id `yc_jobs`).

Audit and permission record: docs/source_audits/yc_job_pages.md. YC's Terms of Use prohibit automated scraping
without YC's express authorization; the operator states (user-attested, no reference number, not independently
verified) that they hold such authorization, so this adapter runs ONLY on an explicit manual command and keeps to
conservative limits: one GET per company, sequential, a pause between requests, a hard request cap, a runtime cap,
no retries, no redirect following, no cookies, no login, no proxy, TLS verification on, an honest User-Agent,
and an immediate stop on 403/429 (no workaround).

Page structure VERIFIED against two live pages on 2026-10-20 (docs/source_audits/yc_job_pages.md section 15):
an element `WaasShowJobsPage-react-component-*` whose `data-page` attribute holds JSON with `props.company.id`
(equal to the YC id we already store), `props.jobPostings[]` (each: numeric `id`, `title`, `url` relative to the
YC host, `applyUrl`, `createdAt`, `location`, `minExperience`, `type`, `role`, ...) and an unrelated `relatedJobs`
list that is never read. `createdAt` is RELATIVE text ("about 7 years"), not a date, so it is kept as age text in
`facts.yc_jobs` and never converted into a posting date. `applyUrl` is YC's generic login gate, not an employer
application link; it is kept, flagged as gated and never followed. Anything that does not match the expected
structure is classified as failed/partial, never as "no jobs".

Data model (no schema change):
  * directory hiring flag      -> companies.facts.yc.is_hiring_reported (written by the directory import, untouched here)
  * company scan status        -> companies.facts.yc_jobs (separate top-level key the directory refresh never overwrites)
  * observed job listings      -> jobs + sightings via db.observe_job (source `yc_jobs`, YC job id as external_id)
  * check history              -> one source_checks row per company (scope `company/<slug>`), never complete=1 yet
A zero-job page, a failed page and a blocked page are three different states; none of them closes or ages out
anything, and none says a company is "not hiring".

Freshness / evidence model (decision of 2026-10-20; a YC page is never proof that a job is open):
  current_employer_confirmed    reserved for an authorized employer/ATS source; this adapter NEVER assigns it
  recently_observed_unverified  a listing younger than the recency threshold; open status still uncertain
  historical_stale              an old listing still visible; kept as COMPANY-LEVEL evidence only (no job row,
                                not published, not closed, never counted as "currently hiring")
  unknown                       age missing or unreadable
  unavailable_incomplete        the company page could not be assessed (failed/blocked/partial/not checked)
The recency threshold is a prioritization setting (default 90 days, configurable), not a closure rule: being
older than it never closes a job and being younger never verifies one.
"""

import hashlib
import html as html_lib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .. import database as db

SOURCE_ID = "yc_jobs"
SOURCE_NAME = "YC company job pages"
SOURCE_HOME = "https://www.ycombinator.com/companies"
HOST = "www.ycombinator.com"
USER_AGENT = "ab-discovery-engine/0.9 (personal job research; manual run; read-only)"
SNAPSHOT_DIR = db.PROJECT_ROOT / "data" / "yc_jobs"
SNAPSHOT_FORMAT = 1
AUTHORIZATION_BASIS = "user_attested"          # the operator's statement; no reference number exists or is invented

# Hard ceilings (a caller cannot exceed them without a code change)
MAX_REQUESTS_CEILING = 300
MAX_SECONDS_CEILING = 1800
MIN_DELAY_SECONDS = 2.0
TIMEOUT_SECONDS = 15
MAX_BYTES = 8 * 1024 * 1024
DEFAULT_BATCH = 25
DEFAULT_BATCH_PAUSE = 10.0

# Company scan states
CHECKED_LISTINGS = "checked_with_listings"
CHECKED_NONE = "checked_no_listings"      # a parsed page that lists zero jobs; NOT "not hiring"
PARTIAL = "partial"
BLOCKED = "blocked"
FAILED = "failed"
NOT_CHECKED = "not_checked"
STATES = (CHECKED_LISTINGS, CHECKED_NONE, PARTIAL, BLOCKED, FAILED, NOT_CHECKED)

# Evidence categories (see the module docstring)
CURRENT_CONFIRMED = "current_employer_confirmed"
RECENT_UNVERIFIED = "recently_observed_unverified"
HISTORICAL_STALE = "historical_stale"
UNKNOWN_AGE = "unknown"
UNAVAILABLE = "unavailable_incomplete"
CATEGORIES = (CURRENT_CONFIRMED, RECENT_UNVERIFIED, HISTORICAL_STALE, UNKNOWN_AGE, UNAVAILABLE)
DEFAULT_RECENCY_DAYS = 90      # prioritization threshold only; configurable per run

SELECTION_CLASSES = ("india_based", "potentially_relevant", "unverified", "no_india_evidence_yet")
DEFAULT_CLASSES = ("india_based",)

_COMPONENT = re.compile(r'<div[^>]*id="WaasShowJobsPage-react-component-[^"]*"[^>]*data-page="(?P<data>[^"]*)"', re.I)
_PAGINATION_KEYS = re.compile(r"next.?page|has.?more|total.?pages|cursor|page.?count|total.?count|total.?jobs", re.I)
_JOB_ID = re.compile(r"/jobs/([A-Za-z0-9]+)")


class YCJobsError(Exception):
    """Collection or import cannot proceed safely. Nothing was changed."""


# ------------------------------------------------------------------ selection

def _hiring_flag(yc_facts):
    value = (yc_facts or {}).get("is_hiring_reported")
    return value if isinstance(value, bool) else None


def select_companies(conn, classes=DEFAULT_CLASSES):
    """Relevant YC companies, chosen ONLY by India relevance class. The hiring flag, status and size never filter."""
    bad = [c for c in classes if c not in SELECTION_CLASSES]
    if bad:
        raise YCJobsError(f"unknown relevance class(es): {', '.join(bad)}")
    out = []
    rows = conn.execute("""SELECT c.company_id, c.name, c.facts, s.external_id
                           FROM companies c JOIN sightings s ON s.company_id = c.company_id
                           WHERE s.source_id = 'yc_oss' AND s.job_id IS NULL ORDER BY s.external_id""")
    for row in rows:
        facts = json.loads(row["facts"])
        yc = facts.get("yc") or {}
        cls = (facts.get("india_relevance") or {}).get("class")
        slug = yc.get("slug")
        if cls in classes and slug:
            out.append({"company_id": row["company_id"], "yc_id": str(row["external_id"]), "slug": slug,
                        "name": row["name"], "class": cls, "hiring_flag": _hiring_flag(yc),
                        "batch": yc.get("batch"), "locations": yc.get("locations"), "status": yc.get("status")})
    return sorted(out, key=lambda c: int(c["yc_id"]) if c["yc_id"].isdigit() else 0)


def plan_batch(companies, *, newest_first=False, limit=None, skip_slugs=()):
    """Order and trim a selection for one bounded batch. Never filters by the hiring flag.

    newest_first orders by YC id descending (ids grow with launch order), so recent companies, whose pages are
    more likely to hold current postings, are checked first. skip_slugs removes companies already checked.
    """
    skip = set(skip_slugs)
    rest = [c for c in companies if c["slug"] not in skip]
    rest.sort(key=lambda c: int(c["yc_id"]) if c["yc_id"].isdigit() else 0, reverse=newest_first)
    return rest[:limit] if limit else rest


def slugs_in_snapshot(path):
    """Slugs of companies a saved snapshot actually requested (state other than not_checked)."""
    return [c["slug"] for c in load_snapshot(path)["companies"] if c["state"] != NOT_CHECKED]


def selection_counts(companies):
    out = {"selected": len(companies), "by_class": {}, "hiring_true": 0, "hiring_false": 0, "hiring_unknown": 0}
    for c in companies:
        out["by_class"][c["class"]] = out["by_class"].get(c["class"], 0) + 1
        key = {True: "hiring_true", False: "hiring_false", None: "hiring_unknown"}[c["hiring_flag"]]
        out[key] += 1
    return out


def representative_sample(companies, n):
    """Deterministic small sample that always includes each hiring-flag group present (true, false, unknown)."""
    groups = {True: [], False: [], None: []}
    for c in companies:
        groups[c["hiring_flag"]].append(c)
    present = [k for k in (False, True, None) if groups[k]]
    picked, i = [], 0
    while len(picked) < min(n, len(companies)):
        progressed = False
        for k in present:
            if i < len(groups[k]) and len(picked) < n:
                picked.append(groups[k][i])
                progressed = True
        if not progressed:
            break
        i += 1
    return picked


def page_url(slug):
    return f"https://{HOST}/companies/{slug}/jobs"


# --------------------------------------------------------------------- fetching

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def fetch_page(url, *, opener=None):
    """One GET. Returns (http_status|None, body|None, error|None). Never retries, never follows redirects."""
    if urlsplit(url).netloc != HOST:
        raise YCJobsError("refusing to fetch a URL outside the YC host")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html;q=0.9,*/*;q=0.5"})
    opener = opener or urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as resp:
            body = resp.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                return resp.status, None, "response larger than the size limit"
            return resp.status, body, None
    except urllib.error.HTTPError as exc:
        return exc.code, None, f"HTTP {exc.code}"
    except Exception as exc:      # timeouts, DNS, TLS errors: an uncertain check, not an empty one
        return None, None, f"{type(exc).__name__}: {str(exc)[:120]}"


# ---------------------------------------------------------------------- parsing

@dataclass
class ParsedPage:
    state: str
    jobs: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    page_company_id: str = ""


def _clean(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _date(value):
    """An ISO date only. Relative text such as 'about 7 years' is NOT a date and is never turned into one."""
    text = _clean(value)
    return text[:10] if text and re.match(r"\d{4}-\d{2}-\d{2}", text) else None


def _experience(value):
    """'6+ years' -> (6.0, '6+ years'); anything unparseable keeps only the text."""
    text = _clean(value)
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*\+?\s*years?", text or "", re.I)
    return (float(m.group(1)) if m else None), text


_UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}


def age_days(text):
    """Approximate age in days from relative text ('about 7 years' -> 2555; 'over 8 years' -> a lower bound). None if unknown."""
    m = re.match(r"\s*(?:about|over|almost|less than|more than)?\s*(\d+|a|an|one)\s+(minute|hour|day|week|month|year)s?", text or "", re.I)
    if not m:
        return None
    n = 1 if m.group(1).lower() in ("a", "an", "one") else int(m.group(1))
    return n * _UNIT_DAYS[m.group(2).lower()]


GATED_APPLY_HOSTS = ("account.ycombinator.com", "www.workatastartup.com", "workatastartup.com")


def parse_jobs_page(body, slug, base_url=None):
    """Classify a page and extract its postings. Anything unexpected degrades to PARTIAL/FAILED, never to zero jobs."""
    base_url = base_url or page_url(slug)
    text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body or "")
    match = _COMPONENT.search(text)
    if not match:
        return ParsedPage(FAILED, notes=["job-page component not found (login, challenge, redirect or changed page)"])
    try:
        payload = json.loads(html_lib.unescape(match.group("data")))
        props = payload["props"]
    except (ValueError, KeyError, TypeError):
        return ParsedPage(FAILED, notes=["page data is not valid JSON"])
    if not isinstance(props, dict) or not isinstance(props.get("jobPostings"), list):
        return ParsedPage(FAILED, notes=["jobPostings list missing"])
    company = props.get("company") if isinstance(props.get("company"), dict) else {}
    page = ParsedPage(CHECKED_NONE, page_company_id=str(company.get("id")) if company.get("id") is not None else "")
    if any(_PAGINATION_KEYS.search(str(k)) for k in props):
        page.notes.append("page data carries pagination markers; pagination is not verified")
        page.state = PARTIAL
    seen, unusable = set(), 0
    for item in props["jobPostings"]:
        if not isinstance(item, dict):
            unusable += 1
            continue
        title = _clean(item.get("title"))
        raw_url = _clean(item.get("url")) or _clean(item.get("applyUrl"))
        job_url = urljoin(base_url, raw_url) if raw_url else None
        if not title or not job_url or urlsplit(job_url).scheme not in ("http", "https"):
            unusable += 1
            continue
        ext = _clean(str(item.get("id"))) if item.get("id") is not None else None
        if not ext:
            m = _JOB_ID.search(urlsplit(job_url).path)
            ext = m.group(1) if m else None
        key = ext or job_url
        if key in seen:
            continue
        seen.add(key)
        apply_url = _clean(item.get("applyUrl"))
        apply_url = urljoin(base_url, apply_url) if apply_url else None
        exp_min, exp_text = _experience(item.get("minExperience"))
        page.jobs.append({"external_id": ext, "title": title, "url": job_url, "apply_url": apply_url,
                          "apply_url_gated": bool(apply_url and urlsplit(apply_url).netloc in GATED_APPLY_HOSTS),
                          "location": _clean(item.get("location")), "posted_date": _date(item.get("createdAt")),
                          "posted_text": _clean(item.get("createdAt")), "age_days_estimate": age_days(item.get("createdAt")),
                          "experience_min_years": exp_min,
                          "experience_text": exp_text, "job_type": _clean(item.get("type")), "role": _clean(item.get("role"))})
    if unusable:
        page.notes.append(f"{unusable} posting(s) unusable and skipped")
        page.state = PARTIAL
    if page.jobs and page.state == CHECKED_NONE:
        page.state = CHECKED_LISTINGS
    return page


def job_age_days(job, reference_iso):
    """Approximate age of a listing in days: an ISO posting date wins, else the page's relative age text, else None."""
    posted = job.get("posted_date")
    if posted and reference_iso:
        try:
            return max((datetime.fromisoformat(reference_iso[:10]) - datetime.fromisoformat(posted)).days, 0)
        except ValueError:
            pass
    if job.get("age_days_estimate") is not None:
        return job["age_days_estimate"]
    return age_days(job.get("posted_text"))


def categorize(job, reference_iso, recency_days=DEFAULT_RECENCY_DAYS):
    """Evidence category of one observed listing. Never returns current_employer_confirmed (a YC page cannot confirm)."""
    age = job_age_days(job, reference_iso)
    if age is None:
        return UNKNOWN_AGE
    return RECENT_UNVERIFIED if age < recency_days else HISTORICAL_STALE


def evidence_counts(company, recency_days=DEFAULT_RECENCY_DAYS):
    ref = company.get("retrieved_at")
    counts = {RECENT_UNVERIFIED: 0, HISTORICAL_STALE: 0, UNKNOWN_AGE: 0}
    for j in company.get("jobs", []):
        counts[categorize(j, ref, recency_days)] += 1
    return counts


def disagreement(flag, state, counts=None):
    """Conflict between the directory hiring flag and what the page showed. Informational only.

    Only credible current evidence can contradict a 'not hiring' flag: historical listings alone never do.
    """
    counts = counts or {}
    recent, stale, unknown = (counts.get(k, 0) for k in (RECENT_UNVERIFIED, HISTORICAL_STALE, UNKNOWN_AGE))
    if flag is False and recent:
        return "directory_not_hiring_but_recent_listings_observed"
    if flag is False and unknown:
        return "directory_not_hiring_but_undated_listings_observed"
    if flag is True and state == CHECKED_NONE:
        return "directory_hiring_but_no_listings_observed"
    if flag is True and state in (CHECKED_LISTINGS, PARTIAL) and not recent and not unknown and stale:
        return "directory_hiring_but_only_historical_listings_observed"
    return None


def hiring_evidence(state, counts):
    """What this source says about CURRENT hiring. Never 'confirmed'."""
    if state not in (CHECKED_LISTINGS, CHECKED_NONE, PARTIAL):
        return UNAVAILABLE
    if counts.get(RECENT_UNVERIFIED):
        return RECENT_UNVERIFIED
    if counts.get(UNKNOWN_AGE):
        return UNKNOWN_AGE
    return HISTORICAL_STALE if counts.get(HISTORICAL_STALE) else "none_observed"


# ------------------------------------------------------------------- collection

def collect(companies, *, fetch=fetch_page, sleep=time.sleep, clock=time.monotonic, now_fn=db.utc_now,
            max_requests=None, max_seconds=900, delay=MIN_DELAY_SECONDS, batch_size=DEFAULT_BATCH,
            batch_pause=DEFAULT_BATCH_PAUSE, recency_days=DEFAULT_RECENCY_DAYS):
    """Bounded, sequential, read-only collection into an in-memory snapshot. Writes nothing."""
    max_requests = min(max_requests or len(companies), MAX_REQUESTS_CEILING)
    max_seconds = min(max_seconds, MAX_SECONDS_CEILING)
    delay = max(delay, MIN_DELAY_SECONDS)
    started, t0, requests, stop_reason = now_fn(), clock(), 0, None
    results = []
    for index, c in enumerate(companies):
        rec = {**c, "state": NOT_CHECKED, "http_status": None, "error": None, "page_url": page_url(c["slug"]),
               "retrieved_at": None, "jobs": [], "notes": [], "disagreement": None}
        results.append(rec)
        if stop_reason:
            rec["error"] = f"not requested: {stop_reason}"
            continue
        if requests >= max_requests:
            stop_reason = "request cap reached"
        elif clock() - t0 > max_seconds:
            stop_reason = "runtime cap reached"
        if stop_reason:
            rec["error"] = f"not requested: {stop_reason}"
            continue
        if index and requests:
            sleep(batch_pause if requests % batch_size == 0 else delay)
        status, body, error = fetch(rec["page_url"])
        requests += 1
        rec["retrieved_at"], rec["http_status"] = now_fn(), status
        if status in (403, 429):
            rec.update(state=BLOCKED, error=f"HTTP {status}: access restricted; run stopped, nothing retried")
            stop_reason = f"blocked by HTTP {status}"
        elif status != 200 or body is None:
            rec.update(state=FAILED, error=error or f"HTTP {status}")
        else:
            page = parse_jobs_page(body, c["slug"], rec["page_url"])
            if page.page_company_id and page.page_company_id != c["yc_id"]:
                rec.update(state=FAILED, error=f"page company id {page.page_company_id} does not match YC id {c['yc_id']}")
            else:
                rec.update(state=page.state, jobs=page.jobs, notes=page.notes)
        rec["evidence"] = evidence_counts(rec, recency_days)
        rec["disagreement"] = disagreement(c["hiring_flag"], rec["state"], rec["evidence"])
    snapshot = {"format": SNAPSHOT_FORMAT, "source": SOURCE_ID, "collected_at": started, "finished_at": now_fn(),
                "authorization": {"basis": AUTHORIZATION_BASIS, "reference": None},
                "params": {"max_requests": max_requests, "max_seconds": max_seconds, "delay": delay,
                           "batch_size": batch_size, "batch_pause": batch_pause, "recency_days": recency_days},
                "requests": requests, "seconds": round(clock() - t0, 1), "stopped": stop_reason, "companies": results}
    snapshot["records_sha256"] = hashlib.sha256(json.dumps(results, sort_keys=True).encode()).hexdigest()
    return snapshot


def write_snapshot(snapshot, directory=None):
    directory = Path(directory or SNAPSHOT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9]", "", snapshot["collected_at"])[:14]
    path = directory / f"{stamp}_yc_jobs.json"
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def load_snapshot(path):
    try:
        snap = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise YCJobsError(f"snapshot unreadable ({type(exc).__name__})") from exc
    if snap.get("format") != SNAPSHOT_FORMAT or snap.get("source") != SOURCE_ID or not isinstance(snap.get("companies"), list):
        raise YCJobsError("not a yc_jobs snapshot of the supported format")
    if hashlib.sha256(json.dumps(snap["companies"], sort_keys=True).encode()).hexdigest() != snap.get("records_sha256"):
        raise YCJobsError("snapshot content does not match its recorded hash; refusing to use it")
    return snap


# ------------------------------------------------------------------ reporting

LISTING_COLUMNS = ("yc_id", "company", "batch", "directory_hiring_flag", "job_id", "title", "job_url", "apply_url",
                   "apply_url_is_login_gate", "posted_text", "posted_date", "retrieved_at", "location", "remote_hint",
                   "remote_eligibility", "evidence_category", "scan_state", "scan_complete")


def listing_rows(snapshot):
    """One flat row per observed listing, with company context and the evidence category (read-only view).

    remote_hint only says the location text mentions Remote; remote_eligibility stays 'unknown' because neither
    the page text nor 'remote' proves India eligibility. scan_complete is False until pagination is verified.
    """
    recency = snapshot.get("params", {}).get("recency_days", DEFAULT_RECENCY_DAYS)
    rows = []
    for c in snapshot["companies"]:
        for j in c["jobs"]:
            rows.append({"yc_id": c["yc_id"], "company": c["name"], "batch": c.get("batch"),
                         "directory_hiring_flag": c["hiring_flag"], "job_id": j["external_id"], "title": j["title"],
                         "job_url": j["url"], "apply_url": j.get("apply_url"), "apply_url_is_login_gate": bool(j.get("apply_url_gated")),
                         "posted_text": j.get("posted_text"), "posted_date": j.get("posted_date"),
                         "retrieved_at": c["retrieved_at"], "location": j.get("location"),
                         "remote_hint": "remote" in (j.get("location") or "").lower(), "remote_eligibility": "unknown",
                         "evidence_category": categorize(j, c.get("retrieved_at"), recency), "scan_state": c["state"],
                         "scan_complete": False})
    return rows


def write_listings_csv(snapshot, path):
    import csv
    path = Path(path)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LISTING_COLUMNS)
        writer.writeheader()
        writer.writerows(listing_rows(snapshot))
    return path

def summarize(snapshot, conn=None):
    """The Day 9 numbers from a snapshot (and, when given a connection, what an import would change)."""
    comps = snapshot["companies"]
    recency = snapshot.get("params", {}).get("recency_days", DEFAULT_RECENCY_DAYS)
    by_state = {s: 0 for s in STATES}
    out = {"companies_in_snapshot": len(comps), "hiring_true": 0, "hiring_false": 0, "hiring_unknown": 0,
           "jobs_retrieved": 0, "companies_with_listings": 0, "contradictions": [], "failed_or_blocked": [],
           "incomplete": [], "evidence": {}, "companies_only_historical": 0, "recency_days": recency,
           "requests": snapshot["requests"], "seconds": snapshot["seconds"], "stopped": snapshot["stopped"]}
    unique = set()
    for c in comps:
        by_state[c["state"]] += 1
        out[{True: "hiring_true", False: "hiring_false", None: "hiring_unknown"}[c["hiring_flag"]]] += 1
        out["jobs_retrieved"] += len(c["jobs"])
        out["companies_with_listings"] += bool(c["jobs"])
        for j in c["jobs"]:
            unique.add((c["yc_id"], j["external_id"] or j["url"]))
        counts = evidence_counts(c, recency)
        kind = disagreement(c["hiring_flag"], c["state"], counts)
        for cat, n in counts.items():
            out["evidence"][cat] = out["evidence"].get(cat, 0) + n
        if counts[HISTORICAL_STALE] and not counts[RECENT_UNVERIFIED] and not counts[UNKNOWN_AGE]:
            out["companies_only_historical"] += 1
        if kind:
            out["contradictions"].append((c["slug"], c["hiring_flag"], c["state"], len(c["jobs"]), kind))
        if c["state"] in (FAILED, BLOCKED):
            out["failed_or_blocked"].append((c["slug"], c["state"], c.get("error")))
        if c["state"] in (PARTIAL, NOT_CHECKED):
            out["incomplete"].append((c["slug"], c["state"], c.get("error") or "; ".join(c.get("notes", []))))
    out["direct_employer_apply_urls"] = sum(1 for c in comps for j in c["jobs"] if j.get("apply_url") and not j.get("apply_url_gated"))
    out["jobs_with_iso_posting_date"] = sum(1 for c in comps for j in c["jobs"] if j.get("posted_date"))
    all_ages = [job_age_days(j, c.get("retrieved_at")) for c in comps for j in c["jobs"]]
    ages = [a for a in all_ages if a is not None]
    out["jobs_older_than_1y"] = sum(a >= 365 for a in ages)
    out["jobs_age_unknown"] = len(all_ages) - len(ages)
    out["jobs_with_relative_age_only"] = sum(1 for c in comps for j in c["jobs"] if j.get("posted_text") and not j.get("posted_date"))
    out["by_state"] = by_state
    out["unique_jobs"] = len(unique)
    out["checked"] = by_state[CHECKED_LISTINGS] + by_state[CHECKED_NONE]
    if conn is not None:
        new = existing = 0
        for c in comps:
            for j in c["jobs"]:
                if categorize(j, c.get("retrieved_at"), recency) == HISTORICAL_STALE:
                    continue                      # historical listings are company-level evidence, not job rows
                found = db.find_sighting(conn, SOURCE_ID, j["url"], j["external_id"], is_job=True)
                existing += bool(found)
                new += not found
        out["jobs_new_if_imported"], out["jobs_existing"] = new, existing
    return out


# --------------------------------------------------------------------- importing

@dataclass
class ImportCounts:
    run_id: str = ""
    companies: int = 0
    jobs_created: int = 0
    jobs_changed: int = 0
    jobs_unchanged: int = 0
    company_errors: list = field(default_factory=list)
    result: str = "success"


def import_snapshot(conn, snapshot, *, now=None):
    """Write a verified snapshot into SQLite. Idempotent, failures isolated per company, nothing is ever closed.

    The caller (CLI) must have taken a verified backup and obtained the operator's separate approval.
    """
    now = now or db.utc_now()
    db.register_source(conn, SOURCE_ID, SOURCE_NAME, "yc", SOURCE_HOME, config_ref="user-attested-authorization", now=now)
    run_id = db.start_run(conn, "import", now=now)
    counts = ImportCounts(run_id=run_id)
    try:
        for c in snapshot["companies"]:
            try:
                with db.transaction(conn):
                    _import_company(conn, c, snapshot, counts, run_id, now)
            except db.IdentityConflict as exc:
                counts.company_errors.append((c["slug"], f"identity conflict: {exc}"))
        clean = not counts.company_errors and all(c["state"] in (CHECKED_LISTINGS, CHECKED_NONE) for c in snapshot["companies"])
        counts.result = "success" if clean else "partial"
        db.finish_run(conn, run_id, counts.result, now=db.utc_now(), summary={
            "source": SOURCE_ID, "companies": counts.companies, "jobs_created": counts.jobs_created,
            "jobs_changed": counts.jobs_changed, "jobs_unchanged": counts.jobs_unchanged,
            "company_errors": len(counts.company_errors), "requests": snapshot["requests"],
            "authorization": snapshot["authorization"]["basis"]})
    except BaseException as exc:
        db.finish_run(conn, run_id, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                      error_detail=f"{type(exc).__name__}: {str(exc)[:200]}", now=db.utc_now(), summary={"source": SOURCE_ID})
        raise
    return counts


def _import_company(conn, c, snapshot, counts, run_id, now):
    company = db.get_company(conn, c["company_id"])
    if company is None:
        raise db.IdentityConflict(f"company {c['company_id']} no longer exists")
    counts.companies += 1
    recency = snapshot.get("params", {}).get("recency_days", DEFAULT_RECENCY_DAYS)
    ref = c.get("retrieved_at")
    cats = {id(j): categorize(j, ref, recency) for j in c["jobs"]}
    evidence = evidence_counts(c, recency)
    kind = disagreement(c["hiring_flag"], c["state"], evidence)
    stale = [j for j in c["jobs"] if cats[id(j)] == HISTORICAL_STALE]
    if c["state"] in (CHECKED_LISTINGS, PARTIAL):
        for j in c["jobs"]:
            if cats[id(j)] == HISTORICAL_STALE:
                continue                                    # company-level evidence only; no job row, never closed
            obs = db.observe_job(conn, source_id=SOURCE_ID, source_url=j["url"], company_id=c["company_id"],
                                 title=j["title"], external_id=j["external_id"], location=j["location"],
                                 posting_url=j["url"], posted_at=j["posted_date"],
                                 experience_min_years=j.get("experience_min_years"), experience_text=j.get("experience_text"),
                                 provider_first_seen_at=j["posted_date"],
                                 snapshot_at=c["retrieved_at"], now=now)
            counts.jobs_created += obs.created
            counts.jobs_changed += obs.changed
            counts.jobs_unchanged += (not obs.created and not obs.changed)
    outcome = {CHECKED_LISTINGS: "success", CHECKED_NONE: "success", PARTIAL: "partial"}.get(c["state"], "failed")
    if c["state"] != NOT_CHECKED:
        db.record_source_check(conn, run_id, SOURCE_ID, outcome, scope=f"company/{c['slug']}", complete=False,
                               record_count=len(c["jobs"]), started_at=c["retrieved_at"], finished_at=c["retrieved_at"],
                               source_snapshot_at=c["retrieved_at"], error_info=c.get("error") or "; ".join(c.get("notes", [])) or None)
    facts = {"state": c["state"], "checked_at": c["retrieved_at"], "page_url": c["page_url"],
             "listed_count": len(c["jobs"]) if c["state"] in (CHECKED_LISTINGS, CHECKED_NONE, PARTIAL) else None,
             "job_rows_imported": sum(1 for j in c["jobs"] if cats[id(j)] != HISTORICAL_STALE),
             "hiring_flag_at_check": c["hiring_flag"], "disagreement": kind, "run_id": run_id,
             "evidence": evidence, "recency_days": recency, "current_hiring_evidence": hiring_evidence(c["state"], evidence),
             "complete": False, "http_status": c["http_status"], "note": c.get("error") or "; ".join(c.get("notes", [])) or None,
             "authorization": snapshot["authorization"]["basis"],
             # relative age text from the page ("about 7 years"); sightings.provider_freshness is a fixed enum, so it lives here
             "observed_jobs": [{"external_id": j["external_id"], "posted_text": j.get("posted_text"), "category": cats[id(j)]}
                               for j in c["jobs"] if cats[id(j)] != HISTORICAL_STALE],
             # years-old listings stay here as historical source evidence (URL, title, location, age text); not jobs
             "historical_listings": [{"external_id": j["external_id"], "title": j["title"], "url": j["url"], "location": j["location"],
                                     "posted_text": j.get("posted_text"), "age_days_estimate": job_age_days(j, ref),
                                     "apply_url": j.get("apply_url"), "apply_url_is_login_gate": bool(j.get("apply_url_gated")),
                                     "observed_at": c["retrieved_at"]} for j in stale][:50]}
    if c["state"] != NOT_CHECKED:
        previous = (json.loads(company["facts"]).get("yc_jobs") or {}).get("last_successful_check")
        record = {"last_attempt": facts,
                  "last_successful_check": facts if c["state"] in (CHECKED_LISTINGS, CHECKED_NONE) else previous}
        db.update_company_details(conn, c["company_id"], facts_patch={"yc_jobs": record}, now=now)
