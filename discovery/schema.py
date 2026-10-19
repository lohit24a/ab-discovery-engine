"""SQLite schema and migrations.

Versioning uses `PRAGMA user_version`: MIGRATIONS[n] upgrades a database from
version n to n + 1. Each migration runs inside a single transaction together
with its user_version bump, so a failed migration leaves the file unchanged.
Migrations only ever add structure; they never delete records.
"""

LIFECYCLE_STATES = ("New", "Active", "Changed", "Not Seen", "Closed", "Reopened", "Unknown")

APP_TABLES = (
    "companies",
    "jobs",
    "sources",
    "sightings",
    "runs",
    "source_checks",
    "status_events",
    "verdicts",
)

_STATES_SQL = ", ".join(f"'{s}'" for s in LIFECYCLE_STATES)

MIGRATION_1 = f"""
BEGIN;

CREATE TABLE companies (
    company_id        TEXT PRIMARY KEY,
    name              TEXT NOT NULL CHECK (length(trim(name)) > 0),
    normalized_name   TEXT NOT NULL,
    domain            TEXT CHECK (domain IS NULL OR length(domain) > 0),
    aliases           TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(aliases)),
    location          TEXT,
    facts             TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(facts)),
    identity_status   TEXT NOT NULL DEFAULT 'provisional'
                      CHECK (identity_status IN ('verified', 'provisional', 'unverified')),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    CHECK (identity_status != 'verified' OR domain IS NOT NULL)
);
CREATE UNIQUE INDEX ux_companies_domain ON companies(domain) WHERE domain IS NOT NULL;
CREATE INDEX ix_companies_normalized_name ON companies(normalized_name);

CREATE TABLE jobs (
    job_id               TEXT PRIMARY KEY,
    company_id           TEXT NOT NULL REFERENCES companies(company_id) ON DELETE RESTRICT,
    title                TEXT NOT NULL CHECK (length(trim(title)) > 0),
    normalized_title     TEXT NOT NULL,
    location             TEXT,
    remote_eligibility   TEXT NOT NULL DEFAULT 'unknown'
                         CHECK (remote_eligibility IN ('unknown', 'eligible', 'possible', 'incompatible')),
    experience_min_years REAL CHECK (experience_min_years IS NULL OR experience_min_years >= 0),
    experience_max_years REAL CHECK (experience_max_years IS NULL OR experience_max_years >= 0),
    experience_text      TEXT,
    lifecycle_state      TEXT NOT NULL DEFAULT 'New' CHECK (lifecycle_state IN ({_STATES_SQL})),
    posting_url          TEXT,
    posted_at            TEXT,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX ix_jobs_company ON jobs(company_id);
CREATE INDEX ix_jobs_state ON jobs(lifecycle_state);
CREATE INDEX ix_jobs_normalized_title ON jobs(normalized_title);

CREATE TABLE sources (
    source_id         TEXT PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    source_type       TEXT NOT NULL
                      CHECK (source_type IN ('ats_feed', 'aggregator', 'yc', 'investor_board', 'manual', 'other')),
    url_or_identifier TEXT,
    enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    config_ref        TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE sightings (
    sighting_id          TEXT PRIMARY KEY,
    source_id            TEXT NOT NULL REFERENCES sources(source_id) ON DELETE RESTRICT,
    job_id               TEXT REFERENCES jobs(job_id) ON DELETE RESTRICT,
    company_id           TEXT REFERENCES companies(company_id) ON DELETE RESTRICT,
    external_id          TEXT CHECK (external_id IS NULL OR length(external_id) > 0),
    source_url           TEXT NOT NULL CHECK (length(source_url) > 0),
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    provider_observed_at TEXT,
    content_hash         TEXT,
    CHECK ((job_id IS NULL) != (company_id IS NULL))
);
-- A provider ID identifies one record per source; distinct requisitions have distinct IDs.
CREATE UNIQUE INDEX ux_sightings_external
    ON sightings(source_id, external_id, (job_id IS NOT NULL)) WHERE external_id IS NOT NULL;
CREATE UNIQUE INDEX ux_sightings_url
    ON sightings(source_id, source_url, (job_id IS NOT NULL)) WHERE external_id IS NULL;
CREATE INDEX ix_sightings_job ON sightings(job_id);
CREATE INDEX ix_sightings_company ON sightings(company_id);
CREATE INDEX ix_sightings_source_seen ON sightings(source_id, last_seen_at);

CREATE TABLE runs (
    run_id        TEXT PRIMARY KEY,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    run_type      TEXT NOT NULL CHECK (run_type IN ('discovery', 'import', 'sync', 'maintenance')),
    result        TEXT NOT NULL DEFAULT 'running'
                  CHECK (result IN ('running', 'success', 'partial', 'failed', 'interrupted')),
    summary       TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(summary)),
    error_detail  TEXT
);
CREATE INDEX ix_runs_started ON runs(started_at);

CREATE TABLE source_checks (
    check_id          TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
    source_id         TEXT NOT NULL REFERENCES sources(source_id) ON DELETE RESTRICT,
    scope             TEXT NOT NULL DEFAULT '',
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    outcome           TEXT NOT NULL CHECK (outcome IN ('success', 'partial', 'failed', 'skipped')),
    complete          INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0, 1)),
    record_count      INTEGER NOT NULL DEFAULT 0 CHECK (record_count >= 0),
    source_snapshot_at TEXT,
    error_info        TEXT,
    -- Only a successful check can be complete; failed/partial/skipped never prove absence.
    CHECK (complete = 0 OR outcome = 'success'),
    UNIQUE (run_id, source_id, scope)
);
CREATE INDEX ix_source_checks_source ON source_checks(source_id, started_at);

CREATE TABLE status_events (
    event_id        TEXT PRIMARY KEY,
    job_id          TEXT REFERENCES jobs(job_id) ON DELETE RESTRICT,
    company_id      TEXT REFERENCES companies(company_id) ON DELETE RESTRICT,
    previous_state  TEXT CHECK (previous_state IS NULL OR previous_state IN ({_STATES_SQL})),
    new_state       TEXT NOT NULL CHECK (new_state IN ({_STATES_SQL})),
    occurred_at     TEXT NOT NULL,
    reason          TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    source_check_id TEXT REFERENCES source_checks(check_id) ON DELETE RESTRICT,
    sighting_id     TEXT REFERENCES sightings(sighting_id) ON DELETE RESTRICT,
    CHECK ((job_id IS NULL) != (company_id IS NULL))
);
CREATE INDEX ix_status_events_job ON status_events(job_id, occurred_at);
CREATE INDEX ix_status_events_company ON status_events(company_id, occurred_at);

-- Append-only history: events can never be edited or removed.
CREATE TRIGGER trg_status_events_no_update BEFORE UPDATE ON status_events
BEGIN SELECT RAISE(ABORT, 'status_events is append-only'); END;
CREATE TRIGGER trg_status_events_no_delete BEFORE DELETE ON status_events
BEGIN SELECT RAISE(ABORT, 'status_events is append-only'); END;

CREATE TABLE verdicts (
    verdict_id  TEXT PRIMARY KEY,
    job_id      TEXT REFERENCES jobs(job_id) ON DELETE RESTRICT,
    company_id  TEXT REFERENCES companies(company_id) ON DELETE RESTRICT,
    decision    TEXT NOT NULL CHECK (decision IN ('Yes', 'Maybe', 'No')),
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    CHECK ((job_id IS NULL) != (company_id IS NULL))
);
CREATE UNIQUE INDEX ux_verdicts_job ON verdicts(job_id) WHERE job_id IS NOT NULL;
CREATE UNIQUE INDEX ux_verdicts_company ON verdicts(company_id) WHERE company_id IS NOT NULL;

PRAGMA user_version = 1;
COMMIT;
"""

# v2: cross-source job identity (employer requisition ID) and closure-evidence guard.
MIGRATION_2 = """
BEGIN;

ALTER TABLE jobs ADD COLUMN requisition_id TEXT
    CHECK (requisition_id IS NULL OR length(trim(requisition_id)) > 0);
-- One requisition ID identifies one job at one company; identical titles stay distinct.
CREATE UNIQUE INDEX ux_jobs_company_requisition
    ON jobs(company_id, requisition_id) WHERE requisition_id IS NOT NULL;

-- Not Seen / Closed may only be recorded with a successful AND complete source check as
-- evidence. Failed, partial, skipped or missing evidence can never close a job.
CREATE TRIGGER trg_status_events_absence_evidence BEFORE INSERT ON status_events
WHEN NEW.new_state IN ('Not Seen', 'Closed') AND NOT EXISTS (
    SELECT 1 FROM source_checks
    WHERE check_id = NEW.source_check_id AND outcome = 'success' AND complete = 1)
BEGIN SELECT RAISE(ABORT, 'Not Seen/Closed requires a successful complete source check'); END;

PRAGMA user_version = 2;
COMMIT;
"""

MIGRATIONS = [MIGRATION_1, MIGRATION_2]
SCHEMA_VERSION = len(MIGRATIONS)
