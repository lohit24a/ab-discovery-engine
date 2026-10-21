# Day 16 - open operational questions

## A. Consider-only jobs in LIVE (Khatabook, Stable Money, apna, composio)
* **Fact:** each of these four listed jobs has sightings only from Consider-hosted boards (Lightspeed, Peak XV). Consider's terms (4(l)) prohibit automated access without its express written permission; the earlier reads rest on the operator's user-attested, unverified authorization. The operator has not yet confirmed Consider-level permission.
* **What was and was not done:** the rows already in LIVE come from stored snapshots (no new request was made on Day 15 or Day 16). Provenance and history stay intact in SQLite (`sightings.source_id`, the snapshot records) and no row was changed or removed.
* **Handling today:** each is labelled as observed on a source, not employer-verified; the Runs row says nothing was contacted. They remain displayed (a displayed observation is not a new request), but the operator can ask for them to be hidden from the next shortlist.
* **Needed before any further Consider read or import:** a written statement from the operator that Consider-level (or the investor's, as the platform's customer) permission exists and covers automated reads, with its reference if any; until then no new Consider-hosted request, and the existing observations are not extended or refreshed. If permission is NOT confirmed, the cautious option is to stop showing Consider-only rows in future shortlists; that is a ranking/publication decision for the operator, and existing history is still never deleted.

## B. One verdict store shared by TEST and LIVE (possible feedback leakage)
**Status: mitigated in code on Day 16 without a migration (operator-approved interim policy); the target-scoped table below stays a documented future option, with no migration number assigned until it is approved.**

Implemented safeguard (`sheets_sync.py`, tests in `tests/test_day16_verdict_isolation.py`): LIVE is the authoritative review interface. A TEST sync never imports verdicts or notes (a difference or edit is ignored with a warning; `TEST_VERDICT_IMPORT_ENABLED = False` is fail-closed and exists only for unit tests) and never writes into TEST a decision whose store value came from LIVE (each LIVE sync records the record ids it imported in its run summary, `live_verdicts`). A LIVE sync never copies stored decisions into LIVE cells, imports what the person typed, and reports a change to older review memory as "replaces earlier review memory". A blank cell never erases a decision, so a rejected record stays rejected unless the person changes it in LIVE. Plans and dry-runs never write verdicts. The original analysis (kept for the record) follows.

* **Mechanism:** `verdicts` has one row per job/company (no target). Both sheets read and write it, and "last sync" is global.
* **Effects (by reading the code, `reconcile_human`):**
  1. A verdict typed in LIVE on a job also shown in TEST is imported; the next TEST sync **restores it into TEST's blank cell** (a write to a TEST human cell). TEST verdicts are never copied into LIVE (Day 15 rule), so the leak is one-directional unless both sheets hold a value.
  2. If the two sheets hold **different** values for the same record, each sync imports the sheet's value over the other's: the last sync wins and the store flips back and forth. No conflict is flagged because the stored value is older than the last sync.
  3. Ranking uses the stored verdict (a No drops the record from every shortlist).
* **Safest practical rule now (no code, no migration):** treat TEST as a rehearsal only. After LIVE verdicts begin, do not sync TEST (or keep TEST verdict cells empty) and never type different verdicts for the same record in the two sheets.
* **Smallest durable design (needs the operator's approval; NOT implemented):** migration 8 adds a `target` column to `verdicts` (values `test`/`live`, existing rows = `test`), unique on (target, job_id/company_id); `sync` reads and writes only its own target; ranking considers a verdict from either target as "seen" but only `live` ones as decisions; no data is rewritten and TEST history stays as is. Needs: backup, migration test on a copy, restore test, and the operator's explicit approval before applying to production.

## C. Still deferred (unchanged)
Enrichment and migration 8 for descriptions; the 58 Workable candidate pairs; Apna and Sarvam identity promotions; Keka/Workable/other ATS integrations; new restricted JobSpy runs; investor-board backfills.
