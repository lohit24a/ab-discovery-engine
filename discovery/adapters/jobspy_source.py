"""Restricted-source adapter: Indeed, Naukri and LinkedIn through JobSpy's Python API (python-jobspy 1.4.0).

Phase A  collect_snapshot(): the only code that can reach a provider. It needs a live, one-time Authorization
         (discovery/restricted_policy.py) bound to the exact plan, runs the VPN preflight before EVERY search,
         enforces the policy budgets at the transport level, never retries after a restriction, and writes
         a temporary snapshot file. It writes nothing to SQLite or Google Sheets.
Phase B  import_snapshot(): reads a saved snapshot (no network) and imports it after a separate approval.

JobSpy's scrapers are used as shipped. Enforcement notes (see docs/source_audits/jobspy.md):
  * Every request of the Indeed and Naukri scrapers goes through jobspy.util.RotatingProxySession.request, which
    RequestBudget wraps to count and cap requests and to stop after the deadline.
  * "Requested results" is not a request limit: Indeed asks its API for up to 100 jobs per page and Naukri
    for 20 even when 10 are wanted; the library trims the list. The cap on stored results is applied locally.
  * No proxies, CA overrides, cookies or personal data are configured; certificate verification stays on.
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import database as db
from ..network_guard import NetworkGuardError, require_vpn
from ..restricted_policy import Authorization, AuthorizationError, PolicyError

SOURCE_IDS = {"indeed": "jobspy_indeed", "naukri": "jobspy_naukri", "linkedin": "jobspy_linkedin"}
IMPLEMENTED = tuple(SOURCE_IDS)
SOURCE_LABELS = {"indeed": "JobSpy Indeed India", "naukri": "JobSpy Naukri", "linkedin": "JobSpy LinkedIn"}
COUNTRY_PARAM_SOURCES = ("indeed",)                  # JobSpy reads country_indeed for Indeed only here
SNAPSHOT_DIR = db.PROJECT_ROOT / "data" / "restricted"
AUDIT_LOG = db.PROJECT_ROOT / "logs" / "restricted_runs.jsonl"
SNAPSHOT_FORMAT = 1
RESTRICTION = re.compile(r"\b(401|403|406|429|999)\b|captcha|recaptcha|blocked|rate.?limit|too many requests|authwall|"
                         r"unauthori[sz]ed|forbidden|access denied|cloudflare", re.I)
# Direct HTTP/socket use inside a scraper would bypass the request budget; such a scraper is refused.
_FORBIDDEN_IMPORT = re.compile(r"^\s*(?:import|from)\s+(requests|httpx|urllib\.request|urllib3|aiohttp|socket|curl_cffi|"
                               r"http\.client|websockets?)\b", re.M)
BATCH_SIZE = 200


class RequestBudgetExceeded(ValueError):
    """The transport-level request budget or deadline was reached; the request was NOT sent."""


class CredentialGuardError(ValueError):
    """A request would carry personal credentials, cookies, a proxy or ambient (environment/netrc) settings."""


_CREDENTIAL_HEADERS = {"authorization", "cookie", "proxy-authorization"}


_TRACE_HEADER_VALUES = ("server", "content-type", "retry-after")
_TRACE_HEADER_HIDE = {"set-cookie", "cookie", "authorization", "proxy-authorization"}


def _trace_entry(method, url, response):
    """Sanitized diagnostics for one request: stage, host, path (no query), status and non-sensitive header facts.

    Header NAMES (never values) are kept so a WAF vendor marker can be recognised; only server, content-type and
    retry-after values are kept. No body, cookie, token, query string or address is ever recorded.
    """
    from urllib.parse import urlsplit
    parts = urlsplit(str(url))
    entry = {"method": str(method).upper(), "host": parts.netloc, "path": parts.path, "status": getattr(response, "status_code", None)}
    try:
        headers = {str(k).lower(): v for k, v in dict(getattr(response, "headers", {}) or {}).items()}
    except Exception:
        headers = {}
    entry["header_names"] = sorted(h for h in headers if h not in _TRACE_HEADER_HIDE)[:40]
    for key in _TRACE_HEADER_VALUES:
        if key in headers:
            entry[key.replace("-", "_")] = str(headers[key])[:80]
    return entry


def _guard_request(session, kwargs, first_request):
    """Refuse any request that could carry a personal credential. Called for every request before it is sent.

    Only the library's own anonymous headers (user agent, accept-language and similar fixed headers) may be attached.
    trust_env is switched off so `requests` cannot pick up ~/.netrc credentials, proxy variables or CA-bundle variables.
    """
    try:
        session.trust_env = False
    except Exception:
        pass
    try:
        names = {str(k).lower() for k in (getattr(session, "headers", None) or {})}
        names |= {str(k).lower() for k in (kwargs.get("headers") or {})}
        jar = getattr(session, "cookies", None)
        stored_cookies = len(jar) if (first_request and jar is not None) else 0
    except Exception as exc:                          # cannot inspect -> cannot prove it is clean -> fail closed
        raise CredentialGuardError("request headers could not be inspected") from exc
    if names & _CREDENTIAL_HEADERS or kwargs.get("auth") or kwargs.get("cookies") or kwargs.get("proxies") or stored_cookies:
        raise CredentialGuardError("request would carry credentials, cookies or a proxy; refused")


def check_transport_enforceable(source, *, module_text=None):
    """Fail closed unless every request of this provider's scraper provably goes through RotatingProxySession.

    Reads the installed scraper's source (no network): it must build its session with create_session() from
    RequestsRotating/TLSRotating and must not import any other HTTP or socket library. Raises PolicyError otherwise,
    so a request budget that cannot be enforced reliably never reaches a provider.
    """
    if module_text is None:
        import importlib.util
        spec = importlib.util.find_spec(f"jobspy.{source}")
        if spec is None or not spec.origin:
            raise PolicyError(f"{source}: scraper module not found; request budget cannot be enforced")
        module_text = Path(spec.origin).read_text(encoding="utf-8")
        from jobspy import util
        if not (issubclass(util.RequestsRotating, util.RotatingProxySession)
                and issubclass(util.TLSRotating, util.RotatingProxySession)):
            raise PolicyError(f"{source}: JobSpy session classes changed; request budget cannot be enforced")
    typing_only = re.compile(r"^\s*from\s+(?:curl_cffi\.requests|requests)\s+import\s+Response\s*$", re.M)   # annotation, no I/O
    if _FORBIDDEN_IMPORT.search(typing_only.sub("", module_text)):
        raise PolicyError(f"{source}: scraper uses a transport outside RotatingProxySession; budget cannot be enforced")
    if "create_session(" not in module_text or not re.search(r"\b(RequestsRotating|TLSRotating)\(", module_text):
        raise PolicyError(f"{source}: scraper does not use the known session classes; budget cannot be enforced")
    return True


class RequestBudget:
    """Count and cap every HTTP request JobSpy's sessions make while active (context manager).

    Wraps RotatingProxySession.request, the single choke point of the Indeed/Naukri/LinkedIn sessions.
    With the default target it also caps wire-level hops of requests-based sessions (redirects are followed
    inside requests.Session.send, below .request), so a redirect cannot add requests beyond the budget.
    """

    def __init__(self, max_requests, deadline, clock=time.monotonic, target=None, wire_target=None):
        self.max_requests, self.deadline, self.clock, self.target = max_requests, deadline, clock, target
        self.wire_target = wire_target
        self.used = 0
        self.wire_used = 0
        self.trace = []
        self._original = None
        self._wire_original = None

    def __enter__(self):
        if self.target is None:
            from jobspy import util
            self.target = util.RotatingProxySession
            if self.wire_target is None:
                import requests.adapters
                self.wire_target = requests.adapters.HTTPAdapter
        self._original = self.target.request
        budget, original = self, self._original
        if self.wire_target is not None:
            self._wire_original = self.wire_target.send
            wire_original = self._wire_original

            def wire_limited(adapter, *args, **kwargs):
                if budget.wire_used >= budget.max_requests:
                    raise RequestBudgetExceeded("request budget exhausted (redirect hop)")
                budget.wire_used += 1
                return wire_original(adapter, *args, **kwargs)

            self.wire_target.send = wire_limited

        def limited(session, *args, **kwargs):
            if budget.used >= budget.max_requests:
                raise RequestBudgetExceeded("request budget exhausted")
            if budget.clock() > budget.deadline:
                raise RequestBudgetExceeded("maximum run duration reached")
            _guard_request(session, kwargs, first_request=budget.used == 0)
            budget.used += 1
            response = original(session, *args, **kwargs)
            try:
                budget.trace.append(dict(_trace_entry(args[0] if args else kwargs.get("method", ""),
                                                      args[1] if len(args) > 1 else kwargs.get("url", ""), response),
                                         stage=budget.used))
            except Exception:
                pass                                   # diagnostics must never change the outcome
            return response

        self.target.request = limited
        return self

    def __exit__(self, *exc):
        self.target.request = self._original
        if self._wire_original is not None:
            self.wire_target.send = self._wire_original
        return False


def _default_scrape(**kwargs):
    import jobspy
    return jobspy.scrape_jobs(**kwargs)


@dataclass
class SearchOutcome:
    source: str
    term: str
    location: str
    results_requested: int
    status: str = "skipped"     # success | partial | failed | blocked | skipped
    rows: int = 0
    requests_used: int = 0
    seconds: float = 0.0
    error: str = ""
    warnings: list = field(default_factory=list)
    trace: list = field(default_factory=list)       # sanitized per-request diagnostics (see _trace_entry)


def _clean(value):
    if value is None or isinstance(value, (list, dict, tuple, str, bool, int)):
        return value
    try:
        import pandas as pd
        return None if pd.isna(value) else value
    except Exception:
        return value


def normalize_record(source, row):
    """One JobSpy row -> plain record, or None if mandatory fields are missing. No invented values."""
    get = lambda k: _clean(row.get(k))
    pid, title, url = get("id"), get("title"), get("job_url")
    if not (isinstance(pid, str) and pid.strip() and isinstance(title, str) and title.strip()
            and isinstance(url, str) and url.startswith(("http://", "https://"))):
        return None
    posted = get("date_posted")
    exp = get("experience_range") if isinstance(get("experience_range"), str) else None
    match = re.match(r"\s*(\d+)\s*-\s*(\d+)", exp or "")
    direct = get("job_url_direct")
    return {
        "source": source, "id": pid.strip(), "title": title.strip(), "url": url,
        "company": (get("company") or "").strip() if isinstance(get("company"), str) else None,
        "company_url": get("company_url") if isinstance(get("company_url"), str) else None,
        "direct_url": direct if isinstance(direct, str) and direct.startswith(("http://", "https://")) else None,
        "location": get("location") if isinstance(get("location"), str) else None,
        "is_remote": get("is_remote") if isinstance(get("is_remote"), bool) else None,
        "date_posted": str(posted)[:10] if posted else None,
        "experience_text": exp,
        "experience_min": int(match.group(1)) if match else None,
        "experience_max": int(match.group(2)) if match else None,
        "has_description": bool(get("description")),   # the text itself is not kept in the snapshot or the database
    }


def _live_search(auth, spec, policy, *, scrape, vpn_check, clock, deadline, request_allowance):
    """The ONLY function that calls the provider. It re-checks authorization and the VPN right before every search."""
    if not isinstance(auth, Authorization):
        raise AuthorizationError("a live one-time authorization is required before any restricted request")
    auth.require_running(spec)                      # raises AuthorizationError unless the authorization is live
    if spec.source not in IMPLEMENTED:
        raise PolicyError(f"{spec.source} has no approved live implementation")
    if policy.provider_authorization != "operator_confirmed" or not policy.authorization_covers_vpn_egress:
        raise PolicyError(f"{spec.source}: provider authorization (including VPN egress) is not established")
    vpn_check(quick=True)                           # raises NetworkGuardError; never falls back to a direct connection
    kwargs = dict(site_name=spec.source, search_term=spec.term, location=spec.location, results_wanted=spec.results,
                  fetch_description=False, offset=0, verbose=1)
    if spec.source in COUNTRY_PARAM_SOURCES:
        kwargs["country_indeed"] = spec.country
    with RequestBudget(request_allowance, deadline, clock) as budget:
        frame = scrape(**kwargs)
    attrs = getattr(frame, "attrs", {}) or {}
    return frame, attrs.get("errors", {}) or {}, attrs.get("warnings", {}) or {}, budget.used, budget.trace


def collect_snapshot(plan, auth, policies, *, scrape=None, vpn_check=None, sleep=time.sleep, clock=time.monotonic,
                     now_fn=db.utc_now, transport_check=None):
    """Phase A. Returns the snapshot dict (also the audit trail of what was and was not run)."""
    scrape = scrape or _default_scrape
    vpn_check = vpn_check or require_vpn
    if not isinstance(auth, Authorization) or auth.action != "collect":
        raise AuthorizationError("a live one-time 'collect' authorization is required before any restricted request")
    for source in plan.sources:                     # before the authorization is consumed and before any request
        if source not in IMPLEMENTED:
            raise PolicyError(f"{source} has no approved live implementation")
        (transport_check or check_transport_enforceable)(source)
    auth.begin(plan)                                # single use; fails if the scope or policy changed
    started = now_fn()
    outcomes, records, stop_all = [], {}, False
    try:
        for index, source in enumerate(plan.sources):
            policy = policies[source]
            specs = plan.searches_for(source)
            source_start, used_total, stop_source = clock(), 0, False
            deadline = source_start + policy.max_duration_seconds
            if index > 0 and not stop_all:
                sleep(policy.min_delay_seconds)
            for n, spec in enumerate(specs):
                out = SearchOutcome(source, spec.term, spec.location, spec.results)
                outcomes.append(out)
                if stop_all or stop_source:
                    out.status, out.error = "skipped", "an earlier restriction, failure or limit stopped this run"
                    continue
                allowance = min(policy.base_requests_per_search + policy.max_pagination_requests + policy.max_enrichment_requests,
                                policy.request_budget - used_total)
                if allowance <= 0 or clock() > deadline:
                    out.status, out.error = "skipped", "request budget or duration limit reached"
                    continue
                if n > 0:
                    sleep(policy.min_delay_seconds)
                t0 = clock()
                try:
                    frame, errors, warnings, used, trace = _live_search(auth, spec, policy, scrape=scrape, vpn_check=vpn_check,
                                                                 clock=clock, deadline=deadline, request_allowance=allowance)
                except NetworkGuardError as exc:
                    out.status, out.error, stop_all = "failed", f"VPN check failed: {exc}", True
                    continue
                except AuthorizationError:
                    raise
                except Exception as exc:            # includes a scrape function raising; JobSpy itself reports via attrs
                    out.status, out.error, stop_source = "failed", f"{type(exc).__name__}: {str(exc)[:160]}", True
                    continue
                out.seconds, out.requests_used, out.trace = round(clock() - t0, 1), used, trace
                used_total += used
                out.warnings = [str(w) for ws in warnings.values() for w in ws][:5]
                err = errors.get(spec.source)
                rows = [] if frame is None else frame.to_dict("records")
                good = [r for r in (normalize_record(spec.source, row) for row in rows[:spec.results]) if r]
                out.rows = len(good)
                for rec in good:
                    records.setdefault((spec.source, rec["id"]), rec)
                if err:
                    out.error = str(err)[:200]
                    out.status = "blocked" if RESTRICTION.search(out.error) else ("partial" if good else "failed")
                    stop_source = True              # no retries around a restriction, no widening
                elif RESTRICTION.search(" ".join(out.warnings)):
                    out.error, out.status, stop_source = "restriction signal in provider warnings", "blocked", True
                elif "budget" in " ".join(out.warnings).lower():
                    out.status = "partial"
                else:
                    out.status = "partial" if len(good) < len(rows[:spec.results]) else "success"
    finally:
        auth.finish()
    snapshot = {"format": SNAPSHOT_FORMAT, "collected_at": started, "finished_at": now_fn(),
                "plan_fingerprint": plan.fingerprint(), "sources": list(plan.sources),
                "outcomes": [vars(o) for o in outcomes], "records": sorted(records.values(), key=lambda r: (r["source"], r["id"]))}
    snapshot["records_sha256"] = hashlib.sha256(json.dumps(snapshot["records"], sort_keys=True).encode()).hexdigest()
    return snapshot


def write_snapshot(snapshot, directory=None):
    directory = Path(directory or SNAPSHOT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9]", "", snapshot["collected_at"])[:14]
    path = directory / f"{stamp}_{'-'.join(snapshot['sources'])}.json"
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def load_snapshot(path):
    try:
        snap = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PolicyError(f"snapshot unreadable ({type(exc).__name__})") from exc
    if snap.get("format") != SNAPSHOT_FORMAT or not isinstance(snap.get("records"), list):
        raise PolicyError("not a restricted-source snapshot of the supported format")
    digest = hashlib.sha256(json.dumps(snap["records"], sort_keys=True).encode()).hexdigest()
    if digest != snap.get("records_sha256"):
        raise PolicyError("snapshot content does not match its recorded hash; refusing to import")
    return snap


def append_audit(entry, path=None):
    """Local audit trail: what was authorized/run, never an IP address, URL list or credential."""
    path = Path(path or AUDIT_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# --------------------------------------------------------------- preview / import

def _name_hints(conn):
    index = {}
    for cid, name, facts in conn.execute("SELECT company_id, normalized_name, facts FROM companies"):
        f = json.loads(facts)
        if "yc" in f or "feashliaa" in f:
            index.setdefault(db.normalize_name(name.replace("-", " ").replace("_", " ")), []).append(cid)
    return index


def _job_ids_for_url(conn, url):
    return {r[0] for r in conn.execute("SELECT job_id FROM sightings WHERE job_id IS NOT NULL AND source_url = ?",
                                       (db.normalize_url(url),))}


def preview(conn, snapshot):
    """Read-only comparison with what is already stored. Never decides identity by title similarity."""
    hints = _name_hints(conn) if conn is not None else {}
    lines = []
    for source in snapshot["sources"]:
        recs = [r for r in snapshot["records"] if r["source"] == source]
        sid = SOURCE_IDS[source]
        existing = ({r[0] for r in conn.execute("SELECT external_id FROM sightings WHERE source_id = ? AND job_id IS NOT NULL", (sid,))}
                    if conn is not None else set())
        direct = [r for r in recs if r["direct_url"]]
        matches = [r for r in direct if conn is not None and len(_job_ids_for_url(conn, r["direct_url"])) == 1]
        company_hints = [r for r in recs if r["company"] and db.normalize_name(r["company"]) in hints]
        lines.append(f"{source.upper()}: {len(recs)} unique records | already imported from this source: "
                     f"{sum(1 for r in recs if r['id'] in existing)} | direct employer URL: {len(direct)} "
                     f"(equal to an existing job's URL, strong evidence: {len(matches)}) | company name equals a YC/Feashliaa "
                     f"company (hint only): {len(company_hints)}")
        lines.append(f"    with posting date: {sum(1 for r in recs if r['date_posted'])} | with experience range: "
                     f"{sum(1 for r in recs if r['experience_text'])} | description present at the provider: "
                     f"{sum(1 for r in recs if r['has_description'])} (text not kept) | remote flagged: "
                     f"{sum(1 for r in recs if r['is_remote'])}")
        lines.append(f"    new jobs if imported: {len(recs) - sum(1 for r in recs if r['id'] in existing) - len(matches)} | "
                     f"would attach to an existing job: {len(matches)}")
    for o in snapshot["outcomes"]:
        lines.append(f"search {o['source']} {o['term']!r} @ {o['location']!r}: {o['status'].upper()} rows={o['rows']} "
                     f"requests={o['requests_used']} {o['seconds']}s" + (f" | {o['error']}" if o["error"] else ""))
    return lines


@dataclass
class ImportCounts:
    jobs_created: int = 0
    attached_to_existing: int = 0
    already_seen: int = 0
    companies_created: int = 0
    skipped_identity: int = 0


def _eligibility(rec):
    text = " ".join(filter(None, [rec.get("location")])).lower()
    return "eligible" if re.search(r"india|bengaluru|bangalore|karnataka|\bka\b", text) else "possible"


def import_snapshot(conn, snapshot, *, now=None):
    """Phase B. Writes only what the saved snapshot contains. Idempotent. Never touches Sheets or closes anything."""
    now = now or db.utc_now()
    counts, hints = ImportCounts(), _name_hints(conn)
    by_source = {}
    for rec in snapshot["records"]:
        by_source.setdefault(rec["source"], []).append(rec)
    run_ids = {}
    for source in snapshot["sources"]:
        sid = SOURCE_IDS[source]
        db.register_source(conn, sid, SOURCE_LABELS[source], "aggregator", None, config_ref=db.NO_CLOSURE_EVIDENCE, now=now)
        run_ids[source] = db.start_run(conn, "import", now=now)
    company_cache = {}
    try:
        recs = [r for s in snapshot["sources"] for r in by_source.get(s, [])]
        for start in range(0, len(recs), BATCH_SIZE):
            with db.transaction(conn):
                for rec in recs[start:start + BATCH_SIZE]:
                    _import_one(conn, rec, snapshot["collected_at"], hints, company_cache, counts, now)
        for source in snapshot["sources"]:
            sid, checks = SOURCE_IDS[source], [o for o in snapshot["outcomes"] if o["source"] == source]
            for o in checks:
                outcome = {"success": "success", "partial": "partial", "skipped": "skipped"}.get(o["status"], "failed")
                db.record_source_check(conn, run_ids[source], sid, outcome, scope=f"{o['term']}|{o['location']}",
                                       complete=False, record_count=o["rows"], started_at=snapshot["collected_at"],
                                       finished_at=snapshot["finished_at"], source_snapshot_at=snapshot["collected_at"],
                                       error_info=(o["error"] or None))
            worst = "success" if all(o["status"] == "success" for o in checks) else "partial"
            db.finish_run(conn, run_ids[source], worst, now=db.utc_now(),
                          summary={"source": sid, "snapshot_sha256": snapshot["records_sha256"], "records": len(by_source.get(source, []))})
    except BaseException as exc:
        for rid in run_ids.values():
            db.finish_run(conn, rid, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                          error_detail=f"{type(exc).__name__}: {str(exc)[:200]}", now=db.utc_now())
        raise
    return counts


def _import_one(conn, rec, collected_at, hints, company_cache, counts, now):
    sid = SOURCE_IDS[rec["source"]]
    common = dict(provider_observed_at=collected_at, provider_first_seen_at=rec["date_posted"],
                  provider_freshness="latest_collection", snapshot_at=collected_at, now=now)
    existing = db.find_sighting(conn, sid, rec["url"], rec["id"], is_job=True)
    if existing:
        job = db.get_job(conn, existing["job_id"])
        facts = json.loads(db.get_company(conn, job["company_id"])["facts"])
        if "jobspy" in facts:       # a job this source created: allow normal change detection
            db.observe_job(conn, source_id=sid, source_url=rec["url"], company_id=job["company_id"], title=rec["title"],
                           external_id=rec["id"], location=rec["location"], remote_eligibility=_eligibility(rec),
                           experience_min_years=rec["experience_min"], experience_max_years=rec["experience_max"],
                           experience_text=rec["experience_text"], posting_url=rec["direct_url"], **common)
        else:                       # attached to someone else's job: only refresh our sighting
            db.record_sighting(conn, sid, rec["url"], job_id=job["job_id"], external_id=rec["id"], **common)
        counts.already_seen += 1
        return
    if rec["direct_url"]:
        matches = _job_ids_for_url(conn, rec["direct_url"])
        if len(matches) == 1:       # strong evidence: the employer's own URL is already a sighting of one job
            db.record_sighting(conn, sid, rec["url"], job_id=next(iter(matches)), external_id=rec["id"], **common)
            counts.attached_to_existing += 1
            return
        if len(matches) > 1:        # ambiguous: do not guess
            counts.skipped_identity += 1
            return
    name = rec["company"] or "(company not given)"
    key = (sid, db.normalize_name(name))
    company_id = company_cache.get(key)
    if company_id is None:
        ext = f"name:{key[1]}"
        sighting = db.find_sighting(conn, sid, rec["company_url"] or rec["url"], ext, is_job=False)
        if sighting:
            company_id = sighting["company_id"]
        else:
            matches = [c for c in hints.get(db.normalize_name(name), [])][:5]
            facts = {"jobspy": {"source": sid, "board_company_name": name, "possible_matches": matches}, "sheet_publish": False}
            company_id, _ = db.get_or_create_company(conn, name, None, facts=facts, now=now)
            counts.companies_created += 1
            db.record_sighting(conn, sid, rec["company_url"] or rec["url"], company_id=company_id, external_id=ext,
                               snapshot_at=collected_at, now=now)
        company_cache[key] = company_id
    obs = db.observe_job(conn, source_id=sid, source_url=rec["url"], company_id=company_id, title=rec["title"],
                         external_id=rec["id"], location=rec["location"], remote_eligibility=_eligibility(rec),
                         experience_min_years=rec["experience_min"], experience_max_years=rec["experience_max"],
                         experience_text=rec["experience_text"], posting_url=rec["direct_url"], **common)
    counts.jobs_created += 1 if obs.created else 0
    counts.already_seen += 0 if obs.created else 1


def verified_backup(db_path, directory=None):
    """SQLite online backup before an import; raises if the copy fails its integrity check."""
    import sqlite3
    directory = Path(directory or db.PROJECT_ROOT / "backups")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"discovery_pre-restricted-import_{time.strftime('%Y%m%d-%H%M%S')}.db"
    src, dst = sqlite3.connect(db_path), sqlite3.connect(target)
    try:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("backup failed its integrity check")
    finally:
        dst.close()
        src.close()
    return target
