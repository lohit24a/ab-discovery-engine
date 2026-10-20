"""Stream B source adapter: Y Combinator companies via the unofficial yc-oss/api dataset.

Audit and field behaviour: docs/source_audits/yc_oss.md. Rules that matter:
  * Identity is the YC numeric `id` (stored as the sighting's external_id). Names are not identities.
  * `website` is source-reported. It is kept as a fact and never written to companies.domain, which
    the schema treats as a verified domain.
  * `isHiring` is stored as a source-reported fact only. It never filters a company and never creates
    a job status event; a company without jobs is a normal Stream B record.
  * Absence from a snapshot never deletes or closes anything (this adapter only adds and refreshes).
  * Location classification is deterministic and uses only the source's own location fields.
"""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .. import database as db

SOURCE_ID = "yc_oss"
SOURCE_NAME = "YC (yc-oss API)"
SOURCE_SCOPE = "companies/all.json"
META_URL = "https://yc-oss.github.io/api/meta.json"
ALL_URL = "https://yc-oss.github.io/api/companies/all.json"
SOURCE_HOME = "https://github.com/yc-oss/api"

STALE_AFTER = timedelta(days=3)
MAX_BYTES = 64 * 1024 * 1024
SCHEMA_FAILURE_RATIO = 0.05  # more than 5% unusable records means the format probably changed
BATCH_SIZE = 500

# Company-level India evidence found in YC's own location fields. These are descriptive labels about
# what the data shows, NOT job-eligibility rulings: a company with no India evidence can still hire
# India-based people, and its jobs must be judged on their own evidence later.
INDIA_BASED = "india_based"
POTENTIALLY_RELEVANT = "potentially_relevant"    # fully remote per YC; India eligibility unconfirmed
UNVERIFIED = "unverified"                        # remote/partly remote or unusable location data
NO_INDIA_EVIDENCE = "no_india_evidence_yet"      # structured non-India location, no remote hint
CLASSES = (INDIA_BASED, POTENTIALLY_RELEVANT, UNVERIFIED, NO_INDIA_EVIDENCE)


class SourceError(Exception):
    """The source could not be fetched or is not in a usable format. Nothing is imported."""


# ------------------------------------------------------------------ fetching

def fetch_json(url, *, timeout=60, retries=3, sleep=time.sleep, opener=urllib.request.urlopen):
    """GET a JSON document with a timeout, bounded size and retries for transient failures."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ab-discovery-engine/0.1 (personal research)",
                                                       "Accept": "application/json"})
            with opener(req, timeout=timeout) as resp:
                if getattr(resp, "status", 200) != 200:
                    raise SourceError(f"{url}: HTTP {resp.status}")
                body = resp.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise SourceError(f"{url}: response larger than {MAX_BYTES} bytes")
            return json.loads(body.decode("utf-8"))  # data only; nothing from it is ever executed
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code < 500 and exc.code != 429:
                raise SourceError(f"{url}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceError(f"{url}: not valid JSON ({type(exc).__name__})") from exc
        if attempt < retries - 1:
            sleep(2 ** attempt)
    raise SourceError(f"{url}: request failed after {retries} attempts ({type(last).__name__})")


def parse_iso(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


@dataclass
class Snapshot:
    records: list
    retrieved_at: str
    source_updated_at: str   # publisher's meta.last_updated, kept separate from retrieved_at
    meta_count: int


def fetch_snapshot(fetch=fetch_json, now=None) -> Snapshot:
    now = now or db.utc_now()
    meta = fetch(META_URL)
    try:
        updated = meta["last_updated"]
        count = meta["companies"]["all"]["count"]
    except (KeyError, TypeError) as exc:
        raise SourceError("meta.json does not have the expected structure (last_updated, companies.all.count)") from exc
    if parse_iso(updated) is None or not isinstance(count, int) or isinstance(count, bool):
        raise SourceError("meta.json has an unreadable last_updated or company count")
    records = fetch(ALL_URL)
    if not isinstance(records, list):
        raise SourceError("companies/all.json is not a list of companies (source format changed?)")
    if not records:
        raise SourceError("companies/all.json is empty; refusing to treat that as 'no companies'")
    return Snapshot(records, now, updated, count)


# -------------------------------------------------------------- normalization

def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _strings(value):
    return [v.strip() for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


def _epoch_date(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def normalize_record(raw):
    """Return (normalized dict, None) or (None, problem text). Never invents values."""
    if not isinstance(raw, dict):
        return None, "record is not an object"
    yc_id = raw.get("id")
    if isinstance(yc_id, bool) or not isinstance(yc_id, int) or yc_id <= 0:
        return None, f"missing or invalid YC id ({yc_id!r}, name {raw.get('name')!r})"
    name = _text(raw.get("name"))
    if not name:
        return None, f"YC id {yc_id}: missing name"
    slug = _text(raw.get("slug"))
    profile_url = _text(raw.get("url")) or (f"https://www.ycombinator.com/companies/{slug}" if slug else None)
    if not profile_url:
        return None, f"YC id {yc_id}: no profile URL or slug"
    website = _text(raw.get("website"))
    team = raw.get("team_size")
    hiring = raw.get("isHiring")
    regions = _strings(raw.get("regions"))
    location = _text(raw.get("all_locations"))
    classification, reason = classify_location(location, regions)
    yc = {
        "yc_id": yc_id, "slug": slug, "profile_url": profile_url,
        "website_reported": website, "website_domain_reported": db.normalize_domain(website),
        "locations": location, "regions": regions,
        "one_liner": _text(raw.get("one_liner")), "long_description": _text(raw.get("long_description")),
        "industry": _text(raw.get("industry")), "subindustry": _text(raw.get("subindustry")),
        "industries": _strings(raw.get("industries")), "tags": _strings(raw.get("tags")),
        "batch": _text(raw.get("batch")), "stage": _text(raw.get("stage")), "status": _text(raw.get("status")),
        "team_size": team if isinstance(team, int) and not isinstance(team, bool) else None,
        "is_hiring_reported": hiring if isinstance(hiring, bool) else None,
        "launched_on": _epoch_date(raw.get("launched_at")),
        "former_names": _strings(raw.get("former_names")),
    }
    return {
        "yc_id": yc_id, "name": name, "profile_url": profile_url, "location": location,
        "aliases": yc["former_names"], "yc": yc, "classification": classification, "reason": reason,
        "hash": db.content_hash(data=json.dumps([yc, name], sort_keys=True)),
    }, None


def classify_location(location, regions):
    """Deterministic India relevance from YC's own location fields. Returns (class, reason).

    Remote is never taken as proof of India eligibility. Unparseable locations are 'unverified'.
    'no_india_evidence_yet' only means: structured locations entirely outside India and no remote hint in
    YC's data. It is not an exclusion and not a statement that the company cannot hire in India; it is
    re-evaluated on every import and says nothing permanent about the company's jobs.
    """
    parts = [p.strip() for p in (location or "").split(";") if p.strip()]
    places = [p for p in parts if p.casefold() != "remote"]
    regs = {r.casefold() for r in regions}
    shown_countries = sorted({p.rsplit(",", 1)[-1].strip() for p in places if "," in p})
    countries = {c.casefold() for c in shown_countries}
    if "india" in countries or "india" in regs:
        return INDIA_BASED, "India listed in YC locations/regions"
    if "fully remote" in regs:
        return POTENTIALLY_RELEVANT, "Fully remote per YC; India eligibility not confirmed"
    remote_hint = bool(regs & {"remote", "partly remote"}) or len(places) < len(parts)
    if remote_hint:
        return UNVERIFIED, "Remote or partly remote per YC; India eligibility unknown"
    if not places:
        return UNVERIFIED, "No usable location information"
    if any("," not in p for p in places):
        return UNVERIFIED, "Location format not recognized"
    return NO_INDIA_EVIDENCE, (f"YC lists only {', '.join(shown_countries)} and no remote option; "
                               "no India evidence yet (not an eligibility restriction)")


# ------------------------------------------------------------------ analysis

@dataclass
class Analysis:
    companies: list = field(default_factory=list)       # normalized, importable
    malformed: list = field(default_factory=list)       # problem texts
    duplicates_dropped: int = 0                         # identical repeats of one YC id
    conflicts: list = field(default_factory=list)       # same YC id, different content -> skipped
    warnings: list = field(default_factory=list)
    classes: dict = field(default_factory=lambda: {c: 0 for c in CLASSES})
    hiring_reported: int = 0
    stale: bool = False
    count_mismatch: bool = False

    @property
    def clean(self):
        return not (self.malformed or self.conflicts or self.stale or self.count_mismatch)

    @property
    def publish_count(self):
        return self.classes[INDIA_BASED]

    def issue_summary(self):
        bits = []
        if self.malformed:
            bits.append(f"{len(self.malformed)} malformed record(s)")
        if self.conflicts:
            bits.append(f"{len(self.conflicts)} identity conflict(s)")
        if self.count_mismatch:
            bits.append("record count differs from meta.json")
        if self.stale:
            bits.append("snapshot is old")
        return "; ".join(bits)


def analyze(snapshot: Snapshot, now=None) -> Analysis:
    """Validate and normalize a snapshot. Raises SourceError if the format looks changed."""
    now_dt = parse_iso(now or db.utc_now())
    out = Analysis()
    by_id, order = {}, []
    for raw in snapshot.records:
        rec, problem = normalize_record(raw)
        if problem:
            out.malformed.append(problem)
            continue
        if rec["yc_id"] in by_id:
            if by_id[rec["yc_id"]]["hash"] == rec["hash"]:
                out.duplicates_dropped += 1
            else:
                by_id[rec["yc_id"]]["_conflict"] = True
                out.conflicts.append(f"YC id {rec['yc_id']} appears twice with different content "
                                     f"({by_id[rec['yc_id']]['name']!r} / {rec['name']!r}); both skipped")
            continue
        by_id[rec["yc_id"]] = rec
        order.append(rec["yc_id"])
    if len(snapshot.records) and len(out.malformed) / len(snapshot.records) > SCHEMA_FAILURE_RATIO:
        raise SourceError(f"{len(out.malformed)} of {len(snapshot.records)} records are unusable; "
                          "the source format has probably changed. Nothing imported")
    out.companies = [by_id[i] for i in order if not by_id[i].get("_conflict")]
    seen_slugs, seen_hosts = {}, {}
    for rec in out.companies:
        out.classes[rec["classification"]] += 1
        out.hiring_reported += 1 if rec["yc"]["is_hiring_reported"] else 0
        slug = rec["yc"]["slug"]
        if slug in seen_slugs and slug:
            out.warnings.append(f"slug {slug!r} shared by YC ids {seen_slugs[slug]} and {rec['yc_id']}")
        seen_slugs.setdefault(slug, rec["yc_id"])
        host = rec["yc"]["website_domain_reported"]
        if host:
            seen_hosts.setdefault(host, []).append(rec["yc_id"])
    shared = {h: ids for h, ids in seen_hosts.items() if len(ids) > 1}
    for host, ids in sorted(shared.items()):
        out.warnings.append(f"website host {host} reported by {len(ids)} companies (YC ids {ids[:5]}); "
                            "not merged, host is not used as identity")
    out.count_mismatch = snapshot.meta_count != len(snapshot.records)
    updated = parse_iso(snapshot.source_updated_at)
    out.stale = bool(updated and now_dt and now_dt - updated > STALE_AFTER)
    return out


# --------------------------------------------------------------- DB comparison

def existing_state(conn):
    """{yc_id(str): {hash, name, facts}} for companies already imported from YC."""
    rows = conn.execute("""SELECT s.external_id, s.content_hash, c.name, c.facts FROM sightings s
                           JOIN companies c USING (company_id) WHERE s.source_id = ?""", (SOURCE_ID,))
    return {r[0]: {"hash": r[1], "name": r[2], "facts": json.loads(r[3])} for r in rows}


def newest_imported_snapshot(conn):
    row = conn.execute("SELECT MAX(provider_observed_at) FROM sightings WHERE source_id = ?", (SOURCE_ID,)).fetchone()
    return row[0]


def preview(analysis: Analysis, existing: dict) -> dict:
    """Read-only comparison with what is stored: new vs existing, source changes, and how many stored
    records an --apply would refresh (their saved facts or name differ from what this snapshot yields)."""
    new = changed = refresh = 0
    for c in analysis.companies:
        old = existing.get(str(c["yc_id"]))
        if old is None:
            new += 1
            continue
        changed += old["hash"] != c["hash"]
        facts = _facts_for(c)
        refresh += old["name"] != c["name"] or any(old["facts"].get(k) != v for k, v in facts.items())
    return {"new": new, "existing": len(analysis.companies) - new, "existing_changed": changed,
            "would_refresh": refresh}


# ----------------------------------------------------------------- importing

def _facts_for(rec):
    return {"yc": rec["yc"],
            "india_relevance": {"class": rec["classification"], "reason": rec["reason"], "source": SOURCE_ID},
            "sheet_publish": rec["classification"] == INDIA_BASED}


@dataclass
class ImportResult:
    run_id: str
    created: int = 0
    refreshed: int = 0        # existing company whose source-reported facts changed
    unchanged: int = 0
    result: str = "success"


def import_companies(conn, snapshot: Snapshot, analysis: Analysis, *, now=None) -> ImportResult:
    """Write the analysed snapshot into SQLite. Idempotent; commits in batches so a rerun resumes safely."""
    now = now or db.utc_now()
    newest = newest_imported_snapshot(conn)
    if newest and parse_iso(snapshot.source_updated_at) < parse_iso(newest):
        raise SourceError(f"snapshot ({snapshot.source_updated_at}) is older than data already imported ({newest})")
    db.register_source(conn, SOURCE_ID, SOURCE_NAME, "yc", SOURCE_HOME, now=now)
    run_id = db.start_run(conn, "import", now=now)
    result = ImportResult(run_id)
    try:
        companies = analysis.companies
        for start in range(0, len(companies), BATCH_SIZE):
            with db.transaction(conn):
                for rec in companies[start:start + BATCH_SIZE]:
                    _import_one(conn, rec, snapshot, result, now)
        complete = analysis.clean
        outcome = "success" if complete else "partial"
        db.record_source_check(conn, run_id, SOURCE_ID, outcome, scope=SOURCE_SCOPE, complete=complete,
                               record_count=len(snapshot.records), started_at=now, finished_at=db.utc_now(),
                               source_snapshot_at=snapshot.source_updated_at,
                               error_info=None if complete else analysis.issue_summary())
        result.result = "success" if complete else "partial"
        db.finish_run(conn, run_id, result.result, now=db.utc_now(), summary={
            "source_updated_at": snapshot.source_updated_at, "records": len(snapshot.records),
            "created": result.created, "refreshed": result.refreshed, "unchanged": result.unchanged,
            "classes": analysis.classes, "malformed": len(analysis.malformed), "conflicts": len(analysis.conflicts)})
    except BaseException as exc:
        db.finish_run(conn, run_id, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                      error_detail=f"{type(exc).__name__}: {str(exc)[:200]}", now=db.utc_now(),
                      summary={"created": result.created, "refreshed": result.refreshed})
        raise
    return result


def _import_one(conn, rec, snapshot, result, now):
    external_id = str(rec["yc_id"])
    sighting = db.find_sighting(conn, SOURCE_ID, rec["profile_url"], external_id, is_job=False)
    facts = _facts_for(rec)
    if sighting:
        company_id, created = sighting["company_id"], False
    else:
        company_id, _ = db.get_or_create_company(conn, rec["name"], None, location=rec["location"],
                                                 aliases=rec["aliases"], facts=facts, now=now)
        created = True
    db.record_sighting(conn, SOURCE_ID, rec["profile_url"], company_id=company_id, external_id=external_id,
                       provider_observed_at=snapshot.source_updated_at, content_hash=rec["hash"], now=now)
    if created:
        result.created += 1
    elif db.update_company_details(conn, company_id, name=rec["name"], location=rec["location"],
                                   aliases_add=rec["aliases"], facts_patch=facts, now=now):
        result.refreshed += 1
    else:
        result.unchanged += 1


def record_failed_fetch(conn, error, *, now=None):
    """Remember that a YC import attempt failed. Touches no company data."""
    now = now or db.utc_now()
    db.register_source(conn, SOURCE_ID, SOURCE_NAME, "yc", SOURCE_HOME, now=now)
    run_id = db.start_run(conn, "import", now=now)
    db.record_source_check(conn, run_id, SOURCE_ID, "failed", scope=SOURCE_SCOPE, complete=False,
                           started_at=now, finished_at=db.utc_now(), error_info=str(error)[:300])
    db.finish_run(conn, run_id, "failed", error_detail=str(error)[:300], now=db.utc_now())
    return run_id
