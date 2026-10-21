# Known limitations (carried forward to the Day 16 README)

Written on Day 14 from measured evidence. Where something is a guess it says so.

## What the engine can and cannot tell you
* **Source coverage gaps.** Feashliaa reads seven ATS platforms (Workday, iCIMS, Greenhouse, Paylocity, Lever, BambooHR, Ashby) and 28,543 employers; it has no Keka, Workable, Zoho Recruit, Darwinbox, SmartRecruiters or employer-owned pages. Investor boards add many of those (84% of their 4,998 job URLs point outside Feashliaa's platforms) but only for employers on those boards. An employer on neither (the benchmark's Prismforce and Libra AI) is invisible until you add it by hand.
* **Restricted and unavailable sources.** Indeed, Naukri and LinkedIn (through JobSpy) never run in normal discovery and need a fresh approval each time; Glassdoor is retired. Weekday and Saarthi are not read (accounts and terms unknown, mostly mirrors); the Nexus board scan failed and its data is absent. Consider-hosted boards rest on your attested, unverified authorization.
* **No job descriptions or stated experience.** Descriptions are not stored for any source; stated experience exists for YC job pages only. Experience is "unknown" for 223 of 249 qualified jobs, so senior roles can qualify (two of three benchmark controls did) and a title word such as "Lead" can wrongly hold back a 2+ year role unless the employer's own figure is stored (Day 14 policy: employer-stated years outrank a title word; today only YC job pages carry them). Weak role words ("Product Manager", "Analyst") can only be held for review.
* **Vacancy availability is unverified.** "Listed in the latest check" means a source showed the posting; no source here is employer-confirmed, and a page that loads proves only the page. Provider-retained feed rows are labelled historical.
* **Dates.** Only investor boards report a creation date (used for freshness points). Feed-only jobs carry none. Since Day 14 the investor-board membership bonus is capped at +3 per job, which narrowed the average gap between board-only and feed-only qualified jobs from 5.1 to 2.1 points; a gap remains, and the 20-slot shortlist is still decided inside a dense 70-89 score band.
* **Partial and filtered scans.** Accel, Lightspeed and Peak XV scans are partial; Techstars is complete only for Bengaluru. No scan can show a job is gone, and none ever closes one.
* **Unresolved identity candidates.** About 280 Tier B candidate pairs (and a long tail of Tier C/D related-posting pairs) remain unmerged by design, including 58 Workable pairs (one company, two boards) that are now visible as candidates. A wrong merge is judged worse than a duplicate.
* **Small and biased benchmark.** 11 positives, mostly Greenhouse and Lever employers, three role families unrepresented; rates have wide intervals (6 of 11, roughly 28-79%). It measures discoverability on one day, not recall.
* **Known ranking weaknesses.** Board-only jobs can still earn provider-date points that feed-only jobs cannot; "forward deployment" is a weak alias, so such titles are review leads, not qualifications; the exploration lane has low precision; "AI-powered" makes large companies read as AI-native; the 20-slot cut decides between near-equal jobs.

## What you have to do
* Review each session's Shortlist and mark Yes / Maybe / No in the **Posts** tab; start the next batch only on purpose (`--new-session`).
* Add employers the sources miss with `import-manual` (dry run first).
* Read `source-health` after a run: a partial, filtered, stale or skipped source says nothing about missing jobs.
* Approve, one by one, any live collection, enrichment, migration or merge plan.

## Provider-specific restrictions
* Feashliaa and YC-directory datasets: up to 3 attempts for temporary errors; 403, 404 and 429 stop at once.
* YC job pages, investor boards, restricted sources: no retries; 403 and 429 stop the source; at least 2 seconds between requests and hard caps.
* Google Sheets: quota errors (429) are retried after 20 s and 40 s because the call is rejected unapplied; TEST only, LIVE is blocked.

## Evidence gaps that remain open (carried to Day 15)
* No proven adapter failure for the three Day 13 misses (Sprinto, Prismforce, Libra AI); posting dates are unknown and every collection was 16-25 hours older than the benchmark.
* Accel's scan is incomplete (2,988 of 2,993 records, duplicates, location-filtered); no scan can show a job is gone.
* The benchmark is small (11 positives) and biased towards Greenhouse and Lever; no employer-open verification was made.
* A bounded enrichment experiment (descriptions, stated experience, posting dates for tenant boards) is a future, separately approved step; its storage (a migration 8) is deferred and not written. Priority order: known-tenant board indexes, selective enrichment, Keka and other India-relevant platforms, manual import for the rest.
* Unresolved identity candidates remain (about 280 Tier B pairs, including 58 Workable pairs, Apna and Sarvam); nothing is merged without a reviewed plan.

## Day 15 additions
* **Windows symlink test.** `tests/test_imports.py:106` (real-symlink refusal) is skipped on this machine because creating symlinks needs a Windows privilege. The logic is covered by a mock-based test that always runs; the skip is expected and not a failure.
* **One verdict store for two sheets.** SQLite `verdicts` serves both TEST and LIVE, and the last-sync pointer is global. LIVE therefore never receives stored verdicts (it is the person's alone) and a verdict typed in either sheet is shared. Tagging verdicts by target would need a schema change and is deferred.
* **First LIVE is stored evidence only.** Accel is partial (2,988 of 2,993), Nexus failed and was never imported, Lightspeed/Peak XV/Techstars are complete only for their Bengaluru filter, yc_jobs is incomplete (42 of 52 scopes unproven). Posts are labelled "observed on source, not employer-verified"; nothing is confirmed open.
* **Consider-only rows.** Four listed jobs (Khatabook, Stable Money, apna, composio) are seen only on Consider-hosted boards, whose use rests on the operator's attested, unverified authorization.
* **Runs tab.** Only LIVE publications are shown; development history is in SQLite.

## Day 16 additions
* **TEST is rehearsal only.** Verdicts typed in TEST are ignored with a warning (one verdict store still serves both sheets). Decisions are entered in LIVE. A target-scoped verdict table (migration, number not assigned) is a future option, not approved.
* **LIVE capacity.** Row limits (25/30/30/2, `config/live_publication.json`) were approved for the first trial. The next new review session would need about Posts 35 and Companies 36 and is refused until the operator approves larger numbers (`live-limits`). Limits are never widened automatically and rows are never deleted to make room.
* **Manual acceptance waived.** The real-user LIVE verdict round trip and the manual close/reopen of Claude Code were waived by the operator and replaced by automated tests; they were not performed.
* **Consider-only rows and provider confirmation:** see `docs/design/day16_open_questions.md`.

## Post-implementation audit (2026-10-21)
* **LIVE capacity is the next operator decision.** The next batch needs about Posts 35 / Companies 36 (limits 30/30) and every publication adds one Runs row (limit 2). `live-limits --next-session` prints the smallest sufficient numbers; only the operator approves them.
* **Same title twice in a batch.** Two postings with the same employer and title (for example BestDoc "Associate Product Manager", two board ids) can both be listed; the per-employer cap allows two. A one-row-per-title rule is a selection-policy proposal, not applied.
* **Foreign cities read as "unknown".** About 20 held-for-review jobs with explicit foreign cities are labelled unknown location rather than foreign; they are never qualified. "Remote" without India stays in the uncertain lane by design.
* **Stored TEST verdicts still count in ranking** (+25 Yes, +5 Maybe, No suppresses); since the audit the explanation names them first. Separating TEST and LIVE verdicts needs a migration and approval.
* **A lost response on the very last LIVE write** can leave SQLite recording that run as failed while the sheet shows its Runs row; the data are complete and nothing is duplicated.
* **A record merged away after it was published** makes the sync stop (fail closed) until the sheet row is resolved; no merge is pending.
