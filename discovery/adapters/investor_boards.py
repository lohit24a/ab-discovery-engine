"""Stream A + B source adapter: investor / accelerator portfolio job boards (Getro and Consider platforms).

Audit and sources: docs/source_audits/investor_boards.md. The investor is the SOURCE; the startup is the
canonical COMPANY. One reader per platform, configured per board from config/investor_boards.json.

Validated live on 2026-10-20 (audit section 10): Getro boards expose a client search API
(POST api.getro.com/api/v2/collections/<network id>/search/jobs|companies; 0-based pages of 20; `filters` hash such as
{"searchable_locations": ["Bengaluru, Karnataka, India"]}; Accept application/json) and Consider boards require the
board's own token flow (GET the page with a cookie jar, then POST /api-boards/search-jobs with the page's csrfToken in
X-CSRF-Token; cursor `meta.sequence`; `query` hash such as {"locations": [...]}).
Request formats started from public upstream descriptions (jobslop, OpenPostings; no code is copied, neither
repository has a license) and were checked in the first bounded live probes (2026-10-20, audit section 10):
  * Getro (Accel): the server-rendered page carries only the FIRST batch (20 jobs of 45,967; 12 companies of 709) and
    ignores ?page=; the API above paginates correctly and applies location filters at the source.
  * Consider (Lightspeed): POST /api-boards/search-jobs answers HTTP 412 INVALID_CSRF without the page-issued token
    (also with Origin/Referer only, and with the token but no session cookie); with the cookie jar and token it
    returns cursor-paginated pages of 50 with a reported total.
Whatever does not match degrades to a failed or partial scan, never to "no records".

Rules that matter:
  * A scan is COMPLETE only if the walk ended because the platform said there is nothing more, no page or
    cursor repeated, no error occurred, and (when a reported total exists) distinct records == reported total.
    Everything else is partial/blocked/failed with the anomaly recorded. Complete + zero records is a verified
    empty scope; a failed request is never an empty result.
  * No retries, no redirects, sequential requests, a pause between requests, hard page/request caps, a host lock
    to the board's own hosts, an honest User-Agent, TLS verification on, no cookies/login/proxy.
  * Companies are linked to an existing canonical company only on strong evidence (the board-reported domain
    equals exactly one known source-reported domain, not a shared host). Names never merge companies.
  * A board job whose employer application URL equals the URL of exactly one job already seen by ANOTHER source is
    the same posting: the board sighting is attached to that job and its company becomes the board company's
    canonical record (strong evidence). Titles and locations never merge jobs; ambiguity or a conflict with an
    earlier company link leaves the job separate and is reported.
  * A board listing is never proof that a job is open; remote is never India eligibility; nothing here closes,
    ages out or publishes anything.
"""

import hashlib
import html as html_lib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

from .. import database as db

CONFIG_PATH = db.PROJECT_ROOT / "config" / "investor_boards.json"
SNAPSHOT_DIR = db.PROJECT_ROOT / "data" / "investor_boards"
SNAPSHOT_FORMAT = 1
SOURCE_TYPE = "investor_board"
USER_AGENT = "ab-discovery-engine/0.10 (personal job research; manual run; read-only)"
PLATFORMS = ("getro", "consider")
STATUSES = ("configured", "page_probed", "probed_partial", "probed_blocked", "validated_live")
SCOPES = ("jobs", "companies")

MAX_PAGES_CEILING = 60   # raised from 50 on 2026-10-20 with operator approval: Accel's 709-company directory is 60 pages of 12
MAX_REQUESTS_CEILING = 120
MIN_DELAY_SECONDS = 2.0
TIMEOUT_SECONDS = 20
MAX_BYTES = 8 * 1024 * 1024

# Domains shared by many companies: never usable as identity evidence.
SHARED_HOSTS = {"ycombinator.com", "producthunt.com", "linkedin.com", "github.com", "linktr.ee", "notion.site",
                "notion.so", "medium.com", "google.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
                "wellfound.com", "angel.co", "crunchbase.com", "getro.com", "considerhq.com", "lever.co",
                "greenhouse.io", "ashbyhq.com", "workable.com", "bamboohr.com"}


class InvestorBoardError(Exception):
    """Configuration, collection or import cannot proceed safely. Nothing was changed."""


# --------------------------------------------------------------------- config

@dataclass(frozen=True)
class Board:
    key: str
    name: str
    platform: str
    url: str
    network_id: object
    board_slug: object
    permission: str
    evidence: str
    page_param: object
    company_directory: str
    extra_hosts: tuple
    page_size: int
    max_pages: int
    max_requests: int
    min_delay_seconds: float
    enabled: bool = False
    status: str = "configured"
    last_check: object = None
    filter_scope: str = "none (all records on the board)"
    companies_page: object = None
    company_only: bool = False
    notes: str = ""
    api: object = None                 # "getro_api" once the client API was validated for this board
    location_filter: object = None     # exact location value applied at the source, e.g. "Bengaluru, Karnataka, India"

    @property
    def supported(self):
        return self.platform in PLATFORMS

    @property
    def origin(self):
        p = urlsplit(self.url)
        return f"{p.scheme}://{p.netloc}"

    @property
    def hosts(self):
        return {urlsplit(self.url).netloc.lower(), *[h.lower() for h in self.extra_hosts]}

    @property
    def source_id(self):
        return f"inv_{self.key}"

    @property
    def jobs_url(self):
        base = self.url.rstrip("/")
        return base if base.endswith("/jobs") else base + "/jobs"

    @property
    def companies_url(self):
        return self.companies_page or self.origin + "/companies"


def load_boards(path=CONFIG_PATH):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))["boards"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InvestorBoardError(f"board config unreadable ({type(exc).__name__})") from exc
    boards, seen = {}, set()
    for b in raw:
        try:
            key, platform, url = b["key"], b["platform"], b["url"]
            if not re.fullmatch(r"[a-z0-9_]{2,32}", key) or key in seen:
                raise ValueError("bad or duplicate key")
            if platform not in PLATFORMS and platform != "unsupported":
                raise ValueError(f"unknown platform {platform!r}")
            if urlsplit(url).scheme != "https" or not urlsplit(url).netloc:
                raise ValueError("board url must be https")
            if b.get("permission") not in ("unverified", "approved", "forbidden"):
                raise ValueError("permission must be unverified, approved or forbidden")
            pages, reqs = int(b["max_pages"]), int(b["max_requests"])
            if not 1 <= pages <= MAX_PAGES_CEILING or not 1 <= reqs <= MAX_REQUESTS_CEILING:
                raise ValueError("page/request limits outside the hard ceilings")
            boards[key] = Board(key=key, name=b["name"], platform=platform, url=url, network_id=b.get("network_id"),
                                board_slug=b.get("board_slug"), permission=b["permission"], evidence=b.get("evidence", ""),
                                page_param=b.get("page_param"), company_directory=b.get("company_directory", "from_jobs"),
                                extra_hosts=tuple(b.get("extra_hosts", [])), page_size=int(b["page_size"]),
                                max_pages=pages, max_requests=reqs,
                                min_delay_seconds=max(float(b.get("min_delay_seconds", MIN_DELAY_SECONDS)), MIN_DELAY_SECONDS),
                                enabled=bool(b.get("enabled", False)), status=b.get("status", "configured"),
                                last_check=b.get("last_check"), filter_scope=b.get("filter_scope", "none (all records on the board)"),
                                companies_page=b.get("companies_url"), company_only=bool(b.get("company_only", False)),
                                notes=b.get("notes", ""), api=b.get("api"), location_filter=b.get("location_filter"))
            if boards[key].status not in STATUSES:
                raise ValueError("status must be one of " + ", ".join(STATUSES))
            seen.add(key)
        except (KeyError, ValueError, TypeError) as exc:
            raise InvestorBoardError(f"board entry {b.get('key', '?')!r} invalid: {exc}") from exc
    return boards


def select_boards(boards, keys):
    names = [k.strip().lower() for k in (keys or "").split(",") if k.strip()]
    if not names:
        raise InvestorBoardError("name the boards explicitly, e.g. --boards accel,lightspeed")
    bad = [k for k in names if k not in boards]
    if bad:
        raise InvestorBoardError(f"unknown board(s): {', '.join(bad)}; available: {', '.join(boards)}")
    forbidden = [k for k in names if boards[k].permission == "forbidden"]
    if forbidden:
        raise InvestorBoardError(f"board(s) marked forbidden: {', '.join(forbidden)}")
    unsupported = [k for k in names if not boards[k].supported]
    if unsupported:
        raise InvestorBoardError(f"board(s) {', '.join(unsupported)} have no supported reader yet (platform not determined or custom)")
    disabled = [k for k in names if not boards[k].enabled]
    if disabled:
        raise InvestorBoardError(f"board(s) {', '.join(disabled)} are not enabled; the operator must approve their probe plan first")
    return [boards[k] for k in dict.fromkeys(names)]


# ------------------------------------------------------------------------ HTTP

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def default_http(board, request, *, opener=None):
    """One request -> (status|None, body|None, error|None). No retries, no redirects, host-locked."""
    url = request["url"]
    if urlsplit(url).netloc.lower() not in board.hosts:
        raise InvestorBoardError(f"refusing to request a host outside the board's allowed hosts: {urlsplit(url).netloc}")
    data = json.dumps(request["json"]).encode() if request.get("json") is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.8, */*;q=0.5"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    headers.update(request.get("headers") or {})
    req = urllib.request.Request(url, data=data, headers=headers, method=request["method"])
    opener = opener or urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=TIMEOUT_SECONDS) as resp:
            body = resp.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                return resp.status, None, "response larger than the size limit"
            return resp.status, body, None
    except urllib.error.HTTPError as exc:
        try:
            detail = re.sub(r"\s+", " ", exc.read(300).decode("utf-8", "replace"))[:80]
        except Exception:
            detail = ""
        return exc.code, None, f"HTTP {exc.code}" + (f": {detail}" if detail else "")
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {str(exc)[:120]}"


class Session:
    """One scan's HTTP session: a cookie jar that only ever holds cookies the board itself set during this scan."""

    def __init__(self):
        import http.cookiejar
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPCookieProcessor(self.jar))

    def __call__(self, board, request):
        return default_http(board, request, opener=self.opener)


# ------------------------------------------------------------------- normalizing

def _text(v):
    return v.strip() if isinstance(v, str) and v.strip() else None


def _date(v):
    """ISO date from a unix timestamp (seconds or milliseconds) or an ISO string; None otherwise."""
    try:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)) and v > 0:
            ts = v / 1000 if v > 1e11 else v
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
            return v[:10]
    except (OverflowError, OSError, ValueError):
        return None
    return None


def _locations(*values):
    for v in values:
        if isinstance(v, list) and v:
            return [s.strip() for s in (x if isinstance(x, str) else (x.get("name") or x.get("label") or "") for x in v)
                    if isinstance(s, str) and s.strip()]
    return []


def reported_domain(value):
    d = db.normalize_domain(value if isinstance(value, str) else None)
    if not d or "." not in d or d in SHARED_HOSTS or any(d.endswith("." + h) for h in SHARED_HOSTS):
        return None
    return d


def _company_ref(name, slug, cid, domain):
    return {"name": _text(name), "slug": _text(slug), "board_company_id": str(cid) if cid not in (None, "") else None,
            "domain": reported_domain(domain)}


def parse_next_data(html_text):
    m = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>\s*(\{.*?\})\s*</script>', html_text or "", re.S | re.I)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def normalize_getro_job(item, board):
    if not isinstance(item, dict):
        return None
    org = item.get("organization") if isinstance(item.get("organization"), dict) else {}
    jid = item.get("id") if item.get("id") not in (None, "") else item.get("slug")
    title = _text(item.get("title"))
    if jid in (None, "") or not title:
        return None
    slug, oslug = _text(item.get("slug")), _text(org.get("slug"))
    listing = f"{board.origin}/companies/{oslug}/jobs/{slug}" if slug and oslug else None
    apply_url = _text(item.get("url"))
    if not (listing or apply_url):
        return None
    locs = _locations(item.get("searchable_locations") or item.get("searchableLocations"), item.get("locations"))
    mode = _text(item.get("work_mode") or item.get("workMode"))
    return {"external_id": str(jid), "title": title, "url": listing or apply_url, "apply_url": apply_url,
            "company": _company_ref(org.get("name"), oslug, org.get("id"), org.get("domain") or org.get("website")),
            "locations": locs, "location": " / ".join(locs[:3]) or None,
            "remote_hint": mode == "remote" or any("remote" in l.lower() for l in locs) or "remote" in title.lower(),
            "work_mode": mode, "posted_date": _date(item.get("created_at") if item.get("created_at") is not None else item.get("createdAt")),
            "seniority": _text(item.get("seniority"))}


def normalize_getro_company(item, board):
    if not isinstance(item, dict):
        return None
    name = _text(item.get("name"))
    cid = item.get("id") if item.get("id") not in (None, "") else item.get("slug")
    if not name or cid in (None, ""):
        return None
    slug = _text(item.get("slug"))
    locs = _locations(item.get("locations"), item.get("searchableLocations"), item.get("searchable_locations"))
    tags = item.get("industryTags") or item.get("industry_tags") or item.get("industries") or []
    return {"external_id": str(cid), "name": name, "slug": slug, "domain": reported_domain(item.get("domain") or item.get("website")),
            "profile_url": f"{board.origin}/companies/{slug}" if slug else None, "location": " / ".join(locs[:3]) or None,
            "industries": [t for t in tags if isinstance(t, str)][:10],
            "description": _text(item.get("description") or item.get("shortDescription")),
            "headcount": item.get("headCount", item.get("head_count")),
            "open_jobs_reported": item.get("activeJobsCount", item.get("active_jobs_count", item.get("jobsCount")))}


def normalize_consider_job(item, board):
    if not isinstance(item, dict):
        return None
    title = _text(item.get("title"))
    jid = item.get("jobId") if item.get("jobId") not in (None, "") else item.get("id")
    apply_url, url = _text(item.get("applyUrl")), _text(item.get("url"))
    if jid in (None, "") or not title or not (apply_url or url):
        return None
    locs = _locations(item.get("locations"))
    return {"external_id": str(jid), "title": title, "url": url or apply_url, "apply_url": apply_url,
            "company": _company_ref(item.get("companyName"), item.get("companySlug"), item.get("companyId"), item.get("companyDomain")),
            "locations": locs, "location": " / ".join(locs[:3]) or None,
            "remote_hint": bool(item.get("remote")) or any("remote" in l.lower() for l in locs),
            "work_mode": "hybrid" if item.get("hybrid") else ("remote" if item.get("remote") else None),
            "posted_date": _date(item.get("timeStamp")), "seniority": None}


# ------------------------------------------------------------------ scan engine

@dataclass
class ScopeResult:
    scope: str
    status: str = "failed"             # complete | partial | blocked | failed
    complete: bool = False
    verified_empty: bool = False
    pages_requested: int = 0
    pages_completed: int = 0
    requests: int = 0
    reported_total: object = None
    records: list = field(default_factory=list)
    anomalies: list = field(default_factory=list)
    error: object = None
    stopped: object = None
    filters: str = "none (all records on the board)"


class _Walk:
    """Bookkeeping shared by all readers: budgets, pacing, duplicate detection, completeness decision."""

    def __init__(self, board, scope, http, sleep, clock):
        self.board, self.http, self.sleep, self.clock = board, http, sleep, clock
        self.res = ScopeResult(scope=scope)
        self.seen_ids, self.seen_pages, self.seen_cursors = set(), set(), set()
        self.exhausted = False
        self.fatal = False

    def request(self, req):
        r = self.res
        if r.requests >= self.board.max_requests:
            r.anomalies.append("request cap reached")
            r.stopped = "request cap"
            return None
        if r.requests:
            self.sleep(self.board.min_delay_seconds)
        r.requests += 1
        r.pages_requested += 1
        status, body, error = self.http(self.board, req)
        if status in (403, 429):
            r.status, r.error, r.stopped, self.fatal = "blocked", f"HTTP {status}: access restricted; nothing retried", f"blocked by HTTP {status}", True
            return None
        if status != 200 or body is None:
            r.error, r.stopped, self.fatal = error or f"HTTP {status}", "request failed", True
            return None
        return body

    def add_page(self, items, page_key):
        """Register one parsed page. Returns False when the walk must stop (duplicate page)."""
        r = self.res
        ids = [i["external_id"] for i in items]
        r.pages_completed += 1
        if page_key in self.seen_pages or (ids and all(i in self.seen_ids for i in ids)):
            r.anomalies.append(f"duplicate page (page {r.pages_completed} repeats earlier records)")
            r.stopped = "duplicate page"
            self.fatal = True
            return False
        self.seen_pages.add(page_key)
        for item in items:
            if item["external_id"] in self.seen_ids:
                r.anomalies.append(f"duplicate record {item['external_id']} inside the feed")
                continue
            self.seen_ids.add(item["external_id"])
            r.records.append(item)
        return True

    def finish(self):
        r = self.res
        if r.status == "blocked":
            return r
        distinct = len(r.records)
        total = r.reported_total
        if self.exhausted and not self.fatal and r.error is None:
            if total is not None and distinct != total:
                r.anomalies.append(f"count mismatch: board reports {total}, retrieved {distinct}")
                r.status = "partial"
            else:
                r.status, r.complete, r.verified_empty = "complete", True, distinct == 0
        elif distinct or r.pages_completed:
            r.status = "partial"
            if not r.stopped:
                r.anomalies.append("walk ended before the board signalled the end of its results")
        else:
            r.status = "failed" if r.error else "partial"
        return r


def _json(body):
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None


def scan_consider(board, http, sleep=time.sleep, clock=time.monotonic, location=None):
    """Consider boards, as the board's own client does it (validated live on Lightspeed):

    1. GET the board page: it embeds the board id ("fixedBoard") and a page-issued "csrfToken", and the server starts a
       session cookie (held only in this scan's cookie jar);
    2. POST {origin}/api-boards/search-jobs with X-CSRF-Token and Accept: application/json, body
       {"meta": {"size": <=50, "sequence": <cursor>}, "board": {"id": <id>, "isParent": true}, "query": {...}, "grouped": false};
       the response has jobs[], total and meta.sequence (the next cursor, absent on the last page).
    A location filter is sent as query {"locations": [<value>]} and applies at the source; the scan is then complete for
    that filter scope only. Only the jobs scope exists on this platform.
    """
    walk = _Walk(board, "jobs", http, sleep, clock)
    res, cursor = walk.res, None
    res.filters = f"locations = {location}" if location else res.filters
    api = f"{board.origin}/api-boards/search-jobs"
    page = walk.request({"method": "GET", "url": board.url, "headers": {"Accept": "text/html"}})
    res.pages_requested -= 1              # the page fetch is a session/lookup request, not a result page
    text = page.decode("utf-8", "replace") if page else ""
    m_token = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', text)
    m_board = re.search(r'"fixedBoard"\s*:\s*"([^"]+)"', text)
    slug = board.board_slug or (m_board.group(1) if m_board else None)
    if page is None:
        return walk.finish()
    if not m_token or not slug:
        res.error, res.stopped, walk.fatal = "board page lacks the csrf token or board id", "no session", True
        return walk.finish()
    headers = {"X-CSRF-Token": m_token.group(1), "Accept": "application/json"}
    query = {"locations": [location]} if location else {}
    for _ in range(board.max_pages):
        meta = {"size": min(board.page_size, 50)}
        if cursor:
            meta["sequence"] = cursor
        body = walk.request({"method": "POST", "url": api, "headers": headers,
                             "json": {"meta": meta, "board": {"id": slug, "isParent": True}, "query": query, "grouped": False}})
        if body is None:
            break
        data = _json(body)
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list) or data.get("errors") or data.get("error"):
            res.error, res.stopped, walk.fatal = f"unexpected response ({str((data or {}).get('error') or (data or {}).get('errors') or 'shape')[:60]})", "schema", True
            break
        if isinstance(data.get("total"), int) and not isinstance(data.get("total"), bool):
            res.reported_total = data["total"]
        items = [j for j in (normalize_consider_job(x, board) for x in data["jobs"]) if j]
        if len(items) < len(data["jobs"]):
            res.anomalies.append(f"{len(data['jobs']) - len(items)} job(s) on a page were unusable and skipped")
        nxt = (data.get("meta") or {}).get("sequence") if isinstance(data.get("meta"), dict) else None
        if not data["jobs"]:
            walk.exhausted = True
            res.pages_completed += 1
            break
        if not walk.add_page(items, ("cursor", cursor)):
            break
        if not nxt:
            walk.exhausted = True
            break
        if nxt in walk.seen_cursors or nxt == cursor:
            res.anomalies.append("repeated cursor")
            res.stopped, walk.fatal = "repeated cursor", True
            break
        walk.seen_cursors.add(nxt)
        cursor = nxt
    else:
        res.anomalies.append("page cap reached")
        res.stopped = "page cap"
    return walk.finish()


def _getro_page_url(base, param, n):
    return base if n == 1 else f"{base}?{urlencode({param or 'page': n})}"


def scan_getro(board, http, scope, sleep=time.sleep, clock=time.monotonic):
    """Board page(s) with __NEXT_DATA__. jobs: initialState.jobs; companies: initialState.companies (assumed shape)."""
    walk = _Walk(board, scope, http, sleep, clock)
    res = walk.res
    base = board.jobs_url if scope == "jobs" else board.companies_url
    key = "jobs" if scope == "jobs" else "companies"
    norm = normalize_getro_job if scope == "jobs" else normalize_getro_company
    for n in range(1, board.max_pages + 1):
        body = walk.request({"method": "GET", "url": _getro_page_url(base, board.page_param, n)})
        if body is None:
            break
        data = parse_next_data(body.decode("utf-8", "replace"))
        state = (((data or {}).get("props") or {}).get("pageProps") or {}).get("initialState") if isinstance(data, dict) else None
        section = state.get(key) if isinstance(state, dict) else None
        found = section.get("found") if isinstance(section, dict) else None
        if not isinstance(found, list):
            res.error, res.stopped, walk.fatal = f"board page has no initialState.{key}.found list", "schema", True
            break
        total = section.get("total")
        if isinstance(total, int) and not isinstance(total, bool):
            res.reported_total = total
        items = [x for x in (norm(i, board) for i in found) if x]
        if len(items) < len(found):
            res.anomalies.append(f"{len(found) - len(items)} {key} entr(ies) on page {n} unusable and skipped")
        if not found:
            walk.exhausted = True
            res.pages_completed += 1
            break
        if not walk.add_page(items, ("page", n, tuple(i["external_id"] for i in items))):
            break
        if res.reported_total is not None and len(res.records) >= res.reported_total:
            walk.exhausted = True
            break
        if not board.page_param:
            res.anomalies.append(f"server-rendered page holds only the first {len(res.records)} of {res.reported_total} {key}; "
                                 "the board pages through its client API, which is not collected")
            res.stopped = "first batch only"
            break
    else:
        res.anomalies.append("page cap reached")
        res.stopped = "page cap"
    return walk.finish()


def _getro_network_id(board, walk):
    """Network id from config, else one GET of the board page (pageProps.network.id)."""
    if board.network_id:
        return int(board.network_id)
    body = walk.request({"method": "GET", "url": board.url, "headers": {"Accept": "text/html"}})
    walk.res.pages_requested -= 1
    data = parse_next_data(body.decode("utf-8", "replace")) if body else None
    nid = ((((data or {}).get("props") or {}).get("pageProps") or {}).get("network") or {}).get("id") if isinstance(data, dict) else None
    try:
        return int(nid)
    except (TypeError, ValueError):
        walk.res.error = walk.res.error or "could not read the network id from the board page"
        walk.res.stopped, walk.fatal = walk.res.stopped or "no network id", True
        return None


def scan_getro_api(board, http, scope, sleep=time.sleep, clock=time.monotonic, location=None):
    """Getro client search API (validated live on Accel, 2026-10-20).

    POST https://api.getro.com/api/v2/collections/<network id>/search/<jobs|companies> with JSON
    {"hitsPerPage": 20, "page": <0-based>, "filters": {"searchable_locations": [<value>]}, "query": ""} and
    Accept: application/json. Reply: {"results": {"jobs"|"companies": [...], "count": <total for these filters>}}.
    hitsPerPage is clamped to 20 by the server; a location filter applies at the source (Accel: 45,967 jobs globally,
    5,021 for "India", 3,065 for "Bengaluru, Karnataka, India"). 'filters' must be a hash (a string answers HTTP 422).
    """
    walk = _Walk(board, scope, http, sleep, clock)
    res = walk.res
    res.filters = f"searchable_locations = {location}" if location else res.filters
    nid = _getro_network_id(board, walk)
    if nid is None:
        return walk.finish()
    key = "jobs" if scope == "jobs" else "companies"
    norm = normalize_getro_job if scope == "jobs" else normalize_getro_company
    url = f"https://api.getro.com/api/v2/collections/{nid}/search/{key}"
    filters = {"searchable_locations": [location]} if location else {}
    for page_no in range(board.max_pages):
        body = walk.request({"method": "POST", "url": url, "headers": {"Accept": "application/json"},
                             "json": {"hitsPerPage": 20, "page": page_no, "filters": filters, "query": ""}})
        if body is None:
            break
        data = _json(body)
        results = data.get("results") if isinstance(data, dict) else None
        found = results.get(key) if isinstance(results, dict) else None
        if not isinstance(found, list):
            res.error, res.stopped, walk.fatal = f"unexpected response ({str((data or {}).get('error') if isinstance(data, dict) else 'shape')[:60]})", "schema", True
            break
        count = results.get("count")
        if isinstance(count, int) and not isinstance(count, bool):
            res.reported_total = count
        items = [x for x in (norm(i, board) for i in found) if x]
        if len(items) < len(found):
            res.anomalies.append(f"{len(found) - len(items)} {key} entr(ies) on page {page_no} unusable and skipped")
        if not found:
            walk.exhausted = True
            res.pages_completed += 1
            break
        if not walk.add_page(items, ("page", page_no, tuple(i["external_id"] for i in items))):
            break
        if res.reported_total is not None and len(res.records) >= res.reported_total:
            walk.exhausted = True
            break
    else:
        res.anomalies.append("page cap reached")
        res.stopped = "page cap"
    return walk.finish()


def companies_from_jobs(job_records):
    """Companies observed through job listings (no directory): complete is never claimed for this scope."""
    seen, out = {}, []
    for j in job_records:
        ref = j["company"]
        key = ref["board_company_id"] or ref["slug"] or (ref["name"] or "").lower()
        if key and key not in seen:
            seen[key] = True
            out.append({"external_id": key, "name": ref["name"], "slug": ref["slug"], "domain": ref["domain"],
                        "profile_url": None, "location": None, "industries": [], "description": None,
                        "headcount": None, "open_jobs_reported": None})
    return [c for c in out if c["name"]]


def scan_board(board, http, scopes=SCOPES, sleep=time.sleep, clock=time.monotonic, location=None, company_location=None):
    """Run the board's platform reader for the requested scopes. `location` is applied at the source when supported (jobs);
    the company directory is filtered only when the operator passes `company_location` explicitly."""
    location = location or board.location_filter
    out = {}
    use_api = board.platform == "getro" and board.api == "getro_api"
    if "jobs" in scopes:
        if board.platform == "consider":
            out["jobs"] = scan_consider(board, http, sleep, clock, location)
        elif use_api:
            out["jobs"] = scan_getro_api(board, http, "jobs", sleep, clock, location)
        else:
            out["jobs"] = scan_getro(board, http, "jobs", sleep, clock)
            if location:
                out["jobs"].anomalies.append("location filter not applied: the server-rendered page cannot be filtered")
    if "companies" in scopes:
        if board.platform == "getro" and board.company_directory == "next_data":
            out["companies"] = (scan_getro_api(board, http, "companies", sleep, clock, company_location) if use_api
                                else scan_getro(board, http, "companies", sleep, clock))
        else:
            derived = ScopeResult(scope="companies", status="partial", complete=False, filters="derived from job listings only (no company directory)")
            jobs = out.get("jobs")
            if jobs:
                derived.records = companies_from_jobs(jobs.records)
                derived.anomalies.append("company directory not available on this platform; companies seen only through job listings")
            else:
                derived.anomalies.append("no job scan available to derive companies from")
            out["companies"] = derived
    return out


def collect(boards, *, http=None, sleep=time.sleep, clock=time.monotonic, now_fn=db.utc_now, scopes=SCOPES, location=None, company_location=None):
    """Bounded read of the given boards. `http` is a callable(board, request); by default each board gets its own Session."""
    started, t0 = now_fn(), clock()
    out = []
    for b in boards:
        scans = scan_board(b, http or Session(), scopes, sleep, clock, location, company_location)
        out.append({"key": b.key, "name": b.name, "platform": b.platform, "url": b.url, "permission": b.permission,
                    "scopes": {k: vars(v) for k, v in scans.items()}})
    snap = {"format": SNAPSHOT_FORMAT, "source": "investor_boards", "collected_at": started, "finished_at": now_fn(),
            "seconds": round(clock() - t0, 1), "boards": out}
    snap["records_sha256"] = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    return snap


def write_snapshot(snapshot, directory=None):
    directory = Path(directory or SNAPSHOT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9]", "", snapshot["collected_at"])[:14]
    path = directory / f"{stamp}_{'-'.join(b['key'] for b in snapshot['boards'])}.json"
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def load_snapshot(path):
    try:
        snap = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvestorBoardError(f"snapshot unreadable ({type(exc).__name__})") from exc
    if snap.get("format") != SNAPSHOT_FORMAT or snap.get("source") != "investor_boards" or not isinstance(snap.get("boards"), list):
        raise InvestorBoardError("not an investor_boards snapshot of the supported format")
    if hashlib.sha256(json.dumps(snap["boards"], sort_keys=True).encode()).hexdigest() != snap.get("records_sha256"):
        raise InvestorBoardError("snapshot content does not match its recorded hash; refusing to use it")
    return snap


# ------------------------------------------------------------- identity resolution

def _keys(ref, first=None):
    """Identifiers a board uses for one company, strongest first (id, slug, then the lowercase name as a last resort)."""
    ids = [k for k in dict.fromkeys([first, ref.get("board_company_id"), ref.get("slug")]) if k]
    if ids:
        return ids
    name = (ref.get("name") or "").lower()
    return [name] if name else []          # a bare name is the key of last resort, only when the board gives no id or slug


def _name_key(name):
    return db.normalize_name(name or "")


class Resolver:
    """Maps a board company to a canonical company. Strong evidence only; otherwise a provisional company."""

    def __init__(self, conn):
        self.conn = conn
        self.by_domain = {}
        self.by_name = {}
        for row in conn.execute("SELECT company_id, name, domain, facts FROM companies"):
            facts = json.loads(row["facts"])
            domains = set()
            if row["domain"]:
                domains.add(db.normalize_domain(row["domain"]))
            domains.add(reported_domain((facts.get("yc") or {}).get("website_domain_reported")))
            domains.update(reported_domain(d) for d in (facts.get("investor_boards") or {}).get("reported_domains", []))
            for d in filter(None, domains):
                self.by_domain.setdefault(d, set()).add(row["company_id"])
            self.by_name.setdefault(_name_key(row["name"]), set()).add(row["company_id"])

    def lookup(self, source_id, keys, ref):
        """-> (kind, company_id|None, possible_duplicates). kind: existing_sighting | domain_match | ambiguous | new."""
        for key in keys:
            s = db.find_sighting(self.conn, source_id, f"board-company:{key}", key, is_job=False)
            if s:
                return "existing_sighting", s["company_id"], []
        dom = ref.get("domain")
        hits = self.by_domain.get(dom, set()) if dom else set()
        names = self.by_name.get(_name_key(ref.get("name")), set()) - hits
        if len(hits) == 1:
            return "domain_match", next(iter(hits)), sorted(names)
        if len(hits) > 1:
            return "ambiguous", None, sorted(hits | names)
        return "new", None, sorted(names)

    def remember(self, company_id, name, domain):
        self.by_name.setdefault(_name_key(name), set()).add(company_id)
        if domain:
            self.by_domain.setdefault(domain, set()).add(company_id)


# ------------------------------------------------------------------- preview

def summarize(snapshot, conn=None):
    out = {"boards": [], "totals": {"jobs": 0, "companies": 0, "requests": 0}}
    for b in snapshot["boards"]:
        row = {"key": b["key"], "name": b["name"], "platform": b["platform"], "scopes": {}}
        for scope, s in b["scopes"].items():
            row["scopes"][scope] = {"status": s["status"], "complete": s["complete"], "verified_empty": s["verified_empty"],
                                    "records": len(s["records"]), "reported_total": s["reported_total"],
                                    "pages_requested": s["pages_requested"], "pages_completed": s["pages_completed"],
                                    "requests": s["requests"], "anomalies": s["anomalies"], "error": s["error"],
                                    "stopped": s["stopped"]}
            out["totals"][scope] += len(s["records"])
            out["totals"]["requests"] += s["requests"]
        out["boards"].append(row)
    if conn is not None:
        out["identity"] = preview_identity(conn, snapshot)
    return out


def preview_identity(conn, snapshot):
    """Read-only: how each DISTINCT board company and each job would resolve if imported (no double counting)."""
    resolver = Resolver(conn)
    kinds, possible, seen_domains = {}, 0, {}
    jobs_new = jobs_known = attach_by_url = ambiguous_url = 0
    dup_jobs = {}
    for b in snapshot["boards"]:
        sid = f"inv_{b['key']}"
        url_index = employer_url_index(conn, sid)
        refs, seen_keys = [], set()
        comp_scope = b["scopes"].get("companies", {"records": []})
        for c in comp_scope["records"]:
            refs.append((_keys(c, c["external_id"]), c))
        job_records = b["scopes"].get("jobs", {"records": []})["records"]
        for j in job_records:
            refs.append((_keys(j["company"]), j["company"]))
            found = db.find_sighting(conn, sid, db.normalize_url(sighting_url(j)), j["external_id"], is_job=True)
            jobs_known += bool(found)
            jobs_new += not found
            if not found:
                state = match_by_employer_url(url_index, j)[0]
                attach_by_url += state == "match"
                ambiguous_url += state == "ambiguous"
            k = ((j["company"]["domain"] or (j["company"]["name"] or "").lower()), db.normalize_name(j["title"]), (j["location"] or "").lower())
            dup_jobs.setdefault(k, set()).add(b["key"])
        for keys, ref in refs:
            ident = tuple(keys[:1]) or (ref.get("name"),)
            if ident in seen_keys:
                continue
            seen_keys.add(ident)
            kind, cid, dups = resolver.lookup(sid, keys, ref)
            if kind == "new" and ref.get("domain"):
                kind = "new_linked_to_earlier_board" if seen_domains.get(ref["domain"], b["key"]) != b["key"] else "new"
                seen_domains.setdefault(ref["domain"], b["key"])
            kinds[kind] = kinds.get(kind, 0) + 1
            possible += bool(dups)
    return {"company_resolution": kinds, "companies_with_possible_name_duplicates": possible,
            "jobs_new_if_imported": jobs_new - attach_by_url, "jobs_attach_by_employer_url": attach_by_url,
            "jobs_ambiguous_url": ambiguous_url, "jobs_already_known": jobs_known,
            "possible_duplicate_jobs_across_boards": sum(1 for v in dup_jobs.values() if len(v) > 1)}


def sighting_url(job):
    """The URL a board sighting is filed under: the employer's application URL when the board gives one (the strongest identity
    evidence and the form other sources use), else the board listing URL. The listing URL stays in jobs.posting_url."""
    return job.get("apply_url") or job["url"]


def employer_url_index(conn, exclude_source):
    """normalized job URL -> {(job_id, company_id)} for jobs seen by sources other than `exclude_source`."""
    index = {}
    for row in conn.execute("""SELECT s.source_url, s.job_id, j.company_id FROM sightings s JOIN jobs j ON j.job_id = s.job_id
                               WHERE s.job_id IS NOT NULL AND s.source_id <> ?""", (exclude_source,)):
        index.setdefault(row[0], set()).add((row[1], row[2]))
    return index


def match_by_employer_url(index, job):
    """-> ('match', job_id, company_id) | ('ambiguous', None, None) | (None, None, None)."""
    hits = set()
    for u in (job.get("apply_url"), job.get("url")):
        if u:
            hits |= index.get(db.normalize_url(u), set())
    if len({h[0] for h in hits}) == 1:
        job_id, company_id = next(iter(hits))
        return "match", job_id, company_id
    return ("ambiguous", None, None) if hits else (None, None, None)


# --------------------------------------------------------------------- importing

@dataclass
class ImportCounts:
    run_id: str = ""
    companies_created: int = 0
    companies_linked_by_domain: int = 0
    companies_existing_sighting: int = 0
    companies_ambiguous: int = 0
    jobs_created: int = 0
    jobs_changed: int = 0
    jobs_unchanged: int = 0
    jobs_attached: int = 0            # attached to a job another source already holds (employer URL equality)
    url_conflicts: int = 0            # URL match disagrees with an earlier company link: left separate
    url_ambiguous: int = 0            # the URL belongs to several existing jobs: left separate
    errors: list = field(default_factory=list)
    result: str = "success"


def import_snapshot(conn, snapshot, *, now=None):
    """Idempotent import. The caller must have taken a verified backup and obtained the operator's approval."""
    now = now or db.utc_now()
    run_id = db.start_run(conn, "import", now=now)
    counts = ImportCounts(run_id=run_id)
    try:
        resolver = Resolver(conn)
        for b in snapshot["boards"]:
            sid = f"inv_{b['key']}"
            db.register_source(conn, sid, f"Investor board: {b['name']}", SOURCE_TYPE, b["url"],
                               config_ref="investor-boards-config", now=now)
            try:
                _import_board(conn, b, sid, resolver, counts, run_id, now, snapshot["collected_at"])
            except db.IdentityConflict as exc:
                counts.errors.append((b["key"], f"identity conflict: {exc}"))
        clean = not counts.errors and all(s["complete"] for b in snapshot["boards"] for k, s in b["scopes"].items() if k == "jobs")
        counts.result = "success" if clean else "partial"
        db.finish_run(conn, run_id, counts.result, now=db.utc_now(), summary={
            "source": "investor_boards", **{k: v for k, v in vars(counts).items() if isinstance(v, int)},
            "boards": [b["key"] for b in snapshot["boards"]], "errors": len(counts.errors)})
    except BaseException as exc:
        db.finish_run(conn, run_id, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                      error_detail=f"{type(exc).__name__}: {str(exc)[:200]}", now=db.utc_now(), summary={"source": "investor_boards"})
        raise
    return counts


def _import_board(conn, b, sid, resolver, counts, run_id, now, collected_at):
    """Order matters: (1) attach jobs whose employer URL identifies an existing job, which also fixes the board company's
    canonical record from strong evidence; (2) the company directory; (3) the remaining jobs. Doing the directory first would
    create a provisional company and then make the URL evidence look like a conflict."""
    url_index = employer_url_index(conn, sid)
    attached = set()
    with db.transaction(conn):
        comp_scope = b["scopes"].get("companies")
        job_scope = b["scopes"].get("jobs")
        for j in (job_scope["records"] if job_scope else []):
            ref = j["company"]
            keys = _keys(ref)
            if not ref["name"] or not keys:
                continue
            if db.find_sighting(conn, sid, db.normalize_url(sighting_url(j)), j["external_id"], is_job=True):
                continue
            state, match_job, match_company = match_by_employer_url(url_index, j)
            if state == "ambiguous":
                counts.url_ambiguous += 1
            elif state == "match":
                kind0, cid0, _ = resolver.lookup(sid, keys, ref)
                if kind0 in ("existing_sighting", "domain_match") and cid0 != match_company:
                    counts.url_conflicts += 1
                    counts.errors.append((b["key"], f"job {j['external_id']}: employer URL matches a job of another company than the board company's earlier link; left separate"))
                    continue
                for key in keys:
                    db.record_sighting(conn, sid, f"board-company:{key}", company_id=match_company, external_id=key, now=now)
                _merge_company_facts(conn, match_company, b, ref, None, None, [], now)
                resolver.remember(match_company, ref.get("name"), ref.get("domain"))
                db.record_sighting(conn, sid, sighting_url(j), job_id=match_job, external_id=j["external_id"],
                                   provider_first_seen_at=j["posted_date"], now=now)
                counts.jobs_attached += 1
                attached.add(j["external_id"])
        if comp_scope:
            for c in comp_scope["records"]:
                _resolve_and_record(conn, b, sid, resolver, counts, now, _keys(c, c["external_id"]), c, c.get("profile_url"), c)
        for j in (job_scope["records"] if job_scope else []):
            if j["external_id"] in attached:
                continue
            ref = j["company"]
            keys = _keys(ref)
            if not ref["name"] or not keys:
                counts.errors.append((b["key"], f"job {j['external_id']} has no usable company"))
                continue
            cid = _resolve_and_record(conn, b, sid, resolver, counts, now, keys, ref, None, None)
            if cid is None:
                continue
            obs = db.observe_job(conn, source_id=sid, source_url=sighting_url(j), company_id=cid, title=j["title"],
                                 external_id=j["external_id"], location=j["location"], posting_url=j["url"],
                                 posted_at=None, provider_first_seen_at=j["posted_date"], now=now)
            counts.jobs_created += obs.created
            counts.jobs_changed += obs.changed
            counts.jobs_unchanged += (not obs.created and not obs.changed)
        for scope, s_ in b["scopes"].items():
            outcome = {"complete": "success", "partial": "partial", "blocked": "partial", "failed": "failed"}[s_["status"]]
            note = "; ".join(filter(None, [s_.get("error"), *s_.get("anomalies", [])]))[:500] or None
            db.record_source_check(conn, run_id, sid, outcome, scope=f"{scope}:{s_['filters']}"[:120], complete=bool(s_["complete"]),
                                   record_count=len(s_["records"]), started_at=collected_at, finished_at=collected_at,
                                   source_snapshot_at=collected_at, error_info=note)


def _resolve_and_record(conn, b, sid, resolver, counts, now, keys, ref, profile_url, company_rec):
    kind, cid, dups = resolver.lookup(sid, keys, ref)
    name, domain = ref.get("name"), ref.get("domain")
    if kind == "ambiguous":
        counts.errors.append((b["key"], f"company {name!r}: domain {domain} matches several companies; left unlinked"))
        counts.companies_ambiguous += 1
        return None
    if kind in ("existing_sighting", "domain_match"):
        counts.companies_existing_sighting += kind == "existing_sighting"
        counts.companies_linked_by_domain += kind == "domain_match"
    else:
        cid, _ = db.get_or_create_company(conn, name, None, location=(company_rec or {}).get("location"),
                                          facts={"sheet_publish": False}, now=now)
        counts.companies_created += 1
        resolver.remember(cid, name, domain)
    for key in keys:                      # every identifier this board uses for the company points to the same record
        db.record_sighting(conn, sid, f"board-company:{key}", company_id=cid, external_id=key, now=now)
    _merge_company_facts(conn, cid, b, ref, company_rec, profile_url, dups, now)
    if domain:
        resolver.remember(cid, name, domain)
    return cid


def _merge_company_facts(conn, cid, b, ref, rec, profile_url, dups, now):
    company = db.get_company(conn, cid)
    inv = (json.loads(company["facts"]).get("investor_boards") or {"boards": {}, "reported_domains": []})
    entry = inv["boards"].get(b["key"], {})
    entry.update({"name": ref.get("name"), "board_company_id": ref.get("board_company_id") or ref.get("external_id"),
                  "slug": ref.get("slug"), "reported_domain": ref.get("domain"),
                  "profile_url": profile_url or entry.get("profile_url"), "platform": b["platform"]})
    if rec:
        entry.update({k: rec.get(k) for k in ("location", "industries", "description", "headcount", "open_jobs_reported")
                      if rec.get(k) not in (None, [], "")})
    if dups:
        entry["possible_duplicates"] = sorted(set(dups))[:10]
    inv["boards"][b["key"]] = entry
    if ref.get("domain") and ref["domain"] not in inv["reported_domains"]:
        inv["reported_domains"].append(ref["domain"])
    db.update_company_details(conn, cid, facts_patch={"investor_boards": inv}, now=now)
