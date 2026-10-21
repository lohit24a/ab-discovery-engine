# Day 15 - Checkpoint 1: production readiness and first LIVE run preflight

> Historical preflight report written BEFORE the operator decisions of 2026-10-21. Superseded by the Day 15 closure entry in the implementation guide (shortlist-only scope; first LIVE publication done).

Status: **CONDITIONAL** (nothing written to LIVE or TEST; no collection; no production SQLite change; DB SHA-256 `b781c5f9fff3...` unchanged).

## A. Readiness
CONDITIONAL. The LIVE sheet is reachable, empty and correctly shaped, and a full dry-run plan exists. Four decisions are open (section J), and LIVE writes are still blocked in code (`open_backend` raises `LIVE_BLOCKED`), by design.

## B. Source table (stored evidence; no new request was made)
| Source | Exact scope of the latest check | State | Closure evidence | First-LIVE use |
|---|---|---|---|---|
| inv_accel (Getro) | jobs: location-filtered; 2,988 of 2,993 records read | **partial** | never | stored rows usable as "observed on source"; attested, unverified permission |
| inv_lightspeed (Consider) | jobs: `locations = Bengaluru, Karnataka, India`, complete for that scope; companies derived from jobs only (by design) | complete_filtered | never | usable; Consider terms need operator confirmation before further reads |
| inv_peakxv (Consider) | same as Lightspeed | complete_filtered | never | same |
| inv_nexus (Consider) | snapshot failed, never imported | failed, not in DB | never | not used |
| inv_techstars (Getro) | `searchable_locations = Bengaluru, Karnataka, India`, complete for that scope | complete_filtered | never | usable |
| inv_three_one4 (Getro) | whole board, complete | complete | never | usable |
| feashliaa | snapshot validated; no per-employer refresh evidence | complete (snapshot) | never | usable for discovery only |
| yc_oss | company dataset | complete | never | company-only items |
| yc_jobs | 52 scopes, 42 returned zero without proof | incomplete | never | few jobs; absence proves nothing |
| Indeed / Naukri / LinkedIn | not run | restricted; need separate approval | never | excluded |

The earlier mismatch (Lightspeed/Peak XV shown "partial" by Day 14 health versus "complete" in the scan logs) was a reporting defect: their company scope is partial by design. Fixed now: `source-health` shows per-scope-kind states and `derived_only`; the job-scan state is no longer masked. Nothing here ever lets a job be closed.

## C. First-run proposal
Recommended: **existing-data-only** (A: read stored SQLite evidence; B: no new collection; C: publish the continuing review session `2e05066b` to LIVE). It adds no network request to any source and no new Consider/Getro access. A fresh collection is optional and separately approvable later.

## D. LIVE access
Read-only open succeeded (scope `spreadsheets.readonly`). Tabs Shortlist, Posts, Companies, Runs present; each has a blank header row and no data rows; no problems. The service account has editor capability on LIVE and TEST (Drive metadata, no write). Writes need the unlocked LIVE path plus your approval.

## E. Proposed shortlist (continuing session, 24 rows: 16 new jobs, 3 uncertain jobs, 5 company-only)
Identical to the TEST session, scores 67-116. Five rows carry verdicts (Yes x3: Khatabook, Prodigal, dozee; Maybe: Seezo; one No on a non-listed job). Four rows are seen only on Consider boards (Khatabook, Stable Money, apna, composio). All wording remains "observed on source, not employer-verified". The alternative `--new-session` plan lists 25 rows and deprioritises the TEST session's records; sessions are global, so it is not recommended for the first run.

## F. LIVE dry-run (continuing session; `exports/day15_live_dryrun_plan.json`)
- Headers written: Shortlist, Posts, Companies, Runs.
- Shortlist: 24 rows (163 machine cells). Posts: 20 rows (19 shortlist, 1 publication gate), including re-adding the 5 verdict-holders with their stored verdicts. Companies: **224** rows (5 shortlist + 219 from the older publication gate: the YC Bengaluru subset, same as TEST). Runs: 31 rows.
- Deletions 0; human cells overwritten 0; verdicts restored into blank cells 5; SQLite verdict imports 0; conflicts 0; warnings 0.
- Difference from TEST: Posts 20 vs 33 (13 legacy Meesho rows are not re-added).
- The 224-row Companies write is far more than "20-30 worthwhile items": decision J2.

## G. TEST differences
TEST dry-run: 0 rows; 25 Score/Explanation cells in Shortlist and 25 in Posts differ (10 scores -3, 15 explanations: the approved Day 14 changes). Recommendation: apply them to TEST first as a rehearsal (TEST-only, verdicts untouched), then LIVE.

## H. Backup and rollback
Before an approved LIVE run: `verified_backup` (online backup + integrity check) into `backups/`, restore test on a temporary copy, record fingerprint. A LIVE sync would add one `sync` run row (with `session`/`listed`) and import no verdicts. A partial Sheets write is healed by re-running the idempotent sync (record_id keyed); a SQLite restore does not revert the Sheet. Caveat: the verdict store and last-sync pointer are shared by TEST and LIVE. A user-facing `backup` command does not exist yet (Day 16).

## I. Verification
856 passed, 1 skipped (Windows symlink privilege); `pip check` clean; doctor 18/18; DB integrity ok, 0 FK violations, schema 7; hash unchanged. Git: modified `discovery/source_health.py`, `tests/test_day14_health.py`; new `discovery/live_preflight.py`, `tests/test_day15_live_preflight.py`, this report; nothing committed.

## J. Approvals needed before the first real LIVE run
1. Choose existing-data-only (recommended) or approve a separately planned collection.
2. Decide the Companies scope for LIVE: accept all 224 (TEST parity) or limit the first LIVE to the 5 shortlisted plus verdict-holders (needs a small code change and its own test).
3. Confirm Consider-level permission for publishing the four Consider-only rows, or exclude them.
4. Waive or schedule the checklist item "pagination complete for enabled boards" (Accel partial, Nexus failed).
5. Authorize unlocking the LIVE guard for exactly one sync, after a fresh verified backup, plus a TEST-first rehearsal decision.
