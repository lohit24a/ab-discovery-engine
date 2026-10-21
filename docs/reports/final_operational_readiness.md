# Final operational readiness (2026-10-21)

Offline work only: no source was contacted, no Sheet was written, the production database was only read (SHA-256 `a7d81f0ec514...` before and after), no migration, no merge, no new review session, no change to publication limits.

## A. Baseline and final state

* Start: `main` = `origin/main` = `7e631d8`, clean tree; 948 passed / 1 skipped at that commit; schema 7, integrity ok, 0 foreign-key violations; 30,081 jobs, 11,004 companies, 5 verdicts (all from the TEST sheet, 2026-10-21 06:43-06:56, before LIVE existed); current session `2e05066b`; LIVE 24/19/23/1 rows, no human cells filled; limits 25/30/30/2.
* Source health unchanged: Accel partial; Lightspeed/Peak XV complete for the Bengaluru filter (company lists derived only); Techstars complete for its filter; 3one4 complete; Feashliaa complete (never closure evidence); yc_oss complete; yc_jobs incomplete; all collected 2026-10-20.
* Final: see section J and the guide's decision log for the commit.

## B. Audit F4 and the pending Runs row

* **F4 was never a fix.** The audit numbered candidates as it went; F4 was "source-health omits configured investor boards" and was withdrawn once the evidence showed the only such board with an attempt (Nexus) is disabled in the configuration, so its absence is correct. The audit therefore fixed **nine** defects (F1-F3, F5-F10), not ten; the audit report and the guide now say so.
* **The pending Runs row is not history.** It is the row a LIVE publication writes for itself. The read-only LIVE plan contains machine-cell corrections (now: 4 Score and 4 Explanation cells in Shortlist and in Posts), so applying it would be a publication and would add its own Runs row; its run id (`(assigned when the sync runs)`) does not exist until the write. Type: "LIVE publication (stored snapshots; no new source collection)". No historical or failed run is waiting: the newest LIVE sync (`869d62fa`) succeeded and has its row. It stays unpublished because no LIVE write is approved.

## C. Legacy TEST verdicts and LIVE ranking

* **Finding:** all 5 stored verdicts were entered in the TEST sheet. Three Yes (Prodigal FDE, Khatabook, dozee Founder's Office) and one Maybe (Seezo) added +25/+5 to LIVE scores although their LIVE Verdict cells are blank; Yes/Maybe also marked them "reviewed", so a new LIVE session would never offer them; the No (one job never published to LIVE) suppresses it. Since the audit the explanation named the verdict, but as "your stored verdict", which implies a LIVE entry.
* **Provenance is grounded:** LIVE human cells are only ever typed by the person (stored values are never copied into them), every LIVE sync now records which records carried a typed decision (`live_decisions`) and any typed value (`live_verdicts`) in its run summary, written in the same transaction as the import, also for failed or interrupted runs. Before LIVE existed no LIVE decision could exist (the two LIVE syncs imported nothing).
* **Correction (read-time, no migration, nothing rewritten):** a stored decision counts as a LIVE decision only if a LIVE sync recorded it as typed or confirmed in a LIVE Verdict cell. Otherwise it is *legacy*: a legacy Yes/Maybe earns no points, does not mark the record reviewed, and is shown as "an earlier TEST-sheet review said Yes (history only, not counted: decisions count when you enter them in LIVE)"; a legacy No keeps suppressing, with "(entered in the TEST sheet; it stays in force until you decide otherwise in LIVE)". Typing the same value again in LIVE confirms it as a LIVE decision (counted already in the plan, recorded at the write); a LIVE note alone never upgrades a legacy decision; a conflicting LIVE value replaces older memory and is reported as such; TEST still never imports and never receives LIVE values. The distinction is stored in SQLite, so it survives restart, backup/restore, retries and new sessions (tested).
* **Effect:** the four records' LIVE and TEST scores drop by 25/25/25/5 (116→91, 115→90, 113→88, 86→81); they stay qualified; in the next LIVE session Prodigal FDE, Khatabook and dozee are offered again as "previously unreviewed" (they were never reviewed in LIVE). The legacy No job stays out. The schema stays 7: a target-scoped verdict table is not needed for this policy.

## D. "Head - Business Finance"

* Job `b353d509`: Whatfix, "Head - Business Finance | 19681", Bengaluru, `https://whatfix101.hire.trakstar.com/jobs/fk0zp3h/`, seen only on the Peak XV (Consider) board, provider date 2026-05-25; no stored experience or description. It qualified (69, business/finance operations, strong) because the executive term list contains "head of" but not "Head - ".
* **Fix (narrow, title-only, in `filters.classify_experience`):** "Head" as the role ("Head of X", "Head - X", "Head, X", "<business unit> Head" such as Department/Business/Product/Regional/Category Head) is an executive signal; "head" in a reporting line ("reporting to the Head of Growth", "Chief of Staff to the Head of Sales", "Assistant to Department Head", "Office of the Head of ...") is not, which also corrects an existing false positive; headquarters/headcount/head office and descriptions never count. The "Lead" precedence policy is unchanged.
* **Stored jobs affected:** 37 change experience category (33 unknown→executive, 2 senior→executive, 1 early_career→executive, 1 executive→unknown); 3 change classification: Whatfix "Head - Business Finance" qualified 69 → held for review 29; Orange Health "Head - Supply Operations" exploration → low priority; assured "Chief of Staff to the Head of Sales" low priority → held for review (32). No other classification changed.

## E. BestDoc and repeated postings

* The two BestDoc "Associate Product Manager" records are distinct Getro listings `68906780` (posted 2026-03-01, apply `bestdocapp.com/careers#...`) and `41356702` (posted 2024-10-12, apply `careers/?job=2`); neither has an ATS requisition id. They stay separate records with their own sightings and URLs; nothing is merged.
* **Display-only rule (approved):** in a NEW session, never-shown postings with the same employer, normalised title and set of locations compete for one slot; the stronger one is offered (qualified before held-for-review, higher score, newer provider date) and its Shortlist row says "[N more posting(s) at this employer with the same title and location are kept in the database, not listed separately]"; the note persists in continued sessions. Records shown before or carrying a decision are never displaced; different location, title wording/level, role or employer are never folded (tested). Identity is untouched.
* **Effect on the next batch:** one row changes: the 2024 BestDoc listing is replaced by Niyo "Associate Product Manager - Customer Excellence"; the 2026 BestDoc listing is offered. The alternative can appear in a later session.

## F. Next LIVE batch capacity

`live-limits --next-session` (read-only, after the changes above): Shortlist 25 (fits 25), Posts 19 + 16 = **35**, Companies 23 + 14 = **37**, Runs 1 + 1 = 2. All new rows are admitted only by the shortlist rules; no duplicate ids; no rejected job; no human cell; no cleared Posts/Companies/Runs cell. **Companies needs 37, one more than the approved 36** (the legacy-verdict change brings Khatabook's job into the batch, so Khatabook no longer fills a company-only slot and SureBright, a new company, does). Following the instruction, **no limit was changed** (the file still says 25/30/30/2). Runs needs 2 for the next batch directly, 3 if the pending corrections are published first.

## G. Fresh source refresh (nothing requested)

| Source | Host / endpoint | Permission state | Scope | Last run | Requests (expected / cap) | Expected value |
|---|---|---|---|---|---|---|
| Accel (Getro) | `jobs.accel.com`, POST `api.getro.com/api/v2/collections/8672/search/jobs` | operator-attested, unverified (no Getro terms found) | `searchable_locations = Bengaluru, Karnataka, India` | 2026-10-20, PARTIAL 2,988/2,993 in 151 requests under a temporarily raised ceiling | about 152 / **cap 60 pages, 62 requests (code ceiling 60/120)**: a complete scan does not fit and needs an approved one-run raise | highest: 3.7% of its jobs qualify, 91 qualified jobs only it carries, ~460 of 2,988 posted within 7 days (about 66 a day), so roughly 2-5 new qualified jobs per day of age |
| 3one4 (Getro) | `jobs.3one4capital.com`, `api.getro.com` network 51275 | as above | whole board | 2026-10-20 complete, 660 jobs in 33 requests | about 34 / 41 | low: 0.8% qualify, ~11 new postings a day |
| Techstars (Getro) | `jobs.techstars.com`, `api.getro.com` network 89 | as above; robots crawl-delay 10 s honoured | Bengaluru filter | 2026-10-20 complete, 87 jobs in 5 requests | about 5 / 27 | very low: 4 postings in 7 days |
| Feashliaa | `riley-dorrington.github.io/job-board-data/data/chunks/` (manifest + 58 gz chunks) | public dataset | full feed, India/title prefilter | 2026-10-20 (snapshot of 2026-10-19), later run skipped as unchanged | about 59 GETs | daily snapshot; 126 qualified jobs (100 only here); 0.5% qualify |
| YC companies | `yc-oss.github.io/api/meta.json`, `/companies/all.json` | public dataset | all, India relevance | 2026-10-20 complete | 2 GETs | Stream B companies; few changes per day |
| Lightspeed, Peak XV (Consider) | Consider token + cursor API | **disabled in config** (Consider terms 4(l); permission not confirmed) | - | 2026-10-20 | - | not available until permission is confirmed |
| YC job pages | `ycombinator.com/companies/<slug>/jobs` | user-attested, unverified | per-company | 2026-10-20 incomplete | `--plan` first | low; separate approval |

Rules for every refresh: sequential, at least 2 s apart (Techstars 10 s), hard page/request caps, no retries, stop on 403/429, no login/proxy/evasion; collection writes a git-ignored snapshot only; a scan is complete only if the whole filtered scope was traversed and the count matched; import is a separate dry-run then `--apply` with a verified backup; a partial or failed scan never closes or ages a job. Measure success as new qualified jobs not already stored, not feed size. Feashliaa and YC run through `/run-discovery yc,feashliaa` (the run writes SQLite after its own approval).

## H. Description enrichment

* No description text exists in any stored or cached evidence: Getro/Consider snapshots carry seniority, posting date and work mode; the Feashliaa cache carries an aggregator `skill_level`, salary bands and first-seen; YC job pages carry company-level listings; there are no manual inputs. Experience is unknown for 233 of the 296 strongest candidates and for 11 of the 20 jobs in the next batch.
* **Pilot proposal (not run):** the 13 next-batch jobs hosted on Greenhouse (9) and Lever (4), read through their publicly documented job-board APIs (`boards-api.greenhouse.io/v1/boards/<board>/jobs/<id>`, `api.lever.co/v0/postings/<site>/<id>`): 13 GETs, sequential, 2 s apart, no retries; results to a git-ignored snapshot only (no database write, no migration); measure how many state years or seniority and how many classifications would change. A page that loads proves nothing about the vacancy. Storage (a migration) is justified only if the pilot shows classification changes worth keeping.

## I. Benchmark (frozen Day 13 fixture, unchanged)

| Metric | Day 14 | Post-audit | Now |
|---|---|---|---|
| Confirmed automatically discovered | 6/11 | 6/11 | 6/11 |
| Probable | 2 | 2 | 2 |
| Not found | 3 | 3 | 3 |
| Negative controls shortlisted | 0/5 | 0/5 | 0/5 |
| Confirmed finds in the clean top 20 | 1 | 1 | **0** |

The only per-item change: A07 (Prodigal "Strategy and Operations Analyst", score 82, unchanged) leaves the clean and next-session top 20. Cause: Prodigal's legacy-Yes "Forward Deployed Engineer" (91) is now offered as LIVE-unreviewed and, with "AI Deployment Lead (Bengaluru)", fills Prodigal's per-employer cap of 2. This is the approved verdict policy, not a scoring change; no score was adjusted for the benchmark. No new collection occurred, so discovery counts did not change.

## J. Reliability, recovery, backup and isolation

New tests (fake sheets, disposable databases): 38 in `tests/test_final_readiness.py` (legacy Yes/Maybe/No, LIVE decision, confirmation, note-only, interrupted sync, restart and backup restore, new-session lanes; 9 head titles positive, 8 negative, description mention, Lead precedence; diversity benefit, 4 false-suppression guards, shown and decided records never displaced, stronger posting preferred) and board selection refusing Consider boards; the end-to-end acceptance test now also covers legacy history, the look-alike rule and LIVE-decision provenance. All post-audit fixes remain covered (exact saved-plan match, Shortlist shrink after No, deletion refusal elsewhere, interrupted write and Runs repair, new session only inside the approved write, failed/stale source wording, invalid verdict text, approval status). The full results are in the guide entry.

## K. Real TEST and LIVE (read-only)

* TEST dry-run (continue `2e05066b`): 0 rows; 8 machine cells in Shortlist and 8 in Posts (Score and Explanation of the four legacy-verdict records); 0 human cells; 0 imports; 0 conflicts or warnings.
* LIVE plan (continue): the same 8 + 8 cells, plus its own Runs row; 0 violations; inspection 24/19/23/1, no problems, no duplicate or unknown ids, no human cells filled; no hidden gate additions (Posts admitted only by shortlist).
* LIVE next session (`--new-session`, read-only): 25 Shortlist rows, 16 Posts + 14 Companies rows (shortlist rules only), 1 Runs row; refused under current limits (Posts 35 > 30, Companies 37 > 30). Previewing recorded no session.

## L. Approvals still needed (one consolidated request)

1. **Capacity:** approve Posts=35, Companies=37 (not 36), Runs=3; or keep the current limits.
2. **LIVE write:** either publish the pending corrections in the current session, or skip them and publish the next session directly (it includes them).
3. **Source refresh:** 3one4 (about 34 requests) and Techstars (about 5) within existing caps; Accel complete Bengaluru scan only with a one-run raise to about 155 pages/requests; Feashliaa + YC via `/run-discovery yc,feashliaa`; each import separately.
4. **Enrichment pilot:** 13 GETs (Greenhouse 9, Lever 4), snapshot only.
5. **Consider:** confirm Consider-level permission to re-enable Lightspeed/Peak XV, or keep them disabled.

Genuine limitations: discovery is bounded by source coverage and freshness (all data from 2026-10-20); descriptions and stated experience are mostly missing; a complete Accel scan is hard while the board changes; the four Consider-only LIVE rows stay as recorded history.

## M. The next useful discovery session

1. Open the project in Claude Code: `/run-discovery existing` (stored data only) and read source health.
2. Decide the approvals in L; for fresh data, approve the source refresh, then each import.
3. Run `/run-discovery existing` again, ask for the next batch: Claude shows `live-limits --next-session`, then the `live-publish --new-session` plan, and stops.
4. Approve that plan in a new message; Claude backs up and publishes, then shows a zero-change repeat plan.
5. Review in LIVE: Yes / Maybe / No and notes. Older TEST decisions are shown only as history.
