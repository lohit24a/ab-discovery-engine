"""Stream A supporting feed: Feashliaa / Riley-Dorrington job-board-data (published ATS job snapshots).

Audit and field behaviour: docs/source_audits/feashliaa.md. Rules that matter:
  * Supplementary source only. A row in the snapshot is NOT proof that a job is open: roughly half of
    the rows are retained from earlier collections (up to 30 days). Retained rows are labelled
    `provider_retained` and their job state is Unknown. This adapter never marks anything Not Seen or Closed
    and never promotes a job to Active.
  * Provider time fields (first seen, last observed, snapshot publication) are stored separately from ours.
  * Identity: company = (ATS, board code) scoped to this source; job = (ATS, board code, job URL).
    A board code is not a domain, so no company is ever merged into a YC company automatically; possible
    name matches are only recorded for later human review.
  * Memory is bounded: the worldwide feed is read one 25,000-row chunk at a time and only rows passing the
    geography + title prefilter are kept. Nothing is written until every chunk has been validated.
"""

import gzip
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .. import database as db

SOURCE_ID = "feashliaa"
SOURCE_NAME = "Feashliaa job feed (job-board-data)"
SOURCE_HOME = "https://github.com/Riley-Dorrington/job-board-data"
DEFAULT_BASE_URL = "https://riley-dorrington.github.io/job-board-data/data/chunks/"
MANIFEST_NAME = "jobs_manifest.json"
SOURCE_SCOPE = "jobs snapshot (published feed)"
PREFILTER_PATH = db.PROJECT_ROOT / "config" / "feashliaa_prefilter.json"

STALE_AFTER = timedelta(days=3)          # the feed is regenerated daily
LATEST_WINDOW = timedelta(hours=6)       # observed run window on 2026-10-19 was ~1.5 h
MAX_FILE_BYTES = 100 * 1024 * 1024
BATCH_SIZE = 500
CHUNK_NAME = re.compile(r"^jobs_chunk_\d+\.json\.gz$")

LATEST, RETAINED, UNKNOWN_FRESHNESS = "latest_collection", "provider_retained", "unknown"


class SourceError(Exception):
    """The feed could not be fetched or is not in a usable format."""


class ChunkError(SourceError):
    """One chunk is missing or corrupt."""


# ------------------------------------------------------------------ fetching

def fetch_bytes(url, *, timeout=60, retries=3, sleep=time.sleep, opener=urllib.request.urlopen):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ab-discovery-engine/0.1 (personal research)"})
            with opener(req, timeout=timeout) as resp:
                if getattr(resp, "status", 200) != 200:
                    raise SourceError(f"{url}: HTTP {resp.status}")
                body = resp.read(MAX_FILE_BYTES + 1)
            if len(body) > MAX_FILE_BYTES:
                raise SourceError(f"{url}: file larger than {MAX_FILE_BYTES} bytes")
            return body
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code < 500 and exc.code != 429:
                raise SourceError(f"{url}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = exc
        if attempt < retries - 1:
            sleep(2 ** attempt)
    raise SourceError(f"{url}: request failed after {retries} attempts ({type(last).__name__})")


def parse_iso(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


@dataclass
class Manifest:
    chunks: list
    total_jobs: int
    generated_at: str   # the manifest's last_updated: publication time of this snapshot

    @property
    def snapshot_id(self):
        digest = hashlib.sha256(f"{self.generated_at}|{self.total_jobs}|{','.join(self.chunks)}".encode()).hexdigest()
        return digest[:16]


def fetch_manifest(fetch=fetch_bytes, base_url=DEFAULT_BASE_URL, **kw) -> Manifest:
    raw = fetch(base_url + MANIFEST_NAME, **kw)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceError(f"manifest is not valid JSON ({type(exc).__name__})") from exc
    if not isinstance(data, dict):
        raise SourceError("manifest is not an object")
    chunks, total, generated = data.get("chunks"), data.get("totalJobs"), data.get("last_updated")
    if not isinstance(chunks, list) or not chunks or not all(isinstance(c, str) and CHUNK_NAME.match(c) for c in chunks):
        raise SourceError("manifest has no valid 'chunks' list")
    if len(set(chunks)) != len(chunks):
        raise SourceError("manifest lists a chunk twice")
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        raise SourceError("manifest has no valid 'totalJobs'")
    if parse_iso(generated) is None:
        raise SourceError("manifest has no valid 'last_updated'")
    return Manifest(chunks, total, generated)


class SnapshotStore:
    """Downloads manifest-listed chunks into a git-ignored cache and reads them one at a time."""

    def __init__(self, manifest, cache_dir, fetch=fetch_bytes, base_url=DEFAULT_BASE_URL, **fetch_kw):
        self.manifest, self.fetch, self.base_url, self.fetch_kw = manifest, fetch, base_url, fetch_kw
        self.dir = Path(cache_dir) / manifest.snapshot_id
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name):
        return self.dir / name

    def load_chunk(self, name):
        """Return the parsed rows of one chunk, downloading it if needed. Raises ChunkError."""
        for attempt in (1, 2):
            path = self._path(name)
            if not path.is_file():
                try:
                    url = f"{self.base_url}{name}?v={self.manifest.generated_at}"
                    data = self.fetch(url, **self.fetch_kw)
                except SourceError as exc:
                    raise ChunkError(f"{name}: download failed ({exc})") from exc
                tmp = path.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.replace(path)
            try:
                with gzip.open(path, "rb") as f:
                    rows = json.loads(f.read().decode("utf-8"))
            except (OSError, EOFError, ValueError, UnicodeDecodeError) as exc:
                path.unlink(missing_ok=True)   # corrupt: drop the cached copy and retry once
                if attempt == 2:
                    raise ChunkError(f"{name}: corrupt data ({type(exc).__name__})") from exc
                continue
            if not isinstance(rows, list):
                raise ChunkError(f"{name}: chunk is not a list of jobs")
            return rows
        raise ChunkError(f"{name}: unreadable")


# ------------------------------------------------------- geography and titles

INDIA_CITIES = {
    "bengaluru", "bangalore", "mumbai", "navi mumbai", "new delhi", "delhi ncr", "ncr", "gurgaon", "gurugram",
    "noida", "greater noida", "hyderabad", "chennai", "pune", "kolkata", "ahmedabad", "jaipur", "kochi", "cochin",
    "thiruvananthapuram", "trivandrum", "chandigarh", "indore", "coimbatore", "nagpur", "bhubaneswar", "lucknow",
    "vadodara", "surat", "mysuru", "mysore", "visakhapatnam", "vizag", "kanpur", "patna", "ranchi", "bhopal",
    "mohali", "faridabad", "ghaziabad", "nashik", "madurai", "mangalore", "mangaluru", "thane",
}
INDIA_STATES = {
    "karnataka", "maharashtra", "telangana", "tamil nadu", "kerala", "gujarat", "rajasthan", "uttar pradesh",
    "haryana", "odisha", "orissa", "west bengal", "andhra pradesh", "madhya pradesh", "bihar", "jharkhand", "goa",
}
_BENGALURU = re.compile(r"\b(bengaluru|bangalore|bengalooru)\b", re.I)
_INDIA = re.compile(r"\bindia\b", re.I)
_REMOTE = re.compile(r"\bremote\b|\banywhere\b|\bwork from home\b|\bwfh\b", re.I)
_FOREIGN = re.compile(r"\b(pakistan|usa|united states|canada|united kingdom|england|australia|germany|france|"
                      r"nigeria|singapore|china|japan|ireland|brazil|mexico)\b", re.I)
_SPLIT = re.compile(r"[,;/|()\-–—]")

BENGALURU, INDIA_EXPLICIT, INDIA_INFERRED, REMOTE_UNVERIFIED, OTHER = (
    "bengaluru", "india_explicit", "india_inferred", "remote_unverified", "other")
ELIGIBILITY = {BENGALURU: "eligible", INDIA_EXPLICIT: "eligible", INDIA_INFERRED: "possible",
               REMOTE_UNVERIFIED: "unknown"}


def classify_geo(location, remote_flag=None):
    """Conservative geography from the free-text location. Returns (class, reason).

    'Remote' never means India-eligible: remote-without-India is REMOTE_UNVERIFIED (needs review).
    """
    text = location or ""
    if _BENGALURU.search(text):
        return BENGALURU, "location names Bengaluru/Bangalore"
    if _INDIA.search(text):
        return INDIA_EXPLICIT, "location says India"
    if not _FOREIGN.search(text):
        tokens = {t.strip().casefold() for t in _SPLIT.split(text)}
        if tokens & INDIA_CITIES or tokens & INDIA_STATES:
            return INDIA_INFERRED, "known Indian city/state; country not stated"
    if remote_flag is True or _REMOTE.search(text):
        return REMOTE_UNVERIFIED, "remote, India eligibility not stated"
    return OTHER, "no India or remote evidence"


class Prefilter:
    """Title keyword volume filter loaded from config/feashliaa_prefilter.json (editable)."""

    def __init__(self, keywords):
        parts = []
        for kw in keywords:
            if not isinstance(kw, str) or not kw.strip():
                continue
            esc = re.escape(kw.strip())
            parts.append(r"\b" + esc + (r"\b" if len(kw.strip()) <= 3 else ""))
        if not parts:
            raise SourceError("prefilter has no keywords; refusing to import the whole feed")
        self._re = re.compile("|".join(parts), re.I)
        self.keywords = list(keywords)

    @classmethod
    def load(cls, path=PREFILTER_PATH):
        try:
            return cls(json.loads(Path(path).read_text(encoding="utf-8"))["title_keywords"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SourceError(f"prefilter config unreadable ({path}): {type(exc).__name__}") from exc

    def matches(self, title):
        return bool(self._re.search(title or ""))


# ------------------------------------------------------------ row handling

def classify_freshness(scraped_at, generated_dt):
    observed = parse_iso(scraped_at)
    if observed is None or generated_dt is None:
        return UNKNOWN_FRESHNESS
    if observed > generated_dt + timedelta(hours=1):
        return UNKNOWN_FRESHNESS
    return LATEST if generated_dt - observed <= LATEST_WINDOW else RETAINED


def board_url(ats, job_url, board):
    parts = urlsplit(job_url)
    base = f"{parts.scheme}://{parts.netloc}"
    if ats in ("Greenhouse", "Lever", "Ashby"):
        return f"{base}/{board}"
    if ats == "Workday":
        first = parts.path.strip("/").split("/", 1)[0]
        return f"{base}/{first}" if first else base
    return base


@dataclass
class Metrics:
    chunks_total: int = 0
    chunks_ok: int = 0
    chunk_errors: list = field(default_factory=list)
    scanned: int = 0
    malformed: int = 0
    duplicates: int = 0
    geo: dict = field(default_factory=dict)            # geo class -> rows seen (before title filter)
    title_passed: dict = field(default_factory=dict)   # geo class -> rows kept
    freshness: dict = field(default_factory=dict)      # freshness -> kept rows
    ats: dict = field(default_factory=dict)            # ats -> kept rows
    seconds: float = 0.0
    peak_mb: float = 0.0

    @property
    def kept(self):
        return sum(self.title_passed.values())


def _bump(counter, key, n=1):
    counter[key] = counter.get(key, 0) + n


def scan(manifest, store, prefilter, now=None):
    """Read every chunk once, one at a time, keeping only prefilter candidates. Returns (candidates, Metrics)."""
    started = time.time()
    generated = parse_iso(manifest.generated_at)
    metrics = Metrics(chunks_total=len(manifest.chunks))
    kept = {}
    for name in manifest.chunks:
        try:
            rows = store.load_chunk(name)
        except ChunkError as exc:
            metrics.chunk_errors.append(str(exc))
            continue
        metrics.chunks_ok += 1
        for raw in rows:
            metrics.scanned += 1
            if not isinstance(raw, dict):
                metrics.malformed += 1
                continue
            url, title, company, ats = raw.get("url"), raw.get("title"), raw.get("company"), raw.get("ats")
            if not (isinstance(url, str) and url.startswith(("http://", "https://")) and isinstance(title, str)
                    and title.strip() and isinstance(company, str) and company.strip() and isinstance(ats, str)):
                metrics.malformed += 1
                continue
            location = raw.get("location") if isinstance(raw.get("location"), str) else None
            geo, reason = classify_geo(location, raw.get("remote") if isinstance(raw.get("remote"), bool) else None)
            _bump(metrics.geo, geo)
            if geo == OTHER or not prefilter.matches(title):
                continue
            ext_company = f"{ats}|{company.strip()}"
            ext_job = f"{ext_company}|{db.normalize_url(url)}"
            cand = {
                "ats": ats, "board": company.strip(), "title": title.strip(), "url": url, "location": location,
                "geo": geo, "geo_reason": reason, "eligibility": ELIGIBILITY[geo],
                "scraped_at": raw.get("scraped_at") if isinstance(raw.get("scraped_at"), str) else None,
                "first_seen": raw.get("first_seen") if isinstance(raw.get("first_seen"), str) else None,
                "freshness": classify_freshness(raw.get("scraped_at"), generated),
                "ext_company": ext_company, "ext_job": ext_job,
            }
            previous = kept.get(ext_job)
            if previous is not None:
                metrics.duplicates += 1
                if (parse_iso(cand["scraped_at"]) or datetime.min.replace(tzinfo=timezone.utc)) <= \
                        (parse_iso(previous["scraped_at"]) or datetime.min.replace(tzinfo=timezone.utc)):
                    continue
            else:
                _bump(metrics.title_passed, geo)
                _bump(metrics.freshness, cand["freshness"])
                _bump(metrics.ats, ats)
            kept[ext_job] = cand
        del rows
    metrics.seconds = time.time() - started
    metrics.peak_mb = peak_memory_mb()
    return list(kept.values()), metrics


def prune_cache(cache_dir, keep):
    """Delete cached snapshots other than the current one (only direct children of the cache folder)."""
    import shutil

    root = Path(cache_dir)
    for child in root.iterdir() if root.is_dir() else []:
        if child.is_dir() and child.name != keep and re.fullmatch(r"[0-9a-f]{16}", child.name):
            shutil.rmtree(child, ignore_errors=True)


def peak_memory_mb():
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            counters = PMC()
            counters.cb = ctypes.sizeof(PMC)
            kernel32 = ctypes.WinDLL("kernel32")
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
            kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
            return counters.PeakWorkingSetSize / (1024 * 1024)
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
        return 0.0


# ------------------------------------------------------------------ database

def yc_name_index(conn):
    """normalized name -> [(company_id, yc_id)] for already imported YC companies (review hints only)."""
    index = {}
    rows = conn.execute("""SELECT c.company_id, c.normalized_name, s.external_id FROM companies c
                           JOIN sightings s ON s.company_id = c.company_id AND s.source_id = 'yc_oss'""")
    for company_id, name, yc_id in rows:
        index.setdefault(name, []).append((company_id, yc_id))
    return index


def existing_job_ids(conn):
    rows = conn.execute("SELECT external_id FROM sightings WHERE source_id = ? AND job_id IS NOT NULL", (SOURCE_ID,))
    return {r[0] for r in rows}


def existing_company_ids(conn):
    rows = conn.execute("SELECT external_id FROM sightings WHERE source_id = ? AND company_id IS NOT NULL", (SOURCE_ID,))
    return {r[0] for r in rows}


def _possible_yc_matches(board, ats, index):
    slug = board.replace("-", " ").replace("_", " ")
    found = index.get(db.normalize_name(slug), [])
    return [{"company_id": cid, "yc_id": yc_id, "reason": "normalized name equals board code"} for cid, yc_id in found[:5]]


@dataclass
class ImportCounts:
    companies_created: int = 0
    jobs_created: int = 0
    jobs_changed: int = 0
    jobs_unchanged: int = 0
    marked_unknown: int = 0
    possible_yc_matches: int = 0


def import_candidates(conn, manifest, candidates, *, now=None):
    now = now or db.utc_now()
    counts = ImportCounts()
    index = yc_name_index(conn)
    company_ids = {}
    for start in range(0, len(candidates), BATCH_SIZE):
        with db.transaction(conn):
            for cand in candidates[start:start + BATCH_SIZE]:
                company_id = company_ids.get(cand["ext_company"])
                if company_id is None:
                    company_id = _resolve_company(conn, cand, index, manifest, counts, now)
                    company_ids[cand["ext_company"]] = company_id
                obs = db.observe_job(
                    conn, source_id=SOURCE_ID, source_url=cand["url"], company_id=company_id, title=cand["title"],
                    external_id=cand["ext_job"], location=cand["location"], remote_eligibility=cand["eligibility"],
                    posting_url=cand["url"], provider_observed_at=cand["scraped_at"],
                    provider_first_seen_at=cand["first_seen"], provider_freshness=cand["freshness"],
                    snapshot_at=manifest.generated_at, now=now)
                if obs.created:
                    counts.jobs_created += 1
                    if cand["freshness"] != LATEST:   # retained or unverifiable rows are not confirmed postings
                        db.append_status_event(conn, "Unknown", "Provider-retained or unverifiable row; "
                                               "availability not confirmed", job_id=obs.job_id, now=now)
                        counts.marked_unknown += 1
                elif obs.changed:
                    counts.jobs_changed += 1
                else:
                    counts.jobs_unchanged += 1
    return counts


def _resolve_company(conn, cand, index, manifest, counts, now):
    sighting = db.find_sighting(conn, SOURCE_ID, board_url(cand["ats"], cand["url"], cand["board"]),
                                cand["ext_company"], is_job=False)
    if sighting:
        company_id = sighting["company_id"]
    else:
        matches = _possible_yc_matches(cand["board"], cand["ats"], index)
        counts.possible_yc_matches += bool(matches)
        facts = {"feashliaa": {"ats": cand["ats"], "board_code": cand["board"], "name_is_board_code": True,
                               "possible_yc_matches": matches},
                 "sheet_publish": False}   # board-code companies are not published to the review sheet
        company_id, _ = db.get_or_create_company(conn, cand["board"], None, facts=facts, now=now)
        counts.companies_created += 1
    db.record_sighting(conn, SOURCE_ID, board_url(cand["ats"], cand["url"], cand["board"]), company_id=company_id,
                       external_id=cand["ext_company"], snapshot_at=manifest.generated_at, now=now)
    return company_id


# ------------------------------------------------------------- orchestration

@dataclass
class Report:
    apply: bool
    status: str = "ok"                  # ok | refused | already_processed | failed
    reasons: list = field(default_factory=list)
    manifest: Manifest = None
    metrics: Metrics = None
    counts: ImportCounts = None
    new_companies_est: int = 0
    new_jobs_est: int = 0
    existing_jobs: int = 0
    possible_yc_matches_est: int = 0
    run_id: str = ""

    @property
    def importable(self):
        return self.status == "ok"

    def lines(self):
        m, mf = self.metrics, self.manifest
        out = [("APPLIED" if self.apply and self.importable and self.counts
                else "APPLY REQUESTED - nothing was imported" if self.apply else "DRY RUN - nothing was changed")
               + f" | status: {self.status}" + (f" ({'; '.join(self.reasons)})" if self.reasons else "")]
        if mf:
            out.append(f"Snapshot published (manifest last_updated): {mf.generated_at} | id {mf.snapshot_id}")
            out.append(f"Chunks: {m.chunks_ok if m else 0}/{len(mf.chunks)} read OK | rows scanned: "
                       f"{m.scanned if m else 0} (manifest says {mf.total_jobs})")
        if m:
            out.append(f"Malformed rows: {m.malformed} | duplicate candidate rows: {m.duplicates}")
            out.append("Geography of all rows: " + ", ".join(f"{k}={v}" for k, v in sorted(m.geo.items())))
            out.append("Kept after title prefilter: " + ", ".join(f"{k}={v}" for k, v in sorted(m.title_passed.items()))
                       + f" (total {m.kept})")
            out.append("Kept by freshness: " + ", ".join(f"{k}={v}" for k, v in sorted(m.freshness.items())))
            out.append("Kept by ATS: " + ", ".join(f"{k}={v}" for k, v in sorted(m.ats.items())))
            out.append(f"Estimated new jobs: {self.new_jobs_est} (already stored: {self.existing_jobs}) | "
                       f"new board companies: {self.new_companies_est} | boards with possible YC name match: "
                       f"{self.possible_yc_matches_est}")
            out.append(f"Runtime: {m.seconds:.0f}s | peak memory: {m.peak_mb:.0f} MB")
            out.extend(f"  ! {e}" for e in m.chunk_errors[:5])
        if self.counts:
            c = self.counts
            out.append(f"Run {self.run_id}: companies created {c.companies_created}, jobs created {c.jobs_created} "
                       f"(marked Unknown: {c.marked_unknown}), changed {c.jobs_changed}, unchanged {c.jobs_unchanged}")
        return out


def record_failure(conn, error, *, outcome="failed", now=None):
    """Remember a failed/refused/skipped attempt. Touches no job or company data."""
    now = now or db.utc_now()
    db.register_source(conn, SOURCE_ID, SOURCE_NAME, "ats_feed", SOURCE_HOME, config_ref=db.NO_CLOSURE_EVIDENCE, now=now)
    run_id = db.start_run(conn, "import", now=now)
    db.record_source_check(conn, run_id, SOURCE_ID, outcome, scope=SOURCE_SCOPE, complete=False,
                           started_at=now, finished_at=db.utc_now(), error_info=str(error)[:300])
    db.finish_run(conn, run_id, {"failed": "failed", "skipped": "success"}.get(outcome, "partial"),
                  error_detail=str(error)[:300], now=db.utc_now())
    return run_id


def execute(conn, *, apply, cache_dir, fetch=fetch_bytes, base_url=DEFAULT_BASE_URL, prefilter=None, now=None,
            **fetch_kw) -> Report:
    """Fetch, validate and scan the snapshot; with apply=True also import it. Never closes anything."""
    now = now or db.utc_now()
    report = Report(apply=apply)
    prefilter = prefilter or Prefilter.load()
    try:
        manifest = fetch_manifest(fetch, base_url, **fetch_kw)
    except SourceError as exc:
        report.status, report.reasons = "failed", [str(exc)]
        if apply and conn is not None:
            record_failure(conn, exc, now=now)
        return report
    report.manifest = manifest
    generated = parse_iso(manifest.generated_at)
    now_dt = parse_iso(now)

    if conn is not None:
        done = conn.execute("""SELECT 1 FROM source_checks WHERE source_id = ? AND outcome = 'success' AND complete = 1
                               AND source_snapshot_at = ?""", (SOURCE_ID, manifest.generated_at)).fetchone()
        newest = (conn.execute("SELECT MAX(snapshot_at) FROM sightings WHERE source_id = ?", (SOURCE_ID,)).fetchone()[0]
                  if db.get_schema_version(conn) >= 4 else None)   # v3 databases predate the column
        if done:
            report.status, report.reasons = "already_processed", [f"snapshot {manifest.snapshot_id} was imported before"]
            if apply:
                report.run_id = record_failure(conn, report.reasons[0], outcome="skipped", now=now)
            return report
        if newest and parse_iso(newest) > generated:
            report.status, report.reasons = "refused", [f"snapshot is older than data already imported ({newest})"]
            if apply:
                record_failure(conn, report.reasons[0], outcome="partial", now=now)
            return report
    if now_dt and now_dt - generated > STALE_AFTER:
        report.status, report.reasons = "refused", [f"stale snapshot published {manifest.generated_at}"]
        if apply and conn is not None:
            record_failure(conn, report.reasons[0], outcome="partial", now=now)
        return report

    store = SnapshotStore(manifest, cache_dir, fetch, base_url, **fetch_kw)
    candidates, metrics = scan(manifest, store, prefilter, now)
    report.metrics = metrics
    prune_cache(cache_dir, keep=manifest.snapshot_id)
    problems = list(metrics.chunk_errors)
    if metrics.scanned != manifest.total_jobs and not metrics.chunk_errors:
        problems.append(f"row count {metrics.scanned} differs from manifest totalJobs {manifest.total_jobs}")
    try:
        again = fetch_manifest(fetch, base_url, **fetch_kw)
        if again.generated_at != manifest.generated_at:
            problems.append("the feed was republished while downloading")
    except SourceError:
        pass   # the first manifest was valid and all chunks were read; a transient failure here is not fatal
    if problems:
        report.status, report.reasons = "refused", problems[:3]
        if apply and conn is not None:
            record_failure(conn, "; ".join(problems)[:300], outcome="partial", now=now)
        return report

    if conn is not None:
        report.existing_jobs = len({c["ext_job"] for c in candidates} & existing_job_ids(conn))
        report.new_jobs_est = len(candidates) - report.existing_jobs
        boards = {c["ext_company"]: c for c in candidates}
        new_boards = {k: v for k, v in boards.items() if k not in existing_company_ids(conn)}
        report.new_companies_est = len(new_boards)
        index = yc_name_index(conn)
        report.possible_yc_matches_est = sum(1 for c in new_boards.values()
                                             if _possible_yc_matches(c["board"], c["ats"], index))
    else:
        report.new_jobs_est, report.new_companies_est = len(candidates), len({c["ext_company"] for c in candidates})
    if not apply:
        return report

    db.register_source(conn, SOURCE_ID, SOURCE_NAME, "ats_feed", SOURCE_HOME, config_ref=db.NO_CLOSURE_EVIDENCE, now=now)
    run_id = db.start_run(conn, "import", now=now)
    report.run_id = run_id
    try:
        report.counts = import_candidates(conn, manifest, candidates, now=now)
        db.record_source_check(
            conn, run_id, SOURCE_ID, "success", scope=SOURCE_SCOPE, complete=True, record_count=metrics.scanned,
            started_at=now, finished_at=db.utc_now(), source_snapshot_at=manifest.generated_at,
            error_info="Snapshot fully validated. This says nothing about whether each employer board was "
                       "refreshed, so it is never evidence for closing jobs.")
        c = report.counts
        db.finish_run(conn, run_id, "success", now=db.utc_now(), summary={
            "snapshot_id": manifest.snapshot_id, "snapshot_at": manifest.generated_at, "scanned": metrics.scanned,
            "kept": metrics.kept, "companies_created": c.companies_created, "jobs_created": c.jobs_created,
            "jobs_changed": c.jobs_changed, "marked_unknown": c.marked_unknown, "malformed": metrics.malformed,
            "runtime_s": round(metrics.seconds), "peak_mb": round(metrics.peak_mb)})
    except BaseException as exc:
        db.finish_run(conn, run_id, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                      error_detail=f"{type(exc).__name__}: {str(exc)[:200]}", now=db.utc_now())
        raise
    return report
