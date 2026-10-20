"""SQLite persistence for the discovery engine (built-in sqlite3 only).

Connections run in autocommit mode (isolation_level=None) so that every
transaction is explicit: use `transaction(conn)` around writes.
"""

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .schema import APP_TABLES, MIGRATIONS, SCHEMA_VERSION

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = "data/discovery.db"
DB_PATH_ENV = "DISCOVERY_DB_PATH"
BUSY_TIMEOUT_MS = 30_000


class DatabaseConfigError(Exception):
    """The database location is missing or invalid."""


class SchemaVersionError(Exception):
    """The database was created by a newer version of this application."""


class IdentityConflict(Exception):
    """Evidence points at two different canonical records; a human must decide.

    Nothing is merged automatically when this is raised.
    """


class JobObservation(NamedTuple):
    job_id: str
    created: bool
    changed: bool
    sighting_id: str


# ---------------------------------------------------------------- helpers

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().casefold()


def normalize_domain(domain):
    """Lowercase host only: strips scheme, 'www.', path and port. None if empty."""
    if not domain:
        return None
    d = domain.strip().lower()
    d = re.sub(r"^[a-z][a-z0-9+.-]*://", "", d)
    d = d.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]
    d = re.sub(r"^www\.", "", d).strip(".")
    return d or None


# Query parameters that only track marketing/referrals. Anything else is kept:
# job and requisition identifiers (e.g. Greenhouse gh_jid, ?id=123) are meaningful.
_TRACKING_PARAMS = {"gclid", "fbclid", "mc_cid", "mc_eid", "gh_src", "lever-source", "igshid"}


def normalize_url(url):
    """Canonical form for comparing source URLs: lowercase scheme/host, no fragment,
    no trailing slash, tracking parameters removed, remaining parameters sorted."""
    parts = urlsplit(url.strip())
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS)
    path = parts.path.rstrip("/") if parts.path != "/" else ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def content_hash(**fields) -> str:
    """Stable hash of the meaningful fields of a record (None and blanks ignored)."""
    cleaned = {k: re.sub(r"\s+", " ", str(v)).strip() for k, v in sorted(fields.items())
               if v is not None and str(v).strip()}
    return hashlib.sha256(json.dumps(cleaned, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------- location / config

def resolve_db_path(root: Path = PROJECT_ROOT) -> Path:
    """DISCOVERY_DB_PATH from the environment, else root/.env, else the default.

    Only that one key is read from .env. Relative paths resolve against `root`.
    """
    value = os.environ.get(DB_PATH_ENV)
    env_file = Path(root) / ".env"
    if value is None and env_file.is_file():
        from dotenv import dotenv_values

        value = dotenv_values(env_file).get(DB_PATH_ENV)
    if value is None:
        value = DEFAULT_DB_PATH
    value = value.strip()
    if not value:
        raise DatabaseConfigError(f"{DB_PATH_ENV} is set but empty")
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


# -------------------------------------------------------------- connection

def connect(db_path, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection with foreign keys on and a busy timeout.

    readonly=True never creates or modifies the file. Otherwise the file is
    created if missing and put into WAL mode.
    """
    db_path = Path(db_path)
    if readonly:
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True,
                               timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection):
    """Explicit write transaction: commits on success, rolls back on any error.

    Re-entrant: inside an already open transaction the outer one decides.
    """
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# ---------------------------------------------------------- initialization

def get_schema_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def initialize_database(db_path) -> int:
    """Create or upgrade the database. Idempotent; never deletes records."""
    conn = connect(db_path)
    try:
        version = get_schema_version(conn)
        if version > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"Database schema version {version} is newer than supported {SCHEMA_VERSION}")
        for migration in MIGRATIONS[version:]:
            try:
                conn.executescript(migration)
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return get_schema_version(conn)
    finally:
        conn.close()


def list_app_tables(conn) -> list:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    return [r[0] for r in rows]


# --------------------------------------------------------------- companies

def get_company(conn, company_id):
    return conn.execute("SELECT * FROM companies WHERE company_id = ?", (company_id,)).fetchone()


def find_company_by_domain(conn, domain):
    domain = normalize_domain(domain)
    if not domain:
        return None
    return conn.execute("SELECT * FROM companies WHERE domain = ?", (domain,)).fetchone()


def get_or_create_company(conn, name, domain=None, *, location=None, aliases=None,
                          facts=None, now=None):
    """Return (company_id, created).

    A company is matched ONLY by verified domain. Without a domain a new
    provisional company is always created: names alone never merge employers.
    Callers re-find provisional companies through their sightings.
    """
    now = now or utc_now()
    domain = normalize_domain(domain)
    with transaction(conn):
        existing = find_company_by_domain(conn, domain)
        if existing:
            return existing["company_id"], False
        company_id = new_id()
        conn.execute(
            """INSERT INTO companies (company_id, name, normalized_name, domain, aliases, location,
                                      facts, identity_status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (company_id, name, normalize_name(name), domain, json.dumps(aliases or []), location,
             json.dumps(facts or {}), "verified" if domain else "provisional", now, now))
        return company_id, True


# -------------------------------------------------------------------- jobs

def get_job(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()


def insert_job(conn, company_id, title, *, location=None, remote_eligibility="unknown",
               experience_min_years=None, experience_max_years=None, experience_text=None,
               posting_url=None, posted_at=None, requisition_id=None, now=None):
    """Create a canonical job in state 'New' and record the first status event.

    Always inserts: identical titles at one company are distinct jobs unless the
    caller's identity logic (provider IDs, sightings) says otherwise.
    posted_at is stored only if the caller has a real value.
    """
    now = now or utc_now()
    job_id = new_id()
    with transaction(conn):
        conn.execute(
            """INSERT INTO jobs (job_id, company_id, title, normalized_title, location,
                                 remote_eligibility, experience_min_years, experience_max_years,
                                 experience_text, lifecycle_state, posting_url, posted_at,
                                 first_seen_at, last_seen_at, created_at, updated_at, requisition_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'New', ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, company_id, title, normalize_name(title), location, remote_eligibility,
             experience_min_years, experience_max_years, experience_text, posting_url, posted_at,
             now, now, now, now, requisition_id))
        conn.execute(
            """INSERT INTO status_events (event_id, job_id, previous_state, new_state, occurred_at, reason)
               VALUES (?, ?, NULL, 'New', ?, 'First observation')""",
            (new_id(), job_id, now))
    return job_id


# ----------------------------------------------------------------- sources

def register_source(conn, source_id, name, source_type, url_or_identifier=None, *,
                    enabled=True, config_ref=None, now=None):
    """Register a source. Idempotent: an existing source is left untouched."""
    now = now or utc_now()
    with transaction(conn):
        conn.execute(
            """INSERT INTO sources (source_id, name, source_type, url_or_identifier, enabled,
                                    config_ref, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_id) DO NOTHING""",
            (source_id, name, source_type, url_or_identifier, int(enabled), config_ref, now, now))
    return conn.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()


#---------------------------------------------------------------- sightings

def find_sighting(conn, source_id, source_url, external_id, is_job):
    """Look up a sighting by its identity: (source, provider ID) or (source, normalized URL)."""
    if external_id is not None:
        return conn.execute(
            """SELECT * FROM sightings
               WHERE source_id = ? AND external_id = ? AND (job_id IS NOT NULL) = ?""",
            (source_id, external_id, int(is_job))).fetchone()
    return conn.execute(
        """SELECT * FROM sightings
           WHERE source_id = ? AND source_url = ? AND external_id IS NULL
             AND (job_id IS NOT NULL) = ?""",
        (source_id, normalize_url(source_url), int(is_job))).fetchone()


def record_sighting(conn, source_id, source_url, *, job_id=None, company_id=None, external_id=None,
                    provider_observed_at=None, content_hash=None, now=None):
    """Insert a sighting or refresh an identical one. Returns (sighting_id, created).

    Identity is (source, provider external_id) or, without one, (source, URL),
    separately for jobs and companies. A repeat updates last_seen_at (and the
    provider timestamp / content hash) instead of inserting a duplicate, and
    never changes which canonical record the sighting points to.
    """
    now = now or utc_now()
    is_job = job_id is not None
    source_url = normalize_url(source_url)
    with transaction(conn):
        row = find_sighting(conn, source_id, source_url, external_id, is_job)
        if row:
            conn.execute(
                """UPDATE sightings SET last_seen_at = ?, source_url = ?,
                          provider_observed_at = COALESCE(?, provider_observed_at),
                          content_hash = COALESCE(?, content_hash)
                   WHERE sighting_id = ?""",
                (now, source_url, provider_observed_at, content_hash, row["sighting_id"]))
            sighting_id, created = row["sighting_id"], False
        else:
            sighting_id, created = new_id(), True
            conn.execute(
                """INSERT INTO sightings (sighting_id, source_id, job_id, company_id, external_id,
                                          source_url, first_seen_at, last_seen_at,
                                          provider_observed_at, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sighting_id, source_id, job_id, company_id, external_id, source_url, now, now,
                 provider_observed_at, content_hash))
        if is_job:
            conn.execute("UPDATE jobs SET last_seen_at = ? WHERE job_id = ? AND last_seen_at < ?",
                         (now, job_id, now))
    return sighting_id, created


# ------------------------------------------------------- runs & source checks

def start_run(conn, run_type, *, run_id=None, now=None):
    """Start a run. Passing an existing run_id resumes/replays it instead of duplicating it."""
    run_id, now = run_id or new_id(), now or utc_now()
    with transaction(conn):
        conn.execute("""INSERT INTO runs (run_id, started_at, run_type, result)
                        VALUES (?, ?, ?, 'running') ON CONFLICT(run_id) DO NOTHING""",
                     (run_id, now, run_type))
    return run_id


def finish_run(conn, run_id, result, *, summary=None, error_detail=None, now=None):
    now = now or utc_now()
    with transaction(conn):
        cur = conn.execute(
            "UPDATE runs SET finished_at = ?, result = ?, summary = ?, error_detail = ? WHERE run_id = ?",
            (now, result, json.dumps(summary or {}), error_detail, run_id))
        if cur.rowcount != 1:
            raise KeyError(f"Unknown run_id {run_id}")


def record_source_check(conn, run_id, source_id, outcome, *, scope="", complete=False,
                        record_count=0, started_at=None, finished_at=None,
                        source_snapshot_at=None, error_info=None):
    """Record one source check at an explicit scope (board, feed, query...).

    `complete` is only accepted for outcome 'success'; failed, partial and
    skipped checks are stored as incomplete and are never evidence of closure.

    Idempotent per (run, source, scope): replaying identical results returns the
    existing check; conflicting results for the same scope raise IntegrityError.
    """
    check_id, now = new_id(), utc_now()
    with transaction(conn):
        existing = get_source_check(conn, run_id, source_id, scope)
        if existing:
            if (existing["outcome"], existing["complete"], existing["record_count"]) == (
                    outcome, int(complete), record_count):
                return existing["check_id"]
            raise sqlite3.IntegrityError(
                f"Conflicting source check already recorded for {source_id!r} scope {scope!r} in this run")
        conn.execute(
            """INSERT INTO source_checks (check_id, run_id, source_id, scope, started_at, finished_at,
                                          outcome, complete, record_count, source_snapshot_at, error_info)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (check_id, run_id, source_id, scope, started_at or now, finished_at or now, outcome,
             int(complete), record_count, source_snapshot_at, error_info))
    return check_id


def get_source_check(conn, run_id, source_id, scope=""):
    return conn.execute(
        "SELECT * FROM source_checks WHERE run_id = ? AND source_id = ? AND scope = ?",
        (run_id, source_id, scope)).fetchone()


# ------------------------------------------------------------ status events

def _current_state(conn, job_id, company_id):
    if job_id:
        row = conn.execute("SELECT lifecycle_state FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown job_id {job_id}")
        return row[0]
    row = conn.execute(
        "SELECT new_state FROM status_events WHERE company_id = ? ORDER BY rowid DESC LIMIT 1",
        (company_id,)).fetchone()
    return row[0] if row else None


def append_status_event(conn, new_state, reason, *, job_id=None, company_id=None,
                        source_check_id=None, sighting_id=None, allow_same_state=False, now=None):
    """Append a state-change event; returns its id, or None if nothing changed.

    Re-observing a record in the state it already has creates no event
    (allow_same_state=True is for 'Changed': every distinct change is history).
    For jobs the job's lifecycle_state is updated in the same transaction.

    Only jobs have a lifecycle. Company-level events are rejected until a
    separate company model is designed: a closed vacancy says nothing about
    whether the company itself is interesting or active.

    'Not Seen' and 'Closed' need a successful, complete source check from a source
    that has actually sighted the job (the database enforces the completeness part).
    """
    now = now or utc_now()
    if company_id is not None:
        raise ValueError("Company lifecycle events are not supported; lifecycle applies to jobs only")
    with transaction(conn):
        if new_state in ("Not Seen", "Closed"):
            check = conn.execute("SELECT * FROM source_checks WHERE check_id = ?",
                                 (source_check_id,)).fetchone() if source_check_id else None
            if check is None or check["outcome"] != "success" or not check["complete"]:
                raise ValueError(f"'{new_state}' requires a successful, complete source check as evidence")
            seen_there = conn.execute("SELECT 1 FROM sightings WHERE job_id = ? AND source_id = ?",
                                      (job_id, check["source_id"])).fetchone()
            if not seen_there:
                raise ValueError("The evidence source has never sighted this job")
        previous = _current_state(conn, job_id, company_id)
        if previous == new_state and not allow_same_state:
            return None
        event_id = new_id()
        conn.execute(
            """INSERT INTO status_events (event_id, job_id, company_id, previous_state, new_state,
                                          occurred_at, reason, source_check_id, sighting_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, job_id, company_id, previous, new_state, now, reason, source_check_id,
             sighting_id))
        if job_id:
            conn.execute("UPDATE jobs SET lifecycle_state = ?, updated_at = ? WHERE job_id = ?",
                         (new_state, now, job_id))
    return event_id


# ------------------------------------------------------------------ verdicts

_UNSET = object()


def save_verdict(conn, decision=_UNSET, notes=_UNSET, *, job_id=None, company_id=None, now=None):
    """Save the human verdict and/or note for one job or company (one row per record).

    decision: 'Yes' / 'Maybe' / 'No', or None for "not reviewed yet" (a note-only entry).
    Anything left out keeps its stored value, so setting a verdict never drops the note and
    editing a note never drops the verdict. Passing decision=None or notes="" is the explicit
    way to clear one of them; the database refuses to leave a row with neither.
    A save that changes nothing does not touch updated_at.
    """
    now = now or utc_now()
    with transaction(conn):
        existing = get_verdict(conn, job_id=job_id, company_id=company_id)
        if existing:
            new_decision = existing["decision"] if decision is _UNSET else decision
            new_notes = existing["notes"] if notes is _UNSET else notes
            if (new_decision, new_notes) != (existing["decision"], existing["notes"]):
                conn.execute("UPDATE verdicts SET decision = ?, notes = ?, updated_at = ? WHERE verdict_id = ?",
                             (new_decision, new_notes, now, existing["verdict_id"]))
            return existing["verdict_id"]
        verdict_id = new_id()
        conn.execute(
            """INSERT INTO verdicts (verdict_id, job_id, company_id, decision, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (verdict_id, job_id, company_id, None if decision is _UNSET else decision,
             "" if notes is _UNSET else notes, now, now))
        return verdict_id


def get_verdict(conn, *, job_id=None, company_id=None):
    if job_id:
        return conn.execute("SELECT * FROM verdicts WHERE job_id = ?", (job_id,)).fetchone()
    return conn.execute("SELECT * FROM verdicts WHERE company_id = ?", (company_id,)).fetchone()


# ------------------------------------------------- identity-aware observation

def set_company_domain(conn, company_id, domain, *, now=None):
    """Attach a verified domain to a provisional company, in place.

    The company keeps its UUID, so jobs, sightings, history and verdicts stay attached.
    If another company already owns the domain the two records are the same real
    employer, but merging them is NOT automatic: IdentityConflict is raised.
    """
    now = now or utc_now()
    domain = normalize_domain(domain)
    if not domain:
        raise ValueError("A domain is required")
    with transaction(conn):
        company = get_company(conn, company_id)
        if company is None:
            raise KeyError(f"Unknown company_id {company_id}")
        if company["domain"] == domain:
            return
        if company["domain"] is not None:
            raise IdentityConflict(
                f"Company {company_id} is verified as {company['domain']}, not {domain}")
        owner = find_company_by_domain(conn, domain)
        if owner is not None:
            raise IdentityConflict(
                f"Domain {domain} already belongs to company {owner['company_id']}; "
                f"merging {company_id} into it requires review")
        conn.execute("UPDATE companies SET domain = ?, identity_status = 'verified', updated_at = ? "
                     "WHERE company_id = ?", (domain, now, company_id))


def _add_alias(conn, company_id, name, now):
    company = get_company(conn, company_id)
    known = {company["normalized_name"], *(normalize_name(a) for a in json.loads(company["aliases"]))}
    if normalize_name(name) not in known:
        aliases = json.loads(company["aliases"]) + [name]
        conn.execute("UPDATE companies SET aliases = ?, updated_at = ? WHERE company_id = ?",
                     (json.dumps(aliases), now, company_id))


def observe_company(conn, *, source_id, source_url, name, external_id=None, domain=None,
                    location=None, provider_observed_at=None, now=None):
    """Resolve a company seen at a source and record the sighting. Returns (company_id, created).

    Order of evidence (no fuzzy name matching, ever):
      1. this source's own ID/URL for the company (stable across repeats, even without a domain);
      2. the verified domain (shared across sources);
      3. otherwise a new company (provisional when there is no domain).
    A later-learned domain upgrades a provisional company in place (set_company_domain).
    """
    now = now or utc_now()
    domain = normalize_domain(domain)
    with transaction(conn):
        sighting = find_sighting(conn, source_id, source_url, external_id, is_job=False)
        if sighting:
            company_id, created = sighting["company_id"], False
            if domain:
                set_company_domain(conn, company_id, domain, now=now)
        else:
            company_id, created = get_or_create_company(conn, name, domain, location=location, now=now)
        _add_alias(conn, company_id, name, now)
        record_sighting(conn, source_id, source_url, company_id=company_id, external_id=external_id,
                        provider_observed_at=provider_observed_at, now=now)
    return company_id, created


def observe_job(conn, *, source_id, source_url, company_id, title, external_id=None,
                requisition_id=None, location=None, remote_eligibility=None,
                experience_min_years=None, experience_max_years=None, experience_text=None,
                description=None, posting_url=None, posted_at=None, provider_observed_at=None,
                now=None) -> JobObservation:
    """Resolve a job seen at a source, record the sighting, and note real changes.

    Identity evidence, strongest first:
      1. this source's own ID/URL for the job (rediscovery);
      2. the employer requisition ID at the same company (the same vacancy via another source).
    Title, location or URL similarity alone never merges two jobs.

    A different content hash from the same source updates the job's supplied fields and
    appends a 'Changed' event; an identical repeat only refreshes last-seen. posted_at is
    stored only if supplied.
    """
    now = now or utc_now()
    fields = dict(title=title, location=location, remote_eligibility=remote_eligibility,
                  experience_min_years=experience_min_years, experience_max_years=experience_max_years,
                  experience_text=experience_text, description=description)
    new_hash = content_hash(**fields)
    created = changed = False
    with transaction(conn):
        sighting = find_sighting(conn, source_id, source_url, external_id, is_job=True)
        if sighting:
            job_id = sighting["job_id"]
            job = get_job(conn, job_id)
            if job["company_id"] != company_id:
                raise IdentityConflict(f"Sighting belongs to job {job_id} of another company")
            changed = sighting["content_hash"] is not None and sighting["content_hash"] != new_hash
        else:
            job = None
            if requisition_id:
                job = conn.execute("SELECT * FROM jobs WHERE company_id = ? AND requisition_id = ?",
                                   (company_id, requisition_id)).fetchone()
            if job is None:
                job_id = insert_job(conn, company_id, title, location=location,
                                    remote_eligibility=remote_eligibility or "unknown",
                                    experience_min_years=experience_min_years,
                                    experience_max_years=experience_max_years,
                                    experience_text=experience_text, posting_url=posting_url,
                                    posted_at=posted_at, requisition_id=requisition_id, now=now)
                created = True
            else:
                job_id = job["job_id"]
        if job is not None:
            if requisition_id and job["requisition_id"] not in (None, requisition_id):
                raise IdentityConflict(f"Job {job_id} has requisition {job['requisition_id']}, "
                                       f"not {requisition_id}")
            if requisition_id and job["requisition_id"] is None:
                conn.execute("UPDATE jobs SET requisition_id = ? WHERE job_id = ?", (requisition_id, job_id))
        sighting_id, _ = record_sighting(conn, source_id, source_url, job_id=job_id,
                                         external_id=external_id, provider_observed_at=provider_observed_at,
                                         content_hash=new_hash, now=now)
        if changed:
            updates = {"title": title, "normalized_title": normalize_name(title), "location": location,
                       "remote_eligibility": remote_eligibility, "experience_min_years": experience_min_years,
                       "experience_max_years": experience_max_years, "experience_text": experience_text,
                       "posting_url": posting_url}
            updates = {k: v for k, v in updates.items() if v is not None}
            assignments = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE jobs SET {assignments}, updated_at = ? WHERE job_id = ?",
                         (*updates.values(), now, job_id))
            append_status_event(conn, "Changed", f"Content changed at source {source_id}",
                                job_id=job_id, sighting_id=sighting_id, allow_same_state=True, now=now)
    return JobObservation(job_id, created, changed, sighting_id)
