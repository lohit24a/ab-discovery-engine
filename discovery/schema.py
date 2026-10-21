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

# v3: allow a note without a verdict (decision NULL = not yet reviewed). SQLite cannot relax a
# column constraint in place, so verdicts is rebuilt; nothing references it, and all rows are copied.
# A row must still carry something: a verdict, a non-blank note, or both.
MIGRATION_3 = """
BEGIN;

CREATE TABLE verdicts_new (
    verdict_id  TEXT PRIMARY KEY,
    job_id      TEXT REFERENCES jobs(job_id) ON DELETE RESTRICT,
    company_id  TEXT REFERENCES companies(company_id) ON DELETE RESTRICT,
    decision    TEXT CHECK (decision IS NULL OR decision IN ('Yes', 'Maybe', 'No')),
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    CHECK ((job_id IS NULL) != (company_id IS NULL)),
    CHECK (decision IS NOT NULL OR length(trim(notes)) > 0)
);
INSERT INTO verdicts_new (verdict_id, job_id, company_id, decision, notes, created_at, updated_at)
    SELECT verdict_id, job_id, company_id, decision, notes, created_at, updated_at FROM verdicts;
DROP TABLE verdicts;
ALTER TABLE verdicts_new RENAME TO verdicts;
CREATE UNIQUE INDEX ux_verdicts_job ON verdicts(job_id) WHERE job_id IS NOT NULL;
CREATE UNIQUE INDEX ux_verdicts_company ON verdicts(company_id) WHERE company_id IS NOT NULL;

PRAGMA user_version = 3;
COMMIT;
"""

# v4: provider provenance on sightings, kept separate from OUR first/last seen:
#   provider_first_seen_at - when the provider first reported the record
#   provider_freshness     - how the latest snapshot treated it (latest_collection / provider_retained / unknown)
#   snapshot_at            - publication time of the newest snapshot that contained it
# (provider_observed_at, added in v1, holds the provider's last observation of the record.)
# Purely additive; existing rows keep NULLs.
MIGRATION_4 = """
BEGIN;

ALTER TABLE sightings ADD COLUMN provider_first_seen_at TEXT;
ALTER TABLE sightings ADD COLUMN provider_freshness TEXT
    CHECK (provider_freshness IS NULL OR provider_freshness IN ('latest_collection', 'provider_retained', 'unknown'));
ALTER TABLE sightings ADD COLUMN snapshot_at TEXT;

PRAGMA user_version = 4;
COMMIT;
"""

# v5 (Day 11): reviewable identity decisions. A confirmed link says "alias is the same real job/company as
# canonical"; a possible link records a candidate for review; a rejected link records a human "not the same"
# so it is never proposed again. Nothing is deleted or rewritten by creating a link; merging (merge.py) moves
# sightings/jobs to the canonical record and keeps the alias row, its events and its history. Purely additive.
# No foreign keys: the ids point into jobs or companies depending on entity_type (checked in code).
MIGRATION_5 = """
BEGIN;

CREATE TABLE identity_links (
    link_id      TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL CHECK (entity_type IN ('job', 'company')),
    canonical_id TEXT NOT NULL,
    alias_id     TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('confirmed', 'possible', 'rejected')),
    tier         TEXT NOT NULL CHECK (tier IN ('A', 'B', 'C', 'D')),
    rule         TEXT NOT NULL CHECK (length(trim(rule)) > 0),
    evidence     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(evidence)),
    decided_by   TEXT NOT NULL CHECK (length(trim(decided_by)) > 0),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    CHECK (canonical_id <> alias_id),
    UNIQUE (entity_type, canonical_id, alias_id)
);
-- An alias can be confirmed into at most one canonical record.
CREATE UNIQUE INDEX ux_identity_alias_confirmed ON identity_links(entity_type, alias_id) WHERE status = 'confirmed';
CREATE INDEX ix_identity_status ON identity_links(entity_type, status);
CREATE TRIGGER trg_identity_links_no_delete BEFORE DELETE ON identity_links
BEGIN SELECT RAISE(ABORT, 'identity_links is a history of decisions and cannot be deleted'); END;

PRAGMA user_version = 5;
COMMIT;
"""

# v6 (Day 11 hardening): every identity decision keeps the rule version and a review status, and a separate table holds
# RELATIONSHIPS that are not identity (a parent/subsidiary/legacy-brand candidate, the same posting under different employer
# labels, two postings of one requisition, a placeholder source label). Relations are review/presentation records only:
# nothing reads them to merge, close or suppress a record. Purely additive; existing links are marked as operator-reviewed.
MIGRATION_6 = """
BEGIN;

ALTER TABLE identity_links ADD COLUMN rule_version TEXT;
ALTER TABLE identity_links ADD COLUMN review_status TEXT NOT NULL DEFAULT 'unreviewed'
    CHECK (review_status IN ('unreviewed', 'reviewed'));
-- The 156 Tier-A links created before this version were approved by the operator in chat.
UPDATE identity_links SET review_status = 'reviewed', rule_version = 'day11-v1' WHERE status = 'confirmed' AND rule_version IS NULL;

CREATE TABLE identity_relations (
    relation_id   TEXT PRIMARY KEY,
    entity_type   TEXT NOT NULL CHECK (entity_type IN ('job', 'company')),
    kind          TEXT NOT NULL CHECK (kind IN ('related_company', 'same_posting_different_label',
                                                'same_requisition_different_posting', 'invalid_source_label')),
    left_id       TEXT NOT NULL,
    right_id      TEXT,
    status        TEXT NOT NULL CHECK (status IN ('candidate', 'verified', 'dismissed')),
    detail        TEXT NOT NULL DEFAULT '',
    rule          TEXT NOT NULL CHECK (length(trim(rule)) > 0),
    rule_version  TEXT NOT NULL,
    evidence      TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(evidence)),
    review_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK (review_status IN ('unreviewed', 'reviewed')),
    decided_by    TEXT NOT NULL CHECK (length(trim(decided_by)) > 0),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    CHECK (right_id IS NULL OR left_id <> right_id),
    CHECK ((kind = 'invalid_source_label') = (right_id IS NULL))
);
CREATE UNIQUE INDEX ux_identity_relation ON identity_relations(entity_type, kind, left_id, COALESCE(right_id, ''));
CREATE INDEX ix_identity_relation_status ON identity_relations(entity_type, status);
CREATE TRIGGER trg_identity_relations_no_delete BEFORE DELETE ON identity_relations
BEGIN SELECT RAISE(ABORT, 'identity_relations is a history of review decisions and cannot be deleted'); END;

PRAGMA user_version = 6;
COMMIT;
"""

# v7 (Day 12 manual import): the text and hints a person supplies by hand. Jobs have no description column, and a supplied
# description feeds role evidence, so it must live inside the backed-up database with its provenance: always user-supplied,
# never verified (both enforced by CHECK), append-only (a changed description is a new row; history is never rewritten).
# Purely additive. Not applied to production without the operator's approval; commands keep working at version 6.
MIGRATION_7 = """
BEGIN;

CREATE TABLE manual_inputs (
    input_id      TEXT PRIMARY KEY,
    sighting_id   TEXT NOT NULL REFERENCES sightings(sighting_id) ON DELETE RESTRICT,
    kind          TEXT NOT NULL CHECK (kind IN ('url_row', 'description_text', 'export_row')),
    sha256        TEXT NOT NULL CHECK (length(sha256) = 64),
    text          TEXT NOT NULL DEFAULT '',
    hints         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(hints)),
    origin        TEXT NOT NULL DEFAULT '',
    user_supplied INTEGER NOT NULL DEFAULT 1 CHECK (user_supplied = 1),
    verified      INTEGER NOT NULL DEFAULT 0 CHECK (verified = 0),
    created_at    TEXT NOT NULL,
    UNIQUE (sighting_id, kind, sha256)
);
CREATE INDEX ix_manual_inputs_sighting ON manual_inputs(sighting_id, created_at);
CREATE TRIGGER trg_manual_inputs_no_update BEFORE UPDATE ON manual_inputs
BEGIN SELECT RAISE(ABORT, 'manual_inputs is append-only'); END;
CREATE TRIGGER trg_manual_inputs_no_delete BEFORE DELETE ON manual_inputs
BEGIN SELECT RAISE(ABORT, 'manual_inputs is append-only'); END;

PRAGMA user_version = 7;
COMMIT;
"""

MIGRATIONS = [MIGRATION_1, MIGRATION_2, MIGRATION_3, MIGRATION_4, MIGRATION_5, MIGRATION_6, MIGRATION_7]
# Supplementary tables (not part of the original eight): added by later migrations.
SUPPLEMENTARY_TABLES = ("identity_links", "identity_relations", "manual_inputs")
SCHEMA_VERSION = len(MIGRATIONS)
# Oldest version the commands still operate on. Migration 7 (manual_inputs) is optional until the operator approves it;
# only the manual importer's --apply requires it.
MIN_OPERABLE_VERSION = 6


def operable(version):
    return MIN_OPERABLE_VERSION <= version <= SCHEMA_VERSION
