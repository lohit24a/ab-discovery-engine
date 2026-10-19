"""SQLite persistence for the discovery engine (built-in sqlite3 only).

Connections run in autocommit mode (isolation_level=None) so that every
transaction is explicit: use `transaction(conn)` around writes.
"""

import contextlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .schema import APP_TABLES, MIGRATIONS, SCHEMA_VERSION

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = "data/discovery.db"
DB_PATH_ENV = "DISCOVERY_DB_PATH"
BUSY_TIMEOUT_MS = 30_000


class DatabaseConfigError(Exception):
    """The database location is missing or invalid."""


class SchemaVersionError(Exception):
    """The database was created by a newer version of this application."""


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
               posting_url=None, posted_at=None, now=None):
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
                                 first_seen_at, last_seen_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'New', ?, ?, ?, ?, ?, ?)""",
            (job_id, company_id, title, normalize_name(title), location, remote_eligibility,
             experience_min_years, experience_max_years, experience_text, posting_url, posted_at,
             now, now, now, now))
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
    with transaction(conn):
        if external_id is not None:
            row = conn.execute(
                """SELECT sighting_id FROM sightings
                   WHERE source_id = ? AND external_id = ? AND (job_id IS NOT NULL) = ?""",
                (source_id, external_id, int(is_job))).fetchone()
        else:
            row = conn.execute(
                """SELECT sighting_id FROM sightings
                   WHERE source_id = ? AND source_url = ? AND external_id IS NULL
                     AND (job_id IS NOT NULL) = ?""",
                (source_id, source_url, int(is_job))).fetchone()
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

def start_run(conn, run_type, *, now=None):
    run_id, now = new_id(), now or utc_now()
    with transaction(conn):
        conn.execute("INSERT INTO runs (run_id, started_at, run_type, result) VALUES (?, ?, ?, 'running')",
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
    """
    check_id, now = new_id(), utc_now()
    with transaction(conn):
        conn.execute(
            """INSERT INTO source_checks (check_id, run_id, source_id, scope, started_at, finished_at,
                                          outcome, complete, record_count, source_snapshot_at, error_info)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (check_id, run_id, source_id, scope, started_at or now, finished_at or now, outcome,
             int(complete), record_count, source_snapshot_at, error_info))
    return check_id


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
                        source_check_id=None, sighting_id=None, now=None):
    """Append a state-change event; returns its id, or None if nothing changed.

    Re-observing a record in the state it already has creates no event.
    For jobs the job's lifecycle_state is updated in the same transaction.
    """
    now = now or utc_now()
    with transaction(conn):
        previous = _current_state(conn, job_id, company_id)
        if previous == new_state:
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

def save_verdict(conn, decision, notes="", *, job_id=None, company_id=None, now=None):
    """Save the human verdict for one job or company (one verdict per record)."""
    now = now or utc_now()
    with transaction(conn):
        existing = get_verdict(conn, job_id=job_id, company_id=company_id)
        if existing:
            conn.execute("UPDATE verdicts SET decision = ?, notes = ?, updated_at = ? WHERE verdict_id = ?",
                         (decision, notes, now, existing["verdict_id"]))
            return existing["verdict_id"]
        verdict_id = new_id()
        conn.execute(
            """INSERT INTO verdicts (verdict_id, job_id, company_id, decision, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (verdict_id, job_id, company_id, decision, notes, now, now))
        return verdict_id


def get_verdict(conn, *, job_id=None, company_id=None):
    if job_id:
        return conn.execute("SELECT * FROM verdicts WHERE job_id = ?", (job_id,)).fetchone()
    return conn.execute("SELECT * FROM verdicts WHERE company_id = ?", (company_id,)).fetchone()
