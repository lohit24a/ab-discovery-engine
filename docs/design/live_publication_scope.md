# LIVE publication scope (Day 15)

Decided by the operator on 2026-10-21: **shortlist only**. Implemented as `sheets_sync.sync_to_sheets(..., scope="live")` and guarded by `discovery/live_publish.py` (`live-publish`).

## What LIVE shows
* **Shortlist**: the current review session (jobs and up to five company-only picks), at most 25 rows.
* **Posts**: only the shortlisted jobs (at most 30 rows). The older broad gate (every job of a publishable company) is never used. Unrelated Feashliaa, investor-board or YC jobs are never published just because their company is eligible. A job rejected with No is dropped from the session and is not published.
* **Companies**: the companies of the listed jobs plus the company-only (Stream B) picks, at most 30 rows. Stream B stays independent of hiring: a company with no open job is still listed. The other ~11,000 companies stay in SQLite for discovery and ranking; nothing is deleted or re-identified.
* **Runs**: one row per LIVE publication that changed something (at most 2 rows in total), typed "LIVE publication (stored snapshots; no new source collection)". It lists the source-health caveats at that time. The 31+ development/TEST runs stay in SQLite and are never backfilled; a historical backfill would be a separate explicit action.
* **Human cells**: LIVE's Your Verdict / Your Notes are only ever typed by the person. Stored verdicts (from TEST) are NOT copied into LIVE; what the person types in LIVE is imported as usual. (The verdict store is one table shared by TEST and LIVE: see Known Limitations.)

## How later review sessions add companies
A new session (`--new-session` on TEST, or the equivalent decision for LIVE) produces a new shortlist. The next `live-publish` run adds, for that shortlist only, its jobs to Posts and their companies plus the new company-only picks to Companies, within the same limits. Rows already in LIVE are kept and updated (machine cells only) and are never deleted, so the sheet grows by reviewed batches. Raising a limit is a code change and needs the operator's approval.

## Enforced in code (`live_publish.check_limits`)
Max 25/30/30/2 rows; no deletions or cleared cells; no write to a human-owned column; no stored verdict copied; every new row admitted only by the shortlist rules; zero conflicts and warnings; the saved plan must equal a fresh plan, the database fingerprint must be unchanged and a verified backup younger than 120 minutes must exist. `--authorized-first-live-trial` is required. The general guard (`sync --target live`, `open_backend('live')`) stays closed.

## Recovery
Partial writes: re-read LIVE and re-run `live-publish`; the sync is keyed by record_id, appends only missing rows and writes the Runs row last. A SQLite restore does not roll back the Sheet. The Sheet is never cleared to force idempotency.
