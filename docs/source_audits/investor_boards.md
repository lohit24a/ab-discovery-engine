# Source audit: investor and accelerator boards (Day 10)

- **Inspected:** 2026-10-20 (UTC). **Method:** upstream repository code and issues read through the GitHub API, plus public web search results. **No request was sent to any investor board, to Getro or to Consider** for this audit; everything about live endpoints below is therefore *taken from upstream code and marked UNVERIFIED* until the approved probe (section 9).
- **Status (updated, section 14):** both approved probes are complete (Accel/Getro 5 + 1 script + 6 API requests; Lightspeed/Consider 12 + 1 script request). The first requests showed the upstream formats were insufficient; the follow-up steps then found and validated each platform's real mechanism: **Getro: client search API with 0-based pages of 20 and source-side location filters; Consider: page-issued CSRF token plus session cookie, cursor pages of 50 and source-side location filters.** Readers implement exactly that. No complete scan and no import has been run yet.

## 1. Upstream repositories

| Item | jobslop | OpenPostings |
|---|---|---|
| URL | https://github.com/sohan-shingade/jobslop | https://github.com/Masterjx9/OpenPostings |
| Inspected HEAD | `b5dff4bb` (2026-04-10); last push 2026-07-04 | `114fccf6` (2026-06-07) |
| License | **none** (no license file; all rights reserved) | **none** |
| Language | Python scrapers (httpx/asyncio) + Next.js web app | JavaScript (React Native + Node server) |
| Activity | 0 stars; 1 open issue (README docs); runs by GitHub Actions twice daily | 293 stars; 2 open issues (feature/perf) |
| Relevant code | `scrapers/consider.py`, `scrapers/getro.py`, `config/vc_boards.yaml`, `scripts/aggregate.py` | `server/ats/getro/service.js` (no Consider, no Weekday reader) |

**Licensing consequence.** Neither repository grants reuse rights, so **no code is copied**. Only facts about public interfaces (URL patterns, JSON field names, the cursor mechanism) are used, in a clean-room Python implementation written for our own data model. The published jobslop job database is not used and is not assumed fresh. jobslop's web app, Turso/D1 storage, Groq resume matching and GitHub Actions scheduling are all out of scope (no second database, no controller, no scheduler, no Node runtime).

## 2. What the upstream readers show (UNVERIFIED against live boards)

**Consider boards** (`scrapers/consider.py`): `POST {board origin}/api-boards/search-jobs`, JSON body `{"meta": {"size": 50, "sequence": <cursor>}, "board": {"id": <board slug>, "isParent": true}, "query": {}, "grouped": false}`; the slug is either configured or read from the page HTML (`"fixedBoard":"<slug>"`). Response: `jobs[]`, `meta.sequence` (the **cursor** for the next page, absent on the last page), `total`, optional `errors`. No authentication is used upstream. Job fields: `title`, `companyName`, `companySlug`, `companyDomain`, `companyStaffCount`, `locations[]`, `remote`, `hybrid`, `timeStamp` (ISO), `applyUrl`/`url`, `jobSeniorities`, `jobTypes`, `departments`, `markets`, `salary`, `skills`.
**Pagination weaknesses upstream (do not copy):** stops after a cap (2,000 or 500 jobs), stops "incrementally" when pages are mostly already known (skips unseen jobs), treats any HTTP/JSON error as the end of the feed and returns what it has, compares `len(jobs) >= total` on a filtered list, and never records that a scan was partial. The API sorts by relevance, not date.

**Getro boards** (`scrapers/getro.py`, OpenPostings `getro/service.js`): the board's Next.js page embeds `__NEXT_DATA__`; `props.pageProps.initialState.jobs.found[]` holds the first batch and `.total` the reported count; `pageProps.network.id` is the network id. jobslop then tries `GET https://api.getro.com/v2/networks/{id}/jobs?page=N&per_page=50` (`items[]`, `total_count`) but falls back to the first batch **silently on HTTP 401**, so upstream pagination for Getro is **unproven**. OpenPostings reads only the first batch (no pagination). Job fields: `title`, `slug`, `url`, `organization{name, slug, headCount, industryTags}`, `locations[]`, `searchableLocations[]`, `workMode`, `seniority`, `skills`, `createdAt` (unix seconds). A board-internal job URL is `{origin}/companies/{organization slug}/jobs/{job slug}`.
**Consequence:** a "first-page-only" Getro reader would be incomplete exactly as the guide warns; the real paging mechanism (page query parameter, the board front-end's own search call, or something else) must be found in the probe. Until then the reader marks Getro scans partial.

**Identity.** Both upstream tools key jobs by URL and treat the investor as a "backer" label on the job; nothing resolves the employer to a canonical company. We do the opposite (section 6).

## 3. Candidate boards

Only boards with evidence are listed; nothing is invented. "Config evidence" means a URL and id in jobslop's `config/vc_boards.yaml`, not verified by us.

| Network | Public board URL | Platform | Evidence | India relevance | Directory | Jobs | Notes |
|---|---|---|---|---|---|---|---|
| **Accel** | https://jobs.accel.com/ | Getro (network id 8672) | jobslop config; Getro lists Accel as a client | Global portfolio including Accel India companies | company directory likely (`/companies`), UNVERIFIED | yes | **Priority 1 (Getro)** |
| **Techstars** | https://jobs.techstars.com/ | Getro (vendor-listed client; `jobs.techstars.com/companies/...` pages seen in search) | search results; one undated snapshot said 3,289 companies / 4,675 jobs | many non-YC startups, some in India, unverified | yes (company pages) | yes | **Priority 2 (Getro)**; large, strong Stream B value |
| **Space Capital** | https://jobs.spacecapital.com/ (also spacecapital.com/talent) | Getro (inferred from a listing page whose withdrawal address is a getro.com address) | one listing page | low (space sector) | UNVERIFIED | yes | Priority 4: niche |
| **Lightspeed** | https://jobs.lsvp.com/jobs | Consider (slug `lightspeed`) | jobslop config | Lightspeed has an India practice | UNVERIFIED | yes | **Priority 1 (Consider)** |
| Sequoia Capital | https://jobs.sequoiacap.com/jobs | Consider (slug `sequoia-capital`) | jobslop config | Peak XV (ex-Sequoia India) is separate; limited | UNVERIFIED | yes | Priority 3 |
| Andreessen Horowitz | https://portfoliojobs.a16z.com/jobs | Consider (slug `andreessen-horowitz`) | jobslop config | mostly US | UNVERIFIED | yes | Priority 5 |
| General Catalyst | https://jobs.generalcatalyst.com/jobs | Getro (222) | jobslop config | has India presence | UNVERIFIED | yes | candidate |
| Khosla Ventures, Insight, Greylock, BVP, Kleiner, ... | see jobslop config | Getro/Consider | jobslop config | low/unknown | | | not prioritized |
| Peak XV, Elevation, Blume, Nexus, other India funds | **none found** | unknown | web search found no job boards | high | | | **no board URL is invented**; the operator may supply known URLs |
| Weekday | not an investor board in this evidence | n/a | no evidence in either upstream | n/a | | | not used |

All of these are "UNVERIFIED" for pagination, totals, filters, access conditions and terms. Two of the three highest-priority platforms (Getro, Consider) are genuinely distinct and have different schemas; boards are not assumed to share a system because they look alike (each board config names its platform explicitly and the probe confirms it).

**Recommended initial set** (quality and unique coverage first): Getro: **Accel**, then **Techstars**; Consider: **Lightspeed**, then **Sequoia**. Rationale: India relevance (Accel, Lightspeed), likelihood of non-YC companies (Techstars, Accel), two genuinely different reusable platforms, and low maintenance (one reader per platform). Space Capital and a16z are deferred as low India relevance.

## 4. Permission and access (INCONCLUSIVE)

Nothing about these boards' terms or robots.txt has been read. They are public pages and public JSON endpoints used by the boards' own front-ends, but the project rule is "public/permitted interfaces only", so each platform's first live probe starts with `robots.txt` and the operator-approved review of the terms. If a board forbids automated reading and no authorization exists, it becomes a manual-import source (Day 12) and is not read. No authentication, CAPTCHA bypass, proxy or fingerprint change will be used.

## 5. Reader architecture (implemented offline)

`discovery/adapters/investor_boards.py` with one reader class per **platform** (`GetroReader`, `ConsiderReader`) configured per board from `config/investor_boards.json`. Each reader implements a bounded, sequential page walk with an injected fetch function and returns a `BoardScan` (records, pages requested/completed, reported total, scan status, anomalies). Shared code: page-walk engine with duplicate-page and repeated-cursor detection, completeness decision, snapshot (git-ignored, sha256-checked), preview, import, report.

## 6. Identity model (investor vs employer)

- Each board is a **source** (`sources` row, type `investor_board`, id `inv_<board key>`); the investor is never the employer.
- The employer is a canonical **company**. A board company is linked to an existing canonical company only on strong evidence: the board's reported company domain equals exactly one existing company's source-reported domain (YC `website_domain_reported` or a domain previously reported by another board), and the domain is not a shared host. Otherwise a provisional company is created (no domain written to `companies.domain`, `sheet_publish = false`), and the same company seen on a second board resolves to it through the reported domain. Names are never merged by similarity; same-name candidates are listed as possible duplicates only.
- Each board contributes its own **sighting** of the company and of each job (so a company on three boards has three company sightings), and its facts live under `companies.facts.investor_boards.boards.<board key>`.
- Jobs: `observe_job` with the board's job id as `external_id`, the board listing URL, location text, and the board posting date as provider first-seen; remote eligibility stays `unknown` (remote is not India eligibility). Employer apply URLs stay in the snapshot. A job is never "confirmed open" because a board lists it; jobs from boards are held back from Sheets by the existing publication gate.

## 7. Pagination and completeness rules (implemented)

A scan is **complete** only if: the walk ended because the platform said there is nothing more (an empty page, or no next cursor) **and** no page repeated **and** no error occurred **and** (when a reported total exists) the number of distinct records equals the reported total. Otherwise it is **partial** with the anomaly recorded (cap reached, repeated cursor or page, count mismatch, missing page, error). Zero records from a completed walk is a **verified-empty** scan; zero records from a failed request is a **failed** scan. Filters and caps are recorded in the scope string. Partial and failed scans never close or age out anything.

## 8. Source-checks and history

Reuses `sources`, `sightings`, `jobs`, `companies`, `runs`, `source_checks`; no schema change. One `source_checks` row per board and scope (`jobs:all`, `companies:all`) with outcome, `complete`, record count and an error/anomaly summary; the run summary carries pages requested/completed, reported total, distinct jobs and companies. Replays are idempotent.

## 9. Bounded live probe (NOT YET RUN; needs the operator's approval per platform)

See the Day 10 report. Nothing in this audit was fetched from the boards.

## 10. Live probe results (2026-10-20; operator-approved, budgets respected, nothing imported)

All requests: plain GET/POST, honest User-Agent, no cookies sent, no redirects, no retries, 3-5 s apart, hosts locked, raw responses kept outside the repository.

**Probe A - Accel (Getro), 5 of 8 approved requests used**
| # | Request | Result |
|---|---|---|
| 1 | `GET jobs.accel.com/robots.txt` | 200; rules only for three named bots (`ExaSearchBot`, `AhrefsBot`, `meta-externalagent`: `Disallow:` empty, `Crawl-delay: 10`); nothing for other agents. We keep >= 2 s pacing and used 5 s |
| 2 | `GET /jobs` | 200, 341 KB, Cloudflare; Next.js `__NEXT_DATA__` present. `initialState.jobs.found` = **20** jobs, `total` = **45,967** (the whole Accel portfolio, global, not a filtered count). Network id `8672` confirmed (`pageProps.network.id`), name Accel, tabs jobs/companies/talent_network, filters available in the UI (location, seniority, stage, size, function, industry) |
| 3 | `GET /jobs?page=2` | 200 but **identical 20 jobs**: the server-rendered payload ignores `?page=` (Next sees `query: {page: "2"}` but the initial state is unchanged) |
| 4 | `GET /companies` | 200; `companies.found` = **12**, `total` = **709**; company fields: `id`, `name`, `slug`, **`domain`**, `description`, `locations[]` (e.g. "Bengaluru, Karnataka, India"), `industryTags`, `headCount`, `stage`, `activeJobsCount`, `topics` |
| 5 | `GET /companies?page=2` | 200 but **identical 12 companies**: same behaviour |
| (6-8) | terms, `?page=3` | not sent: no terms link on the board host (privacy policy only), and a third page adds nothing after the first two |

Job record (Getro): `id` (int), `slug`, `title`, `url` (the **employer's application URL**, e.g. boards.greenhouse.io / jobs.ashbyhq.com), `createdAt` (unix seconds), `workMode` (`on_site`, `hybrid`, `remote`, null), `locations[]`, `searchableLocations[]`, `locationDetails[{name, areaType, point}]`, `seniority`, `skills`, `source` (`career_page`), compensation fields, and `organization{id, name, slug, headCount, industryTags, stage, topics}`; **no domain on the job's organization** (domains exist only in the company directory). The board listing URL is `https://jobs.accel.com/companies/<org slug>/jobs/<job slug>`. Ids are stable ints; 0 duplicates on the page.

**Consequence.** The server-rendered page is only a first batch (20 of 45,967 jobs; 12 of 709 companies). Complete traversal, and any India filter applied at the source, need the board's client search API (reached by the page's JavaScript, apparently on a different Getro host). That API, its host and its parameters are unverified and were not approved, so they were not touched. The Getro reader now reports `partial` (stopped: first batch only) whenever total > first batch, and never walks `?page=`.

**Probe B - Lightspeed (Consider), 4 of 12 approved requests used**
| # | Request | Result |
|---|---|---|
| 1 | `GET jobs.lsvp.com/robots.txt` | 200; `User-agent: *` `Allow: /` (only `LinkedInBot` is disallowed) |
| 2 | `GET /jobs` | 200, 21 KB, nginx; the page config embeds `"fixedBoard":"lightspeed"` (board id confirmed), `"csrfToken":"<36-char token>"`, a reCAPTCHA site key, and links to `consider.com/legal/terms` and `/legal/privacy` (terms are on a different host) |
| 3 | `POST /api-boards/search-jobs` (page 1, size 50) | **HTTP 412** `{"error":"INVALID_CSRF"}` |
| 4 | same POST with `Origin` and `Referer` | **HTTP 412 INVALID_CSRF** again |

**Consequence.** The Consider search API requires the CSRF token the page issues (header name and any session cookie unknown). No jobs, totals, cursors or employer ids could be observed, so the cursor mechanism remains unverified. Using the page-issued token is the provider's intended first-party flow, not a bypass, but it is a different access method from the one approved, so it was not tried. The reader now reports such a refusal as `failed` (never as an empty board) and can read the board id from the page when it is not configured.

**What was NOT observed:** Consider pagination, Consider totals and job fields, Getro API pagination, any filter applied at the source, the terms of use of Getro or Consider (the Consider terms live on consider.com, outside the approved host).

## 11. Expanded inventory (operator-supplied websites, 2026-10-20)

Platforms below are as stated by the operator unless noted; none has been probed yet. `enabled` is false for all but Accel and Lightspeed.

| Board | Jobs URL | Company directory URL | Platform | Priority | Status |
|---|---|---|---|---|---|
| Peak XV Partners | careers.peakxv.com/jobs | careers.peakxv.com/companies | Consider (stated) | A, India | configured |
| Blume Ventures | jobs.blume.vc/jobs | jobs.blume.vc/companies | Getro (stated) | A, India | configured |
| 3one4 Capital | jobs.3one4capital.com/jobs | jobs.3one4capital.com/companies | **not determined** (not assumed) | A, India | configured; no reader until the page shows its platform |
| Accel | jobs.accel.com/jobs | jobs.accel.com/companies | Getro (confirmed live) | B | probed_partial |
| Lightspeed | jobs.lsvp.com/jobs | (none observed) | Consider (confirmed live: board id and CSRF token) | B | probed_blocked |
| Techstars | jobs.techstars.com/jobs | .../companies | Getro (stated) | B | configured |
| Nexus Venture Partners | consider.com/boards/vc/nexus-venture-partners/jobs | n/a | Consider (stated), hosted on consider.com; counts reported inconsistent | B | configured; confirm it returns real data |
| Sequoia Capital | jobs.sequoiacap.com/jobs | n/a | Consider (jobslop config) | C | configured; separate from Peak XV |
| Crane Venture Partners | careers.crane.vc/jobs | careers.crane.vc/companies | Getro (stated) | C | configured |
| Space Capital | jobs.spacecapital.com/jobs | n/a | Getro (stated) | C | configured |
| Elevation Capital | elevationcapital.com/portfolio | same page | none (official portfolio directory, no shared job API assumed) | Stream B | configured, no reader |
| Stellaris, Kalaari, other India funds | **none found** by targeted search (Stellaris: no board found; Kalaari: not found) | | | | no endpoint invented |

**Per-board bounded probe plans (not run; for approval).** Same method that was approved for Accel's server-rendered page, now for new hosts: `robots.txt` (1) + board jobs page (1; platform check, first batch, total, token presence, board id) + company directory page (1) + the terms link only when it is on the same host = 3-4 requests per board, 3-5 s apart, no API calls, no pagination attempts, snapshot-free (structure only). Peak XV: careers.peakxv.com; Blume: jobs.blume.vc; 3one4: jobs.3one4capital.com (also decides its platform); Techstars: jobs.techstars.com; Nexus: consider.com (3 requests: robots, the board page, the terms link on the same host). About 17 requests in total.

## 12. Licensing and provenance of upstream code

The operator states that they hold separate permission/license for relevant upstream code. This is recorded as **claimed and unverified**: the public repositories (jobslop, OpenPostings) contain no license file and must not be presented as open-source licensed. **No upstream code has been copied.** The implemented readers are original Python written from observed public interface behaviour. If copying code ever becomes necessary, the following is required first: the written grant or license text (or its reference) from the copyright holder, the exact file and function, required attribution, and a record of every modification. Jobslop's app, database, scheduler and Node pieces will not be installed.

## 13. Open decisions (need the operator)

1. **Getro:** approve discovery of the board's client search API: one GET of the board's JavaScript (on `cdn.getro.com`, a new host) to read the real search call, then a bounded number of API calls to the host it names (likely `api.getro.com`, new host), first with a location filter (India) to measure the filtered total before any traversal. Needs the new hosts approved.
2. **Consider:** approve the page-issued token flow: one GET of the board's boards script (path `/mendel/.../boards` on `jobs.lsvp.com`, same host) to learn how the token is sent, then POST pages with the token (and any cookie the server sets), at most the original 12-request budget (8 left).
3. Approve the per-board probe plans in section 11 (about 17 requests, no API calls).
4. Whether the 45,967-job global Accel/Techstars boards should be traversed at all: recommend only an India-filtered scope with its own recorded filter scope.

## 14. Second-stage probes and validated mechanisms (2026-10-20, operator-approved)

**Getro (Accel).** One GET of the board's `_app` chunk on `cdn.getro.com` (1 request, 2.1 MB JavaScript, HTTP 200) shows the board client calling `POST https://api.getro.com/api/v2/collections/<network id>/search/<jobs|companies>` with headers `Content-Type: application/json` and `Accept: application/json` and body `{hitsPerPage, page, filters, query}`; the reply is `{"results": {"jobs"|"companies": [...], "count": N}}`. Six calls to `api.getro.com` (budget 6/6) validated it:
| # | Call | Result |
|---|---|---|
| 1 | page 0, no filter, generic multi-type Accept header | HTTP 406 (Cloudflare, empty body); the board client sends exactly `Accept: application/json` |
| 2 | same with `Accept: application/json` | 200: 20 jobs, count 45,967 (global); snake_case fields (`created_at`, `work_mode`, `searchable_locations`, `organization{...}`) |
| 3 | `filters` as a string | 422 `filters: must be a hash` |
| 4 | `filters: {"searchable_locations": ["India"]}`, page 0 | 200: **count 5,021** of 45,967; all 20 rows India |
| 5 | same, page 1 | 200: 20 rows, **0 overlap** with page 0, count unchanged |
| 6 | `filters: {"searchable_locations": ["Bengaluru, Karnataka, India"]}`, `hitsPerPage: 100` | 200: **count 3,065**; only 20 rows returned (**page size is clamped to 20**) |
Conclusions: pages are 0-based and fixed at 20; the location filter applies at the source; the reported count is for the filtered scope. A complete Bengaluru scan would be about 154 requests (over our hard ceiling of 120); India about 252. Jobs returned are dominated by a few large employers (for example one company's postings fill the India first pages), so a capped partial run is a skewed sample. Job records give the employer's own application URL (`url`), the stable integer `id`, and the organization, but **no company domain**; the company directory (709 companies) does carry `domain`. The company search API (`search/companies`) was not probed; the location filter is not sent to it.

**Consider (Lightspeed).** One GET of the board's `/mendel/.../boards` script (same host, 1.9 MB) shows the client sends `X-CSRF-Token` (the page's `csrfToken`) and `Accept: application/json` on `POST /api-boards/search-jobs`, with body `{meta: {size, sequence}, board: {id, isParent}, query, grouped}`; filters live in `query` (`locations`, `skills`, `jobTypes`, `stages`, `markets`, `remoteOnly`, ...). Requests 5-12 (budget now 12/12):
| # | Call | Result |
|---|---|---|
| 5 | POST with the token but no session cookie | 412 INVALID_CSRF (the token is tied to the session cookie) |
| 6-7 | fresh page GET with a cookie jar (the server set `session`, `session.sig` and load-balancer cookies), then POST with the token | 200: 50 jobs, **total 17,108**, `meta.sequence` cursor (base64 of `{_id, score}`), `availableFilters`, `errors: []` |
| 8-9 | new session, POST with the saved cursor | 200: 50 jobs, **0 overlap** with page 1 (a cursor from an earlier session also works) |
| 10 | `query: {"locations": ["Bengaluru, Karnataka, India"]}` | 200: **total 691** (filter applied at the source; all rows Bengaluru) |
| 11 | same with the cursor | 200: 50 rows, **0 overlap**, total 691 |
| 12 | `query: {"locations": ["India"]}` | 200: **total 1,831** |
Conclusions: cursor pagination and source-side location filtering are demonstrated; a complete Bengaluru scan is 14 result pages plus one session page = about 15 requests (config ceiling 16, not yet approved to run). Job fields: `jobId` (stable string), `title`, `companyName`, `companySlug`, `companyDomain` (present), `companyId` (a name-like string), `locations[]`, `remote`, `hybrid`, `timeStamp` (ISO), `applyUrl` and `url` (employer URL), `normalizedLocations`, `regions`, salary, skills. There is no company directory endpoint in the probe; companies stay derived from jobs. Cookies are held only in the scan's own jar; nothing is sent from a browser profile.

**What this changes in the code.** `scan_getro_api` (Getro) and the session/token `scan_consider` replace the earlier first-page-only assumptions; boards carry `api`, `extra_hosts` and a `location_filter`; the scan scope string records the exact filter (`searchable_locations = ...` / `locations = ...`); completeness is claimed only for that scope; a capped or mismatched run is partial. 487 tests pass.

**Still open.** (1) A complete filtered scan on any board has not been run: Lightspeed/Bengaluru (about 15 requests) is the natural first multi-page validation; Accel/Bengaluru needs more than the ceiling and stays partial. (2) The Getro company search API shape (needed for Stream B, 709 Accel companies = 36 pages) is unprobed. (3) The other boards' platforms are as stated by the operator and unprobed (page probes proposed in section 11). (4) Terms of use: Getro boards show only a privacy link on the board host; Consider's terms are on `consider.com` and were not fetched. (5) The status `validated_live` for Accel and Lightspeed means the mechanism works, not that coverage is complete.

## 15. Complete Lightspeed scan, page probes of five more boards, and the Consider terms (2026-10-20, operator-approved)

**Lightspeed, Bengaluru scope - first complete multi-page validation.** `investor-boards-collect --boards lightspeed --scope both --location "Bengaluru, Karnataka, India"`: 15 requests (1 session page + 14 result pages of 50, cap 16), about 45 s, no errors. Filter applied at the source: **691 of 691 reported jobs retrieved, 0 duplicate or looping cursors, status COMPLETE for the scope `locations = Bengaluru, Karnataka, India` only** (the whole board has 17,108 jobs). Companies are derived from the jobs: 38 distinct employers, all with a reported domain; the companies scope is PARTIAL by design (no directory endpoint). Snapshot kept outside Git (`data/investor_boards/`), nothing imported.
Data observed: posting dates 2023-06-23 to 2026-10-19 (no missing dates; 62 in May, 143 in September, 139 in October 2026); employers are large and concentrated (Texas Instruments 131, Intel 66, Exponent Energy 58, Oolka 56, apna 49, Yubi 43, Sarvam AI 33, Pixxel 28; only 6 employers have a single listing); 691 employer application URLs (Oracle cloud 131, Instahyre 128, Workable 112, Ashby 79, Workday 66); work mode: 4 remote, 10 hybrid, 677 unspecified; 92 listings repeat the same title, employer and location under different job ids (kept separate, flagged as possible duplicates only).
**Dry-run comparison with SQLite (read-only):** employers: 2 strong identity matches by reported domain to existing companies (Zepto, Stripe), 36 new provisional companies; 6 employers share a normalized name with an existing company without domain evidence (for example Intel, FalconX, Handshake, Composio, Cartesia) and would be left unmerged and listed as possible duplicates; jobs: 691 new, 0 already known, 0 cross-board duplicates. Confirmed duplicates against Feashliaa/YC jobs: none detectable by URL (Feashliaa uses ATS board URLs, Lightspeed gives employer URLs; a URL non-match does not prove uniqueness).

**Page probes (3-4 requests each, no API calls, 3 s apart):**
| Board | Result |
|---|---|
| **Peak XV** careers.peakxv.com | robots `User-agent: *` `Allow: /`. **Consider confirmed**; board id **`sequoia-capital-india`** (not "peakxv"); csrf token present. Totals not read (API untouched) |
| **Blume** jobs.blume.vc | robots `Allow: /`, `Crawl-delay: 1`. **`/jobs` and `/companies` both answer HTTP 302 to `/not-found`** for our client; platform not confirmed. The operator should supply the working URL; no alternatives were guessed (the fourth request was left unused) |
| **3one4 Capital** jobs.3one4capital.com | robots `Allow: /`, `Crawl-delay: 1`. **Getro confirmed** (network id 51275, "powered by Getro"); first batch 20 of **660 jobs**; companies 12 of **77**. Small enough for a complete unfiltered scan through the validated client API (33 job pages + 7 company pages) |
| **Techstars** jobs.techstars.com | robots: rules only for three named bots (`Crawl-delay: 10`). **Getro confirmed** (network id 89); first batch 20 of **5,207 jobs**; 12 of **2,868 companies** (a large Stream B source; a location filter is needed for jobs) |
| **Nexus** consider.com/boards/vc/nexus-venture-partners/jobs | robots `Allow: /`. **Consider confirmed**, path-based parent board on consider.com (`fixedBoard` false, csrf token present). Job counts not read, so the reported inconsistency is still unchecked; the board id/API path need one more probe |

**Consider terms of use (`consider.com/legal/terms`, read in full for rule text).** Section 4 "Service Rules" **(l): "use any robot, spider, scraper, or other automated means to access the Service for any purpose without our express written permission, or bypass ..."** Also (h) not to "damage, disable, overburden, or impair" the Service, (s) not to "remove, circumvent, disable, damage or otherwise interfere with security-related features", (t) no access by direct competitors, (u) no collection of personally identifiable information. **Consequence:** every Consider-hosted board (Lightspeed, Peak XV, Nexus, Sequoia) needs Consider's express written permission for automated reads. The operator states authorization for the relevant public investor-board data; this is **user-attested and unverified (no reference number)**, so the Lightspeed scan above was run on that attestation only. The page-issued CSRF token and cookie were used exactly as the board's own client uses them (not as a bypass), but the operator should confirm that their permission covers Consider's service, not just the investors' boards. Getro's terms were not read (the boards link only to a privacy policy on their own host).

**Open items.** (1) Operator confirmation of Consider-level permission before further Consider reads or any import of the Lightspeed snapshot. (2) Blume's working URL. (3) Getro company search API probe (needed for Stream B on Accel 709, Techstars 2,868, 3one4 77 companies; 3one4 can be done fully). (4) Nexus board id/API probe. (5) Production import approval (36 new companies, 2 matched, 691 jobs for Lightspeed).

## 16. First production import: Lightspeed / Bengaluru (2026-10-20, operator-approved, Consider-level permission user-attested)

Verified backup (`backups/discovery_pre-investor-boards-import_20261020-194437.db`, integrity ok), then the complete Lightspeed Bengaluru snapshot. **Result (compared with the backup):** companies +36 (all provisional, no verified domain, `sheet_publish` false); jobs +691 (all `New`, unverified, `posted_at` null, provider first-seen = the board's posting date); sightings +766 (691 job + 75 company sightings, all `inv_lightspeed`); status events +691 (`New`); source checks +2; runs +1; sources +1 (`inv_lightspeed`, type `investor_board`); verdicts unchanged. The two strong identity matches (Zepto, Stripe, by reported domain) were linked to their existing YC companies: only an `investor_boards` facts key was added, their YC and `yc_jobs` facts are semantically unchanged, and no new company was created for them. No existing job, sighting or other company changed. Six employers with a same-name existing company but no domain evidence were created separately and list the existing companies as `possible_duplicates` for review (no name-based merge). The investor is not stored as an employer (the two companies with "lightspeed" in their name, lightspeedhq and lightspeedsystems, pre-date the import). Source checks: `jobs:locations = Bengaluru, Karnataka, India` = success, **complete**, 691 records; `companies:derived from job listings only` = partial, 38 records (never complete).
**Replay** (second verified backup): 0 companies, 0 jobs, 0 sightings, 0 status events created or changed (691 unchanged); only bookkeeping grew (+2 source checks, +1 run). SQLite integrity ok, 0 foreign-key violations. **TEST sheet:** read-only dry run shows `Posts: 0 rows to add` (investor-board jobs are gated) and Companies 0 rows to add; 16 machine cells would update at the next sync from earlier imports (nothing was written). LIVE untouched.

## 17. 3one4 (Getro, complete) and Peak XV (Consider, complete for Bengaluru); employer-URL identity (2026-10-20)

**Getro company search API (3one4, network 51275), 2 probe calls:** `POST api.getro.com/api/v2/collections/51275/search/companies` with `{hitsPerPage: 20, page, filters: {}, query: ""}` returns `{results: {companies: [...], count: 77}}` in **pages of 12** (the server ignores 20 for companies), no overlap between pages 0 and 1, and every company carries a `domain`, `description`, `locations`, `industry_tags`, `head_count`, `stage` and `active_jobs_count`. The investor (3one4 Capital) is itself listed as a company with its own jobs.

**3one4 complete scan (operator-approved, unfiltered):** jobs **660 of 660** (33 pages of 20) and companies **77 of 77** (7 pages of 12), 40 requests in total, both scopes COMPLETE for "all records on the board", no anomalies. First complete multi-page validation of the **Getro** platform. Snapshot only.
**Peak XV complete scan (Consider board `sequoia-capital-india`, filter `locations = Bengaluru, Karnataka, India`):** **572 of 572** jobs, 12 pages of 50, 13 requests, COMPLETE for that filter scope; 37 employers derived from the jobs (companies scope partial by design). Snapshot only.

**Incremental coverage (read-only comparison with SQLite after the Lightspeed import):**
| Board | Jobs retrieved | Employers | Strong domain matches to existing companies | New | Same-name possible duplicates (unmerged) | Employer URL equals an existing job of another source | India location in listing | Posted within ~90 days |
|---|---|---|---|---|---|---|---|---|
| 3one4 (all) | 660 | 77 companies | 2 | 75 | 3 | 3 (Feashliaa) | 569 of 660 | 449 |
| Peak XV (Bengaluru) | 572 | 37 | 5 | 32 | 7 | 98 (26 Feashliaa, 72 Lightspeed-imported) | 572 of 572 | 159 |
| Lightspeed (Bengaluru, already imported) | 691 | 38 | 2 | 36 | 0 | 15 (Feashliaa) | 691 of 691 | 391 |
Requests so far: Lightspeed 15, 3one4 40, Peak XV 13 (all complete). Employers are large and concentrated (Multiplier 96, Capillary 81, Meesho 51 on Peak XV; Scimplify, Lokal 89 each on 3one4). A URL non-match is not proof that a job is unique; a match is strong evidence of the same posting.

**New identity rule (implemented after seeing the overlaps).** A board job whose employer application URL equals the URL of exactly one job already seen by another source is the same posting: the board sighting is attached to that job and the board's company is linked to that job's company (strong evidence); ambiguity (several jobs share the URL) or a conflict with an earlier company link leaves the job separate and is reported (`jobs_attached`, `url_ambiguous`, `url_conflicts`). Board sightings are now filed under the employer URL (the listing URL stays in `jobs.posting_url`) so that boards also match each other. The Lightspeed import was run before this rule: its 15 jobs that duplicate Feashliaa jobs by employer URL were created as separate rows; no merge tool exists yet (Day 11), so they remain known duplicates, listed here, not merged or deleted.

**Status.** Mechanisms validated live and complete scans demonstrated: Getro (3one4) and Consider (Lightspeed, Peak XV). Not yet read: Accel (jobs mechanism validated, complete scan beyond the request ceiling; companies directory 709 needs 60 pages), Techstars (5,207 jobs, 2,868 companies), Nexus (board id/API unprobed), Blume (URLs redirect to /not-found). Nothing from 3one4 or Peak XV is imported yet.

## 18. 3one4 and Peak XV imports (2026-10-20, operator-approved, verified backups, replayed)

| | 3one4 (Getro, all) | Peak XV (Consider, Bengaluru) |
|---|---|---|
| Companies created (provisional, no domain, not published) | 75 | 28 |
| Linked by exact domain / already known | 2 | 4 / 493 |
| Jobs created | 657 | 488 |
| Jobs attached to an existing job by identical employer URL | 3 | 84 (Feashliaa 11, Lightspeed-imported 72, 3one4 1) |
| Employer-URL conflicts reported | 0 | 15 |
| Replay | 0 created, 0 errors | 0 created, 0 errors, 572 unchanged |

Verification after each import (backup comparison): verdicts identical (0), no existing job or company fact changed except the `investor_boards` key on companies, status events appended only (New), no duplicate sightings, integrity ok, 0 foreign-key violations. `sync --target test --dry-run`: Posts 0 rows to add (board jobs are held back); nothing was written to TEST or LIVE.

**Import-order defect found and fixed.** The first 3one4 import processed the company directory before the employer-URL attach pass, so a company created from the directory turned URL evidence into a false conflict and produced 3 duplicate job rows. `_import_board` now runs (1) URL attach, (2) company directory, (3) remaining jobs; a regression test covers it. The Peak XV import used the fixed order.

**Peak XV: 84 attached, 15 conflicts (the 99 previewed).** The 15 are Meesho jobs whose employer URL equals an existing job under company `meesho`, while Peak XV's own "Meesho" resolved to a different company row (already linked to it from earlier data). The rule refuses to attach when the URL match and the earlier company link disagree, so the 15 jobs were created as separate rows. These are known unmerged duplicates, plus the duplicate company rows behind them.

**Known unmerged duplicates (no merge tool until Day 11):** 41 employer URLs are shared by more than one job row: Feashliaa-only 21 (pre-existing, outside Day 10), Feashliaa + Lightspeed 15, Feashliaa + 3one4 3, 3one4 only 2. Nothing merged or deleted.

## 19. Accel company directory scan and page-ceiling change (2026-10-20, operator-approved)

**Limit change (documented, operator-approved):** the hard page ceiling `MAX_PAGES_CEILING` was raised from 50 to 60 and Accel's caps set to 60 pages / 62 requests, because Accel's directory is 709 companies at 12 per page (60 pages). The request ceiling (120) is unchanged. Other boards keep their own lower caps.

**Scan:** Accel (Getro, network 8672) companies, no location filter (the company-API location filter remains unprobed): **709 of 709, COMPLETE**, 60 pages, 60 requests, no anomalies, 709 distinct ids. 705 carry a domain; 171 list an India-type location; 483 report open jobs. Snapshot only (`data/investor_boards/20261020143642_accel.json`).

**Dry-run vs SQLite (no import):** 649 new provisional companies, 60 exact-domain matches to existing companies, 101 companies with a same-name existing company left unmerged (possible duplicates); no jobs in this scope. Accel jobs (Bengaluru filter, 3,065) remain unscanned: about 154 requests, over the ceiling.

**Accel companies import (operator-approved, verified backup, replayed).** 649 provisional companies created (no domain written, not published), 60 linked by exact domain (only the `investor_boards` facts key changed on those), 1,418 company sightings, 0 jobs, 0 errors, verdicts identical, integrity ok, 0 foreign-key violations. Replay: 0 created, 0 errors, 709 existing, nothing duplicated. `sync --target test --dry-run`: Posts 0 rows; nothing written to TEST or LIVE. The 101 same-name possible duplicates remain unmerged (Day 11).

## 20. Techstars (Getro, network 89), Bengaluru-filtered scan (2026-10-20, operator-approved; snapshot only)

**Changes made for this scan (approved):** Techstars enabled with `location_filter` Bengaluru, caps 25 pages / 27 requests, 10 s between requests (robots.txt lists a Crawl-delay of 10 for three named bots; we are not one, but the board is paced at it). New explicit option `--company-location` applies the filter to the company directory search; without it the directory stays unfiltered (the Accel scan is unaffected; a test covers both).

**Count probes (2 requests):** jobs filtered to `searchable_locations = Bengaluru, Karnataka, India`: 87 (of 5,207); company search API **accepts the same filter**: 27 (of 2,868).
**Scans (8 requests, 10 requests in total):** jobs **87 of 87 COMPLETE** (5 pages), companies **27 of 27 COMPLETE** (3 pages), both for the Bengaluru filter scope only. Snapshot `20261020144805_techstars.json`.

**Dry-run vs SQLite (no import):** 35 distinct companies: 33 new provisional, 2 exact-domain matches, 1 same-name possible duplicate (unmerged); jobs: 60 new, **27 attach by identical employer URL** to jobs already imported from Lightspeed (Pixxel, the largest employer with 27 listings). All 87 jobs list an India location; 32 posted within about 90 days.

**Techstars import (operator-approved, verified backup, replayed).** 33 provisional companies created, 1 linked by domain (the second domain match was already linked through a URL attach in the same import), 60 jobs created (all New), 27 attached to Lightspeed-imported jobs by identical employer URL, 0 errors; verdicts identical, no existing job or sighting changed, integrity ok, 0 foreign-key violations. Replay: 0 created, 0 errors, 87 unchanged; the only existing-company change is the `investor_boards` facts key. `sync --target test --dry-run`: Posts 0 rows; TEST and LIVE untouched.

## 21. Nexus Venture Partners (Consider, consider.com) probe: the board is empty (2026-10-20, operator-approved, 5 of 8 requests used)

Requests: board page GET x3 (the first for inspection, two more to obtain a session), `POST consider.com/api-boards/search-jobs` x2 (board id `nexus-venture-partners` isParent true; then the page's own parent id `Nexus Venture Partners`). Both POSTs answered HTTP 200 with `total: 0` and no jobs; the page itself embeds `parents.items["nexus-venture-partners"]` with `numJobs: 0, numCompanies: 0` and renders "0 jobs". Conclusion: the Consider-hosted Nexus board currently lists nothing, which explains the inconsistent counts reported earlier; there is no scan, no snapshot of value (a failed 0-record snapshot from the first attempt is git-ignored and was never imported) and nothing to import. The reader correctly stopped without a board id when `fixedBoard` is false (path-based parent board); no code change was needed. Nexus stays disabled with status `page_probed`. An empty board is not evidence that Nexus portfolio companies are not hiring; their jobs are reachable through other sources (Feashliaa, YC, other investor boards). The first Nexus request to consider.com was approved under the operator's Consider attestation (user-attested, unverified).

## 22. Accel Bengaluru jobs scan: PARTIAL (2026-10-20, operator-approved, snapshot only)

**Limits:** for this one scan the page and request ceilings were raised to 160 and Accel's caps to 156 (operator-approved); afterwards the ceilings were restored to 60/120 and Accel's caps to 60/62 (verified).
**Result:** 151 requests (the board shrank while the scan ran). Retrieved **2,988 distinct** jobs; the board reported **2,993** at the end (3,065 at the first probe); 5 records appeared twice, the signature of pages shifting while the board changed. The reader therefore marks the scope **PARTIAL** (count mismatch + duplicates) and a partial scan can never close or age a job. No repeat scan was run: that needs a new approval. Snapshot `20261020150556_accel.json`.
**Dry-run vs SQLite (no import):** all 130 employers are already known through Accel's company import (0 new companies); 2,916 jobs would be new and 72 would attach to existing jobs by identical employer URL (Peak XV, 3one4, Lightspeed, Feashliaa). All 2,988 list an India location; 2,254 were posted within about 90 days.
**Relevance caution:** the listing is dominated by a few legacy portfolio employers (Instana 664, Nimble Storage 286, HPE SimpliVity 282, Google 128, Cortex by Palo Alto Networks 90), which look like large acquired companies' postings rather than startup opportunities. Their relevance must be judged before anything is published; board jobs are held back from Sheets regardless. Importing this snapshot would add about 2,900 jobs (+11%) that are mostly such employers; that is for the operator to decide.

**Accel jobs import (operator-approved, partial snapshot, verified backup, replayed).** 2,924 jobs created (all New), 64 attached to jobs of other sources by identical employer URL (Lightspeed 17, Peak XV 19, 3one4 45), 0 companies created, 2,988 job sightings, 0 jobs changed or closed. **8 employer-URL conflicts** (the URL matched a job of a different company row than the board company's earlier link) were reported and left separate, which is why the result differs from the preview (2,916 new + 72 attached): Feashliaa's 8 expected attachments became 8 separate rows. Verdicts identical; the only change to the 128 existing companies touched is the `investor_boards` facts key; integrity ok, 0 foreign-key violations. The source check is recorded as **partial** (not complete), so nothing from this scan can close or age a job; no accel job is in any state other than New. Replay: 0 created, 0 errors, 2,988 unchanged. `sync --target test --dry-run`: Posts 0 rows; TEST and LIVE untouched. The conflicts are further unmerged duplicates for Day 11.

## 23. Day 10 closure reconciliation (2026-10-20, read-only checks; no new requests, no new imports)

**Pending Sheets update (TEST Companies tab, dry-run only, never applied).** 40 machine-owned cell updates on 23 unique companies out of 224 sheet rows: `Hiring Evidence` 20 (YC-sourced companies whose text changes from "No jobs observed" to "N of N observed job(s) currently live" because investor-board jobs for them now exist), `Sources` 10 and `Last Seen` 10 (an existing company gains "Investor board: <name>" and a newer last-seen time because a board listed it; for example Drip Capital, Khatabook, Zepto, Meesho). No `Company`, `Domain`, `Location`, `record_id`, `Your Verdict` or `Your Notes` cell is touched; 0 rows deleted, 0 duplicate or unmatched record ids (all 224 sheet ids exist in SQLite). All differences are expected machine-owned updates; the sync stays **deferred** (not applied). Caveat for Day 11: the existing "currently live" wording counts a board listing as live evidence, which a board listing alone does not prove; the wording should be reviewed before the next sync.

**Count reconciliation (SQLite; the oldest `pre-investor-boards-import` backup is the end of Day 9: 10,183 companies / 25,258 jobs):**

| Import | New jobs | Jobs attached to an existing job | New companies | Companies matched to existing |
|---|---|---|---|---|
| Lightspeed | 691 | 0 | 36 | 2 |
| 3one4 | 660 (3 are duplicates created before the import-order fix) | 0 | 75 | 2 |
| Peak XV | 488 | 84 | 28 | 9 |
| Accel companies | 0 | 0 | 649 | 60 |
| Techstars | 60 | 27 | 33 | 2 |
| Accel jobs (partial) | 2,924 | 64 | 0 | all 130 employers already known |
| **Total** | **4,823** (25,258 to 30,081) | **175** | **821** (10,183 to 11,004) | **75** |

Board observations: jobs 2,988 + 691 + 572 + 660 + 87 = 4,998 = 4,823 new + 175 attached by identical employer URL; companies 709 + 38 + 37 + 77 + 35 = 896 = 821 new + 75 matched to an existing company (exact domain, or an earlier sighting). Within-source de-duplication: 5 repeated Accel records were dropped by the reader before import (the feed shifted). Validation rejected nothing. Versus the Day 9 baseline: 0 sightings, jobs, companies, status events, verdicts, runs, sources or source checks are missing; 0 existing jobs changed company, title or state; 0 companies changed name, domain, aliases or identity status; 0 sightings changed job, company, URL or source. No merge other than the evidence-based rules (exact domain; identical employer URL to exactly one existing job).

**Persisted source checks (inv_ sources).** Lightspeed jobs, Peak XV jobs, Techstars jobs and companies, 3one4 jobs and companies, Accel companies: success + complete, each with its filter scope in the scope text. Lightspeed and Peak XV companies (derived from jobs): partial by design. Accel jobs: partial, with the anomaly text preserved ("duplicate record ... inside the feed" x5; "count mismatch: board reports 2993, retrieved 2988"). Each import replay adds one more row with the same snapshot timestamp (rows are per import run); this is not a second scan. Nexus: nothing was imported, so no source check exists; its verified-empty result (board page `numJobs 0`, API total 0) is recorded only in config and this audit, not as a persisted check. Blume and the unprobed boards: configured/deferred, no check, never treated as empty. The database trigger rejects Not Seen / Closed without a successful complete source check, no investor-board code path attempts closure, and all 4,834 jobs touched by investor boards are still `New`. Board statuses in the config were corrected at closure (3one4, Peak XV, Techstars: `validated_live`).

**Safety state.** Page and request ceilings are back at 60 / 120; Accel caps 60 / 62; Techstars 25 / 27 at 10 s pacing; Lightspeed 15/16, Peak XV 15/16, 3one4 40/41 (2 s); Nexus, Blume, Space Capital, Sequoia, Crane and Elevation are disabled. Indeed/Naukri/LinkedIn remain `enabled: false`; Glassdoor stays retired. The investor module has no scheduler, thread or subprocess and no reference to credentials, `.env` or any personal Brain file. Board jobs remain blocked from Posts (dry run: Posts 0 rows to add). `--company-location` is optional and off by default; without it the company directory is read unfiltered.

**Permission evidence, stated accurately.** All reads rest on the operator's attestations in chat (user-attested, unverified, no reference numbers) for upstream code and for public investor-board data, extended to Consider-hosted boards. Consider's terms (4(l)) prohibit automated access without express written permission; that permission was NOT inspected by us. Getro's terms were never found, so no Getro permission is confirmed; we relied on the operator's attestation, public pages and robots rules. Techstars' robots.txt lists a Crawl-delay of 10 for three named bots; we are not one of them but paced that board at 10 s.

**Deferred to Day 11 (identity reconciliation and lifecycle), with the evidence Day 10 produced:** 26 cross-source duplicate job pairs by identical employer URL left as separate rows (Feashliaa + Lightspeed 15, Feashliaa + Accel 8, Feashliaa + 3one4 3), 23 further same-source shared-URL groups outside Accel (21 Feashliaa, 2 3one4), 445 URLs shared by several jobs inside Accel alone (apparently generic careers URLs; never merged on URL when titles differ), duplicate company rows behind the 8 Accel and 15 Peak XV URL conflicts (for example `meesho`/`Meesho`), 101 Accel and 11 other same-name company candidates, the Accel jobs relevance question (Instana 664, Nimble Storage 286, HPE SimpliVity 282), re-scanning Accel jobs to a complete scope, and the Companies "Hiring Evidence" wording.
