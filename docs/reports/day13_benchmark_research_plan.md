# Day 13 (revised): bounded plan for building the independent benchmark — AWAITING APPROVAL

Status: nothing below has been run. No live request has been made. The evaluation baseline was frozen first
(`exports/day13_frozen_baseline.json`, Git-ignored): commit `09258af`, schema 7, database SHA-256 `b781c5f9…`, integrity ok, config hashes
for the profile, ranking, restricted-source and investor-board files, eight sources with their last sighting and last check, current review
session `2e05066b` (24 records), 5 verdicts.

## Why the methodology changed
The original 18 October research (15 postings, 11 open) is not in the project, and the operator approved replacing it with a freshly built,
independent benchmark. This is a deviation from the Day 13 text only, not a change to Plan A. The result will be reported as a newly
constructed benchmark, not as the historical sample.

## What I would do
1. **Find candidate postings independently of our data.** Run the web-search tool on public queries only, never looking in SQLite first:
   3 queries for each of the 8 role families (24 searches), each phrased for Bengaluru or India-remote, e.g. "founder's office associate Bengaluru careers",
   "forward deployed engineer Bengaluru", "applied AI implementation Bengaluru", "associate product manager Bengaluru careers", "business finance associate
   Bengaluru careers", "GTM engineer India remote", "AI automation specialist Bengaluru", "venture studio associate Bengaluru".
2. **Verify each chosen posting on its own public page** with a single GET of the posting URL (employer careers page or its public ATS page:
   expected hosts are boards.greenhouse.io / job-boards.greenhouse.io, jobs.lever.co, jobs.ashbyhq.com, apply.workable.com and employer-owned careers
   domains; the exact list is whatever the searches return and is written to the fixture before matching). Captured: title, location and remote wording,
   experience text, stable ATS identifier, fetch timestamp, HTTP status.
3. **Sample design:** target 15 positives, about 2 per role family, at most 2 per employer and at most 4 per ATS provider, plus up to 5 clearly
   non-target negative controls kept out of the recall denominator. If fewer than 15 grounded positives exist I report the smaller denominator; under 10
   I stop and ask you.
4. **Freeze the fixture** (`tests/fixtures/benchmark_day13_independent_2026.csv`) with selection rationale and creation timestamp before any comparison
   with SQLite. Replacements only for objective reasons (dead page, wrong location, duplicate employer) decided before looking at database matches.

## Request budget and rules
* Searches: at most 24 (plus 6 spare for replacements), through the search tool, not by scraping a search engine.
* Page fetches: at most 40 (about 20 targets plus replacements and negative controls), HTTP GET only, one request per URL, sequential, at least 2 s apart,
  no retries, no pagination, no crawling beyond the single posting page, no sitemap or API enumeration.
* No login, cookies, private session, CAPTCHA solving, proxy, TLS changes, restricted JobSpy sources, Weekday/Saarthi app access (they need accounts; I skip them
  unless a public page is reachable without login).
* robots.txt is not bypassed: a page the operator's browser could not open without signing in is skipped.
* **Stop conditions:** any 403, 429, CAPTCHA, login wall or terms notice stops requests to that host for the session; two host failures in a row stop the whole run;
  the budget above is a hard ceiling.
* Output: only public posting text needed for the fixture, stored in Git-ignored `exports/` (research notes) and the small fixture CSV (public facts only).

## After approval
Fixture first, then a read-only comparison against the frozen database, then ranking in a clean evaluation context and the current session context, then the
manual-import dry run on a disposable database, then the report, tests and the Day 14 handoff. Nothing is written to production or either Sheet, and nothing is
committed until you approve the final report.
