# Post-implementation audit (2026-10-21)

Scope: the whole working system after Day 16 (`aeac606`). Offline only: no source was contacted, no Google Sheet was written, the production database was only read (SHA-256 `a7d81f0ec514...` before and after), no migration, no merge, no restricted provider.

**Verdict: READY WITH LIMITATIONS.** Ten defects were found by reproduction. Two were high impact (P1) and are fixed: LIVE feedback could leak into TEST after an interrupted LIVE sync, and any LIVE "No" blocked every later publication. Each fix has a regression test that fails on the old code. What remains is limited by source coverage and by approvals only the operator can give (LIVE capacity, provider permissions, fresh collection).

## 1. Baseline (before any change)

| Item | Value |
|---|---|
| Git | `main` = `origin/main` = `aeac606`, clean tree |
| Runtime | Python 3.14.3; `requirements.txt` equals `pip freeze`; `python-jobspy==1.4.0`; `pip check` clean |
| Tests / doctor | 906 passed, 1 skipped (Windows symlink privilege); doctor 18/18 |
| Database | schema 7, WAL, integrity ok, 0 foreign-key violations; companies 11,004, jobs 30,081 (New 17,223, Unknown 12,858; none Closed/Not Seen), sightings 42,227, source checks 453, status events 42,939, identity links 156 (all tier A, reviewed), verdicts 5 (Yes 3, Maybe 1, No 1; all from TEST) |
| Sessions | current `2e05066b` (24 records); 9 sync runs (7 TEST, 2 LIVE); no LIVE-origin verdicts |
| Configuration | search_profile `56d6873c...`, ranking `fead5ed8...`, live_publication `5d46b379...` (25/30/30/2); benchmark fixture `a635b47e...` |
| Sources | feashliaa complete; Accel partial (2,988/2,993); Lightspeed and Peak XV complete for `locations = Bengaluru, Karnataka, India` (company lists derived only); Techstars complete for its Bengaluru filter; 3one4 complete; yc_oss complete; yc_jobs incomplete; Nexus disabled in config, its single probe failed (no CSRF token) and was never imported; Indeed/Naukri/LinkedIn disabled; all collected 2026-10-20 |
| Sheets (read-only) | TEST 24/33/224/31 rows, 5 verdicts, 2 notes; LIVE 24/19/23/1 rows, no human cells filled; no duplicate or unknown ids; both dry-runs planned 0 changes |
| Backups | 39 files, newest `discovery_day16-final_20261021-162410.db` sound with manifest |
| Benchmark (frozen Day 13) | confirmed 6/11, probable 2, not found 3, confirmed in clean top 20: 1, negative controls shortlisted 0/5 |
| Timings | ranking 22.5 s (29,959 jobs, 10,982 companies); TEST or LIVE dry-run 29-32 s including sheet reads; source-health 0.4 s; benchmark 36 s; backup 6.2 s for 78 MB; Feashliaa scan peak memory ~158 MB (stored Day 7 evidence) |

## 2. Findings and fixes

| ID | Priority | Problem | Evidence (reproduction) | Root cause | Fix |
|---|---|---|---|---|---|
| F1 | P1 | After a LIVE sync failed or was stopped *after* importing the person's verdict, the next TEST sync wrote that LIVE verdict and note into TEST's human cells | fake sheets: LIVE "Maybe"/"LIVE only", write fails, TEST sync restores `'Maybe'` and `'LIVE only'` | provenance (`live_verdicts`) was only written by a successful run's final summary, and only success/partial runs were read | provenance written in the same transaction as the import; kept in the failure summary; every sync run is read |
| F10 | P1 | After any LIVE "No", every later `live-publish` was refused ("Shortlist: cell ... would be cleared") | end-to-end test: mark a LIVE job No, re-plan: 8 violations | the Day 15 gate treated cleared cells of the regenerated Shortlist view (the list shrinks; a company row moves into a former job row) as deletions | cleared cells allowed only in the Shortlist tab; clearing anything in Posts, Companies or Runs is still refused |
| F2 | P2 | `live-publish --apply` re-planned at write time and wrote that plan without comparing it with the approved one | fake LIVE whose data changed between the last check and the write: the different plan was written | sync re-planned internally; no hook to check the final plan | `before_write` gate: the plan about to be written must equal the approved plan and pass the limits, else nothing is written |
| F3 | P2 | The next batch could only start through a TEST write (`sync --new-session`), which advanced the shared session before LIVE capacity was checked; `live-limits` gave no actionable numbers | `live-publish` had no session option; preview printed only fits/exceeds | design gap from Day 15/16 | `live-publish --plan-output ... --new-session` (the session starts only inside the approved write); `live-limits` prints rows now / new / after / approved, the smallest sufficient limits and the exact command, and the consequences |
| F8 | P2 | If a publication's last write (the Runs row) failed or its response was lost, the retry had nothing else to write and never recorded the publication in LIVE Runs | lost response on the Runs header/row: retry left Runs empty | the Runs row was written only when the retry itself changed data rows | an interrupted LIVE publication with no Runs row is recorded by the next LIVE sync, with "Completes the publication interrupted in run ..." |
| F9 | P2 | A job from a source whose newest attempt failed was still "listed in the latest X check" with no caveat; a stale but complete source appeared as "(complete)" in the attention line that the LIVE Runs row quotes | end-to-end: HTTP 403 after an older success; render of a stale complete source | availability uses the newest successful run (correct) but the text did not mention the later failure | explanation adds "[the source's newest attempt (date) failed; this listing is from its earlier successful check]" (no score change); attention line says "(complete, stale)" |
| F5 | P2 | In LIVE, Khatabook 115, Prodigal 116 and dozee 113 carried +25 from older TEST verdicts with a blank LIVE verdict cell and no visible reason | explanations are cut at 600 characters and the verdict text came last (Maybe had none) | ordering of explanation facts | the stored verdict is named first: "your stored verdict is Yes (+25 points)" (jobs and companies; weights unchanged) |
| F6 | P2 | A typo such as "Yess" in a LIVE verdict cell blocked publication with only "1 warning(s)" | check_limits message | message did not include the warning | the violation quotes the warnings |
| F7 | P2 | `ranking-preview` said weights are "PROPOSALS awaiting operator review" although both files are approved | preview output | fixed sentence | the note states the status read from the files |

Real-data effect (read-only dry-runs after the fixes): TEST and LIVE would each update 4 Explanation cells (the four verdict-bearing jobs; text moved to the front), LIVE would add 1 Runs row for that publication, no rows added, no human cells, no conflicts, no limit violations. These cells are not written: that needs the operator's approval of a plan.

## 3. Tests added (all offline; fake sheets; disposable databases)

* `tests/test_audit_fixes.py` (21): one or more regression tests per fix; 16 of them fail on the pre-audit code (as do 2 resilience cases and the end-to-end test: 19 in all), the other 5 are guards that must hold either way.
* `tests/test_audit_sync_resilience.py` (18): a write that reaches the sheet but loses its response, at every write position, for TEST (6) and LIVE (8): no duplicate rows, exactly one Runs row, the next dry-run plans nothing; stale saved plans (session change, sheet change, violations, no authorisation) are refused; a record merged away after publication makes the sync fail closed without moving feedback.
* `tests/test_audit_e2e.py` (1, end to end): fresh-process CLI on an isolated database (init-db, doctor, source-health, ranking-preview, backup, backup-verify with restore) plus the real ranking and the real `live-publish` plan/apply against a fake LIVE sheet, network blocked. Complete, partial, failed and stale sources; a cross-source duplicate resolved by requisition id; an unresolved look-alike kept separate; Bengaluru, India-remote, foreign, senior and non-target jobs; two Stream B companies without jobs; legacy Yes/Maybe/No. Verifies the first publication, replay without change, a LIVE No and note (dropped without replacement, Posts row kept, nothing blocked), TEST isolation, an interrupted write completed and recorded once, an explicit new session, refusal under the shipped limits, no Closed/Not Seen, no merges, identical verdicts after a restore, restart on the restored copy.
* `tests/test_day16_backups.py` (+2): an interrupted backup's `.partial` file is never listed or verified as a backup; a backup without a manifest is still integrity-checked and restore-tested, and damage is caught.

## 4. Checked and deliberately not changed

| Area | Finding | Why no change |
|---|---|---|
| Eligibility | 0 qualified jobs without an Indian location (249 qualified: 234 Bengaluru, 15 explicit India-remote); 2,382 Bengaluru jobs excluded are all `NON_TARGET_ROLE` titles | correct behaviour |
| Seniority | 7 qualified titles with seniority words: 6 are "Chief of Staff" (a founder's-office role) or level "III"; 1 is "Head - Business Finance" with no senior warning | "Head" is not in the approved senior terms: subjective, recommended for review, not changed |
| Engineering vs AI implementation | 6 qualified titles look like engineering ("Applied AI and ML Engineer", "Software Engineer 2 - AI Transformation") | matched by approved aliases; subjective |
| Repeated identical titles | same employer + same title in one batch: 14 groups / 54 records in the qualified pool (mostly multiple real requisitions, e.g. 19 Instana postings); the next batch would list BestDoc "Associate Product Manager" twice (Getro ids 68906780 and 41356702) | selection policy (per-employer cap allows 2); recommended for approval: list one row per employer+title per batch |
| Foreign locations read as "unknown" | about 20 held-for-review jobs with explicit foreign cities (e.g. "Hybrid NYC / Remote (US Timezones)") are labelled unknown, not foreign | held for review, never qualified; low impact; a location-dictionary change is a filter-policy change |
| Remote without India | 231 held-for-review "Remote" jobs can reach the 3-slot uncertain lane in later batches | by design: labelled "location eligibility not confirmed" |
| Legacy TEST verdicts in ranking | stored Yes/Maybe still add points and reviewed records are not re-offered | consistent with the Day 16 policy that history stays intact; now visible (F5); a target-scoped verdict table still needs a migration and approval |
| Stale wall-clock age | ranking is reproducible (reference time = newest observation), so staleness against today is shown in source-health and the LIVE Runs row, and each Posts row has Last Seen | correct by design |
| DB fingerprint | `live-publish` hashes the main database file only (committed WAL pages are invisible until checkpoint) | every CLI process checkpoints on close; the fresh-plan and final-plan comparisons catch any change that matters |
| Lost response on the final Runs row | the sheet row says success while SQLite records the run as failed | cosmetic; no duplicate, data complete |
| Identity | merges only through `merge.py`, never called by discovery or import code; rejected pairs are respected; requisition and tenant scoping tested | correct |
| Security | TLS verification on; no proxies; secrets ignored by Git and absent from logs/exports; restricted sources disabled and refused (`all`, wildcards, Glassdoor); old `sync --target live` refused; the test-only `TEST_VERDICT_IMPORT_ENABLED` switch is False and set only by tests | no concrete vulnerability found |
| Performance | no operation over 40 s; dominated by ranking (22.5 s) | no material benefit from optimisation |
| Sources | Nexus is disabled and failed once: correctly absent from source-health; Accel partial; nothing stale yet (feeds 72 h, boards/YC 168 h) | correct; refreshing needs authorisation |

## 5. Discovery quality

The frozen benchmark is unchanged after the fixes (metrics and per-item CSV identical): confirmed 6 of 11, probable 2, not found 3, 1 in the clean top 20, 0 of 5 negative controls. The three misses are not in any collected source (Day 14 gap analysis); coverage, not ranking, is the binding constraint. All stored evidence is from 2026-10-20 and every job is "observed on source, not employer-verified". No new discovery is claimed. The wording fixes (F5, F9) make results more honest; they do not add opportunities.

Material improvement requires new collection, each step separately approved: (1) refresh Feashliaa and YC through `/run-discovery yc,feashliaa`; (2) refresh the Getro boards (Accel complete scan, Techstars, 3one4) with `investor-boards-collect --plan` first; (3) Lightspeed/Peak XV only after Consider-level permission is confirmed; (4) the deferred enrichment experiment (descriptions, stated experience) with its own plan.

## 6. Operator actions

* **Capacity:** the next batch does not fit 25/30/30/2 (`live-limits --next-session`: Posts 35, Companies 36, Runs 2). Approving Posts=35 and Companies=36 allows exactly that batch. Every publication adds a Runs row, so publishing the 4 Explanation updates first would make the batch need Runs=3; publishing the next batch directly includes those updates.
* **Permissions:** Consider-level permission for Lightspeed/Peak XV (four LIVE rows are Consider-only) remains unconfirmed.
* **Migration:** not needed for safety now; a target-scoped verdict table remains a future option.
* **Policy reviews (optional):** one row per employer+title per batch; "Head" as a senior term.
* **Real-world tests not performed:** the LIVE verdict round trip by a person and a reopen of the desktop app (waived on Day 16; automated substitutes in place).
