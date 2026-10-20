# Source audit: Feashliaa job-board-aggregator / job-board-data

- **Inspected:** 2026-10-20 (UTC), following the mandatory audit checklist in the implementation guide.
- **Role in our system:** *supplementary* Stream A feed (jobs from employer ATS boards). Not authoritative for "currently open".

## 1. Where the project actually lives (verified again today)

| Original URL in our guide | Resolves to (GitHub API `full_name`) |
|---|---|
| `github.com/Feashliaa/job-board-aggregator` | `Riley-Dorrington/job-board-aggregator` (redirect) |
| `github.com/Feashliaa/job-board-data` | `Riley-Dorrington/job-board-data` (redirect) |

- Collector repo HEAD: `ba8cfa9d` "Trend snapshot 2026-10-19" (2026-10-19T15:51:31Z); not archived; 2 open issues; one release.
- Data repo HEAD: `beb43849` "Data snapshot 2026-10-19 15:51 UTC" — and it is the repo's **only commit** (see §4: the data repo is force-replaced every run, so there is no history to diff).
- README of the collector still says the data repo is `Feashliaa/job-board-data` while the frontend loader uses `https://riley-dorrington.github.io/job-board-data/data/chunks`. **Published URLs have moved once already (Oct 18) and must be treated as configuration, not constants.**
- Activity: workflow "Update Job Listings" (cron `33 7 * * *`, plus manual) succeeded on 2026-10-19; "Deploy static content to Pages" and "Test Job URL's" succeeded 2026-10-20. Commits are daily "Trend snapshot" plus link-rename commits on Oct 18.
- Open issues: **#53 "Scraper anomaly 2026-10-19"** (auto-created: Ashby DROP 39,024 vs 42,398 mean, z=-4.5; BambooHR and iCIMS small drops) and **#42** (≈47% of `workday_companies.json` entries are duplicate tenants).
- License: a `LICENSE` file exists in the collector repo (type not reviewed). Usage restrictions: none stated for the data repo; it republishes public ATS job-board data. Rate limits: none for static Pages files; we download ~75 MB once per snapshot.

## 2. Pipeline (read from `scripts/scraper.py`, `merge_data.py`, `scrape-jobs.yml`)

1. GitHub Actions clones the previous data repo into `data/`, runs `scraper.py --source automated` (parallel threads, `requests`, 30 s timeouts, up to 2-3 retries on some platforms) over per-ATS company lists (`data/*_companies.json`: Greenhouse 8,333, Lever 4,368, Ashby 3,161, BambooHR 11,316, iCIMS 10,108, Paylocity 10,252, Workday 12,884 boards).
2. `merge_data.py`: loads previous chunks + new scrape. Previous jobs are **kept if their `scraped_at` is ≤ 30 days old**; new-scrape jobs overwrite on the same key and inherit the older `first_seen`; everything else is dropped. Key = URL (Workday: `workday:<company>:<digits after /jobs/>`, Paylocity: `id` or company+title).
3. Writes chunks of 25,000 rows (`jobs_chunk_N.json.gz`, sorted by company then title) and `jobs_manifest.json`, then **force-pushes an orphan commit** to the data repo; a Pages workflow deploys it.
4. A trend snapshot and an anomaly check (per-platform volume z-score) run; an anomaly opens a GitHub issue but does **not** stop publication.

## 3. Published data (inspected live: manifest `last_updated` 2026-10-19T15:48:40Z)

- Base used by the official frontend loader: `https://riley-dorrington.github.io/job-board-data/data/chunks/` (Pages; deployed atomically). Raw GitHub (`raw.githubusercontent.com/.../main/data/chunks/`) currently serves an identical manifest.
- `jobs_manifest.json`: `{"chunks": ["jobs_chunk_0.json.gz", …58 names], "totalJobs": 1436282, "last_updated": "2026-10-19T15:48:40.506836Z"}`.
  - **Verified: 58 chunks listed, 58 chunk files exist, rows sum to exactly `totalJobs` (1,436,282); every chunk has 25,000 rows except the last (11,282).** This gives us a reliable completeness check (chunk list + row total). `last_updated` is the merge/publish time.
- `data/metadata.json`: `last_updated` 2026-10-19T15:47:45Z (scrape end), `total_companies` 59,160, `active_companies` 28,605, `total_jobs`, `recruiter_jobs`, `platforms`.
- Chunk row schema (all 1,436,282 rows inspected):

| Field | Type / presence | Notes |
|---|---|---|
| `url` | str, always | job page; the only per-job identifier published |
| `company` | str, always | ATS **board slug/tenant** (e.g. `0g`, `10alabs`, Paylocity company name or numeric GUID-ish text), *not* a legal name or domain |
| `ats` | str, always | Workday 636,031; iCIMS 247,452; Greenhouse 236,489; Paylocity 102,819; Lever 92,435; BambooHR 63,000; Ashby 58,056 |
| `title` | str, always | may contain trailing spaces |
| `location` | str; 11 null | free text; iCIMS often "Not specified" |
| `remote` | bool; **absent on all 58,056 Ashby rows** | derived by the collector from the *location string* (geolocation lookup), not an employer flag |
| `coords` | list/null; absent for Ashby | |
| `scraped_at` | str ISO-Z, always | **provider's last observation of this job** (time of the scrape that saw it, or the older run it was retained from) |
| `first_seen` | str ISO-Z, always | provider's first observation; carried across runs. 141 rows have `first_seen` > `scraped_at` (data quirk) |
| `updated_at` | str/null; present in 1,222,791 rows, non-null 995,451 | ATS-reported update time where the ATS gives one; mixed formats; **not** a posting date |
| `skill_level` | `mid` 820,457 / `senior` 393,524 / `entry` 196,762 / `intern` 25,539 | keyword guess from the title; not an experience requirement |
| `is_recruiter` | bool | |
| `salary` | dict/null (136,966 non-null) | estimate from a lookup table, not employer data |

- **Not published** even though the scraper produces them: the ATS job `id`, `company_slug`, `departments`, `absolute_url`, `source`. **There are no job descriptions.**

## 4. Freshness and retention — answers to the critical questions

- *Does the snapshot include jobs from previous runs?* **Yes.** 686,149 rows (47.8%) have `scraped_at` within 3 h of the manifest time (the latest run: all between 14:xx and 15:47 UTC on 10-19); 213,420 are ≤7 d old, 521,276 are ≤30 d old, 15,437 are just over 30 d (age measured at merge time).
- *How are jobs retained after disappearing?* Silently, for up to 30 days after their last `scraped_at`; nothing marks them as retained other than an older `scraped_at`.
- *Can we tell if a job was in the latest successful collection?* **Approximately.** Rule used by the adapter: `scraped_at` within 6 hours before the manifest's `last_updated` ⇒ `latest_collection`; older ⇒ `provider_retained`. This is an inference from the observed run window (about 1.5 h on 2026-10-19), not a documented field; classify as `unknown` if `scraped_at` is missing/unparseable.
- *Last observation vs publication date?* Yes, separate: per-job `scraped_at` vs manifest `last_updated` vs metadata `last_updated`.
- *Complete or partially published?* The data repo is replaced atomically (orphan commit force-pushed, then a single Pages deploy), but the push step is not transactional with the scrape. The manifest chunk list + `totalJobs` let us **detect** missing/truncated chunks. Raw and Pages hosts can briefly disagree after a push; the adapter uses Pages and re-checks the manifest after downloading.
- *Upstream failure behavior:* a board that errors or returns non-200 yields an empty list (`except Exception: … return slug, [], None`). Its jobs then simply keep their old `scraped_at` and are retained up to 30 days. **A failed board is indistinguishable from a board with no jobs**; an anomaly issue is opened for large platform drops but publication continues (issue #53 is open now). Consequence: absence from the latest run proves nothing, and Feashliaa can never be used for closure.
- *Identity ambiguity:* yes. The board slug is unique only within an ATS; the same slug can exist on two ATS platforms as different employers. Paylocity rows are ambiguous: 6,312 surplus rows repeat a URL already used by another Paylocity row (2,627 same company/different title; 3,538 different company/same title; 147 both different), because the published rows lost the Paylocity job `id` the merge logic relies on. Workday's provider key regex (`/jobs/(\d+)`) never matches real Workday URLs (`/job/...`), so URL is the effective key. Distinct URLs: 1,429,970 of 1,436,282 rows.

## 5. India relevance (conservative text match on `location`)

| Category | Rows | Distinct boards |
|---|---|---|
| Bengaluru / Bangalore | 14,346 | 747 |
| Other text containing "India" | 11,519 | 824 (with Bengaluru: see dry-run) |
| Known Indian city/state, country not stated (e.g. "Bhubaneswar, Odisha") | 13,201 | - |
| "Remote" (flag or text) with no India mention | 60,593 | - |

Of the India/Bengaluru rows ≈ 45-50% are from the latest collection, the rest retained. These counts are before any role filtering.

## 6. Overlap with existing sources

None with the 6,311 YC companies by identity: the feed has board slugs, not domains or YC IDs. Possible overlaps are name-based only and will be recorded for later review, never merged.

## 7. Differences from our guide's assumptions

Mostly confirmed: the manifest + gzip chunks exist; retained/stale jobs exist; provider `first_seen` ≠ ours. New or sharper facts: owner changed to Riley-Dorrington; **no job-ID field and no descriptions**; `remote` is a location-text inference and missing for Ashby; the 30-day retention is implemented by `scraped_at`; failed boards look like retention; the data repo has no history; the manifest also carries `totalJobs`. None of this changes the architecture (supplementary feed, never closes jobs), so no approval to change the plan is needed.

## 8. Integration choice

Published feed, consumed directly: fetch manifest from the Pages base URL (configurable), validate, download manifest-listed chunks to a git-ignored cache, stream-filter chunk by chunk (≈25 MB parsed at a time), import only candidates. No new dependency (stdlib `urllib`, `gzip`, `json`). Details, identity rules, freshness fields and tests: see the Day 6 section of the implementation guide and `tests/test_feashliaa.py`.

## 8b. Live dry-run findings (2026-10-20, adapter v1)

58/58 chunks read, 1,436,282 rows (equal to `totalJobs`), 0 malformed, 66 s, ~158 MB peak. Candidates after geography + title prefilter: 25,219 jobs (12,352 latest collection, 12,867 provider-retained) from 3,872 boards; 171 boards have a normalized-name match to an existing YC company (recorded for review, not merged). Spot check: 15/15 sampled latest-collection India jobs returned HTTP 200 on the employer ATS or career site. HTTP 200 shows the page is reachable, not that the vacancy is open.

## 9. Risks

Feed owner/URL may move again; the retained-vs-fresh rule is inferred; board-slug company names are not company names; silent per-board failures; anomaly issues do not stop publication; title-only `skill_level`; no descriptions to judge fit; repo size (6 GB history in the code repo) makes cloning unwise — we never clone, only download the 75 MB of chunks.
