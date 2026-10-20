# Source audit: JobSpy (speedyapply/JobSpy) - Naukri and Indeed India

- **Inspected:** 2026-10-20 (UTC), before any adapter code or live request.
- **Verdict (summary):** the library is healthy and installs cleanly on Python 3.14.3, **but both boards we wanted are reached through unofficial private endpoints. For Indeed that access is explicitly prohibited by Indeed's terms and by robots.txt; for Naukri permission could not be established.** No JobSpy request has been sent to either board, and no adapter was written. A decision is needed (see section 9).

## 1. Repository and package

- Repository: https://github.com/speedyapply/JobSpy, MIT licence, not archived, ~4.5k stars, 27 open issues. HEAD `8dd83cdd` (2026-10-20, "publish mcp server as mcp-jobspy").
- Releases: **v1.4.0 (2026-10-19)**, v1.3.0 (10-08), v1.2.0 (10-02), v1.1.82 (GitHub release dated 2026-09-30; its PyPI upload is dated 2025-07-28, an inconsistency we did not resolve). Very active in October 2026 (CLI added 10-19, MCP server 10-19, failure reporting in `result.attrs` 10-15, strict argument validation 10-12, mypy/ruff CI).
- PyPI `python-jobspy` 1.4.0: `requires_python >=3.10`, classifiers list Python 3.10-3.15. Requires `beautifulsoup4>=4.12.2`, `curl_cffi>=0.16.2`, `markdownify>=1.1.0`, `pandas>=2.1.2`, `pydantic>=2.3.0`, `requests>=2.31.0`; optional extra `mcp` (not wanted, not installed).
- Boards in 1.4.0: LinkedIn, Indeed, ZipRecruiter, Glassdoor, Google, Bayt, Naukri, BDJobs, HelloWork. README: "Google Jobs is currently unavailable" (open issue #284 since 2025-06).
- Open issues are mostly feature requests (Wellfound, RemoteOK, Workable, async support, salary conversion); #284 (Google unavailable) is the notable failure.

## 2. Installation and Python 3.14.3 compatibility

- `pip install --dry-run python-jobspy==1.4.0` in the project `.venv` resolved without touching any installed package. It would add 15 packages: python-jobspy 1.4.0, pandas 3.0.6, numpy 2.5.4, curl_cffi 0.16.3, pydantic 2.14.1, pydantic_core 2.50.1, annotated-types, typing-inspection, typing_extensions, beautifulsoup4 4.15.0, soupsieve, markdownify 1.2.3, python-dateutil, six, tzdata. All have `cp314` Windows wheels.
- A real install into a throw-away venv outside the project (Python 3.14.3) succeeded, `pip check` was clean, `import jobspy` works, and the `Site` list and `ScraperInput` validation run. **The project `.venv` was not changed.**
- Cost: pandas + numpy + curl_cffi + pydantic are heavy new dependencies for a library we would call one function of. Per `CLAUDE.md` they need explicit approval before installation.

## 3. Integration surface

`scrape_jobs(site_name, search_term, location, results_wanted, hours_old, country_indeed, fetch_description, proxies, ...)` returns a pandas DataFrame; `df.attrs["errors"]` / `["warnings"]` report per-board failures (new in 1.4.0). A `jobspy` CLI (JSON lines on stdout, errors on stderr) also exists. The Python API is simpler and gives structured failures; the CLI would add a subprocess layer for no benefit.

## 4. How Naukri and Indeed are actually accessed (read from the source)

- **Indeed** (`jobspy/indeed`): POST to `https://apis.indeed.com/graphql` using Indeed's **mobile-app API key** hard-coded in `indeed/constant.py` (header `indeed-api-key`), with `indeed-co` / `indeed-locale` headers. This is not an Indeed-published API; it is the app's internal endpoint.
- **Naukri** (`jobspy/naukri`): GET `https://www.naukri.com/jobapi/v3/search` (and `/jobapi/v4/job/<id>` with `fetch_description`) with an `nkparam` token generated client-side from a public key in the library. On HTTP 406 it reports "recaptcha required: the address is rate limited, or the request token was rejected". 3-7 s random delay between pages.
- Both use TLS-fingerprint impersonation / rotating sessions (`curl_cffi`, `TLSRotating`, `RequestsRotating`) and optional proxy rotation. The README advertises "Proxy support to bypass blocking" and says "all of the job board sites are aggressive with blocking".

## 5. Permission and terms (the deciding evidence)

| Board | Evidence | Reading |
|---|---|---|
| Indeed | Indeed Terms of Service, "Last Updated: September 29, 2026", section D.21 (Site Rules): "Access the Site through any means other than the public interfaces provided by Indeed"; "Use any automated system (bots, scrapers, spiders, AI or Agentic AI) to access, data-mine, or submit content to the Site"; "You may not crawl, scrape, extract data from, reproduce, duplicate, copy"; crawling is conditionally allowed "solely as outlined in our robots.txt"; "Bypass any security measures or Site limitations"; D.22.4: scraping/bot restrictions stay in force; AI connectors only through a "Permitted AI Connector". `https://apis.indeed.com/robots.txt` = `User-agent: *` / `Disallow: /` (the host JobSpy calls). `in.indeed.com/robots.txt` also disallows `/viewjob` and `/rc/`. | **Not permitted.** Automated access through JobSpy is prohibited by the terms and by robots.txt. |
| Naukri | `robots.txt` and the terms page returned HTTP 403 to our fetch tool; a direct request failed with a TLS error (self-signed certificate in the chain) which we did not bypass. The library itself treats 406/recaptcha as normal behavior. | **Unknown.** Permission cannot be established; the endpoint is a private site API protected by anti-bot measures. |

Project rules that apply: `CLAUDE.md` ("No login automation or restricted-site scraping ... Use public/permitted interfaces; otherwise use manual import"), the guide's Day 8 text ("If a source is inaccessible or not authorized for automated retrieval, keep it disabled and use manual import"), and the Day 8 instruction not to use prohibited access paths.

## 6. Data fields (from `JobPost`, not yet observed live)

`id` (`in-<key>` / `nk-<id>`), `job_url`, `job_url_direct`, `title`, `company`, `location{city,state,country}`, `date_posted` (date only), `is_remote`, `job_type`, salary fields, `experience_range` (Naukri), `description` (only with `fetch_description` on Naukri; included for Indeed), `company_*` extras. Stable identifiers exist per board (Indeed job key, Naukri job id). Posting date is a day, not a time. Eligibility for India-based candidates would still be inferred from location text.

## 7. Pagination, limits, failure behavior

Both scrapers paginate until `results_wanted` or the board runs out; boards cap at roughly 1,000 results per search. Failures are swallowed into `scraper.error` / `df.attrs["errors"]` and the call still returns a (possibly empty) DataFrame - an empty result is **not** proof of "no jobs" and must be a failed/partial source check, never a closure signal. Naukri 406 and Indeed GraphQL errors are the expected failure modes.

## 8. Experiments

**None run.** Indeed: prohibited, so no request was made. Naukri: permission unknown, so no request was made pending your decision. Overlap with the 25,219 Feashliaa jobs therefore could not be measured; Feashliaa already covers Greenhouse/Lever/Ashby/Workday/iCIMS/BambooHR/Paylocity employer boards, which JobSpy's Indeed/Naukri would not duplicate directly (they list aggregated postings).

## 9. Options and recommendation

1. **Indeed via JobSpy: do not enable, do not test.** Prohibited by Indeed's terms and by robots.txt on the API host. Keep the source disabled permanently in `config`. Legitimate Indeed routes are manual link import (Day 12) or the official Indeed MCP connector (backlog appendix; interactive, Claude-assisted).
2. **Naukri via JobSpy: do not enable until permission is established.** Recommended: leave disabled. If you want it tested, you would need to confirm that you accept using Naukri's private API against its anti-bot posture; I would run at most a few small searches, stop at the first 406/block, and never use proxies. I recommend against it.
3. **Better-aligned alternatives already in the plan:** direct employer ATS public APIs (Greenhouse Job Board API, Lever Postings API, Ashby public posting API - official, documented, no scraping) for boards found via YC/Feashliaa; manual link import; investor-board readers (Day 10).
4. If you approve any JobSpy use: the dependency install (15 packages; disk size not measured) needs approval; the adapter would import `jobspy` lazily, default disabled, treat `df.attrs["errors"]` as failed/partial checks, never close jobs, and publish nothing to TEST.

## 11. Update after the operator's authorization (2026-10-20)

The operator stated that they hold authorization from both Indeed and Naukri for automated job-data access and approved proceeding with JobSpy. That statement changes the terms-based reading in section 5 (the prohibitions apply to unauthorized access) but is not something we can verify, and it does not by itself supply a provider-issued credential. Specific points still open:

- **Indeed:** JobSpy authenticates with the mobile app's API key that ships inside the library (`indeed/constant.py`), not with a credential issued to the operator. If Indeed granted a credential or documented API scope for this use, it should replace that key; otherwise this is the one requirement to clarify with the provider.
- **Naukri:** access uses a client-generated `nkparam` token against a private API. If Naukri granted a credential, token or documented challenge process for this use, we need it; none is configured.
- No proxies, CAPTCHA solving or other block evasion beyond the library's default Chrome-style TLS handshake were or will be used.

**Install (done, approved):** `python-jobspy==1.4.0` installed into the project `.venv` with 14 dependencies (pandas 3.0.6, numpy 2.5.4, curl_cffi 0.16.3, pydantic 2.14.1, pydantic_core 2.50.1, beautifulsoup4 4.15.0, soupsieve 3.0.3, markdownify 1.2.3, python-dateutil, six, tzdata, annotated-types, typing-inspection, typing_extensions). `pip check` clean, `import jobspy` works, all 243 existing tests still pass, `requirements.txt` re-pinned (37 lines).

**Library behavior confirmed in `util.py`:** TLS certificate verification is on by default (`verify`, with a `ca_cert` option); each request has a 15 s timeout and **no retries or backoff**; any status other than 200 raises `response status code N` (403/429 add the hint "blocked: use a proxy"); the session impersonates Chrome's TLS fingerprint (`curl_cffi`). Failures land in `df.attrs["errors"]`; they do not raise from `scrape_jobs`.

**Naukri certificate error - root cause (diagnosed without weakening verification):** the earlier error from Python's `urllib` was "self-signed certificate in certificate chain". Naukri's chain is `*.naukri.com` -> GlobalSign GCC R46 OV TLS CA 2025 -> **GlobalSign Root R46** (a root created in 2023). `openssl` verified it OK against its own bundle, and the Windows machine store did not yet contain that root when the error occurred. After other software (the Windows `curl.exe`/Schannel check that was run next) made Windows fetch the root automatically, Python's default store trusted it (R46 is now present among the 82 loaded roots) and the handshake to naukri.com and apis.indeed.com succeeds. The `certifi` bundle (117 roots, certifi 2026.10.18) also contains R46 and also succeeds, so a safe fallback exists: pass `ca_cert=certifi.where()` to `scrape_jobs` instead of ever disabling verification. This is an inference from the before/after behavior; the exact trigger on that machine was not isolated. `curl.exe` to naukri.com timed out after 25 s with no bytes, which looks like the site not answering non-browser clients, not a TLS problem.

**Live probes:** the first attempt was blocked by the assistant environment's permission layer before any request was sent (a tool setting, not a source result); after the operator added a permission rule they were run one source at a time (section 12). No block, rate limit, CAPTCHA or certificate problem occurred.

## 12. Live results (2026-10-20; nothing written to SQLite, TEST or LIVE)

Setup: `scrape_jobs` from the project `.venv`, no proxies, library defaults (Chrome-style TLS, 15 s timeout, no retries), certificate verification on. Six queries per board, 15 results wanted each, pausing 5 s between searches; results saved only in the assistant scratch folder. Indeed: `location="Bengaluru, Karnataka, India"`, `country_indeed="India"`. Naukri: `location="Bengaluru"`. Queries (taken from the guide's example role types and your stated areas, not new preferences): associate product manager, business analyst, business operations, AI implementation, forward deployed engineer, data analyst. Plus one Naukri run with `fetch_description=True` for 5 jobs.

| Measure | Indeed | Naukri |
|---|---|---|
| Searches / HTTP status | 6 searches, all returned 15 rows, 1 page request each, 0.9-1.3 s, `attrs["errors"]` empty | same: 6 searches, 15 rows each, 0.5-1.2 s, no errors, no 406/429 |
| Postings | 90 rows, 89 unique ids (1 duplicate across queries) | 90 rows, 90 unique ids |
| Location | all 90 name Karnataka/India; shown as state code, e.g. "KA, India" (no city in the output) | all 90 name Bengaluru/India; multi-city lists such as "Bengaluru, Hyderabad, Pune, India" occur |
| Remote flag | 3 | 1 |
| Experience requirement | **absent** (field never filled) | **present on all 90** ("2-7 Yrs") |
| Description | **on all 90** (markdown) | only with `fetch_description` (1 extra request per job; worked for 5/5) |
| Posting date | on all 90, a date only; median age 46 days, 53 older than 30 d, 22 older than 90 d, oldest 349 d | on all 90; median age 6 days, 9 older than 30 d, oldest 132 d. A garbled probe term surfaced postings from 2024-09-27, so stale postings stay listed |
| Direct employer URL (`job_url_direct`) | 88 of 90 | 50 of 90 |
| Identifier | `in-<hex key>` (stable per board) | `nk-<numeric id>` (stable per board) |
| Equals a Feashliaa job URL (strong identity evidence) | 8 of 88 direct URLs | 2 of 50 |
| Company name equals a Feashliaa board name / YC name (hints only) | 21 / 3 rows | 27 / 1 rows |
| Pass the Day-6 title prefilter | 84 of 89 | 89 of 90 |
| ...of those, no URL match in Feashliaa | 76 | 87 |
| ...of those, posted within 30 days | 35 | 80 |
| Early-career (stated minimum experience <= 3 years) | not available | 37 |

Cross-source: 1 direct URL shared by Indeed and Naukri. Employer-page spot check: 8 sampled direct URLs (4 per board) all answered HTTP 200 with the title words on the page (Cognizant, NTT DATA, JPMorganChase, Thomson Reuters Workday, Accenture, a Zoho Recruit page and others). Aggregator pages (`in.indeed.com/viewjob`, naukri.com) were deliberately not fetched. An accessible page is not proof of an open vacancy.

Reading: "no URL match" is evidence of no overlap, not proof of uniqueness (other URL forms could exist). The overlap is small by identity evidence, so these boards mostly surface employer career sites (large companies' own portals, Zoho Recruit, Workday tenants) that Feashliaa's seven ATS platforms and title/geo filter do not hold. Request volume: 19 requests in total (12 searches, 1 search plus 5 detail requests for the description test, plus employer spot checks).

**Not established:** provider rate limits for repeated or larger use (only ~19 small requests were made); whether either board allows the volume of a daily run; Indeed's `location` lacking a city in the India output.

## 13. Interface decision: Python API, thin layer (2026-10-20; design only, no code written)

Reviewed from upstream `jobspy/cli.py`, `records.py`, `mcp.py`, `util.py` (v1.4.0).

| | `scrape_jobs()` (Python API) | `jobspy` CLI (JSON Lines) | `mcp-jobspy` (MCP server) |
|---|---|---|---|
| What it is | The function everything else wraps | `cli.py` calls `scrape_jobs(**args)` and prints selected fields | `mcp.py` calls `scrape_jobs` and exposes `search_jobs` / `get_job_details` tools |
| Per-board failures | Structured: `df.attrs["errors"]` and `["warnings"]` per board | Only free text on stderr; exit code 1 only when **every** requested board failed, so a partial failure is invisible to the exit code | Returned as `errors`/`warnings` in the tool result |
| Fields | Full `JobPost` (all columns, typed) | Preview fields by default; `--fields all` gives all; **fields a board did not give are omitted** (NaN dropped), so "missing" and "not requested" must be inferred | Preview only (up to 25 jobs per board), details via `search_id` |
| Control | All parameters (`proxies`, `ca_cert`, `hours_old`, `offset`, `fetch_description`...), per-call retries and delays are ours | Same parameters as flags, one process per search, no retry hook | Interactive, stateful (`search_id`) |
| Extra cost | none (already imported) | subprocess, JSON re-parsing, stderr scraping | extra dependency `mcp>=2.3.0` and a server process |
| Fit for a repeatable pipeline | **Best** | Works but loses the diagnostics we require | None: designed for an LLM to explore interactively |

**Recommendation: use `scrape_jobs()` through one thin module, one board per call.** One board per call keeps Indeed and Naukri independently selectable, gives per-board timing and error attribution, and lets us wrap each search with our own bounded delay and 429 backoff (the library has none). The CLI is rejected because it would hide partial failures and omit-vs-empty information; the MCP gives no benefit for repeatable ingestion (and job text returned through an LLM tool is untrusted input). **The MCP server is not installed**, and neither JobSpy scraper is rewritten.

**Smallest integration module (to be built after approval):**
- `discovery/adapters/jobspy_source.py` (not named `jobspy.py`, which would shadow the library): lazy `import jobspy`; `run_search()` calls `scrape_jobs` for one board with a bounded delay between searches, a capped retry with backoff when the board's error text shows 429, and a fail-closed proxy rule; converts the DataFrame to plain dicts; normalizes to candidates; imports them with the existing `observe_job` / `record_sighting` (plus one small lookup that finds an existing job by the sighting URL).
- Each board is its own source (`jobspy_naukri`, `jobspy_indeed`) with its own source check, so they can be selected, fail and be skipped independently, and both carry `config_ref = "no-closure-evidence"`.
- Search settings in an editable `config/jobspy_searches.json`; board switches in `config/sources.json` with both JobSpy boards **disabled by default**. A board runs only if it is named in `--sources` **and** enabled in the config.
- Mapping: id `in-...`/`nk-...` scoped to the source = external id; board job URL = sighting URL; `job_url_direct` (employer URL) is strong cross-source evidence - if it equals an existing sighting URL (for example a Feashliaa job) the new sighting attaches to that job, otherwise a new job is created; **never matched by title**. Company = provisional, keyed by (source, normalized company name), `sheet_publish = false`, possible YC/Feashliaa name matches recorded as hints only. `date_posted` goes to `sightings.provider_first_seen_at` (a board date, not an employer posting date; `jobs.posted_at` stays empty); local first/last seen remain ours; `provider_freshness = latest_collection` (the board listed it in this query) which is still not "open"; Naukri `experience_range` -> `experience_text` plus parsed minimum/maximum; eligibility `eligible` for India-located results, `possible` otherwise.
- Source check per search scope (`term|location`): outcome `success`, `partial` when rows came with warnings, `failed` when `attrs["errors"]` names the board; **`complete` is always false** because a query returns a window, not a full listing; nothing is ever marked Not Seen/Closed; nothing is published to the sheet (existing Posts gate).

## 14. Network and privacy configuration (proposal)

What the library does: `proxies` (list or string) are rotated round-robin per request and applied to every request, so a configured proxy that fails produces an error, not a direct connection - **except** an entry named `localhost`, which means "no proxy" and must never be configured; `ca_cert` replaces the TLS verification bundle for the session (use it only for a proxy's own CA, otherwise leave default verification on); LinkedIn clears cookies per request, Indeed/Naukri keep in-memory cookies for the process only; the session impersonates Chrome's TLS fingerprint; timeout 15 s; no retries.

Proposed configuration, to avoid exposing the home IP **without claiming anonymity**:
1. Preferred: send searches through a proxy or VPN egress that **you control or contract with and that the boards' authorization covers** (a small VPS or static-IP VPN is also easy to give to a provider that wants IP allow-listing). Read from `.env` as `JOBSPY_PROXIES` (comma list) and optional `JOBSPY_CA_CERT`; placeholders only in `.env.example`; credentials never printed, logged or committed (reports show the proxy host only).
2. **Fail closed:** if a proxy is configured and unreachable, the source check is `failed`; the adapter never retries without it. If no proxy is configured the run says so in the report ("direct connection from this machine") and requires `JOBSPY_ALLOW_DIRECT=true` in `.env`, so a direct (home-IP) run is a visible choice.
3. Limits: at most a few searches per source per run, small `results_wanted`, delays of several seconds, no parallel searches, no proxy rotation tricks, no CAPTCHA solving.
4. Not claimed: the proxy operator sees all traffic; the boards still see request patterns and a Chrome-like fingerprint; a proxy is a different party handling traffic and must be one you trust. Residential-proxy resale networks are not recommended.
5. Personal data: the adapter sends only search terms and locations. No resume, account cookies, login or other credentials are collected, stored or transmitted.

## 15. LinkedIn and Glassdoor - candidates only (not enabled, not tested)

> **Retired source - Not part of the active discovery engine.** The Glassdoor material in this section is kept as engineering evidence for decision D-23 (2026-10-20); the engine supports only Indeed, Naukri and LinkedIn.

- LinkedIn (`jobspy/linkedin`): uses the logged-out **guest** search endpoint (`/jobs-guest/jobs/api/seeMoreJobPostings/search`), HTML parsing, no login; README says it rate-limits bursts with 429 and recommends proxies; descriptions need an extra request per job.
- Glassdoor (`jobspy/glassdoor`): GraphQL with a hard-coded `gd-csrf-token` embedded in the library; README: rate limits above about 2 requests per second.
- **Authorization gap:** your authorization statement named Indeed and Naukri only. Before any request to LinkedIn or Glassdoor we need your explicit confirmation that you hold authorization for them too (and note LinkedIn's terms are widely understood to prohibit scraping; Glassdoor's embedded token is not a credential issued to you). Planned validation when confirmed: one board at a time, 10 Bengaluru results for one query, direct or proxied exactly as in section 14, stop at the first 403/429/CAPTCHA, results kept out of the database.

## 16. Network privacy: Surfshark OS-level VPN instead of a JobSpy proxy (2026-10-20)

**Decision:** use the operator's Surfshark Windows app (system-wide VPN) as the default network layer for any live job-board request, rather than configuring `JOBSPY_PROXIES`. Reasons: one VPN covers Python, tests and tools consistently; no proxy credentials in `.env`; no per-library proxy rotation. This supersedes the proxy-first proposal in section 14 for the default case; the section 14 rules still apply in spirit: **fail closed** (no live board run when the VPN is not verified), direct runs are an explicit visible choice, no rotation or other evasion, and no claim of anonymity. A JobSpy `proxies` setting remains available only if the operator later asks for it.

**What was checked (read-only; no board contacted; nothing about the machine changed; no IP address printed or stored):**

| Check | Result on 2026-10-20 |
|---|---|
| Surfshark client present | Only a `SurfsharkSetup` process was running (installer). No Surfshark service, WireGuard/OpenVPN process or VPN network adapter exists yet. |
| Network adapters up / default route | Only the Wi-Fi adapter is up and carries the IPv4 and IPv6 default routes. |
| Egress seen by the project `.venv` Python 3.14.3 | **Consumer-ISP network in India - not Surfshark.** Windows PowerShell and Windows `curl.exe` leave from the same address as Python (compared by one-way hash in memory). |
| Proxy settings | No `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` variables; WinHTTP is direct; the user proxy setting is off with no PAC script; Python's own proxy view is empty. Nothing would interfere with a VPN. |
| DNS | Both DNS servers on the Wi-Fi adapter are the ISP's public resolvers (no VPN DNS yet); no DNS policy (NRPT) rules. |
| TLS verification | Untouched; the egress lookup used normal verified HTTPS. |

**Conclusion: Python's outbound traffic is NOT VPN-routed today** because the VPN is not connected yet; this is the expected state before the operator configures the app. Live job-board testing must wait for a passing check.

**To finish (operator, in the Surfshark app; no Windows-wide setting was changed by us):**
1. Connect to a server (the country choice is yours; for India-focused searches an Indian or nearby location is typical).
2. Settings > VPN settings > turn on the **Kill Switch**. Surfshark describes a *Soft* mode (blocks all traffic only if the VPN drops unexpectedly) and a *Strict* mode (blocks internet whenever the VPN is off, including after a manual disconnect); *Strict* is the safer choice for this use. It "helps minimize the risk of IP leaks" and is not a guarantee. We cannot verify the setting from outside the app (it needs administrator inspection of firewall filters), so you confirm it in the app.
3. Settings > VPN settings > **Bypasser** (Surfshark's split tunnelling): make sure `python.exe` (this project's `.venv`), PowerShell and `curl.exe` are **not** in the excluded apps; excluded apps use your real connection and show your real IP. Our check detects the Python-versus-`curl.exe` mismatch only partly, so also look at the list.
4. Run Surfshark's own DNS leak test with the VPN off and then on and compare: the DNS details must change after connecting; if they still show your ISP, DNS is leaking.
5. Then run `.\.venv\Scripts\python.exe scripts\check_vpn_egress.py`. It contacts one neutral IP-information endpoint, prints only the egress class and country (never an IP), reports proxy/split-tunnel hints, and exits 0 only if the egress no longer looks like a consumer ISP. Required before any live board run.

**Re-check after the operator connected Surfshark (2026-10-20, same read-only methods; no job board contacted, no IP printed or stored): VERIFIED VPN-routed.**

| Check | Result |
|---|---|
| `scripts/check_vpn_egress.py` (project `.venv`, Python 3.14.3) | egress class **hosting/vpn-like**, country **SG**; no proxy variables and an empty Python proxy view; same egress as Windows `curl.exe` (no per-app split tunnel for either); heuristic verdict "VPN-routed: yes", exit 0 |
| Adapter / routes | `SurfsharkWireGuard` (WireGuard tunnel) is up beside Wi-Fi; IPv4 default coverage is the VPN's split routes `0.0.0.0/1` and `128.0.0.0/1` on the VPN adapter (route metric 0) and the VPN interface metric is 5 against Wi-Fi's 50, so VPN routes win. Services `Surfshark Service` and `Surfshark WireGuard` are running |
| DNS | The VPN adapter has two DNS servers and none overlaps the Wi-Fi adapter's ISP resolvers. A resolver-echo query (`nslookup` of Google's `o-o.myaddr.l.google.com` TXT name) shows the system resolver reaches the authoritative servers from a **hosting/VPN-like network in SG**, not from the ISP: no DNS leak observed |
| IPv6 | No IPv6 default route exists any more and IPv6 name lookups/connections fail (Wi-Fi still has a global IPv6 address and the IPv6 binding enabled, so this relies on the VPN client; no IPv6 path was observed) |
| Proxies / WinHTTP / TLS | unchanged: none set; certificate verification untouched |
| Not verifiable from outside the app | the **Kill Switch** mode and the **Bypasser** list (reported by the operator as on and checked); we cannot read them without administrator inspection of firewall filters or the app's private configuration, which we did not do |

The residual limitations below apply unchanged; a passing check is a snapshot, so re-run the script at the start of any live session, and it must pass before any live board run.

**Residual limitations (not anonymity):** Surfshark can see the traffic metadata it carries; the job boards see a shared VPN address range (which some sites rate-limit or challenge more) plus a Chrome-like TLS fingerprint and our request pattern; the egress class is a heuristic from the network owner name, not proof of privacy; the VPN does not make unauthorized access authorized and no IP rotation, CAPTCHA solving or other circumvention is configured; existing request limits, the manual-run rule and the access permissions in this file are unchanged.

## 17. Restricted-source implementation (2026-10-20)

Implemented per sections 13-16 and the guide's "Restricted Job Board Execution Policy". Where the code lives: policy `config/restricted_sources.json` + `discovery/restricted_policy.py`; VPN guard `discovery/network_guard.py` (`scripts/check_vpn_egress.py` wraps it); integration `discovery/adapters/jobspy_source.py` using `jobspy.scrape_jobs` unmodified, one board per call, with no proxies, CA overrides, cookies, user agent changes or description fetching.

Verified in the 1.4.0 code and by offline tests:
- Every Indeed/Naukri (and LinkedIn/Glassdoor) request goes through `jobspy.util.RotatingProxySession.request`; `RequestBudget` wraps it to count requests and stop after the budget or the duration limit. Checked against JobSpy's real `RequestsRotating` class with an offline transport (the curl_cffi `TLSRotating` class shares the same wrapped method but was not exercised offline).
- JobSpy has no retries; the adapter adds none, and any 403/406/429/CAPTCHA/timeout stops the source.
- **`results_wanted` does not bound the HTTP request.** Indeed's request asks the provider for `jobs_per_page = 100` and Naukri for 20 regardless; with results_wanted 10 the library normally needs one request, but may issue another page if some rows fail parsing. The budget (pagination 0, enrichment 0) refuses that second request; the stored result count is trimmed locally.
- Failure handling: `df.attrs["errors"]` is mapped to `blocked` (restriction words), `partial` (rows plus an error or budget stop) or `failed`; none is ever recorded as a complete or successful-listing check, and no job is closed.
- The VPN preflight runs immediately before every search (core checks) and once in full before the operator is asked to confirm.

Not verified: behavior against the live providers under the budget wrapper (the earlier small probes ran before it existed), the curl_cffi transport path offline, and any change in providers' access terms since the operator's authorization statement.

## 10. Maintenance burden if used

High: private endpoints and embedded keys change without notice (the library ships fixes almost weekly), the dependency set is large, and boards actively block. Expect repeated breakage.

## 18. LinkedIn and Glassdoor preparation audit (2026-10-20; offline only, no request sent to either provider)

Scope: python-jobspy 1.4.0 as installed. Read from the installed scraper source, upstream issues/commits (GitHub API, not the job boards) and the providers' public rules. **No LinkedIn or Glassdoor host was contacted.** Indeed and Naukri are not re-audited here.

### 18.1 LinkedIn (`jobspy/linkedin/__init__.py`)

| Item | Finding |
|---|---|
| Endpoint | `GET https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search` (logged-out "guest" HTML fragments); parsed with BeautifulSoup |
| Transport | `RequestsRotating(proxies, clear_cookies=True)` (python `requests`), so every call passes `RotatingProxySession.request`; `requests` follows redirects inside `Session.send` (below `.request`), so the adapter also caps wire-level hops |
| Location / country | free-text `location` parameter (`Bengaluru, India`); there is no country parameter (`country_indeed` is not passed) |
| Requests per search | 1 per result page, 10 job cards per page (`jobs_per_page = 10`). With `results_wanted = 5` one page normally suffices; a second page (3-7 s sleep first) would be requested only if fewer than 5 unique cards were parsed. The budget of 1 refuses that second request |
| Enrichment | `fetch_description=True` would add `GET /jobs/view/{id}` per job (not allowed; we pass False, budget forbids it) |
| Identifiers | job id from the card's `data-entity-urn` (numeric string); URL `https://www.linkedin.com/jobs/view/{id}` |
| Fields | title, company (+ company URL), location, date posted (date only), job URL; description absent without enrichment |
| Errors | `check_status` raises on 4xx/5xx; an empty page that is not the known empty marker gives "unexpected page with no jobs (blocked?)"; all recorded in `df.attrs["errors"]`, no retries. LinkedIn's non-standard status 999 is treated as a restriction by the adapter |
| Authentication | none (guest endpoint). No credentials or cookies are used or accepted |
| Upstream | recent commits fix LinkedIn parsing; closed issues about 429/403 on bursts; no open LinkedIn-specific issue found; README says LinkedIn rate-limits quickly |
| Suitability | Technically small (1 request). **Not suitable until the provider permits it** (see 18.3) |

### 18.2 Glassdoor (`jobspy/glassdoor/__init__.py`)

> **Retired source - Not part of the active discovery engine.** The Glassdoor material in this section is kept as engineering evidence for decision D-23 (2026-10-20); the engine supports only Indeed, Naukri and LinkedIn.

| Item | Finding |
|---|---|
| Endpoints | (1) `GET {base}/autocomplete/location?...&term=<location>` to resolve the location; (2) `POST {base}/graph` GraphQL, up to `jobs_per_page = 100` listings per call, cursor pagination. India base: `https://www.glassdoor.co.in` |
| Transport | `TLSRotating` (curl_cffi **impersonating Chrome's TLS fingerprint**, `allow_redirects=False`). Same `RotatingProxySession.request` choke point; one `.request` = one wire request |
| Location / country | `country_indeed="India"` selects the domain; `Bengaluru, Karnataka` is resolved by the autocomplete call (a miss returns no results and warns) |
| Requests per search | **minimum 2** (location lookup + first results page); one more per extra page. Hence `base_requests_per_search = 2`, `request_budget = 2`; a second results page would be refused by the budget |
| Enrichment | `fetch_description=True` would add per-job calls (not allowed; False) |
| Identifiers | numeric listing id (as string); URL built from it on the same domain |
| Fields | title, company, location, date posted (from `ageInDays`), pay when present, job URL, company URL; description only with enrichment |
| Errors | `check_status` on both calls; a GraphQL `errors` array raises "API error"; recorded in `df.attrs["errors"]`; no retries |
| Authentication | none sent by us; the library embeds a CSRF token string that is not a credential issued to the operator |
| Upstream | no open Glassdoor-specific issue found; earlier 403 reports closed; schema drift is the main failure mode (unknown columns/records are dropped, never invented) |
| Suitability | Technically 2 requests. **Impersonating a browser TLS fingerprint is evasion-adjacent;** an authorization that does not expressly cover this kind of automated access should not be treated as covering it (see 18.3) |

### 18.3 Provider rules and authorization (checked 2026-10-20)

- **LinkedIn:** the User Agreement (section 8.2) prohibits scraping, bots and other automated access to the Services without LinkedIn's express permission; `robots.txt` carries the same notice. Nothing published grants permission, and nothing mentions VPN egress. **Authorization: NOT established** (the operator's earlier statement covered Indeed and Naukri only). Surfshark egress is **not** assumed to comply; that depends on the wording of any permission LinkedIn gives.
- **Glassdoor:** `robots.txt` disallows `/graph` and `/autocomplete`, the two paths JobSpy needs. The Terms page returned HTTP 403 to our fetch, so its wording could not be read here. **Express written authorization covering this test: NOT established.**
- Consequence: the entries in `config/restricted_sources.json` are **proposals**: `enabled=false`, `provider_authorization="not_established"`, `authorization_covers_vpn_egress=false`. The loader refuses to enable either until the operator records `operator_confirmed`, a reference, and that the authorization covers VPN egress; even then a separate interactive confirmation per provider is required. Approval of testing does not authorize an access method outside what the provider granted.

### 18.4 Every outbound request path

| Provider | Library path | Session class | Counted at |
|---|---|---|---|
| LinkedIn | `session.get` (search; detail only with enrichment) | `RequestsRotating` | `RotatingProxySession.request` + `requests.adapters.HTTPAdapter.send` (redirect hops) |
| Glassdoor | `session.get` (location), `session.post` (graph) | `TLSRotating` | `RotatingProxySession.request` (no redirects) |

`check_transport_enforceable(source)` runs before the authorization is consumed. It reads the installed scraper and refuses it unless it builds its session with `create_session()` from those two classes and imports no other HTTP/socket library (`from curl_cffi.requests import Response`, an annotation, is allowed). Both scrapers pass today (tested). If a future JobSpy changes transport, the search is rejected rather than run with an unenforceable budget. Not covered: a library that dynamically imports a client at run time; mitigated by the socket block in tests and the live budget wrapper.

### 18.5 Safeguards added for these two sources

Per-provider solo authorization (never batched with another provider); provider-authorization and VPN-egress-compatibility gates in the loader, the plan and the live call; `base_requests_per_search` accounting (Glassdoor 2); transport-enforceability check; wire-level redirect cap; 60 s duration cap; VPN quick check before every search with no direct or alternate-route fallback (a failed check stops the run); stop on 401/403/406/429/999/CAPTCHA/authwall/Cloudflare signals in errors or warnings; no retries; import and Sheets publication not allowed by policy.

### 18.6 Overall suitability

Both can technically be bounded to 1 (LinkedIn) and 2 (Glassdoor) requests. Neither is cleared to run: provider authorization is missing for both, LinkedIn's rules prohibit the access method, and Glassdoor's required endpoints are robots-disallowed and use browser impersonation. Alternatives remain official partner/employer feeds, ATS APIs and manual import (Day 12).

## 19. Pre-live security and privacy audit (2026-10-20; offline, no provider contacted)

Operator statement: authorization covers the LinkedIn and Glassdoor access methods discussed, including Surfshark egress, the identified endpoints and Glassdoor's transport. No reference number was supplied; `authorization_reference` stays blank.

**Check 1 - credentials and personal data: PASS (both).** Evidence from the installed code: `jobspy/linkedin` and `jobspy/glassdoor` contain no `os.environ`, `getenv`, dotenv, browser-cookie, keyring, cookiejar, sqlite or file access (a test greps for this). LinkedIn builds `RequestsRotating(proxies=None, clear_cookies=True)` and sends only the library's fixed anonymous headers (`accept`, `accept-language`, a hard-coded macOS Chrome 120 `user-agent`); the cookie jar is cleared before every request. Glassdoor builds `TLSRotating` (curl_cffi, Chrome-like TLS profile) and sends one library-embedded anonymous header (`gd-csrf-token`, a public constant shipped in the package, not issued to or tied to any account). `scrape_jobs` receives no resume, account or Google data. **Found and closed a gap:** `requests` sessions honour ambient `~/.netrc` credentials, proxy variables and CA-bundle variables (`trust_env`). None exist on this machine (checked by presence only), but `RequestBudget` now switches `trust_env` off and refuses any request that carries an `Authorization`/`Cookie`/`Proxy-Authorization` header, per-request `auth`/`cookies`/`proxies`, or pre-existing cookies on the first request (provider cookies issued during the run are allowed). Disclosure: LinkedIn's fixed user-agent string and Glassdoor's Chrome-like TLS profile are library behaviour that presents the client as a browser; they are not credentials, and Glassdoor's was named in the operator's authorization statement. LinkedIn's user-agent string was not named specifically.

**Check 2 - request bounds: PASS (both), with stated limits.** Every request of both scrapers passes `RotatingProxySession.request` (counted, capped, deadline-checked, credential-guarded); LinkedIn's `requests` redirect hops are also capped at `HTTPAdapter.send`; Glassdoor's curl session has `allow_redirects=False`. Tests with the real `RequestsRotating` and `TLSRotating` classes (transport faked below them) show: LinkedIn limited to 1 request (a redirect or second page is refused), Glassdoor to 2 (autocomplete + graph; a second graph page refused), a failed request is not retried, enrichment and pagination parameters are never passed, a single site string is passed so no thread fan-out, and an unsupported transport fails closed before the authorization is consumed. `results_wanted=5` is NOT relied on as a cap. Limits: the runtime check runs before each request; an in-flight request is bounded by JobSpy's fixed 15 s timeout, so the worst case is about 15 s (LinkedIn) and 30 s (Glassdoor), inside the 60 s limit, but it is not a hard kill timer. The check cannot detect a library that imports an HTTP client dynamically.

**Check 3 - storage: PASS.** Snapshots go to `data/restricted/` inside the project (`C:\Users\lohit\Projects`, not under OneDrive); `.gitignore` rule `data/*` (and `logs/*` for the audit log) is verified with `git check-ignore`; the folder inherits an ACL of SYSTEM, Administrators and the operator only. Records keep only 14 normalized fields (descriptions are reduced to a boolean; no headers, cookies or raw responses). Nothing reads `data/restricted` except an explicit `restricted-import --snapshot`; the collect path has no Sheets, dotenv or write-SQL references (tests inspect the source) and opens SQLite read-only for the preview. SHA-256 of `data/discovery.db` before and after the audit work was identical (`71927...`, prefix only). Cleanup is manual: delete the specific `data/restricted/<stamp>_<source>.json` file(s) when no longer needed; there is no automatic expiry.

**Checks 4-6.** Surfshark preflight passed again (8 of 8 critical checks) and runs again at execution time (before the confirmation and before every search). All four sources are `enabled=false`; LinkedIn and Glassdoor remain `provider_authorization="not_established"` in the shipped file, so the live tests will use an execution-scoped temporary policy copy rather than a permanent change. 336 tests pass, `pip check` clean, doctor 18/18.

## 20. Chat-approved execution (2026-10-20; decision D-22; offline)

Request-path facts are unchanged from sections 18-19; only how the operator's permission is given changed. Flow: `restricted-plan` (displays scope, full VPN preflight, records a pending plan, contacts nothing) -> operator's new `Run` message in chat -> `restricted-collect ... --approve-plan CODE` (recomputes and compares the plan, 15-minute expiry, VPN preflight again, atomic single use, then the same one-time `Authorization` object as before). The execution-scoped override (`--enable-for-this-run`, plus `--attest-provider-authorization` for `not_established` sources) is applied to the policy values in memory before validation, so every policy rule still applies and the policy file stays disabled. Limits of this design: Python cannot authenticate a chat message; a process able to run both commands could approve its own plan, so the safeguard is the Claude Code workflow plus its tool-permission prompts; a plan code is only a binding to the displayed scope, not a secret. Request budgets, credential guard, VPN checks and storage rules (sections 18-19) are untouched.

## 21. Live results: LinkedIn and Glassdoor (2026-10-20; one search each, chat-approved)

- **LinkedIn:** success. 1 request, 3.6 s, 5 records (titles, companies, Bengaluru locations, posting dates 2026-10-01 to 2026-10-13, `linkedin.com/jobs/view/<id>` URLs and LinkedIn company URLs). No direct employer URLs, experience ranges or descriptions. 0 URL matches with the stored data; employers were large IT-services firms. Observed behaviour matches section 18.1 (one guest-endpoint request, ids prefixed `li-` by JobSpy).
- **Glassdoor:** blocked. The first of the two planned requests (location lookup) returned HTTP 403 ("blocked: use a proxy"); the adapter stopped the source after 1 request, 2.8 s, 0 records, with no retry or workaround. This confirms the section 18.2/18.3 concern that the endpoints and the library's token/fingerprint approach are not tolerated by Glassdoor in practice, even with the operator's authorization statement. Remedy must be an official route, not evasion.
- Safeguards behaved as designed: VPN preflight 8/8 before each run, request counts 1 and 1 against budgets 1 and 2, enablement in memory only, plan records consumed, production database unchanged, no Sheets writes.

## 22. Glassdoor HTTP 403 diagnosis (2026-10-20; offline; no further provider request)

> **Retired source - Not part of the active discovery engine.** The Glassdoor material in this section is kept as engineering evidence for decision D-23 (2026-10-20); the engine supports only Indeed, Naukri and LinkedIn.

**Where it failed (from the saved outcome and an offline reproduction with the real installed scraper and a faked transport).** The failed run recorded `requests_used = 1`, runtime 2.8 s, error `response status code 403`. The scraper's very first request is the location lookup, so the 403 came from `GET https://www.glassdoor.co.in/autocomplete/location` (stage 1 of 2: location autocomplete); the GraphQL search `POST /graph` was never sent, and CSRF handling is not a separate request in 1.4.0 (the header is a library constant). The offline reproduction shows the request as built: HTTPS, host `www.glassdoor.co.in`, path `/autocomplete/location` (no double slash), query `locationTypeFilters=CITY,STATE,COUNTRY&caller=jobs&term=Bengaluru%2C%20Karnataka` (term percent-encoded), one header (`gd-csrf-token`), no cookies, certificate verification on. Location and country parameters are valid inputs (`country_indeed=India` selects `glassdoor.co.in`; the lookup is the only place the text is used). Surfshark was up: the full preflight passed at approval and the quick check ran immediately before the search. The request budget was applied correctly (1 of 2 used, source stopped).

**What the evidence cannot show.** The first run did not record response headers or body, so whether the 403 was a WAF or bot-management response, an IP-reputation block (the VPN exit is a hosting-style address), a missing-session problem or a Glassdoor India-specific change is **INCONCLUSIVE**. The text "(blocked: use a proxy)" is JobSpy's generic hint appended to any 403/429; it is not information from Glassdoor, and we do not follow it (proxies and rotation are forbidden by policy).

**Upstream comparison (GitHub API only, no provider contact).**
| Item | Finding | Applies to installed 1.4.0? |
|---|---|---|
| #270 / #279 (403, "location not parsed") | Closed 2026-10-01; maintainer: fixed in #394. Some commenters suggest changing the user agent; that is evasion and is not used | Closed by #394 |
| #347 (CSRF page 404, GraphQL errors) | Not merged; superseded by #394 | No |
| #384 (double slash in lookup URL) | Merged 2026-09-30 | Fixed: offline check shows a single slash |
| #394 (location endpoint with encoded term, cookies from autocomplete, tolerant errors) | Merged 2026-09-30, "verified in the US and UK" | Present: installed code uses the same `/autocomplete/location` URL with `quote(term)` and the same library CSRF constant. **India (`glassdoor.co.in`) was not among the verified regions** |
| later commits (#405, #409, #418, #419) | Paging, descriptions, argument validation, result attrs | Included in 1.4.0; none changes the lookup request |
No open upstream issue concerns Glassdoor 403 at present.

**Classification.** Confirmed bug: none found. Suspected bug: none supported by evidence. Provider access denial: most likely (first and only request refused). Network/VPN issue: not shown (preflight passed) but a hosting-style exit address cannot be excluded. Unsupported or changed endpoint: not shown; India behaviour unverified upstream.

**Decision.** No evidence-supported code fix exists, so none was made to the scraper. Changing the user agent or TLS profile, proxies or retries are excluded. The only code change is a diagnostic improvement for any future approved attempt: every request now records a sanitized trace (stage, method, host, path without query, HTTP status, header names with only server/content-type/retry-after values; no cookies, tokens, query strings, bodies or addresses) in the snapshot outcome and the console summary. Four regression tests cover URL construction, the stage-1 403 stop, trace sanitization and the two-request path. 357 tests pass.

**Recommendation.** Keep Glassdoor deferred and disabled. A repeat of the identical request is not justified; it would at most add header names. A new attempt is justified only if Glassdoor provides an authorized path (an official data/partner interface, a provider-issued credential, or an allow-listed endpoint or address range for the operator). Alternatives for equivalent coverage: employer ATS sources (Greenhouse, Lever, Ashby), manual import (Day 12), and LinkedIn (works).

## 23. Glassdoor authorized-access options (2026-10-20; research only, no Glassdoor request)

> **Retired source - Not part of the active discovery engine.** The Glassdoor material in this section is kept as engineering evidence for decision D-23 (2026-10-20); the engine supports only Indeed, Naukri and LinkedIn.

- **JobSpy status:** v1.4.0 (released 2026-10-19) is the newest release and the repository HEAD (`8dd83cdd`); there is no newer Glassdoor fix to adopt, and no open upstream issue about this 403. The relevant fixes (#384, #394) are already in 1.4.0. A version change would not address this failure.
- **Official API / data feed:** public web search (secondary sources; Glassdoor's own pages could not be read from here, and one vendor reports HTTP 410 from the legacy endpoint on 2026-10-08) consistently says Glassdoor's public job API closed (2021-2022), partner API access is closed to new developers, the help-centre "API" page now describes a B2B Customer Insights product, and any current data access is enterprise/sales-led. No self-serve key, registered application or allow-list programme was found. Reports also say Glassdoor has merged into Indeed (reported completion 2026-07-01; unverified); if true, the operator's existing Indeed relationship may be the right place to ask which Glassdoor access route is sanctioned. Licensed third-party resellers of Glassdoor data exist; they are commercial services and a new dependency, so they need separate explicit approval.
- **What our authorization would have to specify:** the endpoint (`/autocomplete/location`, `/graph` are robots-disallowed and private), the client identity (the library's embedded CSRF constant and Chrome-like TLS profile) and the network route. Nothing found shows Glassdoor issues credentials or allow-lists addresses for this use. Whether a stable allow-listed egress would help is moot until Glassdoor offers such a programme; Surfshark's exit is shared and hosting-class, so an allow-list would need a dedicated static address.
- **Cause of the 403:** unchanged from section 22: provider-side refusal of the first (location lookup) request; sub-cause INCONCLUSIVE.
- **Alternatives compared:**
| Path | Feasible now | Covered by authorization | Needs from operator | Fit with JobSpy integration | Reliability | Effort |
|---|---|---|---|---|---|---|
| Re-run JobSpy Glassdoor | Technically yes | Method already refused by provider | Nothing | Full | Poor (403) | None |
| Official Glassdoor/Indeed partner API or feed | Unknown | Yes if issued | Written confirmation of an approved route, credential or allow-list from Glassdoor/Indeed | New adapter | High | Medium |
| Licensed third-party data feed | Likely | Separate contract | Approval of a new paid service | New adapter | Medium-high | Medium |
| Operator manual export / links from their own browsing | Yes | Yes (human use) | Paste or save links | Day 12 manual import (not yet built) | High, low volume | Low |
| Employer ATS boards (Greenhouse, Lever, Ashby), LinkedIn (works) | Yes | Yes | None | Existing / planned | High | Low |
- **Decision:** no scraper or dependency change; Glassdoor remains disabled and deferred; no retest. A new live attempt becomes justified only if Glassdoor/Indeed names an approved route (credential, endpoint or allow-listed address) that we then implement and test offline first.

## 24. Final decision: Glassdoor retired; JobSpy scope is Indeed, Naukri and LinkedIn (2026-10-20, D-23)

Summary of the evidence: one Glassdoor request was sent (location autocomplete) and refused with HTTP 403; the request was well-formed; installed JobSpy 1.4.0 already contained the upstream fixes and is the newest release; Glassdoor's public/partner API is reported closed and no credential, registration or allow-list route exists for the private endpoints. Decision: no further engineering. Removed from the executable engine (policy file, adapter routing, CLI/pipeline selection, budgets, plans, Skill); `glassdoor` and `jobspy_glassdoor` are rejected as unsupported sources; the third-party `python-jobspy` package is untouched. Indeed, Naukri and LinkedIn keep every safeguard (disabled by default, fresh one-time chat approval of an exact displayed plan, Surfshark preflight, credential isolation, transport request budgets, no retries, no automatic import or publishing). If Glassdoor ever offers an authorized route, it would need a new decision, source audit and implementation.
