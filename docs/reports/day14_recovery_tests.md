# Day 14 source health and recovery report

All scenarios run offline on disposable SQLite files with frozen fixtures; no network, no Sheets (fake backend), no restricted source. Tests marked **new** were added on Day 14; the rest already existed and were re-run.

## 1. Source health (new, read-only)
`python -m discovery source-health` classifies each source's newest *collecting* run (a skipped check collected nothing and never replaces it) using `source_checks` and `runs`; no schema change.

| State | Meaning | Completeness confidence |
|---|---|---|
| complete | whole, unfiltered scope walked | high (low if stale) |
| complete_filtered | whole **requested** scope, but a filtered one (for example Bengaluru only) | scoped, never global |
| verified_empty | the source proved the scope empty (`complete = 1`, zero records) | scoped |
| unverified_zero | zero records, **not** proven empty | low: never read as "no jobs" |
| incomplete / partial | part of the scope was not walked (pagination, mismatch, caps) | low |
| blocked | 403, 429, CAPTCHA, refusal | none |
| network_error / schema_error / failed_other | timeout or connection; changed or unexpected shape; anything else | none |
| interrupted | the run ended as interrupted | low |
| skipped_unchanged | snapshot unchanged, nothing collected | n/a |
| no_checks | never collected | none |

Every row also states the age against a per-type stale threshold, the scopes, the record count, the error text, whether the source type could ever be closure evidence (`lifecycle.closure_capable`), and `may_close_jobs = False` always (health never closes anything).
Multi-scope runs (one scope per YC company) are judged on the scopes that returned data, with the zero-record scopes counted separately.
Live result on the frozen data: Feashliaa complete (snapshot only, never closure evidence), Accel / Lightspeed / Peak XV partial, Techstars complete but filtered, 3one4 and YC companies complete, YC job pages incomplete with 42 of 52 scopes returning zero unproven records.

## 2. Retry and stop policy (tested; one change made)
| Adapter | Transient errors (5xx, timeout, connection) | 403 / 4xx | 429 | Pacing and ceilings |
|---|---|---|---|---|
| Feashliaa feed, YC directory (public datasets) | up to 3 attempts, pauses 1 s then 2 s | stop at once | **stop at once (changed on Day 14; it used to retry)** | per-file size cap |
| YC job pages | none | stop host | stop host | at least 2 s, request and runtime caps |
| Investor boards | none | stop scan | stop scan | at least 2 s, page and request caps (retries and redirect hops would count) |
| Restricted JobSpy sources | none after a restriction | stop | stop | policy file limits, VPN preflight, single-use approval |
| Manual import | no network | n/a | n/a | n/a |
| Google Sheets API (not a job source) | 429 is retried after 20 s and 40 s because Google rejects the call unapplied | n/a | as left | batch limits |
| Proposed enrichment | none | stop host | stop host | 90-request ceiling, at least 2 s |

New tests: 403, 404 and 429 make exactly one request with no sleep for both public-dataset fetchers; timeouts and 503 use exactly three attempts with pauses of 1 and 2 seconds.

## 3. Scenario matrix
| Scenario | Behaviour verified | Test |
|---|---|---|
| A. Interrupted collection | investor import interrupted after board one: run ends `interrupted`, board one's jobs and checks remain, no check is claimed for the unfinished board, no closure; the retry finishes and a second replay adds nothing | **new** `test_interrupted_investor_import...`; existing Feashliaa, YC-company and pipeline interruption tests |
| B. Provider failure (403, 429, timeout, schema) | affected source stops; the other sources continue; failure is never read as empty; no lifecycle change | existing adapter tests; **new** retry-policy and health-state tests; **new** mixed-run test |
| C. Partial pagination | missing middle page, duplicate page, repeated cursor, premature empty page, changed total, request or page cap stay partial and never authorise closure | existing; **new** `changed total mid-scan` (this was a real hole: a scan whose total moved 5 to 6 was reported complete) and `a mutated scan never authorises closure` |
| D. Stale or retained snapshot | stale manifest refused; missing or corrupt chunks block the import; provider-retained jobs stay "historical", never "open"; history preserved | existing; **new** stale-snapshot test and age-based stale flag |
| E. Retry and replay | finite budgets; restrictions stop the source; replay creates no duplicate jobs, companies, sightings, events or sessions; verdicts survive | existing; **new** partial-snapshot-then-retry test and replay-after-interrupt test |
| F. Mixed run | one good job source, one good company source, one failed source, one incomplete source, prior jobs and Yes/No verdicts: overall `partial`, zero Closed/Not Seen, verdicts intact, health states correct, shortlist and sync preview still work and write nothing | **new** `test_mixed_run...` |
| G. Restricted sources | normal runs never call JobSpy; `all` and wildcards rejected; failed VPN, blocks, CAPTCHA stop without evasion; nothing contacted in tests | existing `test_jobspy.py` (re-run) |
| H. Check ordering | two collecting runs in the same second no longer pick the newer one at random (`ORDER BY started_at, rowid`) in both `source_health` and `lifecycle.latest_run_checks` | **new** (found when the mixed test failed intermittently) |

## 4. Remaining unrecoverable or unobservable failure modes
* A silent upstream change that keeps the response shape (for example a feed that quietly drops half its employers): detectable only by comparing counts between snapshots; there is no automatic alarm beyond the reported manifest total.
* A process killed outright (no handler can run) can leave a run recorded as `running`. `run --resume <run id>` continues a run that finished as interrupted or failed and skips sources that already finished under it; nothing resumes a `running` run by itself, and the operator learns of it only from the run report or `source-health`.
* Disk full or file corruption: protected by transactions and the verified-backup-before-write rule, recovery is restoring a backup, not an automatic repair.
* A sheet edited while a sync runs can still race (documented since Day 4).
* Staleness thresholds are defaults per source type (72 h feed, 7 days boards); the operator should adjust them to the real cadence.

## 5. Day 14 acceptance checklist (every item is exercised by a passing test)
| Required behaviour | Test (file) |
|---|---|
| Interrupted source runs | `test_interrupted_investor_import_keeps_committed_work_and_a_replay_finishes_cleanly` (day14_recovery); `test_interrupted_import_rolls_back_the_open_batch_and_rerun_is_clean` (feashliaa, yc_companies); `test_interrupted_run_resumes_without_repeating_or_duplicating` (pipeline) |
| Failed providers; failure of one source does not stop the others | `test_yc_succeeds_when_feashliaa_fails`, `test_feashliaa_succeeds_when_yc_fails` (pipeline); `test_mixed_run_one_good_job_source...` (day14_recovery) |
| HTTP 403 and 429 | `test_refusals_and_rate_limits_are_never_retried` (day14_recovery); `test_block_stops_immediately_without_retry` (investor_boards); `test_collect_isolates_failures_and_stops_on_a_block_without_retry` (yc_jobs); `test_blocked_timeouts_and_captcha_stop_the_source_without_retry_or_evasion` (jobspy) |
| Bounded permitted retries | `test_transient_errors_use_a_finite_budget_with_growing_pauses` (day14_recovery); `test_fetch_bytes_retries_then_fails_cleanly` (feashliaa) |
| Schema failures | `test_changed_page_schema_is_a_failed_scan` (investor_boards); `test_schema_change_is_detected_and_nothing_is_imported` (yc_companies); `test_manifest_with_missing_or_bad_fields_fails` (feashliaa) |
| Missing or corrupt snapshots | `test_missing_chunk_blocks_the_import_and_is_recorded_partial`, `test_corrupted_chunk_json_and_gzip_block_the_import`, `test_corrupted_manifest_json` (feashliaa) |
| Partial pagination; repeated cursors; changing totals | `test_premature_empty_page_is_partial_not_complete`, `test_repeated_cursor_is_detected`, `test_request_cap_and_page_cap_make_the_scan_partial` (investor_boards); `test_result_set_that_changes_size_while_being_read_stays_partial`, `test_a_mutated_scan_never_authorises_closure` (day14_recovery) |
| Verified-empty versus unverified-zero | `test_empty_but_complete_feed_is_verified_empty` (investor_boards); `test_every_check_outcome_has_one_honest_state[...]` and `test_multi_scope_runs_are_judged_on_scopes_with_data` (day14_recovery) |
| Stale Feashliaa snapshots | `test_stale_manifest_is_refused_and_recorded_without_importing` (feashliaa); `test_stale_provider_snapshot_is_refused_without_touching_history` (day14_recovery) |
| Failed and partial checks never close jobs | `test_no_failure_or_partial_check_closes_anything_and_feed_is_never_closure_evidence` (feashliaa); `test_partial_failed_or_empty_later_scans_never_close_or_erase_jobs` (investor_boards); `test_failed_or_blocked_or_empty_later_scan_never_closes_or_erases_jobs` (yc_jobs) |
| Deterministic same-second check ordering | `test_two_runs_in_the_same_second_resolve_to_the_later_one_deterministically` (day14_health) |
| Idempotent replay; human verdicts and notes preserved | `test_partial_snapshot_then_retry_is_idempotent_and_keeps_human_verdicts` (day14_recovery); `test_verdicts_and_notes_survive_repeated_runs_and_changes` (pipeline) |
| Restricted JobSpy refusal | `test_all_and_wildcard_cannot_activate_restricted_sources_in_normal_runs`, `test_no_jobspy_call_without_a_live_authorization`, `test_normal_runs_never_call_jobspy` (jobspy) |
| Partial runs are never presented as success | `test_overall_status_rules` (pipeline); `test_a_partial_run_is_never_presented_as_complete_or_able_to_close_jobs`, `test_the_health_report_names_every_source_that_needs_attention` (day14_health). `source-health` ends with a "Sources needing attention" line; the `/run-discovery` Skill runs it after the doctor and after the run and says that a partial, filtered, stale, failed or skipped source never means a job is gone |
