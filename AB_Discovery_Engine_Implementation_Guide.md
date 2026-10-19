# A+B Discovery Engine — Step-by-Step Implementation Guide

**Plan:** Hybrid source discovery, implemented locally in Python, with SQLite memory and Google Sheets review  
**Document version:** 1.0 · **Prepared:** 19 October 2026  
**Target computer:** Windows 10/11 · **Operator:** You, using Claude Code  
**Status:** A build plan — none of the commands below have been run on your computer yet.

> **One-sentence goal:** Whenever you choose to run discovery, the system searches for relevant **jobs (A)** and promising **companies (B)**, links them, avoids duplicates, remembers previous runs, and puts roughly 20–30 worthwhile new or changed items in Google Sheets.

---

## 1. What we are building — and what we are not

### The outcome

At the end, you will be able to open Claude Code in the project folder and type:

```text
/run-discovery
```

The system will then:

1. Check which configured sources are available.
2. Collect jobs (**A**) and companies (**B**) from several independent sources.
3. Normalize company names, website domains, job IDs and links.
4. Match jobs to their companies, preserving *every source sighting*.
5. Apply low-cost rules for your eight role types, Bengaluru / India-eligible remote work, early-career signs and company preferences.
6. Rank the remaining jobs and companies with transparent points; **no AI ranking**.
7. Read your existing interest marks from Google Sheets **before** updating it.
8. Save new and changed observations in SQLite and refresh the four Google Sheets tabs.
9. Tell you what was new, changed, not seen, failed or shortlisted.

You review the shortlist and mark jobs/companies **Yes / Maybe / No** (plus optional notes). Selected items can be copied into ChatGPT for preparation *outside* this application.

### Scope boundaries (non-negotiable)

- **Manual runs only.** No background scheduler, alerts or automatic email.
- **No access to your local Brain** or career-history files. The system uses only a small `search_profile.yaml` that you explicitly edit.
- **No applications, outreach, recruiter messages, resume writing or interview preparation.**
- **No automatic logins or restricted-site scraping.** Use supported/public interfaces and authorized access. For protected platforms, paste a URL, copied text or a permitted export using the import command.
- **No Gemini or paid AI calls in v1.** We can add optional AI extraction only if real testing shows a need.
- **No fixed cap on raw discovery.** Process large collections in safe batches; display only a manageable subset in Sheets.

### Why this design?

| Requirement | Chosen component | Plain-language reason |
|---|---|---|
| Find jobs on many sites | Several independent source adapters | Any one feed misses openings |
| Find companies even with no jobs | YC and investor/accelerator directories | Interesting companies should not disappear just because they aren't hiring today |
| Remember everything | SQLite local database | Keeps relationships and history across repeated runs |
| Avoid duplicate companies/jobs | Stable IDs, website-domain matching, careful fuzzy suggestions | Saves reviewing the same opportunity repeatedly |
| Cheap broad screening | YAML rules and Python | Thousands of records can be filtered without AI charges |
| Easy review | Four-tab Google Sheet | You only need to read and edit a few cells |
| Easy operation | A Claude Code Skill | Run and troubleshoot without personally writing Python |

**Important:** Feashliaa is one **supplementary** feed. Your 18 October benchmark found only 3 of 15 known posts in that snapshot. YC jobs, investor boards and mainstream job sources must remain independent first-class inputs.

---

## 2. Timeline — realistic sequence

**Suggested pace:** **3 weeks**, around **16 focused sessions of 1–3 hours** each. Allow an additional **3–5 days of buffer** for sign-in permissions, source breakage or testing. This is an estimate, not a guarantee that all source adapters will work by a fixed date.

| Session | Focus | What you should have at the end | Approx. effort |
|---|---|---|---|
| **Day 1** | Windows setup | Python, Git, VS Code, Claude Code work | 1–2 h |
| **Day 2** | Google Sheets + credentials | Test Sheet and Google API connection | 1–2 h |
| **Day 3** | Project skeleton + SQLite | Database and health-check command | 2–3 h |
| **Day 4** | Safe Sheets sync | User verdict survives updates and sheet sorting | 2–3 h |
| **Day 5** | YC company feed (B) | Companies stored, with websites and source dates | 1–2 h |
| **Day 6** | Feashliaa supporting feed (A) | Current India-relevant jobs imported safely | 2–3 h |
| **Day 7** | Run pipeline + single Skill | `/run-discovery` runs Stage 1 and produces a report | 2 h |
| **Day 8** | JobSpy experiments | Tested source capability and errors, one site at a time | 2–3 h |
| **Day 9** | YC job pages (A) | Jobs checked for relevant YC companies regardless of hiring flag | 2–3 h |
| **Day 10** | Investor-board readers | Getro/Consider and selected boards, with pagination checks | 2–3 h |
| **Day 11** | Identity + history hardening | Multi-source merging and safe job state transitions | 2–3 h |
| **Day 12** | Rule filtering + ranking + import | Explainable shortlist and manual-import route | 2–3 h |
| **Day 13** | Benchmark replay | Per-source coverage of 18 October's 15-post sample | 2–3 h |
| **Day 14** | Gaps and failure recovery | Documented misses, source health, repair tests | 2–3 h |
| **Day 15** | Real production trial | Production Sheet, successful user-started run | 1–2 h |
| **Day 16** | Documentation + backup | Simple operating routine and tested database backup | 1–2 h |

**Stage gates:** Do not move on until the *end-of-stage tests* pass. Building another source adapter before safe SQLite/Sheets synchronization is working will only add risk.

### The four delivery milestones

- **Milestone 1, end of Day 7:** Data and memory work. YC + Feashliaa -> SQLite -> test Google Sheet; second run makes no duplicates.
- **Milestone 2, end of Day 12:** Useful discovery works. JobSpy + YC job pages + investor boards + manual import; ranked shortlist.
- **Milestone 3, end of Day 14:** Quality is demonstrated against the benchmark; misses are visible.
- **Milestone 4, end of Day 16:** Your normal `/run-discovery` workflow works in production with safe verdicts and backups.

---

## 3. Prerequisites — what you personally need

### 3.1 Accounts and information

| Needed | What you need to do | Cost |
|---|---|---|
| **Claude account with Claude Code access** | Sign in to Claude Code with an eligible subscription | Existing subscription/usage limits |
| **Personal Google account** | Create and own the Google Sheet | Usually no extra charge |
| **Google Cloud project** | Enable the Sheets API and create credentials | No mandatory paid API for basic personal usage; quotas apply |
| **GitHub access** | Read public source repositories; a GitHub account is convenient for your own private code | Free account is enough |
| **A folder on your Windows computer** | Store project code and local SQLite database | Free; disk space required |
| **A copy of your 18 Oct benchmark links** | Keep the original 15 example URLs, identify the 11 that were open on 18 Oct | Existing research; gather before Day 13 |

**Laptop guidance:** A modern Windows laptop with stable internet and several GB of free disk space is enough for a local Python/SQLite project. Bulk compressed feed downloads may temporarily require additional space. No GPU, cloud database or hosting server is needed.

### 3.2 Software and versions

Install these once:

| Software | Version to use | Download / documentation | Why |
|---|---|---|---|
| **Python** | **3.12.x, 64-bit** (latest available security patch in that line) | https://www.python.org/downloads/ | Runs the collector and SQLite |
| **Git for Windows** | Current stable **2.x** | https://git-scm.com/downloads/win | Tracks code history and provides Git Bash |
| **VS Code** | Current stable | https://code.visualstudio.com/ | Easy place to inspect project files (optional but recommended) |
| **Claude Code** | Current stable native Windows release | https://code.claude.com/docs/en/setup | Writes, runs and repairs code |
| **PowerShell** | Windows PowerShell 5.1 or PowerShell 7+ | Included with Windows | Runs setup commands |
| **Web browser** | Current Chrome or Edge | Existing | Google Cloud Console and Sheets setup |

**Do not install Node.js, Docker, WSL, Java, a separate database server, Gemini CLI or an MCP server for this version.** They are not required for this implementation. If Claude Code reports a specific prerequisite, follow its current official Windows setup instructions rather than installing everything preemptively.

### 3.3 Python packages — installed by Claude Code after the project is created

Suggested *package families* (Claude Code will pin tested versions in a lock/requirements file):

```text
httpx
beautifulsoup4
trafilatura
feedparser
PyYAML
rapidfuzz
cleanco
gspread
google-auth
pytest
python-dotenv
python-jobspy        # install/test only at Day 8
```

`sqlite3`, `uuid`, `gzip`, `json`, `hashlib`, `logging` and `pathlib` are included in Python. We are **not** selecting untested exact package versions in advance. Once integration tests pass, pin the exact installed versions so future installations reproduce a working system.

**Do:** Keep all packages inside this project's `.venv` environment. **Don't:** Install every possible scraper package or update all dependencies during a production run.

---

## 4. Day 1 — Set up the Windows computer

**Goal:** Confirm Python, Git and Claude Code work before touching any job websites.

### Step 1: Install Python 3.12

1. Open https://www.python.org/downloads/ and locate the latest **Python 3.12.x** Windows 64-bit installer/release appropriate for Windows. If the website emphasizes a newer Python family, deliberately select Python 3.12 for this project. **Note:** some later 3.12 security releases may provide source code only; choose the latest official 3.12 release with a Windows installer rather than assuming every patch has one.
2. If using a traditional Windows installer, enable the option to add Python to your `PATH` when offered. Finish installation.
3. Open **PowerShell** (Start menu -> type PowerShell).
4. Run:

```powershell
py -3.12 --version
```

Expected: a version beginning with `Python 3.12`.

If `py` isn't found, close/reopen PowerShell and check the official Python Windows launcher instructions; `python --version` may also work. Avoid installing an unrelated Python version just to suppress an error.

### Step 2: Install Git for Windows

1. Download and install https://git-scm.com/downloads/win using standard options.
2. Restart PowerShell if necessary.
3. Run:

```powershell
git --version
```

Expected: `git version 2...`.

### Step 3: Install VS Code

Download https://code.visualstudio.com/ and install. VS Code is recommended for viewing files and opening the integrated terminal; Claude Code can run in an ordinary PowerShell window if you prefer.

### Step 4: Install Claude Code

Use the **current official Windows instructions** at https://code.claude.com/docs/en/setup.

The official native PowerShell installer is:

```powershell
irm https://claude.ai/install.ps1 | iex
```

Run that only after confirming the URL in Anthropic's own documentation. Then:

```powershell
claude --version
claude doctor
```

Launch Claude Code and sign in:

```powershell
claude
```

Follow the login prompt. Exit the session after confirming access.

### Step 5: Create the project folder

Use a local folder **outside an automatically synchronized OneDrive/Dropbox folder**, because SQLite databases should not be concurrently synchronized while in use.

```powershell
New-Item -ItemType Directory -Force "$HOME\Projects\ab-discovery-engine"
Set-Location "$HOME\Projects\ab-discovery-engine"
git init
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe --version
```

You do **not** have to activate the environment; Claude Code can invoke its Python executable directly.

### Day 1 do / don't

**Do:** Verify each command completes; use the exact project folder; keep a copy of error messages so Claude Code can help.  
**Don't:** Run PowerShell as Administrator unnecessarily; put the project in a synced folder; start downloading bulk job feeds yet.

**Pass condition:** Python 3.12, Git, Claude Code and `.venv` are all verified.

---

## 5. Day 2 — Create Google Sheets and API access

**Goal:** The Python application can read and update **your test spreadsheet**, using a dedicated Google identity.

### Step 1: Create two Sheets

Create two ordinary Google spreadsheets in your Google Drive:

1. `A+B Discovery - TEST`
2. `A+B Discovery - LIVE`

For each, create **exactly four tabs** with these names (case-sensitive for our code):

```text
Shortlist
Posts
Companies
Runs
```

Use TEST during development. Do not write test records to LIVE.

### Step 2: Configure your Google Cloud project

1. Open https://console.cloud.google.com/ and sign in with the Google account that owns the Sheets.
2. Create a new project, e.g. `AB Discovery Engine`.
3. Open **APIs & Services -> Library**.
4. Enable **Google Sheets API**. Enable **Google Drive API** too if your chosen `gspread` operations use Drive file lookup/creation; we will primarily open existing Sheets by ID.
5. Go to **IAM & Admin -> Service Accounts** and create an account such as `ab-discovery-sheets`.
6. Create a JSON key **only if your Google Cloud policy permits it**; download it privately. If key creation is disabled, have Claude Code configure the Google-supported desktop OAuth flow instead. Do not disable organization security controls.
7. Copy the service account's email address (it ends in `gserviceaccount.com`).

### Step 3: Give the service account access

Open TEST and LIVE Sheets, click **Share**, and add the service account email as **Editor**. Share only those two Sheets, not your entire Drive.

### Step 4: Store credentials safely

Claude Code should create the following private local structure:

```text
ab-discovery-engine/
  .env                  # Google Sheet IDs and credential-file path; PRIVATE
  .secrets/             # Service account key if used; PRIVATE
  .gitignore            # excludes both of the above
```

Keep the downloaded JSON under `.secrets/` in the local project, named `google-service-account.json`. Example `.env` (replace placeholders locally, not in chat):

```dotenv
GOOGLE_SHEET_ID_TEST=put-test-spreadsheet-id-here
GOOGLE_SHEET_ID_LIVE=put-live-spreadsheet-id-here
GOOGLE_SERVICE_ACCOUNT_JSON=.secrets/google-service-account.json
```

The spreadsheet ID is the part of its Google Sheets URL between `/d/` and `/edit`.

**Never paste the private JSON key or `.env` contents into ChatGPT, GitHub, screenshots or bug reports.** Claude Code may need the local credential file to test the connection; allow it to access only the project folder.

### Step 5: Ask Claude Code to test read-only access

Open PowerShell in the project folder, run `claude`, then paste:

> **Day 2 Claude Code task:** Set up `gspread` with the existing TEST spreadsheet ID and my private service-account credentials. Create a `python -m discovery doctor` command that tests credentials, required tabs and API access **without writing to the sheet**. Create `.gitignore` before doing any Git commit. If authentication fails, explain the exact Google Console or Sheet-sharing action I need to perform. Do not read or print private key contents. Do not touch the LIVE sheet.

### Day 2 do / don't

**Do:** Test against TEST; verify only the two intended Sheets are shared; keep keys private.  
**Don't:** Make the Sheet public; share your entire Google Drive; grant the service account broad project-admin access; overwrite existing rows to test connectivity.

**Pass condition:** A read-only `doctor` check reports the TEST Sheet and its four tabs successfully.

---

## 6. Day 3 — Build the SQLite foundation

**Goal:** The database remembers companies, jobs and their sources, even if the program stops unexpectedly.

### Step 1: Ask Claude Code to create project skeleton

Use this prompt in Claude Code:

> **Day 3 Claude Code task:** Build the first Python 3.12 project skeleton for the A+B discovery engine. Use built-in `sqlite3`, pytest and YAML; no AI calls, no scheduling, no custom dashboard, no Brain access. Create `discovery/` as an importable Python package, `config/`, `tests/`, `logs/`, `imports/`, `exports/`, and `data/`. Add a `python -m discovery doctor` entry point and a `.env.example` with placeholder values only. Make code testable with fake source data. Show the files created and tests run. Do not call live websites or change the LIVE spreadsheet yet.

### Step 2: Database schema

Claude Code must implement these **eight SQLite tables**:

| Table | Meaning |
|---|---|
| `companies` | One stable company ID, name, domain, known aliases and current facts |
| `jobs` | One stable job ID, company ID, title, location, current state |
| `sources` | Known feeds, websites and source configuration |
| `sightings` | Each source URL / provider ID pointing to a company or job, with observation dates |
| `runs` | Every user-started execution and overall result |
| `source_checks` | Source success, failure, partial results and completeness per run |
| `status_events` | Append-only meaningful changes with source evidence |
| `verdicts` | Your decision and notes, linked to stable company/job IDs |

Use SQLite transactions, foreign keys, unique indexes and schema migrations. Keep SQLite at `data/discovery.db` and exclude it from Git.

**Nuance:** The source record can show `last_seen` without creating an extra `status_event` on every unchanged run. `status_events` records meaningful changes, not noise.

### Step 3: Test identity and replay

Ask Claude Code to write tests for:

- Same company domain imported twice -> **one** company.
- A job found through three URLs -> **one** job + **three** source sightings, when identity is confirmed.
- Two different ATS requisitions sharing a title -> **two** jobs.
- Same run replayed -> **no duplicate events**.
- A failed source -> **no automatic Closed status**.

### Day 3 do / don't

**Do:** Require passing automated tests and a short explanation of each database table.  
**Don't:** Ask Claude Code to discover real jobs before identity and replay tests pass; use company name similarity alone to merge firms.

**Pass condition:** `pytest` passes; data survives closing and restarting Python.

---

## 7. Day 4 — Make Google Sheets synchronization safe

**Goal:** You can mark a row **No**, rerun the program, and your mark stays **No**.

### Step 1: Define ownership of columns

- **Machine-owned:** names, links, job titles, source dates, ranking, states, source evidence.
- **Human-owned:** `Your Verdict` and `Your Notes` on **Posts** and **Companies**.
- **Shortlist:** generated list of IDs, reasons and links; not a second place to store decisions.
- **Runs:** one row summarizing each run.

Every Posts and Companies row must contain an immutable `record_id` column. Never rely on row numbers alone.

### Step 2: Ask Claude Code to implement two-way sync

> **Day 4 Claude Code task:** Implement safe `gspread` synchronization for the four-tab TEST Sheet. Read existing Posts/Companies `record_id`, `Your Verdict` and `Your Notes` **before** writing. Save edits into SQLite. Update machine-owned columns only, using stable IDs, batched API operations and read-before-write safeguards. If reading verdicts fails, stop any potentially destructive sync. Sorting rows must not cause verdicts to move to another company or job. Add automated fake-Sheet tests and a TEST-only live round-trip demonstration. Do not touch the LIVE sheet.

### Step 3: Personally test it

1. Let Claude Code create **two fictional test company rows**.
2. In TEST Sheet, mark one company **No**, add a note and sort the sheet by company name.
3. Ask Claude Code to synchronize again.
4. Check that the verdict and note stayed with that exact company ID.
5. Confirm that a new run did not recreate the row.

### Day 4 do / don't

**Do:** Test with sorting, changing a note and repeating the sync.  
**Don't:** Allow full-sheet deletion/recreation of Posts or Companies; write an independent verdict into Shortlist; sync to LIVE before this test passes.

**Pass condition:** All manual marks are preserved after a repeat sync and row sorting.

---

## 8. Day 5 — Add YC companies (Stream B)

**Goal:** Discover companies even when they have no advertised job openings.

### Step 1: Use the current public YC dataset

Start with:

- Project: https://github.com/yc-oss/api
- Dataset metadata: https://yc-oss.github.io/api/meta.json
- Company list: https://yc-oss.github.io/api/companies/all.json

These are publicly accessible JSON endpoints. The dataset is **unofficial** and includes publicly launched YC companies; record its update time. A source company with missing identity, domain or funding details must retain `Unknown` fields instead of acquiring guessed information.

### Step 2: Ask Claude Code to build the importer

> **Day 5 Claude Code task:** Add a YC-company source adapter that reads the source metadata and all-companies JSON, validates its shape, then extracts company name, YC profile, website, regions, industry, description, batch, stage and reported hiring flag **where available**. Give each record a stable UUID and preserve the source URL and retrieval time. Create or update canonical companies by **verified website identity**, not by YC hiring flag. Target the current India-relevant companies for job-page checks later, while retaining the full source inventory efficiently. Add schema-change and duplicate tests. Sync the resulting relevant company rows to TEST.

### Important YC rule

**The YC hiring flag is informational, not authoritative.** Do not filter out companies marked “not hiring.” The October benchmark found a relevant Peoplebox job despite a contradictory hiring flag.

### Day 5 do / don't

**Do:** Preserve YC profile URLs and distinguish reported from verified hiring.  
**Don't:** Treat the published count as a guarantee that all Indian startups are represented; ignore startups outside India if they explicitly hire remotely from India; import every global YC company into the Sheet just to show a large number.

**Pass condition:** Relevant YC company records exist in SQLite and appear once in the TEST Companies tab.

---

## 9. Day 6 — Add Feashliaa as a supporting feed (Stream A)

**Goal:** Capture relevant jobs from its broad ATS collection without trusting the entire snapshot as currently active.

### Step 1: Source references

- Collector repository: https://github.com/Feashliaa/job-board-aggregator
- Generated data: https://github.com/Feashliaa/job-board-data

The generated-data repository publishes a `jobs_manifest.json` and gzipped JSON chunks, and refreshes by replacing its previous published snapshot. Verify current layout before coding hard-coded download paths.

### Step 2: Ask Claude Code to implement streaming import

> **Day 6 Claude Code task:** Add the Feashliaa source adapter. Read manifest metadata and freshness status before retrieving compressed chunks; **do not load the entire global dataset into RAM or write every global record to Sheets**. Stream and filter for Bengaluru, India and potentially India-eligible remote roles; persist raw source identifiers and minimal sightings in SQLite. Distinguish jobs seen in Feashliaa's latest successful collection from jobs retained for its disappearance grace period. Do not mark retained older jobs Active merely because they are still in a chunk. If the feed is stale, truncated or incomplete, record a failed/partial `source_check` without marking missing jobs Not Seen or Closed. Add tests for stale manifests, chunks missing, duplicate snapshots and ambiguous company board codes.

### Step 3: Personal spot checks

Inspect 10–20 returned rows. Confirm job links open and that their reported locations make sense. A Feashliaa provider's `first_seen` is not automatically *your* first-seen timestamp; retain both fields separately where supplied.

### Day 6 do / don't

**Do:** Keep Feashliaa optional; record snapshot timestamps and source-specific provenance; verify source freshness.  
**Don't:** Make it the primary definition of all hiring companies; assume it has full descriptions; interpret a provider's 30-day retained records as confirmed open postings.

**Pass condition:** Import completes without excessive memory use and the repeat import creates no duplicate jobs.

---

## 10. Day 7 — Connect the pipeline and create ONE Claude Code Skill

**Goal:** The foundations work as one manually initiated operation.

### Step 1: Define the Python commands Claude will call

Ask Claude Code to implement these documented commands (they are targets, not executables that exist before implementation):

```powershell
.\.venv\Scripts\python.exe -m discovery doctor
.\.venv\Scripts\python.exe -m discovery run --sources yc,feashliaa --sheet test
.\.venv\Scripts\python.exe -m discovery sync --sheet test
.\.venv\Scripts\python.exe -m discovery report --last
```

Require progress reporting, unique run IDs, resumable source processing, safe interruption and per-source result counts.

### Step 2: Create `.claude/skills/run-discovery/SKILL.md`

Ask Claude Code to create a single project-local Skill. Suggested starting content:

```markdown
---
name: run-discovery
description: Run the local A+B discovery application on explicit user request.
disable-model-invocation: true
---

When the user invokes /run-discovery:
1. Use the project virtual-environment Python.
2. Check credentials, source settings, data paths and database health.
3. Execute the tested discovery command. Never run automatically.
4. Continue past isolated source failures, but report them clearly.
5. Read Google Sheets human verdicts before any system-owned update.
6. Sync only with the explicitly selected test/live Sheet.
7. Report run ID, counts, new/changed/failed items and links.
8. Do not modify source adapters, change schemas, access the Brain,
   apply to jobs, schedule tasks or send messages during a run.
```

The `disable-model-invocation: true` frontmatter means Claude does not autonomously select this Skill; you invoke it yourself. It does **not** replace ordinary permissions for shell commands or Google APIs.

### Step 3: First integrated test

Run the Skill against TEST. Then rerun without changing the source data. Compare both run summaries.

### Day 7 do / don't

**Do:** Keep Python as the authority and Claude as the operator.  
**Don't:** Ask Claude to “just research everything” without executing the Python source adapters; allow Claude to repair source code automatically in the same production run.

**Milestone 1 PASS:** Both A and B run, history persists, TEST updates, user verdicts survive and identical data produces no duplicated records.

---

## 11. Day 8 — Integrate and test JobSpy carefully

**Goal:** Add mainstream India job discovery without letting one blocked site break the run.

Source: https://github.com/speedyapply/JobSpy

### Step 1: Install a tested version

Have Claude Code inspect current releases and start with a tagged release appropriate for Python 3.12, then **pin that exact version/commit** after successful tests. Do not automatically install the newest Git `main` on every run.

**Known concern:** JobSpy has documented upstream-source errors; its October 2026 README says Google Jobs is currently unavailable. It also exposes error/warning information on result DataFrames. Collect those into our `source_checks` table.

### Step 2: One source at a time

Ask Claude Code:

> **Day 8 Claude Code task:** Integrate JobSpy behind a source adapter and normalize its job fields into SQLite. Test available **permitted** source methods one at a time, beginning with Naukri and Indeed India using small Bengaluru searches (e.g. 10–20 records). Capture warnings/errors as failed or partial source checks. Do not enable Google Jobs without proving it works. Do not implement credentialed/login bots, proxy rotation to evade blocks or prohibited platform scraping. Add per-source timeouts and tests so failure of one site cannot erase the results from other sources. Pin the tested version.

If a source is inaccessible or not authorized for automated retrieval, keep it disabled and use manual import.

### Step 3: Confirm contribution

Record how many new relevant posts Naukri and Indeed each add **beyond** existing Feashliaa and YC records. Do not judge source value by raw volume alone.

### Day 8 do / don't

**Do:** Begin with small batches and inspect original job links; record unsuccessful sources honestly.  
**Don't:** Attempt to defeat rate limiting; interpret “zero rows” from a blocked board as “no open positions”; depend on Google Jobs through JobSpy until tested.

**Pass condition:** At least one permitted mainstream source works end-to-end, and failure of another source does not destroy data or trigger job closures.

---

## 12. Day 9 — Read YC job pages, including companies flagged not hiring

**Goal:** Close the important gap between a company directory and actual live job availability.

### Steps

1. Use all **relevant** India-based YC company profiles from the Day 5 import; do not restrict to YC's `hiring=true` records.
2. Have Claude Code inspect public YC job pages and borrow the relevant reader logic from OpenPostings where technically applicable.
3. Fetch each accessible job-page result using a permitted public method.
4. Parse titles, companies, location, source URLs, direct apply links if present, and any available dates.
5. Link jobs to the canonical YC company record.
6. Mark `company_reported_hiring` separately from `verified_open_jobs`.
7. Test for pagination and missing/empty results; incomplete scans must not count as verified no-jobs cases.

### Claude Code prompt

> **Day 9 Claude Code task:** Implement YC public job retrieval for relevant YC companies, including those flagged as not hiring. Do not depend on the directory hiring flag to decide which job pages to examine. Reuse tested reader patterns if available, but verify current URLs/schema. Normalize and link the jobs to existing companies. Report page coverage, inaccessible pages and companies with contradictory hiring signals. Create a regression test for the Peoplebox-style contradiction.

### Do / don't

**Do:** Preserve the actual job-page URL and current check date.  
**Don't:** Classify companies as not hiring simply because the summary flag is false or a single page failed to load.

**Pass condition:** The engine examines relevant YC companies independently of directory hiring flags and reports how many jobs were found.

---

## 13. Day 10 — Investor and accelerator board readers

**Goal:** Capture roles that large ATS feeds missed in your benchmark.

### Priority sources

Investigate permitted public boards connected to:

- Accel
- Space Capital
- Techstars
- India-focused investors with public careers/talent pages

Reuse relevant extraction patterns from:

- https://github.com/sohan-shingade/jobslop
- https://github.com/Masterjx9/OpenPostings

The published **Jobslop data** is not assumed current; use reader code and patterns only. Where an upstream reader is written in JavaScript, **rewrite the small relevant reader in Python** rather than adding a JavaScript/Node runtime to the project. OpenPostings readers also need real pagination and completeness testing before being trusted.

### Steps

1. Inventory candidate investor boards, recording their URLs and whether they use Getro, Consider, Weekday or another system.
2. Reuse a common reader for boards sharing the same public data format.
3. Make the reader page through **all result pages** or report a precisely defined filter scope.
4. Preserve the investor/network name as a source and the underlying employer as the canonical company.
5. Import public company profile data from the board even if no matching job is open, when appropriate for Stream B.
6. Record source completion and count anomalies in `source_checks`.

### Claude Code prompt

> **Day 10 Claude Code task:** Adapt Getro/Consider investor-job readers using Jobslop/OpenPostings as references. Select a small test set of accessible investor boards including the benchmark-related boards. Verify their current formats and paging behavior; do not stop at the first result batch. Write normalization and complete/partial source-check tests. Import companies and jobs into the existing identity model. Do not enable unsupported access paths.

### Do / don't

**Do:** Verify total results versus pages downloaded; test with a board having more than one page.  
**Don't:** Infer an investor-backed company from a job title; accept first-page-only reads as complete; install a standalone Jobslop application that duplicates our SQLite/Sheets stack.

**Pass condition:** Multiple investor boards produce linked jobs and companies, and incomplete pagination is caught.

---

## 14. Day 11 — Finish deduplication and historical state logic

**Goal:** Distinguish a new job from a repost, and a failed check from a closed job.

### Matching sequence

1. **Exact employer/ATS IDs** -> highest confidence.
2. **Verified canonical website domains** -> strong company matches.
3. **Known source URLs and redirects** -> useful job identity hints.
4. **Blocked fuzzy suggestions:** `cleanco` + RapidFuzz only among plausible candidates, using name + title + location + description evidence.
5. If uncertain, **do not merge automatically**. Save a possible-match note for review.

### Historical state policy

- `New`: first confirmed observation in our system.
- `Active`: seen in a successfully checked active source.
- `Changed`: meaningful supported fields changed; store what changed.
- `Not Seen`: absent after an authoritative, successful, complete check; never from a failed check.
- `Closed`: explicit closure or repeated authoritative complete checks under an agreed policy.
- `Reopened`: same established job identity reappears after closure.
- `Unknown`: field or source certainty is insufficient. Keep this distinct from `Closed`.

### Claude Code prompt

> **Day 11 Claude Code task:** Implement robust duplicate matching and state transitions. Source failures, partial pagination, provider-retained snapshots and inaccessible pages must not produce false closures. Store content hashes for meaningful changes and append-only status events. Add unit tests for same-title different requisitions, duplicate reposts, record aliases, failed checks, repeated checks, reopening and source replays.

### Do / don't

**Do:** Prefer leaving one duplicate unresolved over incorrectly merging two different roles.  
**Don't:** Treat a publisher's old `last_seen` as an original posting date; mark all jobs missing from one run Closed.

**Pass condition:** All state tests pass against deliberately tricky examples.

---

## 15. Day 12 — Search profile, rule-based ranking and manual imports

**Goal:** Reduce large raw input to a sensible and explainable shortlist, without any AI model.

### Step 1: Create the profile

Ask Claude Code to create `config/search_profile.yaml`, with these **eight initial categories**:

```yaml
role_types:
  - ai_implementation
  - founders_office
  - gtm_engineering
  - forward_deployed_solutions
  - associate_product_ops
  - internal_ai_automation
  - business_finance_ops
  - venture_studio

locations:
  onsite_or_hybrid: [Bengaluru, Bangalore]
  remote_country: India

early_career_signals:
  - "0-3 years"
  - "new graduate"
  - "founder experience"
  - "work sample"
  - "paid trial"

shortlist:
  jobs: 20
  companies_only: 5
  suppress_rejected: true
```

This is a starting example. Claude Code should add role-title aliases, optional exclusion keywords and `config/ranking.yaml` point weights after you review the terms.

### Step 2: Two ranking systems

- **Posts (A):** role match, early-career signal, confirmed or possible India eligibility, newness, company quality signals, A/B overlap.
- **Companies (B):** AI-centered product evidence, fit to target sectors, funding/growth signals where sourced, Bengaluru/India presence and observed jobs.

Weights must be **editable YAML**, not hard-coded numbers or AI judgment. Every shortlisted row gets one factual one-line reason.

### Step 3: Conservative exclusions

- Explicit **incompatible** geography -> exclude from shortlist; retain the record.
- Unknown India eligibility -> `Needs Review`, not “India eligible.”
- Missing experience range -> Unknown, not “senior-only.”
- A company with no jobs -> remains Stream B, not a failed company discovery.

### Step 4: Implement manual import

Use a simple `imports/links.csv` template such as:

```csv
url,company_hint,job_title_hint,source,notes
https://example.com/role-123,Example Co,AI Implementation Associate,manual,Found while signed in
```

Example domains here are placeholders. The importer must also accept saved plaintext job descriptions or permitted exports and preserve that they were user-provided. Do not log in to protected websites on the user's behalf.

### Claude Code prompt

> **Day 12 Claude Code task:** Implement configurable conservative role/location filters, two deterministic ranking formulas, one-line evidence-based explanations and a top-20-jobs/top-5-companies shortlist. Add `imports/links.csv` and an import command; normalize it through the same identity and sightings pipeline as other sources. Keep rejected items in SQLite, but suppress them from normal suggestions. No Gemini or model-generated scores. Run tests on ambiguous remote eligibility and unknown experience.

### Do / don't

**Do:** Review synonyms to make sure the eight categories are meaningful; test that unusual but relevant titles survive.  
**Don't:** Delete every job with an unknown location; rank on invented funding claims; turn on Gemini “just in case.”

**Milestone 2 PASS:** The system can find from several independent sources, merge records, rank the survivors and generate a shortlist through one manual command.

---

## 16. Day 13 — Benchmark against known October 18 posts

**Goal:** Prove actual useful coverage, not simply count how many jobs were downloaded.

### Prepare a benchmark file

Create `tests/fixtures/benchmark_2026-10-18.csv` with the original **15 job postings**, their source URLs, company names and evidence of availability on **18 October**. Mark which **11** were still open then.

Use the original links from your research, not just names copied from memory. If an original URL is missing, label it as missing rather than inventing it.

### Important measurement distinction

The benchmark is historical. A job that was open on 18 October may be closed on the day we test it. Therefore measure separately:

- **Historical discoverability:** Does the relevant source or archived evidence allow us to locate the known posting?
- **Current availability:** Can we confirm the posting is open *now*?
- **Automatic discovery:** Was it found by an enabled source without manual seeding of that post?
- **Manual recoverability:** Could it be added through the import route?

Do not count a manually entered benchmark URL as an automatically discovered success.

### Per-item test fields

| Field | Example outcomes |
|---|---|
| Benchmark ID | Original post number |
| Company and role | From original October 18 report |
| Original source | Weekday / Saarthi / YC / VC / other |
| Found by engine? | Yes / No |
| Which adapter? | YC / JobSpy / investor / Feashliaa / none |
| Company linked correctly? | Yes / No / Uncertain |
| India eligibility | Confirmed / Unknown / Incompatible |
| Duplicate handled? | Yes / No |
| Filter and ranking correct? | Yes / No / Review |
| Current status verified? | Open / Closed / Uncertain |
| Gap action | None / improve query / reader / manual import |

### Claude Code prompt

> **Day 13 Claude Code task:** Build and run a benchmark report against the 15 historically identified jobs, including the 11 open on 18 October. Use their original URLs from the provided fixture. Compare automatic discovery by each source family and combined recall. Do not pre-feed benchmark URLs into discovery, and do not label expired jobs as current misses without checking current availability. Produce a CSV/Markdown gap report with every missing item and explanation.

### Do / don't

**Do:** Evaluate **unique relevant recovery**, not feed size; inspect false positives and misses.  
**Don't:** Claim 100% recall because we manually pasted all 15 URLs; compare current job-open status directly to a historical reference without qualification.

**Pass condition:** All benchmark items have a documented found/missed/unverifiable result and a reason.

---

## 17. Day 14 — Fill proven gaps and harden failure handling

**Goal:** Improve useful recall only where the benchmark shows a real gap.

### Steps

1. Rank **missing benchmark sources** by how many unique relevant roles they would add.
2. Recheck whether Weekday/Saarthi jobs already appear through employer pages, YC, JobSpy or investor boards.
3. If a permitted public reader would add meaningful coverage, implement and test it. Otherwise preserve a manual import workflow.
4. Add source-health checks for rate limits, stale snapshots, missing pages and incomplete pagination.
5. Require explicit confirmation before applying any source-parser fix to the LIVE run.
6. Test an interrupted run, a failed provider, a partial snapshot and a retry.

### Claude Code prompt

> **Day 14 Claude Code task:** Use the benchmark gap report to identify the top uncovered permitted sources. For each, document estimated unique benefit and maintenance cost. Implement only the smallest justified improvement. Run the complete pytest suite and a TEST-only discovery. Never mark records closed because a source or scraper failed. Produce a 'known limitations' section for the README.

### Do / don't

**Do:** Keep a visible list of uncovered sources; favor a generic reader when several boards share a platform.  
**Don't:** Add many scrapers simply to increase the number of integrations; hide errors; remove failing source records from history.

**Milestone 3 PASS:** We understand the benchmark misses and can reproduce safe discovery and failure behavior.

---

## 18. Day 15 — First controlled LIVE run

**Goal:** Start using the tool for real decisions.

### Before you run LIVE

- [ ] `doctor` reports both credentials and target Sheet access healthy.
- [ ] Test Suite: all core pytest tests pass.
- [ ] Test Sheet: sorting and verdict round trips pass.
- [ ] At least one successful source in both streams A and B.
- [ ] Feashliaa freshness handling is tested.
- [ ] JobSpy failures do not trigger closures.
- [ ] Investor reader pagination is complete for enabled boards.
- [ ] Benchmark results and known gaps are documented.
- [ ] SQLite backup has been produced.

### First production run

Open the project in Claude Code and type:

```text
/run-discovery
```

When prompted, select **LIVE**, or explicitly tell Claude Code to run in live mode; the first LIVE run should require confirmation. Python should use a CLI option such as `--sheet live` to target the correct Sheet.

When it finishes, open `A+B Discovery - LIVE`:

- **Shortlist:** Read the top opportunities first.
- **Posts:** Review details and change `Your Verdict` to Yes / Maybe / No.
- **Companies:** Review promising firms whether or not they have openings.
- **Runs:** Check sources attempted, failures, new records and runtime.

Change one verdict, rerun, and confirm the mark is still present.

### Day 15 do / don't

**Do:** Inspect real source links before acting on high-priority opportunities; mark your interests in Posts/Companies rather than Shortlist.  
**Don't:** Assume the top ranked job is still open without source verification; copy API keys into a Sheet; change system-owned IDs.

**Milestone 4 (partial) PASS:** The production run succeeds and human marks survive the next run.

---

## 19. Day 16 — Backups, hand-off and operational routine

**Goal:** You can run and maintain the system without remembering technical setup.

### Step 1: Ask Claude Code to write a short README

The README should cover only:

- How to open the project.
- How to launch Claude Code.
- `/run-discovery` and its TEST/LIVE options.
- Where the Google Sheet is.
- Where `search_profile.yaml` and `ranking.yaml` are.
- How to import a job link.
- How to interpret failed/partial source checks.
- How to back up and restore SQLite.
- What not to edit or delete.

### Step 2: Database backup

Use SQLite's safe online-backup API or `VACUUM INTO` from a controlled command. Do not assume that copying `discovery.db` while an active transaction is in progress produces a valid consistent backup.

Ask Claude Code to implement a user-invoked `backup` command that creates a timestamped file under `backups/`, checks it with `PRAGMA integrity_check`, and documents how to restore it.

### Step 3: Freeze working dependencies

After the production tests pass, have Claude Code pin all Python dependency versions and the JobSpy source commit/release. Commit **code and documentation only**, never `.env`, key files, your SQLite DB or copied private job content.

### Step 4: Keep operating instructions simple

**Normal use:**

1. Open PowerShell or VS Code in the project folder.
2. Start `claude`.
3. Type `/run-discovery`.
4. Review the Shortlist in Google Sheets.
5. Mark items Yes / Maybe / No in Posts/Companies.
6. Repeat whenever you choose (daily or every couple days during an active search, if convenient).

**If a source breaks:** Ask Claude Code to inspect the failed source log and propose a fix; **do not** allow silent production edits. A source repair should be tested before a LIVE run.

**If you choose 2–3 target role families later:** Edit `config/search_profile.yaml` and rerun. The full prior history stays available in SQLite.

### Day 16 do / don't

**Do:** Back up regularly, keep a record of working versions, test before changing source adapters.  
**Don't:** Delete the SQLite database to “start fresh”; overwrite `companies` or `jobs` IDs manually; let OneDrive synchronize the active database; accept unexplained mass job closures.

**Milestone 4 PASS:** You can operate the system again after closing and reopening the project, and backups are proven restorable.

---

## 20. Project file layout — what Claude Code should create

```text
ab-discovery-engine/
├── CLAUDE.md                            # Permanent safety and scope rules
├── README.md                            # Everyday user instructions
├── pyproject.toml                       # Python package and dependencies
├── .gitignore                           # Excludes secrets, SQLite and local data
├── .env.example                         # Harmless placeholders only
├── .env                                 # PRIVATE: local settings (not Git)
├── .secrets/                            # PRIVATE: Google service-account key
├── .claude/
│   └── skills/
│       └── run-discovery/
│           └── SKILL.md                 # ONLY user-invoked run Skill
├── config/
│   ├── search_profile.yaml              # Your role/location preferences
│   ├── ranking.yaml                     # Transparent weights
│   └── sources.yaml                     # Enabled/disabled sources
├── discovery/
│   ├── __init__.py
│   ├── __main__.py                       # python -m discovery
│   ├── cli.py
│   ├── pipeline.py
│   ├── db.py
│   ├── entities.py
│   ├── dedupe.py
│   ├── lifecycle.py
│   ├── filters.py
│   ├── scoring.py
│   ├── sheets_sync.py
│   ├── imports.py
│   ├── backups.py
│   └── adapters/
│       ├── base.py
│       ├── yc_companies.py
│       ├── feashliaa.py
│       ├── jobspy.py
│       ├── yc_jobs.py
│       ├── investor_boards.py
│       └── manual.py
├── tests/
│   ├── fixtures/
│   │   └── benchmark_2026-10-18.csv
│   └── test_*.py
├── data/                                 # PRIVATE SQLite and cache
├── imports/                              # User-supplied CSV/text
├── exports/                              # Reports and temporary exports
├── logs/                                 # Run and source errors
└── backups/                              # Safe timestamped SQLite backups
```

Folder names and modules are implementation targets, not existing files. Claude Code may simplify internal module boundaries if tests and required behaviors remain intact.

---

## 21. Quality checklist — acceptance tests for the finished product

The system is ready for normal use only when all statements below are true:

- [ ] User can explicitly start `/run-discovery` without typing Python commands.
- [ ] Stream A and Stream B both gather real records from independent sources.
- [ ] Feashliaa is supplementary, not the authority for all active jobs.
- [ ] YC company import does not ignore firms marked `hiring=false`.
- [ ] JobSpy works for at least one permitted tested source; unsupported boards are reported honestly.
- [ ] Investor readers check pagination and never silently truncate to page one.
- [ ] Every job is linked to one canonical company record or clearly marked for unresolved identity review.
- [ ] Identical records from different sources merge without losing sightings.
- [ ] Two distinct requisitions with the same title do not merge incorrectly.
- [ ] Empty, partial and failed source checks are distinguished.
- [ ] `Not Seen`, `Closed` and `Reopened` transitions have supporting evidence.
- [ ] Manual Yes/Maybe/No and notes survive repeated sync, sorting and crashes.
- [ ] All eight role families are searchable through an editable profile.
- [ ] No AI model is required for v1 classification and ranking.
- [ ] Shortlist contains explainable reasons and does not repeatedly suggest dismissed records.
- [ ] 18 October benchmark results show automatic versus manual recovery honestly.
- [ ] Source failures are visible in Runs and in the final run report.
- [ ] Production SQLite has a successful tested backup/restore procedure.
- [ ] No scheduled tasks, outreach, applications, Brain access or login automation exist.

---

## 22. Common issues and what to do

| Problem | Meaning | What you should do |
|---|---|---|
| `py -3.12` not recognized | Python launcher or installed version is missing | Reopen PowerShell, check Windows Python installation/launcher |
| `claude` not recognized | Claude Code installation or PATH issue | Run `claude doctor` if available; follow current official setup troubleshooting |
| `SpreadsheetNotFound` | Usually Sheet sharing or wrong spreadsheet ID | Confirm service-account editor access and sheet ID |
| Google API permission denied | Sheets/Drive API not enabled or wrong Google Cloud project | Verify Cloud project and API access; don't broaden permissions blindly |
| Python package fails to install | Incompatible version or missing binary | Use Python 3.12, project venv and pinned tested versions |
| A source returns zero jobs | Could be genuinely empty **or** inaccessible/blocked | Read source-check status, not just result count |
| Feashliaa job marked active but link closed | Publisher retained an older observation | Check latest provider scan and original employer page; preserve historical sighting |
| YC company says not hiring but has posts | Directory flag is outdated/incomplete | Keep both reported flag and independently observed jobs |
| Many duplicate jobs | Provider IDs or URL normalization need improvement | Re-run identity tests; do not mass-merge by title |
| Verdict moved to wrong row | Sync incorrectly used row numbers | Stop LIVE sync; restore backup and fix ID-based update logic |
| Script interrupted | Run incomplete | Resume/replay safely; verify no duplicate history events |
| Claude Code proposes major redesign | Unnecessary scope expansion | Refer it to `CLAUDE.md` and this agreed plan; require approval for changes |

---

## 23. First message to give Claude Code on Day 1

Copy this into Claude Code once your software is installed and you are inside the empty project folder:

> You are helping me build a local A+B job-and-company discovery engine on Windows. Read the implementation guide `AB_Discovery_Engine_Implementation_Guide.md` in this project folder and follow it as the source of truth. We will implement it one day/stage at a time, with tests and explicit acceptance gates. Use Python 3.12, SQLite, a four-tab Google Sheet (Shortlist, Posts, Companies, Runs), deterministic YAML filtering/ranking, and one user-invoked `/run-discovery` Claude Code Skill. No AI APIs, schedules, outreach, applications, Brain access, login bots, custom web app, or commercial infrastructure in v1. Do not skip ahead. First inspect my development environment, explain any missing prerequisites, create only safe scaffolding, and wait for me to approve Day 2.

**Important:** Put a copy of *this Markdown file* in the project root first so Claude Code can read it. If the file lives only in ChatGPT, Claude Code on your computer cannot automatically see it.

---

## 24. Official references and relevant open-source code

These are useful if Claude Code needs documentation; you do **not** need to read them all yourself.

**Install/setup**

- Claude Code Windows setup: https://code.claude.com/docs/en/setup
- Claude Code Skills: https://code.claude.com/docs/en/skills
- Python `venv`: https://docs.python.org/3.12/library/venv.html
- Git for Windows: https://git-scm.com/downloads/win
- VS Code: https://code.visualstudio.com/
- Google Cloud Console: https://console.cloud.google.com/
- gspread authentication: https://docs.gspread.org/en/master/oauth2.html
- Google Sheets values batch API: https://developers.google.com/workspace/sheets/api/guides/values

**Discovery sources and code**

- Feashliaa collector: https://github.com/Feashliaa/job-board-aggregator
- Feashliaa published feed: https://github.com/Feashliaa/job-board-data
- YC public company data: https://github.com/yc-oss/api
- JobSpy: https://github.com/speedyapply/JobSpy
- Jobslop: https://github.com/sohan-shingade/jobslop
- OpenPostings: https://github.com/Masterjx9/OpenPostings
- Greenhouse Job Board API: https://developers.greenhouse.io/job-board.html
- Lever Postings API: https://github.com/lever/postings-api
- Ashby public job-posting API: https://developers.ashbyhq.com/docs/public-job-posting-api

**Accuracy note:** Source APIs, GitHub repository layouts, job-board permissions, Claude Code releases and Google Cloud Console labels may change after this document's preparation. Use the current *official* documentation at the moment of each stage. Where a live source is not accessible, the program must report it instead of silently pretending the source was covered.

---

## 25. One-page summary of your personal actions

**What you personally need to do:**

1. Install Python 3.12, Git, VS Code and Claude Code; verify each works.
2. Create a local project folder and put this Markdown document inside it.
3. Create the TEST and LIVE Google Sheets with four tabs.
4. Configure the Google API identity; share only the two intended Sheets; keep keys private.
5. On each development day, paste the relevant Day prompt into Claude Code.
6. Insist on that day's **pass condition** before moving on.
7. Supply the original 15 October 18 benchmark links when we reach benchmarking.
8. Run LIVE only after the repeated-run and verdict-preservation tests pass.
9. Thereafter, simply start `/run-discovery`, review the Sheet, and mark Yes / Maybe / No.
10. Back up the database and allow adapter fixes only after tests.

**You are not expected to write the Python implementation yourself.** Your responsibility is setup, approving access, checking observable outputs and deciding what companies/jobs interest you. Claude Code implements the program; Python performs the repeatable discovery; SQLite remembers; Google Sheets makes review simple.
