# Migration 7 (manual_inputs): production plan — NOT APPLIED, awaiting separate approval

## Why it is necessary
Jobs have no description column. A saved plaintext description feeds role and experience evidence, and the person's hints and
notes must keep their provenance (user-supplied, never verified). That text has to live inside the backed-up database, not in
loose files. Everything else the importer does (URL rows, links.csv rows without text, attaching to a known ATS posting) would
work without it, but then supplied hints and notes would be lost, so `--apply` refuses on schema 6. Dry runs, ranking and the
Sheets sync all work at schema 6 today (`schema.operable`).

## What it changes
One new table, `manual_inputs` (append-only triggers; `user_supplied` forced to 1 and `verified` forced to 0 by CHECK constraints),
one index, `PRAGMA user_version = 7`. No existing table, column, row, trigger or index is touched.

## Rehearsal evidence (restored copy of the real production backup, 2026-10-21)
* SQLite online-backup API copy: 78.3 MB in 1.5 s; integrity ok; 0 foreign-key violations; row counts of all 10 tables equal production.
* After migration: user_version 7, integrity ok, 0 foreign-key violations, every pre-existing table byte-identical (row count + ordered
  row digest), only new table `manual_inputs` (0 rows). A second `init-db` is a no-op.
* Ranking and the shortlist are identical on production (schema 6) and the migrated copy.
* Fictional manual import then replay on the migrated copy: 4 created, 1 rejected; replay refreshes only; no pre-existing row changed
  (companies, jobs, sightings, status_events, identity_links, runs: 0 differences); no verdict created.
* Production file hash was unchanged by the rehearsal.

## Exact steps to run after you approve (about 5 minutes)
1. Confirm nothing else is using the database. Record `sha256` of `data/discovery.db` and `python -m discovery doctor` (expect: schema 6 ok).
2. Backup (SQLite online backup, integrity-checked):
   `python -c "from discovery.adapters import jobspy_source as j; from discovery import database as db; print(j.verified_backup(db.resolve_db_path(db.PROJECT_ROOT), label='pre-migration-7'))"`
3. Restore test: copy that backup to a temporary file, open it read-only, check `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, and that
   the row counts of all 10 tables equal production. Stop if anything differs.
4. Apply: `python -m discovery init-db` (the migration runs in one transaction together with the version bump; a failure leaves the file unchanged).
5. Verify: `python -m discovery doctor` (schema 7); `PRAGMA integrity_check`; `PRAGMA foreign_key_check`; every pre-existing table's row count and
   ordered digest equal to step 1; `manual_inputs` has 0 rows; `python -m discovery sync --target test --dry-run --with-shortlist` shows no changes
   (continuing the current session).
6. Rollback if needed: close all processes, copy the step-2 backup over `data/discovery.db`. Because the migration is additive, simply not
   using the new table is also safe.

Nothing is imported by this migration. A production manual import is a further, separate approval: `import-manual` dry run first, then
`--apply` (verified backup again).
