# Source audit: YC public job pages (`ycombinator.com/companies/<slug>/jobs`) - Day 9

- **Inspected:** 2026-10-20 (UTC). **Method:** repository and local-data review only. **No request was sent to `ycombinator.com` or `workatastartup.com`** for this audit; everything below that needs a live page is marked UNVERIFIED and listed in section 9 as an approval-gated probe.
- **Status (updated, section 14):** the first two probe requests stopped on YC's Terms of Use (section 11); the operator then stated, **user-attested and not independently verified, that they hold YC authorization** for automated retrieval, and the adapter was built offline. Probe requests 3 and 4 were completed after the permission block was resolved (section 15), the page structure is now verified, and a 12-company live sample succeeded.
- **Reference reader:** OpenPostings, `server/ats/ycombinator/service.js`, repository `Masterjx9/OpenPostings`, HEAD `114fccf6` (2026-06-07, "MacOS Update for Version 2.0.1"); the file was last changed in `36ae1642` (2026-05-27). Not archived, 293 stars, 2 open issues, **no license file** (license field is empty), JavaScript only.

## 1. What the reader does (read from the code)

1. Accepts a company URL of the form `https://www.ycombinator.com/companies/<slug>/jobs` (host must be `ycombinator.com` or `www.ycombinator.com`).
2. Makes **one HTTP GET** per company with a rate limiter keyed `"ycombinator"`, a **browser-like User-Agent**, `Accept: text/html...`, `Accept-Language`. Non-2xx returns an empty list.
3. Looks for `<div id="WaasShowJobsPage-react-component-...">` and parses its `data-page` attribute (HTML-entity-decoded JSON, an Inertia-style page object). `props.company.name` and `props.jobPostings[]` are read.
4. Per posting it keeps `title`, `url` (or `applyUrl`), `createdAt`, `location`, de-duplicated by URL. Nothing else (no salary, remote flag, job type, description) is used, although the payload may contain more (UNVERIFIED).
5. Any failure (non-2xx, missing component, JSON error) returns `[]`.

**Consequences for us**

- The approach needs no login, no API key and no browser: one HTML page per company, JSON embedded in the page. It is small enough to rewrite in Python (requests + `html`/`json`), as the guide requires; **no JavaScript is added**.
- Because the repository has **no license**, we must not copy code. We reimplement the idea (a URL pattern and the name of an embedded component), which is a fact about YC's page, not OpenPostings' expression.
- The reader returns `[]` for both "page parsed, zero jobs" and "request or parse failed". **That conflation violates our rule** (a failed or partial read is never "no jobs"); our adapter must keep these states distinct.
- It sends a browser User-Agent. We should not impersonate a browser; use an honest, fixed User-Agent that names the tool and is not rotated (see section 6).
- The embedded component name (`WaasShowJobsPage`) and the Inertia payload shape are YC implementation details and can change without notice; schema drift must degrade to "unverified", never to "no jobs".

## 2. Access, permission and terms (INCONCLUSIVE)

- Not checked against YC itself in this audit. Public web search returned no authoritative statement of YC's crawler policy, `robots.txt` rules for `/companies/*/jobs`, or terms of use covering automated reads; it returned only third-party scraper listings (which prove scraping is common, not permitted).
- YC pages are public and need no login. The project rules allow "public/permitted interfaces" and forbid login automation, restricted-site scraping, proxy rotation and block evasion. Whether these pages count as permitted for automated reading depends on YC's `robots.txt` and terms, which must be read first.
- The adjacent product, **Work at a Startup** (`workatastartup.com`), is login-gated for applying and is **excluded**: no login automation, no account use.
- Decision rule once checked: if `robots.txt` disallows the job-page path for our user agent, or the terms prohibit automated access without permission, YC job pages become a **restricted source** (same policy family as Indeed/Naukri/LinkedIn: disabled by default, explicit per-run approval) or are dropped in favour of manual link import (Day 12). If both allow it, treat it as a normal source with conservative limits (section 6).

## 3. Our own data that decides scope (read-only, local SQLite)

YC directory records in SQLite: 6,311 (all from the Day 5 import; `is_hiring_reported` is informational only).

| Group | Count |
|---|---|
| YC companies listing India in `locations` or `regions` | 226 (Active 159, Inactive 43, Acquired 22, Public 2) |
| Same, also counting the `South Asia` region | 239 |
| Of the 239: Bengaluru / Bangalore listed | 134 |
| Of the 239: directory flag `isHiring = true` | 51 (Active and flagged: 49) |
| Companies currently published to TEST (`sheet_publish`) | 224 |

Implication: a full pass over every India-related YC company is about **226-239 requests** (one per company); an Active-only pass is **159**. The directory flag identifies only about a fifth of them, which is exactly why Day 9 must not use it to choose which pages to read.

## 4. Fields and identifiers (from the reader; UNVERIFIED against live pages)

| Field | Expected source | Notes |
|---|---|---|
| company | page slug; `props.company.name` | link to our company by YC slug / `yc_id` (both already stored) |
| job title | `jobPostings[].title` | |
| job URL | `jobPostings[].url` (fallback `applyUrl`) | likely `https://www.ycombinator.com/companies/<slug>/jobs/<id>-<title>`; the numeric/opaque id inside is the candidate stable job identifier (verify) |
| posting date | `jobPostings[].createdAt` | store as `provider_first_seen_at` (provider-reported, never our first-seen) |
| location | `jobPostings[].location` | free text; may be `Remote`, multiple cities or `Bengaluru, KA, IN`; no country normalization guaranteed |
| apply URL | `applyUrl` | may point to `workatastartup.com` (login) or an ATS; record, never follow |

Open questions for the probe: stable id format, pagination (none seen in the reader), presence of salary/remote/experience fields, behaviour for a company with no jobs, for a company page that 404s or redirects, and whether `createdAt` is a date or a timestamp.

## 5. Overlap and value

- **Overlap with Feashliaa:** many YC companies use Greenhouse, Lever or Ashby, which Feashliaa may already list; the overlap is unmeasured. It must be measured on the probe sample by company-name hint and job-URL equality (employer URL equality is the only strong evidence; title similarity is never used).
- **Value:** closes the gap between "company directory says hiring" and "verified open jobs" for roughly 226 India-related companies, including the roughly 80% not flagged as hiring. Unlike JobSpy boards it is employer-sourced, so it should provide stable company linkage.
- Not claimed: how many relevant, non-duplicate jobs it will add; to be measured, not assumed.

## 6. Proposed integration (plan only)

**Selection (independent of the hiring flag):** all YC companies with India in `locations`/`regions` (226; optionally the 13 `South Asia`-only). Open decision for the operator: include Inactive/Acquired companies (65 of the 226) or Active/Public only (161). Recommended: Active/Public first.

**Per-company states (never conflated):**
| State | Meaning | Effect |
|---|---|---|
| `ok_jobs` | 2xx, component and `jobPostings` array parsed, N >= 1 | jobs upserted; `verified_open_jobs = N`, check date stored |
| `ok_empty` | 2xx, component parsed, `jobPostings` is an empty list | `verified_open_jobs = 0` at that date (a verified-empty page) |
| `unverified` | 404, redirect to a non-job page, 5xx, timeout, missing component, malformed JSON, schema drift | no change to counts; counts as a failed/partial check; **never** treated as zero jobs |
| `blocked` | 403/429/CAPTCHA/challenge page | stop the whole source for the run, no retry |

**Storage:** `company_reported_hiring` = the existing directory flag (unchanged); `verified_open_jobs`, last-checked date and page state are recorded on the company (`facts.yc_jobs`) so no schema migration is needed unless the probe shows otherwise; jobs go through the existing job/sighting path with `source = yc_jobs`, URL = job page URL, `provider_first_seen_at` = `createdAt`. A run's `source_checks` row has `complete = false` unless **every** selected company was read in state `ok_*`; a complete check can only be claimed for the scope actually covered, and no Not Seen/Closed logic is built on it until Day 11.

**Politeness and safety limits (conservative, adjustable only with approval):** sequential requests; one request per company; at least 2 s between requests (about 8 minutes for 226); 15 s timeout; **no retries on 403/429/challenge** and the run stops on the first one; honest fixed `User-Agent` such as `ab-discovery-engine/0.9 (personal job research; manual runs)`; TLS verification on; no cookies, no login, no proxies; request counter and a per-run cap equal to the number of selected companies; resumable by company so an interrupted run does not restart from zero; run only on explicit manual command (never in `/run-discovery` until approved); nothing published to Sheets by this stage.

**Network:** these are public employer-directory pages, not a restricted job board; the Surfshark preflight is not mandatory by policy, but running behind the VPN is recommended for consistency (decision for the operator).

## 7. Failure modes and identity risks

- Payload shape change or component rename -> `unverified` for every company; the run reports a systemic parser failure instead of "0 jobs everywhere" (add a sanity check: if more than a set fraction of known-hiring companies parse as empty, mark the whole run suspect).
- Slug changes or redirects; companies with a former name; shared websites between companies (already seen in the directory).
- Duplicate jobs across YC and Feashliaa (different URLs for the same posting): leave possible duplicates unmerged unless employer URLs match.
- Location strings do not prove India eligibility; geography stays descriptive (project rule), "remote" is not eligibility.
- Dates: `createdAt` is provider data and may be edited or absent; absence stays null.

## 8. Test plan (fixtures)

Representative sanitized fixtures from the approved probe: page with several postings; page with zero postings; page with the component but no `jobPostings`; page without the component (challenge/login/redirect); malformed JSON; HTML-entity-heavy payload; duplicate URLs; missing optional fields; 404/403/429/500; timeout. Tests: parsing, state classification, the "failure is not zero" rule, selection independent of `isHiring`, idempotent replays (no duplicate jobs/sightings/events), request-cap and stop-on-block behaviour, dry run writes nothing, no network in tests, company linkage by `yc_id`.

## 9. Approval-gated live probe (not yet performed)

Needed to finish the audit; each item is a single ordinary public GET, no login, no cookies, honest User-Agent, no retries, results kept in the scratchpad (not the repo):
1. `https://www.ycombinator.com/robots.txt` - read the rules for our path and user agent.
2. YC's public terms page (link to be taken from the site footer or `/legal`) - read the automated-access language.
3. One job page of an India-based company flagged hiring, for example `.../companies/razorpay/jobs` - confirm the structure, fields, identifier format and any pagination.
4. One job page of an India-based company flagged not hiring, for example `.../companies/cleartax/jobs` - confirm the verified-empty or absent case.

Maximum 4 requests. If robots.txt or the terms forbid automated reading, stop after items 1-2 and recommend manual import instead.

## 10. Decisions needed from the operator before implementation

1. Approve the 4-request probe above (or provide YC's written permission, if you have one).
2. Scope: Active/Public India companies (about 161) or all 226.
3. Whether the stage should run behind Surfshark by default.
4. Whether to keep job storage as `facts` + existing job tables (preferred) or allow a migration if the probe shows the need.

## 11. Probe results (2026-10-20; 2 of the 4 approved requests used)

Requests made (plain GET, no cookies, no login, no retries, honest User-Agent `ab-discovery-engine/0.9 (personal job research; manual run; read-only)`; raw responses kept outside the repository):
| # | URL | Result |
|---|---|---|
| 1 | `https://www.ycombinator.com/robots.txt` | HTTP 200, 283 bytes, `text/plain`, served via Cloudflare, 1.1 s |
| 2 | `https://www.ycombinator.com/legal/` | HTTP 200, `text/html` (505 KB, contains the Terms of Use), 1.2 s |
| 3, 4 | `.../companies/razorpay/jobs`, `.../companies/cleartax/jobs` | **not sent** (see decision) |

**robots.txt** (for `User-Agent: *`): `Disallow: /verify/*`, `/library?...` variants and `/companies?*`; `Allow: /`. There is no `Crawl-delay`. The path `/companies/<slug>/jobs` (no query string) is not disallowed.

**Terms of Use** (applies to "the Y Combinator website (including all subdomains)"), section "Intellectual Property Rights / Site Content": "Except as expressly authorized by Y Combinator, you agree not to modify, copy, frame, scrape, ... the Site or the Site Content" and "In connection with your use of the Site you will not engage in or use any data mining, robots, scraping or similar data gathering or extraction methods." It also says that if blocked you agree not to circumvent the block (for example by masking your IP or using a proxy). A separate clause bars obtaining materials "through any means not intentionally made available or provided for through the Site".

**Decision (applying section 2's rule):** robots.txt does not forbid the path, but the Terms of Use prohibit automated scraping and robot access to the Site without YC's express authorization, and no such authorization is known. Automated retrieval of the job pages is therefore **not permitted** and was not tested or built. Requests 3 and 4 would have been exactly the kind of automated page read the Terms bar, and the operator's approval of the probe was conditional on the audit confirming the method is permitted; it did not. (The company directory import is unaffected: it consumes the third-party `yc-oss` GitHub dataset and sends no request to ycombinator.com.)

**Reference reader status:** the OpenPostings approach (one GET per company page, parse the embedded `WaasShowJobsPage` payload) is technically viable but is the access the Terms prohibit; it stays unadopted. Its lack of a license and its failure-conflation (any error returns an empty list) remain additional reasons not to copy it.

## 12. Section 10 decisions: what was decided, options, recommendation

1. **Run the probe?** Decided: yes, bounded to the audit's list and conditional on permission. Outcome: stopped after request 2 because of the Terms.
2. **Scope (Active/Public vs all India companies).** Moot until access exists. Recommended if access is ever authorized: every `india_based` company regardless of status or hiring flag (224: 173 flagged not hiring, 51 flagged hiring; request volume about 224, plus the `potentially_relevant` fully-remote tier of 1,124 as a separate, later opt-in), because status and the directory flag are not reliable predictors of open jobs.
3. **Surfshark by default.** Not required for a normal public source; recommended anyway for consistency. Not applicable to restricted-source rules.
4. **Storage.** Recommended and unchanged: no schema migration. Company scan status in `companies.facts.yc_jobs` (a top-level key the directory refresh never overwrites), observed jobs through `observe_job` with the YC job id as `external_id`, `createdAt` as `provider_first_seen_at`, and one `source_checks` row per company (scope `company/<slug>`) so a blocked or failed company can never read as "no jobs". Company states: not checked (no key), checked with listings, checked with no listings observed, partial, blocked, failed.

## 13. Options to obtain YC job data permissibly (for the operator)

| Option | Permitted? | Needs | Effort | Notes |
|---|---|---|---|---|
| A. Written authorization from YC for automated reads of `/companies/<slug>/jobs` (or an official feed/API) | Yes, once granted | Ask YC; keep the reference; name the method and the pages | Medium (the adapter design in section 6 then applies unchanged) | Cleanest; YC may decline |
| B. Operator browses YC pages and supplies links/exports to the Day 12 manual import | Human use; automated parsing of saved pages is not yet cleared | Time per company | Low | Low volume; deliberate, not a scrape |
| C. Read each company's own job board through documented public ATS APIs (Greenhouse, Lever, Ashby) for the 224 India-based YC companies | Designed for public use; needs each board's terms checked | Mapping YC company -> ATS board (Feashliaa already hints at some) | Medium | Employer-sourced and stable ids; closes the same gap without touching ycombinator.com; new scope, needs approval |
| D. Skip job-level YC data; keep the directory flag as a reported signal only | Yes | nothing | None | Leaves the Peoplebox-style gap open |

## 14. Operator authorization, adapter and current state (2026-10-20)

**Authorization record.** The operator confirmed they hold authorization from Y Combinator for automated retrieval of YC job-listing data and chose to proceed with the original plan instead of ATS boards. Recorded as **user-attested, not independently verified written authorization**; there is no reference number and none is invented. The authorization is understood to cover the approved activity only: the public company job pages `/companies/<slug>/jobs` for companies already in our database, at conservative volume. It is **not** assumed to cover other endpoints, higher volumes, login-gated pages (Work at a Startup) or access-control workarounds. The probe stopped after two requests earlier because authorization had not yet been confirmed.

**Probe requests 3 and 4 (not yet sent).** The first attempt (Razorpay, directory flag hiring = true) was **denied by the Claude Code auto-mode permission layer** ("Third-Party Attack") before any request left the machine; it was not retried or rerouted. Requests 3 (`/companies/razorpay/jobs`) and 4 (`/companies/cleartax/jobs`, flag not hiring) remain approved by the operator but need the platform permission (a native approval or a narrow allow rule) before they can run. Until then **the page structure below is assumed, not observed.**

**Adapter built offline** (`discovery/adapters/yc_jobs.py`, source id `yc_jobs`, source type `yc`; CLI `yc-jobs-collect`, `yc-jobs-import`):
- Selection: companies from the Day 5 import chosen **only** by India relevance class (default `india_based`: 224 = 173 flagged not hiring + 51 hiring + 0 missing); other classes are opt-in by `--classes`; the hiring flag, status and size never filter. Counts by class x flag in SQLite: india_based 173/51; potentially_relevant 940/184; unverified 1,414/543; no_india_evidence_yet 2,290/716 (false/true; none missing).
- Collection: one sequential GET per company page, at least 2 s between requests (longer pause every 25), 15 s timeout, no retries, no redirect following, no cookies/login/proxy, TLS verification on, honest User-Agent, hard caps (300 requests, 30 minutes), stop at the first 403/429, host locked to `www.ycombinator.com`. Writes only a git-ignored snapshot under `data/yc_jobs/` (sha256-verified). `--authorized-by-operator` is mandatory and records the user-attested basis.
- Company scan states: `checked_with_listings`, `checked_no_listings` (a parsed page with zero jobs, never "not hiring"), `partial` (unusable postings or pagination markers), `blocked`, `failed` (timeout, non-200, redirect, missing component, bad JSON, company-id mismatch), `not_checked`.
- Import (`yc-jobs-import`, dry-run by default; `--apply` takes a verified backup first and needs the operator's separate approval): jobs through `observe_job` (YC job id as `external_id`, posting URL, `createdAt` as provider first-seen), one `source_checks` row per company (scope `company/<slug>`, `complete` always 0 until pagination is verified), company status in `companies.facts.yc_jobs` (`last_attempt` plus `last_successful_check`, a key the directory refresh never overwrites). No schema migration. Nothing is closed or aged out; verdicts untouched; failures isolated per company; identity conflicts reported per company.
- Not integrated into `run` / `/run-discovery`: `yc_jobs` is a separately selectable source reached only by its own commands (the default pipeline order is unchanged, and `run --sources yc_jobs` is rejected).

**Offline tests.** `tests/test_yc_jobs.py` (35 tests): selection independent of the hiring flag; sample covers all flag groups; parsing (listings, empty, missing fields, duplicates, partial, pagination markers, malformed); failure isolation and stop on 429 with no retry; timeouts and redirects as uncertain; request and runtime caps and delay floor; company-id mismatch; the Peoplebox-style regression (directory false + listings observed is flagged and the flag is kept) and its reverse (directory true + empty page does not establish not-hiring); canonical company linking without duplicates; replay idempotence; changed content as `Changed`, never a duplicate; failed/blocked/empty rescans never close or erase; last successful check preserved; per-company source checks; verdicts survive; directory refresh keeps scan status; identity conflict isolated; snapshot tamper detection; report numbers; CLI plan/collect/dry-run/apply with backup; no Sheets, no JobSpy, not in the default pipeline. Full suite: 388 passed; `pip check` clean; doctor 18/18. Also fixed a pre-existing date-dependent test (`test_repeated_sighting_updates_last_seen_only`) that started failing once the wall clock passed its hard-coded date.

**Coverage so far (observed, not assumed):** 0 companies checked, 0 live job pages read, 0 jobs retrieved. The selected population is 224 companies; at one request every 2-3 s a full pass is about 11-13 minutes.

## 15. Permission block, probe completion and live validation (2026-10-20)

**The permission block was a tool-level denial, not an HTTP response.** The first attempt at probe requests 3-4 was denied by Claude Code's **auto-mode command classifier** ("Third-Party Attack") because the session's permission mode was `auto`. There was no deny rule, hook or managed policy (settings files checked read-only) and no request left the machine. **Resolution:** the operator switched the session to the permission mode that asks before running commands; each command then ran after the app's native approval prompt. No permission setting was changed by the assistant, no broad allow rule was added, and no command was rerouted.

**Probe request count.** Original budget 4: robots.txt (1), legal page (2), `/companies/razorpay/jobs` (3), `/companies/cleartax/jobs` (4). All four are now used; none was repeated. Both job-page requests: HTTP 200, `text/html`, via Cloudflare, 1.5 s and 0.7 s, no redirect, no challenge, no access restriction.

**Observed structure (replaces the assumption).**
| Item | Observed |
|---|---|
| Page data | `<div id="WaasShowJobsPage-react-component-...">` with a JSON `data-page` attribute; `props` keys: company, companyUrl, currentUser, jobPostings, jobsUrl, relatedJobs, showDirectApply, signedIn, ... (`signedIn` false, no user data) |
| Company identity | `props.company.id` equals the YC id already in SQLite (Razorpay 827, ClearTax 664; DB name "Clear"); canonical linking by `yc_id` is confirmed |
| Job object | `id` (numeric string, stable per posting), `title`, `url` (relative, ends with an opaque token plus a title slug), `applyUrl`, `location` (free text, inconsistent: "Bangalore, Karnataka", "Bengaluru", "bangalore"), `createdAt`, `minExperience` ("6+ years"), `type`, `role`, `roleSpecificType`, `visa`, `salaryRange` (blank), `skills`, `isIncomplete`, `lastActive` (null) |
| Posting dates | **`createdAt` is relative text ("about 7 years", "over 8 years"), not a date.** The adapter keeps it as age text in `facts.yc_jobs.*.observed_jobs` and never converts it into a date; `jobs.posted_at` stays null |
| Apply link | `applyUrl` is YC's generic login gate (`account.ycombinator.com/authenticate?continue=workatastartup.com`), not an employer application link. Recorded, flagged gated, never followed. Direct employer apply URLs observed: 0 |
| Pagination | none in the payload (no page/cursor/total keys); jobsUrl is not pagination. Razorpay returned 3 jobs, ClearTax 0. Completeness is therefore unverified beyond "no pagination markers"; source checks stay `complete = 0` |
| `relatedJobs` | present (null here); would hold other companies' jobs; the adapter never reads it (tested) |
| Empty page | ClearTax (directory flag not hiring): component present, `jobPostings = []`, so a verified empty page = `checked_no_listings`, not "not hiring" |

**Adapter changes from the live shapes.** Relative age handled as text (plus an approximate age in days, with "over N" a lower bound), `minExperience` parsed into `experience_min_years`/`experience_text`, `job_type`/`role` kept in the snapshot, gated apply links flagged, a stray control character in a regex removed. `sightings.provider_freshness` is a fixed enum (latest_collection/provider_retained/unknown), so the age text lives in company facts instead. A sanitized fixture (`tests/fixtures/yc_job_page_shapes.json`, only fields the adapter reads, no founder data) now drives 7 more tests. Suite: 401 passed; `pip check` clean; doctor 18/18; SQLite integrity ok, no foreign-key violations; DB hash unchanged.

**Live sample (12 of the 224 India-based companies, 6 flagged hiring and 6 not; 12 requests, 35.5 s, snapshot only).** All 12 returned HTTP 200 with a parsed component: no failure, block, partial or timeout.
- Listings observed: 7 jobs in 3 companies (Razorpay 3, Wifi Dabba 3, Cashfree Payments 1), all Bengaluru-area, all with experience ranges, **all with age text of about 3 to 8+ years** (7 of 7 older than about one year). Zero ISO posting dates; zero direct employer apply URLs.
- Zero listings: 9 companies, including all 6 flagged not hiring and 3 flagged hiring.
- Contradictions: 3, all "directory says hiring, page shows no listings" (Drip Capital, Meesho, Onefin). No Peoplebox-style case (flag not hiring but listings present) occurred in this 12-company sample; that case is covered by the regression test and is still possible in the other 212 companies.
- If imported: 7 new jobs (0 already known), linked to 3 existing canonical companies, 0 new companies.

**Data-quality finding that needs a decision.** Every observed listing is years old. These pages appear to expose long-lived legacy Work at a Startup postings, so "observed" must not be read as "open". Importing them as lifecycle `New` jobs would put multi-year-old roles into the job table. Options for the first import: (a) import everything as observed jobs (history only, never publish), (b) import only listings whose age is under a threshold (for example 1 year) or unknown, (c) hold the import and treat the page only as company-level evidence (`facts.yc_jobs`) until a fresher signal exists.

**Remaining limitations.** Pagination and completeness unproven; location text is free-form; `visa` ("US citizen/visa only") is a US-sponsorship note, not an India-eligibility signal; the 212 other companies are unchecked; the volume and endpoints actually covered by the operator's YC authorization remain user-attested and unverified.

## 16. Freshness and evidence model; sample review; next batch (2026-10-20)

**Decision (operator):** years-old YC listings are **company-level evidence only**. They are not verified-open jobs, not published to the active Jobs shortlist or Sheets, and not closed either (an old posting date alone does not establish closure). A stale listing never makes a company "currently hiring".

**Evidence categories** (assigned in `discovery/adapters/yc_jobs.py`; no schema change):
| Category | Meaning | Where it lives |
|---|---|---|
| `current_employer_confirmed` | an authorized employer/ATS source confirms the opening | reserved; this adapter **never** assigns it (a YC page cannot confirm) |
| `recently_observed_unverified` | listing younger than the recency threshold; open status uncertain | job row (lifecycle `New`, unpublished) plus `facts.yc_jobs.*.observed_jobs` |
| `historical_stale` | old listing still visible | **company facts only** (`historical_listings`: URL, title, location, age text, observed time); no job row |
| `unknown` | age missing or unreadable | job row, flagged unknown; ranked below recent; unpublished |
| `unavailable_incomplete` | company page failed, blocked, partial or not checked | company `current_hiring_evidence`; no listing evidence either way |
The **recency threshold is configurable** (`--recent-days`, default 90, recorded in each snapshot and in each company's facts). It is a prioritization setting only: older than the threshold never closes anything, younger never verifies anything.

**Company-level fields** (`companies.facts.yc_jobs.last_attempt` / `last_successful_check`): `state`, `listed_count`, `job_rows_imported`, `evidence` counts, `current_hiring_evidence` (`recently_observed_unverified`, `unknown`, `historical_stale`, `none_observed`, `unavailable_incomplete`), `disagreement`, `historical_listings`, `observed_jobs`, `recency_days`, `hiring_flag_at_check`. Directory `yc.is_hiring_reported` is never modified.

**Contradictions are credible-evidence only:** directory not hiring + a recent listing (`..._recent_listings_observed`) or an undated one (weaker, `..._undated_listings_observed`); directory hiring + empty page (`directory_hiring_but_no_listings_observed`); directory hiring + only historical listings (`..._only_historical_listings_observed`, informational). **Historical listings never contradict "not hiring".**

**Publication gate (new):** `sheets_sync.load_publishable_job_ids` now never appends jobs that have a sighting from `yc_jobs` or any restricted JobSpy source (`jobspy_indeed`, `jobspy_naukri`, `jobspy_linkedin`) to the review sheet, even for a publishable company; existing rows and human verdicts are unaffected. (Without this, an imported YC job of an `india_based` company would have been appended to Posts at the next TEST sync.)

**The 12-company sample, re-read under this model.** All 12 requests returned HTTP 200 with a parsed page, no failure, partial or block, so each page was fully checked as far as the payload shows (no pagination markers; completeness beyond that is unproven). 3 companies had listings (Razorpay 3, Wifi Dabba 3, Cashfree 1 = 7 listings), 9 had none. Under the 90-day threshold: recently observed 0, **historical 7 (all older than about 3 years)**, unknown 0 -> 0 job rows would be created. Contradictions under the new rules: 3 "hiring but no listings" (Drip Capital, Meesho, Onefin) and 2 informational "hiring but only historical listings" (Razorpay, Wifi Dabba); Cashfree (1 old listing) is hiring-flagged with only historical evidence as well.

**Source data or sample bias?** The sample was the first 12 companies by YC id, which are the **oldest** (batches 2012-2017); none from 2018 or later, although the 224 India-based companies span 2012-2026 (2018: 7, 2019: 22, 2020: 37, 2021: 62, 2022: 43, 2023: 10, 2024: 7, 2025: 4, 2026: 6). So the all-historical result cannot yet be attributed to YC's data; it may be an artifact of sampling old companies. The next batch tests this on newer companies.

**Remaining coverage:** 224 India-based selected; 12 checked; **212 unchecked** (167 flagged not hiring, 45 hiring; 150 Active, 41 Inactive, 20 Acquired, 1 Public); 27 of them are from batches 2023-2026. The 1,124 `potentially_relevant` (fully remote) companies are a later, separately approved tier.

**Proposed next batch (not run):** `yc-jobs-collect --authorized-by-operator --newest-first --limit 40 --skip-checked-in <first snapshot>` = the 40 newest unchecked India-based companies (all 27 from 2023-2026 plus 13 from 2021-2022), regardless of hiring flag: at most 40 requests, 2 s apart, about 2-3 minutes, snapshot only (`data/yc_jobs/`, git-ignored, sha256-checked, a few KB each). If recent listings appear, the rest (172 oldest, about 9 minutes) follows as batch 2. Access limits unchanged: sequential, one request per company, no retries, stop on 403/429, hard caps 300 requests / 30 minutes.

**Expected production changes if the first sample is imported as is:** 0 job rows, 12 companies' `facts.yc_jobs` updated, 12 per-company source-check rows and one import run row; nothing published.

## 17. The 40-company newest-first sample, usefulness analysis and ATS follow-up (2026-10-20)

**Selection (deterministic, logged before running).** `yc-jobs-collect --newest-first --limit 40 --skip-checked-in <first snapshot>`: the 40 newest unchecked companies of the 224 `india_based` ones (YC id 34828 down to 26795; batches Summer 2022 to Fall 2026). All have India in YC's own location data (Bengaluru 31, Delhi/Gurugram 5, Hyderabad 2, Pune 1, Surat 1). Flags: 12 hiring, 28 not hiring, 0 unknown; Active 36, Inactive 3, Acquired 1. The hiring flag excluded nobody. It included Peoplebox (26877, flagged not hiring). Request plan: 40 GETs of `https://www.ycombinator.com/companies/<slug>/jobs`, sequential, 2 s apart (10 s pause after 25), no retries, cap 40, same method and scope as the earlier probe and sample.

**Results.** 40 requests, 121.1 s, all HTTP 200, all pages parsed: 0 failed, 0 blocked, 0 partial, 0 not checked. 11 companies had listings (57 raw listings), 29 had none. Under the 90-day threshold: **13 recently observed (unverified), 44 historical, 0 unknown**. All 57 `applyUrl`s are YC's login gate (direct employer apply URLs: 0). All 11 companies with listings were flagged hiring; **none of the 28 flagged-not-hiring companies had any listing** (so no Peoplebox-style contradiction occurred live; Peoplebox's page was a verified-empty page). Recent listings (4 days to ~2 months old) sit in 4 companies: Lab0 3 (Founding GTM, Forward Deployed Engineer, Founding Product Marketer), Cardboard 5, Surebright 1 (Senior Product Manager), Landeed 4; all India locations, experience from "Any (new grads ok)" to 5+ years. Historical: Landeed 20, Superkalam 12 (5-11 months), and smaller counts at Optifye, Xpay, Infinity, ClickPe, AlgoTest, PrimeVault. Contradictions (credible-evidence rules): 1 "hiring but empty page" (Bolna AI) and 7 informational "hiring but only historical listings".

**Combined 52 companies (12 + 40):** 14 with listings, 64 raw listings, 13 recent, 51 historical, 0 unknown age, 0 direct employer apply URLs, 52 requests, 156.6 s, 0 failures.

**Is the historical result real or an interpretation/parsing error? (checked)**
- Dates: `createdAt` is relative text; parsed consistently ("11 days", "about 2 months", "over 3 years").
- Independent cross-check: job ids grow with time. Across all 64 listings there are **0 inversions** (no larger id is more than 45 days older than a smaller one); recent listings have ids 106,709-116,999, 90-364-day listings 84,880-102,876, over-one-year listings 77-81,534. The age text is therefore correctly interpreted, and the historical share is genuine page content.
- Parsing/pagination: no payload pagination markers on any page; `relatedJobs` ignored; no truncation seen. Pagination completeness is still unproven (no marker is not proof), so scans remain `complete = 0`.
- Separate career sites: Meesho (first sample) has an empty YC page but 28 jobs on its Lever board in our Feashliaa data (not verified open), which shows YC pages can under-report openings.
- The first sample's bias (oldest batches) explained its all-historical result; recent-batch hiring companies do produce recent listings.

**Does YC supply recent jobs?** Modestly. In 52 companies it produced 13 recent unverified listings in 4 recently launched, hiring-flagged companies, nothing for the 34 not-hiring companies, and many stale postings; it never provides employer confirmation or direct apply links. It is useful as (a) a freshness-ranked lead list for new YC companies and (b) company-level evidence, not as a verified current-jobs source.

**Employer ATS confirmation (proposal only; no request made).** Offline cross-check: only 4 of the 224 India-based YC companies have a name-equal company in our Feashliaa data (Meesho: Lever, 28 jobs; Groww: Greenhouse, 2; FamPay: Lever, 4; Able: Greenhouse, 6), so Feashliaa cannot confirm most of them. Public documentation (via search, not fetched; vendor pages should be read before use) describes unauthenticated public job-board endpoints: Greenhouse `boards-api.greenhouse.io/v1/boards/{token}/jobs`, Lever `api.lever.co/v0/postings/{slug}` (asks for about a 1 s delay), Ashby `api.ashbyhq.com/posting-api/job-board/{name}`. Open requirements before any retrieval: confirm each host's current terms and robots.txt, obtain the board token or slug for each YC company (not known; no guessing, no crawling of company websites), set a per-company request budget (one request per board) and reuse canonical YC ids. Recommended as an optional separate source (`yc_ats`) after approval; it is not a replacement for the YC reader.

**Coverage and continuation plan (nothing run).** 224 selected, 52 checked, **172 unchecked** (139 flagged not hiring, 33 hiring; batches 2016: 2, 2017: 12, 2018: 7, 2019: 22, 2020: 37, 2021: 62, 2022: 30; Active 114, Inactive 38, Acquired 19, Public 1). Because older batches produced mostly empty or historical pages and not-hiring-flagged companies produced nothing so far, the remaining value is expected to be low but unmeasured. Recommended: batch 3 = the next 60 newest unchecked (2021-2022, about 3-4 minutes), then decide on the last 112 (about 6 minutes). All 172 would be 172 requests, about 9-10 minutes. The `potentially_relevant` (fully remote) tier is 1,124 companies and needs its own approval. Completed scan states live in the saved snapshots (`--skip-checked-in`) so no company is repeated.

**Expected production changes if the two samples were imported now:** 13 new job rows (all `New`, recently observed unverified, 4 companies, never published), 52 companies' `facts.yc_jobs` updated (51 historical listings preserved there), 52 per-company source-check rows (all `complete = 0`), 2 import run rows, 1 new source row (`yc_jobs`). No Sheets writes.

**Completion status.** Adapter technically complete and tested (423 tests). Sample validation complete (52 companies). Broader coverage pending (172 unchecked). First production import pending the operator's approval and a verified backup.

## 18. First production import (2026-10-20, operator-approved)

Imported snapshot `20261020120438_yc_jobs.json` (the 40-company newest-first sample) after a verified backup (`backups/discovery_pre-yc-jobs-import_20261020-174239.db`, integrity ok). The 12-company first snapshot was **not** imported (its 7 listings are all historical; it can be imported later as company evidence only, on request).

**Result (compared with the backup):** companies 10,183 -> 10,183 (0 created); jobs +13; sightings +13 (all `yc_jobs`); status events +13 (all `New`); source checks +40 (all `success`, `complete = 0`); runs +1; sources +1 (`yc_jobs`); verdicts 0 -> 0 (identical). 40 company rows changed, all and only by the new `facts.yc_jobs` key; no other company, job or sighting changed. SQLite integrity ok, 0 foreign-key violations.

**The 13 jobs (recently observed, unverified; lifecycle `New`, `posted_at` null because YC gives only relative age text):** Lab0 (YC 31424) 3: Founding GTM (AI-native), Forward Deployed Engineer, Founding Product Marketer; Cardboard (31178) 5: Fullstack Engineer, Founding Design Engineer, Founding Designer, Senior Applied ML Engineer Evals & Data, Backend Engineer; SureBright (29850) 1: Senior Product Manager; Landeed (27252) 4: People & Operations Associate, Priority Case Manager, VIP Client Advisor, Property Advisor II. Each sits under the existing canonical company whose YC id matches the page it came from (checked for all 13). The other 44 listings (company evidence only, kept in `facts.yc_jobs.last_attempt.historical_listings`) created no job rows; 7 companies have only historical listings.

**Replay** (same snapshot, second verified backup): jobs created 0, unchanged 13; companies, jobs, sightings and status events unchanged (+0); no duplicate `yc_jobs` sightings; only bookkeeping grew (+40 source-check rows, +1 run row). **TEST sheet:** a read-only `sync --target test --dry-run` after the import shows `Posts: 0 rows to add`, so the publication gate holds (the same dry run lists 4 pending company machine cells and 3 Runs rows from earlier, unapplied changes; nothing was written). LIVE was never touched.

**Remaining coverage plan (nothing run).** 172 unchecked India-based companies, three bounded snapshot-only batches, newest first, each skipping everything already checked:
| Batch | Companies | YC ids | Batch years | Flags (not hiring / hiring) | Requests | Time |
|---|---|---|---|---|---|---|
| 3 | 60 | 26783..23268 | 2021: 30, 2022: 30 | 46 / 14 | <= 60 | about 3.5 min |
| 4 | 60 | 23175..13344 | 2020: 28, 2021: 32 | 51 / 9 | <= 60 | about 3.5 min |
| 5 | 52 | 13343..1267 | 2016-2020 | 42 / 10 | <= 52 | about 3 min |
Total 172 requests, about 10 minutes, same limits (sequential, 2 s apart, no retries, stop on 403/429). Given the yield so far (recent listings only from 2022-2026 batches and only for hiring-flagged companies), a sensible rule is to run batch 3 first and continue only if it finds recent listings. Command for batch 3: `yc-jobs-collect --authorized-by-operator --newest-first --limit 60 --skip-checked-in <snapshot 1> --skip-checked-in <snapshot 2>`.

## 19. Batch 3 (2026-10-20, snapshot only, not imported)

`yc-jobs-collect --newest-first --limit 60` skipping the two earlier snapshots: 60 companies (YC ids 26783..23268; batches Summer 2021 to Summer 2022; 46 flagged not hiring, 14 hiring), 60 requests, 211.2 s, all HTTP 200, 0 failed/blocked/partial. 14 companies had listings (106 raw): **13 recent unverified, 93 historical, 0 unknown, 0 direct employer apply URLs**. All 14 companies with listings were flagged hiring; again **none of the 46 flagged-not-hiring companies had any listing**. Recent listings: Clinikally 10 (Gurugram; E-commerce Manager, Teleconsultation Coordinator, part-time dermatologist, YouTube/social media, quality and training, HR ops, legal intern, DevSecOps, frontend), GoSats 1 (Head of Revenue & Monetisation, Bengaluru), SalaryBox 1 (Tech Lead, Gurugram), AlgoUniversity 1 (Software Engineer, Bengaluru). Clinikally also has 18 historical listings; Enerjazz has 31 and is historical only. 10 of the 14 have only historical listings. Dry-run import preview: 13 new job rows, 0 already known.

**Cumulative (3 snapshots):** 112 of 224 companies checked, 112 requests, 368 s, 0 failures; 28 companies with listings; 170 raw listings (26 recent unverified, 144 historical, 0 unknown, 0 employer apply URLs). Job-id/age cross-check over all 170 listings: 0 inversions. Not-hiring-flagged companies checked: 80, with listings: 0. Production database unchanged by this batch (25,232 jobs, 13 `yc_jobs` sightings). Remaining: 112 unchecked (batch 4: 60, batch 5: 52, older 2016-2021 companies).

## 20. Batch 3 import (2026-10-20, operator-approved)

Verified backup (`backups/discovery_pre-yc-jobs-import_20261020-175453.db`), then the batch 3 snapshot: jobs +13 (all `New`, recently observed and unverified: Clinikally 10, GoSats 1, SalaryBox 1, AlgoUniversity 1, each under the existing canonical company), sightings +13, status events +13 (`New`), source checks +60 (`complete = 0`), runs +1, companies +0 (60 company rows updated only by `facts.yc_jobs`), verdicts unchanged, no other job or sighting changed, 93 historical listings kept as company evidence only. Integrity ok, 0 foreign-key violations. Replay (second verified backup): 0 created, 13 unchanged, no new jobs, sightings or status events; the 13 sightings' `last_seen_at` refreshed and the bookkeeping rows grew (+60 source checks, +1 run) as designed. TEST dry-run: Posts 0 rows to add. Production now holds 25,245 jobs, 26 `yc_jobs` sightings (26 YC job rows in total from batches 2 and 3). Checked 112 of 224 companies; 112 remain unchecked (not collected).

## 21. Batch 4 (2026-10-20, snapshot only, not imported)

60 companies (YC ids 23175..13344; Summer 2020 to Winter 2021 batches plus; 51 flagged not hiring, 9 hiring), 60 requests, 217.0 s, all HTTP 200, 0 failed/blocked/partial. 9 companies had listings (39 raw): **9 recent unverified, 30 historical, 0 unknown, 0 direct employer apply URLs**; again no flagged-not-hiring company had a listing. Recent listings: Codingal 4 (Bengaluru; business development, performance marketing), Gromo 2 (marketing, business development), Zuddl 3 (**United States / Remote (US) sales roles, not India locations**; they stay unverified with unknown remote eligibility and are not an India-eligibility claim). 6 of the 9 companies have only historical listings (Explorex 11 historical). Dry-run import preview: 9 new job rows, 0 already known. Production unchanged (25,245 jobs, 26 `yc_jobs` sightings).

**Cumulative (4 snapshots):** 172 of 224 companies checked, 172 requests, 0 failures; 37 companies with listings; 209 raw listings (35 recent unverified, 174 historical, 0 unknown, 0 employer apply URLs); job-id/age inversions 0. 52 companies unchecked (batch 5: YC ids 13343..1267, batches 2016-2020 mostly older, 42 flagged not hiring and 10 hiring; about 52 requests, 3 minutes).

## 22. Batch 4 import (2026-10-20, operator-approved)

Verified backup (`backups/discovery_pre-yc-jobs-import_20261020-180146.db`), then the batch 4 snapshot: jobs +9 (all `New`, recently observed and unverified: Codingal 4, GroMo 2, Zuddl 3 (US-located, remote eligibility unknown)), sightings +9, status events +9, source checks +60 (`complete = 0`), runs +1, companies +0 (60 rows updated only by `facts.yc_jobs`), verdicts unchanged, nothing else changed; 30 historical listings kept as company evidence only; integrity ok, 0 foreign-key violations. Replay (second verified backup): 0 created, 9 unchanged, no new jobs, sightings or events. TEST dry-run: Posts 0 rows to add. Production now: 25,254 jobs, 35 `yc_jobs` sightings (35 YC job rows from batches 2-4), 172 of 224 companies checked, 52 unchecked (batch 5, not collected).

## 23. Batch 5 and full India-based coverage (2026-10-20; batch 5 snapshot only, not imported)

Batch 5: the final 52 companies (YC ids 13343..1267; batches 2016-2020; 42 flagged not hiring, 10 hiring): 52 requests, 182.3 s, all HTTP 200, 0 failed/blocked/partial. 10 companies had listings (26 raw): **4 recent unverified** (Vahan 1: Lead AI Engineer, Bengaluru; CityFurnish 3: social media, social media executive, procurement intern, Gurugram), **22 historical**, 0 unknown, 0 employer apply URLs. No flagged-not-hiring company had a listing. Dry-run preview: 4 new job rows. Production unchanged by this batch (25,254 jobs, 35 `yc_jobs` sightings).

**Final coverage of the 224 `india_based` companies (all checked, 224 requests, 767 s):** 224 successful scans (47 with listings, 177 verified-empty pages), 0 partial/blocked/failed, 0 unchecked. 235 raw listings: **39 recent unverified, 196 historical, 0 unknown**, 0 direct employer apply URLs. Imported so far: 13 + 13 + 9 = 35 job rows (batches 2, 3, 4 above); pending: batch 5 (4 rows); the 12-company first snapshot (7 historical listings) is not imported. Job-id/age inversions over all 235 listings: 0.

**Finding about the directory flag.** Of the 51 hiring-flagged companies 47 had listings on their YC page; of the 173 flagged not hiring, **0 did** (all 173 pages were verified-empty). Recent listings came from 12 companies, all flagged hiring. So the flag, although never used to select companies, predicted page content almost perfectly, and no Peoplebox-style contradiction (not hiring + listings) occurred anywhere in the India-based set (Peoplebox itself: verified-empty page). The reverse mismatches are common: flagged hiring but only historical listings (35 companies) or an empty page (4). Implication for the optional `potentially_relevant` tier (1,124 companies; 184 flagged hiring, 940 not): check the 184 hiring-flagged ones first and a small fixed sample of the flagged-not-hiring ones to keep testing the pattern, instead of 1,124 blind requests; this is a prioritization based on measured results, not an exclusion rule, and it needs the operator's approval.

**Conclusion on usefulness:** the YC pages yield 39 recent unverified listings over 12 companies out of 224 (about 5 percent of companies), no employer confirmation and no direct apply links; useful as a modest lead list and as company-level evidence, not as a verified current-jobs feed.

## 24. Batch 5 import and Day 9 close-out state (2026-10-20, operator-approved)

Verified backup (`backups/discovery_pre-yc-jobs-import_20261020-180653.db`), then the batch 5 snapshot: jobs +4 (Cityfurnish 3, Vahan 1; all `New`, recently observed, unverified), sightings +4, status events +4, source checks +52 (`complete = 0`), runs +1, companies +0 (52 rows updated only by `facts.yc_jobs`), verdicts unchanged, nothing else changed, integrity ok, 0 foreign-key violations; 22 historical listings kept as company evidence only. Replay: 0 created, 4 unchanged, no new jobs, sightings or events.

**Production state:** 25,258 jobs (39 from `yc_jobs`, all `New`, in 13 companies); 212 of the 224 India-based companies carry `facts.yc_jobs` scan status (current evidence: 13 recently observed unverified, 31 historical only, 168 none observed; 189 historical listings stored as company evidence); the 12 oldest companies (first snapshot, 7 historical listings, 0 job rows) are checked but their snapshot is not imported. TEST dry-run: Posts 0 rows to add; LIVE untouched.

**Status:** adapter technically complete and tested (423 tests); sample and full India-based coverage validation complete (224 of 224 scanned, 0 failures); first imports done for batches 2-5; the optional `potentially_relevant` tier (1,124 companies), the 12-company snapshot import and an employer-ATS follow-up remain pending separate approval.
