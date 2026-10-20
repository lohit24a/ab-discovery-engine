# A+B Discovery Engine — Step-by-Step Implementation Guide

**Plan:** Hybrid source discovery, implemented locally in Python, with SQLite memory and Google Sheets review  
**Document version:** 1.0 · **Prepared:** 19 October 2026  
**Target computer:** Windows 10/11 · **Operator:** You, using Claude Code  
**Status:** Originally a build plan. **Implementation is under way; see the "Project Implementation Status and Decision Log" at the end of this document for what is actually built, what changed from this plan (for example Python 3.14.3 instead of 3.12) and why. Where the log and the original text below disagree, the log is authoritative.** The original plan text is kept unchanged for traceability.

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

---

## Appendix: MCP and External Job API Evaluation Backlog — October 2026

**Status:** Future research backlog only. It does **not** change the implementation plan above, and no connector listed here has been installed. The decisions below are planning decisions about what to test, **not** conclusions about data coverage. No connector listed as "Test" has passed validation. The original source priorities (Feashliaa as a supplementary feed, JobSpy, YC and investor boards) are unchanged.

| Connector | Decision | When to evaluate | Reason |
|---|---|---|---|
| Indeed official MCP | Test; possibly retain for Claude-assisted discovery | Source integration stage | Official interactive job search; India coverage, pagination and incremental value need verification |
| JobDataLake | Test MCP and REST API | Source integration stage | Potentially useful low-cost employer-job data and structured descriptions; assess overlap with Feashliaa |
| JobsPipe | Test free allowance before considering paid access | After baseline sources work | Normalized multi-source job data; paid subscription justified only by unique relevant discoveries |
| Jobs by Workable | Test for incremental ATS coverage | After baseline sources | May expose employers missing from other feeds |
| Job Hunt Copilot (India) | Reference or reuse search-query ideas; do not install as another workflow | If Indian-source coverage gaps remain | India-relevant sources, but no verified replacement for our persistent discovery engine |
| Job Hunt HQ | Reference only | No installation required | Local tracking concepts overlap with SQLite and Google Sheets |
| Greenhouse MCP | Do not install for this use case | Not planned | Designed for authorized employer recruiting accounts; continue using public Greenhouse Job Board APIs |
| Develop21 Career Coach and job tracker | Do not install | Not planned | Duplicates our tracking, profile and review workflows |
| Remaining unrelated job/business connectors | Do not install | Not planned | No demonstrated contribution to A+B discovery |

### Evaluation criteria for future experiments

Judge each tested connector on:

1. Unique relevant Bengaluru / India-remote jobs discovered (beyond sources already enabled).
2. Coverage of our 18 October benchmark vacancies.
3. Accuracy of employer, location and experience requirements.
4. Posting freshness and completeness.
5. Access limits and operating cost.
6. Whether Python can use a documented API independently of Claude.
7. Whether the source can be used repeatedly and reliably.

A connector usable only interactively through Claude cannot become part of the scheduled-free, Python-authoritative `/run-discovery` pipeline unless criterion 6 is met; any such use must be proposed and approved before implementation, consistent with `CLAUDE.md`.

---

## Restricted Job Board Execution Policy

**Permanent.** This applies to all future development, not only Day 8. Source of truth for the maximum scope: `config/restricted_sources.json`; enforced in code by `discovery/restricted_policy.py`, `discovery/network_guard.py` and `discovery/adapters/jobspy_source.py`, not by prompt text.

1. **The three restricted platforms:** Indeed, Naukri and LinkedIn (reached through JobSpy; Glassdoor was retired, see D-23). Unlike YC and Feashliaa they **never run automatically** and never as part of general discovery. `run --sources yc,feashliaa --target test` cannot contact them; `run` rejects their names, `all`, wildcards and `jobspy`; `run_pipeline()` rejects them even when called directly.
2. **Default disabled.** All three are `enabled: false` (a source is enabled in memory only for one approved execution). LinkedIn is additionally recorded as `provider_authorization = not_established`, so it needs `--attest-provider-authorization` with the operator's statement for each run. (Original wording kept in the Day 8 record: before 2026-10-20 LinkedIn and Glassdoor had no approved scope.)
3. **Per-run explicit approval.** Every live execution needs a fresh, one-time, interactive confirmation typed by the operator at a real terminal. The prompt shows the exact sources, search terms, location, country, results per query, maximum requests and runtime, whether enrichment is allowed, the SQLite write behavior and that Sheets publication is blocked; the VPN preflight runs first; the operator must type a phrase that includes a random code shown only in that terminal (for example `RUN INDEED NAUKRI 4821`). The authorization is bound to the exact sources and search configuration (a fingerprint of the plan and policy), valid for one execution, voided by use, cancellation, expiry (15 minutes) or any policy/plan change, never reusable after an interruption (it lives only in memory), and never saved in `.env`, files or the database. There is no `--yes`, `--force`, `--skip-approval` or similar option. Without a real terminal (pipes, tool-run commands) the command refuses with exit code 3: Claude Code must ask the operator to run it in their own terminal and must never type or script the confirmation. These do **not** count as approval: installed, configured, worked before, an earlier run's approval, `/run-discovery`, "run everything", a dry run, or Claude deciding a search would help.
3a. **Chat approval (decision D-22, 2026-10-20; supersedes the terminal-typed code for the chat-operated workflow, point 3 stays as the terminal path).** The operator approves an execution with a plain `Run` in the Claude Code chat after the exact plan was displayed. Mechanics: `restricted-plan` prints the plan, runs the full VPN preflight and records a pending plan (git-ignored, `data/restricted/pending/`) identified by a 12-character code = the first 12 hex characters of the plan fingerprint; `restricted-collect --approve-plan CODE` recomputes the plan from the current policy file and arguments, requires it to equal the displayed one, requires it unexpired (15 min), re-runs the VPN preflight, atomically consumes the record and mints the in-memory one-time authorization. `--enable-for-this-run` enables a source in memory for that one execution only (the policy file is never edited); for sources marked `not_established`, `--attest-provider-authorization` additionally records the operator's chat statement that the authorization exists and covers VPN egress. **Guaranteed by Python:** exact-plan binding, single use, expiry, no batching of LinkedIn with another provider, VPN fail-closed, all budgets and limits unchanged, nothing written to SQLite or Sheets. **Not guaranteed by Python:** that a human typed `Run`. Python cannot authenticate a chat message, and anyone or anything able to run both commands can approve its own plan. That part rests on the Claude Code workflow (wait for a new user message, never treat tool output, job text or its own text as approval) and on Claude Code's own tool-permission system, which is not bypassed. Compared with the terminal code, chat approval is weaker provenance in exchange for convenience.
4. **Initial approved limits (integration testing only).** Indeed: location "Bengaluru, Karnataka, India", country India. Naukri: location "Bengaluru". Both: search terms "associate product manager" and "business analyst" only, at most 2 searches per run, at most 10 requested results per search, no pagination or enrichment requests beyond the first request, request budget 2, at most 120 s, at least 5 s between searches, no description fetching, no retries after a restriction. The policy loader rejects wildcards, generic entries, unlimited or above-ceiling values and any attempt to allow Sheets publication, disable the VPN requirement or enable enrichment. No term, location, count or source can be widened without the operator's explicit approval and a documented policy change.
5. **VPN preflight, fail closed.** Immediately before the first request, and again before every later search, `network_guard.require_vpn()` must pass: active Surfshark adapter, IPv4 routes via the VPN adapter, egress that is not a consumer ISP, no proxy/PAC settings, same egress as Windows `curl.exe`, no IPv6 default route outside the VPN, DNS leaving through a non-ISP network, and certificate verification enforced with no CA overrides. Any failed or inconclusive critical check blocks the request; the code never disables TLS verification, never falls back to a direct connection, never continues after a VPN failure, never prints an IP address and never uses proxies, rotation, CAPTCHA solving or other evasion. **Limitations:** the egress and DNS "network owner" class is a heuristic from an IP-information service, not proof of anonymity; a pass is a snapshot and cannot guarantee the VPN will not drop during a request (the Surfshark Kill Switch is the OS-level protection; its mode and the Bypasser list can only be confirmed by the operator in the app).
6. **No retries around provider restrictions.** HTTP 403/406/429, CAPTCHA, timeouts and budget exhaustion stop the affected source; there is no retry, no second attempt and no scope increase; the other authorized source continues independently; nothing is marked Closed or Not Seen.
7. **Two phases, two approvals.** *Collect* contacts providers inside the approved scope and writes only a temporary Git-ignored snapshot (`data/restricted/`, plus a no-IP audit line in `logs/restricted_runs.jsonl`), reporting counts, data quality and comparison with existing records; it writes nothing to SQLite or Sheets. *Import* never contacts a provider: it verifies the snapshot hash, creates and verifies a SQLite backup, and imports only what was collected, after its own interactive confirmation (`IMPORT ...`). Delete snapshots after importing; they are not committed.
8. **No automatic publishing.** Restricted-source jobs belong to companies flagged `sheet_publish = false`; nothing is exported to TEST or LIVE until relevance rules are approved. **No scheduled or background execution.**
9. **Conditions for enabling LinkedIn (and, historically, Glassdoor, now retired):** the operator confirms authorization for that platform, approves an exact test scope in writing, a source audit of the current upstream scraper is added, the scope is added to the policy file (small numbers, VPN required), an implementation and offline tests exist, and the per-run confirmation applies exactly as above.
10. **Known limitations:** `requested results` is not a request limit - Indeed asks its API for up to 100 jobs per page and Naukri for 20 even when 10 are wanted; the 10-result cap is applied locally. Request counts are enforced at JobSpy's single transport choke point (`RotatingProxySession.request`), which every Indeed/Naukri request uses in v1.4.0 but a future library version could change; the unit tests exercise it with fakes, not the real providers. Deliberate local code changes or a keystroke-injecting process could defeat the terminal confirmation: the rule is also a standing instruction in `CLAUDE.md` and the Skill, and the code makes accidental or scripted bypass fail.

---

## Project Implementation Status and Decision Log

**Last updated:** 2026-10-20 (end of Day 8). **Purpose:** the authoritative record of what was planned, what is actually implemented, what changed and why, and what remains. A new Claude Code conversation with no chat history should be able to continue from this section plus `CLAUDE.md`. Everything above this section is the original plan and is deliberately left unedited.

### Status at a glance

| Day | Original plan focus | Status | Git reference | Automated tests at checkpoint |
|---|---|---|---|---|
| 1 | Windows setup | **Complete** | `6ee532b` Initial project setup | none yet |
| 2 | Google Sheets + credentials | **Complete** | `ca8d806` | none (read-only doctor script verified manually) |
| 3 | Project skeleton + SQLite | **Complete** | `550d4d3`, `29173fb`, `6368458` (+ `9c0984a` appendix) | 7 → 31 → 67 |
| 4 | Safe Sheets sync | **Complete** | `73244c0`, `ddcd72a` | 106 → 116 |
| 5 | YC company feed (Stream B) | **Complete** (includes the approved D-09 label refresh) | commit "Day 5: Add YC company discovery and implementation history" (hash in `git log`) | 159 → 162 |
| 6 | Feashliaa supporting feed (A) | **Complete**: audit, adapter, migration 4, tests, live dry-run and the approved real import done; publishing of jobs to TEST deliberately not started | `67d0964` (+ docs `126a930`) | 221 |
| 7 | Run pipeline + single Skill | **Complete**: pipeline, `run`/`report`, Skill, tests, offline integration, live dry-run, approved real run, live repeat and frozen-data replay all passed | `f9b07e1` | 243 |
| 8 | JobSpy integration (final scope: Indeed India, Naukri, LinkedIn; Glassdoor investigated and retired, D-23) | **Complete**: JobSpy 1.4.0 pinned; restricted-source policy, chat-approved one-time authorization, Surfshark preflight, transport request budgets, credential guard and sanitized request traces implemented; Indeed, Naukri and LinkedIn each returned live listings (Indeed/Naukri in the small probes before the controls existed, LinkedIn through the final chat-approved run); Glassdoor returned HTTP 403 on its first request and was removed from the executable engine. All three sources are disabled by default; nothing from them is imported to SQLite or published to Sheets (import is separately approval-gated). | `8b9bdf9` | 353 |
| 9-16 | per original plan | **Pending** | - | - |

**Milestone 1 ("data and memory work") has passed (Day 7).** YC (B) and Feashliaa (A) are collected through one manual `run` into SQLite, history persists, the TEST sheet updates, a second identical run creates no duplicate companies, jobs, sightings or status events, and `/run-discovery` exists. Human verdict survival through repeated runs is proven by automated tests with fixtures and by the Day 4 live TEST demonstration; the real database currently holds no verdicts. Stream A is still supplementary and unpublished to the sheet by design.

### Deviations from the original plan (applies to all days)

| ID | Original plan | What was actually done | Why |
|---|---|---|---|
| D-01 | Python 3.12.x | **Python 3.14.3** in `.venv` (`py -3.14 -m venv .venv`) | The operator already had 3.14.3 installed and instructed that no other Python be installed. Every dependency installed and tests pass on 3.14.3 (see Day 1). If a future dependency is incompatible (e.g. JobSpy on Day 8), report it; never switch Python silently. |
| D-02 | `python -m discovery doctor` also tests Google credentials | The local `doctor` is offline and read-only; Google checks live in the separate, read-only `scripts/doctor_sheets.py` | Keeps the everyday health check free of network and credential access. |
| D-03 | Four-table-per-concept sketch, modules `dedupe.py`, `lifecycle.py`, `entities.py` etc. | Identity, dedupe, lifecycle guards and repository functions live in `discovery/database.py`; schema in `discovery/schema.py` | Smaller surface while only SQLite logic exists. Split later if it grows. |
| D-04 | Schema listed as eight tables | Still exactly eight tables; **three migrations** (v1 base, v2 `jobs.requisition_id` + closure-evidence trigger, v3 nullable `verdicts.decision` for notes-only reviews) | Needs found while testing identity (Day 3) and review workflow (Day 4). |
| D-05 | Company and job share lifecycle states | Only **jobs** have a lifecycle; company-level status events are rejected by `append_status_event` | A closed vacancy says nothing about whether a company is interesting or alive; company interest is expressed by the human verdict. |
| D-06 | Write to the Sheet freely during development | Sheets sync can only target **TEST**; LIVE is blocked in code and in the CLI | Safety rule: LIVE stays untouched until the pre-LIVE checklist passes and a separate authorization is given. |
| D-07 | Spreadsheets created by Claude | The operator created the TEST and LIVE spreadsheets; tabs were set up via the Google Sheets connector | The connector can read and edit but cannot create files. |
| D-08 | YC `website` becomes the company domain | Source-reported website is a **fact** (`facts.yc.website_reported`), not `companies.domain` | `domain` means "verified" and is uniquely indexed; YC data has missing, scheme-less and shared hosts. |
| D-09 | n/a (new in Day 5 review) | Geographic label `explicitly_incompatible` renamed to **`no_india_evidence_yet`** in code, tests and, after approval, in the 3,006 stored company records (refresh run `80a79aee…`, 2026-10-20) | The old name overstated the evidence: a company located outside India with no remote hint is not "incompatible" with hiring India-based candidates. |
| D-10 | Feashliaa rows carry a job ID; repos are `Feashliaa/...` | Repos now live under `Riley-Dorrington/...`; published rows have **no job ID and no description**; identity is `ATS\|board code\|normalized job URL` | Verified in the live audit (`docs/source_audits/feashliaa.md`). Job IDs exist in the scraper but are dropped before publication. |
| D-11 | Feashliaa freshness "to be verified" | Each row is classified `latest_collection` / `provider_retained` / `unknown` from its own `scraped_at` versus the manifest time (6 h window); retained rows get job state **Unknown**; nothing is ever promoted to Active. **`New` on a Feashliaa job means only "first observed by our engine"; it is not an employer-confirmed open vacancy.** | About half of the published rows are retained from earlier runs and a failed board looks identical to retention. |
| D-12 | Not planned | `sources.config_ref = "no-closure-evidence"` makes `append_status_event` refuse Not Seen/Closed evidence from that source | A fully validated Feashliaa snapshot says nothing about whether employer boards were refreshed. |
| D-13 | Not planned | Companies created from Feashliaa board codes carry `facts.sheet_publish = false`; the sync now also gates **new Posts rows** by their company's flag (a human verdict/note still publishes a job) | Prevents a 25,000-job import from flooding the review sheet. A reviewed publishing rule is a separate approval. |
| D-14 | Not planned | Migration 4: `sightings.provider_first_seen_at`, `provider_freshness`, `snapshot_at` (additive) | Provider first-seen, last observation (existing `provider_observed_at`) and snapshot time must stay separate from our own first/last seen. |
| D-15 | A combined run needs a parent run | One `runs` row per `run` (`run_type = 'discovery'`); source and sync runs are linked by `parent_run_id`/`source` in their JSON summary; no migration | Smallest compatible design that keeps each adapter's audit history intact; a foreign-key parent column was judged unnecessary. |
| D-17 | Day 8: test Naukri and Indeed India through JobSpy | First: stop before any request because Indeed's terms/robots.txt prohibit unauthorized automation and Naukri's permission was unknown. Then, on the operator's stated authorization from both providers: JobSpy 1.4.0 installed and pinned; live probes still pending (blocked by the assistant's permission layer); no adapter; sources stay disabled until verified | `CLAUDE.md` requires permitted access; the operator's authorization is recorded but unverifiable by us. A provider-issued credential, if one exists, is still outstanding for both boards. |
| D-18 | Day 8 text: "install a tested JobSpy and integrate" | Integrate through the Python API (not the CLI or MCP), one board per call; boards separate and disabled by default; fail-closed proxy configuration instead of any default direct connection | Preserves per-board diagnostics, keeps Plan A, avoids an extra MCP dependency, and makes any use of the home IP an explicit visible choice. |
| D-19 | Day 8 design: fail-closed proxy via `JOBSPY_PROXIES` | Default network layer is the operator's Surfshark OS-level VPN; live board runs require a passing `scripts/check_vpn_egress.py` (passed once the VPN was connected on 2026-10-20; re-run each session); a JobSpy proxy stays optional | One system-wide VPN covers every tool consistently without proxy credentials in `.env`. A heuristic egress check supplements, never replaces, the operator's Kill Switch and DNS-leak confirmation. |
| D-20 | Day 8: JobSpy sources integrated like other sources, selectable in `run` | Restricted sources are separate: `restricted-collect` / `restricted-import`, one-time interactive operator confirmation per execution, default disabled, policy in `config/restricted_sources.json`, VPN preflight before every search, hard request/time budgets, no retries around restrictions, snapshots instead of direct DB writes, never published to Sheets | The operator's standing rule for Indeed, Naukri, LinkedIn and Glassdoor (permanent; see "Restricted Job Board Execution Policy"). |
| D-22 | Day 8: operator approves restricted runs by typing a random 4-digit code in a separate terminal | Operator approves by a plain `Run` message in the Claude Code chat after the exact plan and its plan code are displayed; Claude then runs `restricted-collect --approve-plan CODE`; the terminal path remains available | The operator found the terminal step cumbersome. Python still enforces plan binding, single use, 15-minute expiry, VPN preflight and all limits; provenance of the chat message rests on the Claude Code workflow and its tool permissions, which is weaker than an authenticated terminal confirmation (documented limitation). |
| D-23 | Day 8: JobSpy sources Indeed, Naukri, LinkedIn and Glassdoor | **Final JobSpy scope: Indeed India, Naukri, LinkedIn.** Glassdoor is permanently removed from the executable engine (config entry, adapter routing, CLI selection, budgets, plans, Skill); `glassdoor` and `jobspy_glassdoor` are rejected as unsupported sources. The investigation stays documented as a retired source (`docs/source_audits/jobspy.md` sections 15, 18, 22-24); the installed `python-jobspy` package is untouched | Glassdoor returned HTTP 403 on its first (location-autocomplete) request; no bug, newer release or authorized access route (API, credential, allow-list) was found, and further engineering is not justified. All safeguards for the remaining three sources are unchanged. |
| D-21 | Day 8 (LinkedIn/Glassdoor): treat like Indeed/Naukri once the operator approves | Prepare only: proposals disabled with `provider_authorization="not_established"`; each provider authorized alone; loader and live call require established authorization that covers VPN egress; scraper transport must be provably budget-enforceable; Glassdoor budget counts its 2 base requests | LinkedIn prohibits the access method without express permission and Glassdoor's authorization is unverified; browser-fingerprint impersonation and robots-disallowed paths make an unspecific approval insufficient. A VPN is not assumed to comply. |
| D-16 | `run ... --sheet test` | `run ... --target test`, TEST sync previewed by default and applied only with `--apply-sync` or the separate `sync` command | One argument convention shared with `sync`; a write checkpoint the Skill can ask about. |

Planned-layout items from section 20 that **do not exist yet**: `README.md`, `pyproject.toml`, `config/search_profile.yaml`, `config/ranking.yaml`, `config/sources.yaml`, `.claude/skills/run-discovery/SKILL.md`, `discovery/pipeline.py`, `filters.py`, `scoring.py`, `imports.py`, `backups.py`, the `run`/`import`/`report`/`backup` commands, `tests/fixtures/benchmark_2026-10-18.csv`, PyYAML, rapidfuzz and cleanco (not installed). Backups so far are manual SQLite backups in the git-ignored `backups/` folder.

### Day 1 - Environment and project setup (Complete)

- **Objective:** a working Windows toolchain and project folder.
- **Planned:** install Python 3.12, Git, VS Code, Claude Code; create `C:\Users\<user>\Projects\ab-discovery-engine`, `git init`, `.venv`.
- **Done:** project at `C:\Users\lohit\Projects\ab-discovery-engine` (outside any synced folder). Existing Python 3.14.3, Git 2.53.0, VS Code and Claude Code Desktop were reused; nothing was reinstalled. `.venv` created with the 3.14 launcher. `.gitignore` (excludes `.venv/`, `.env`, `.secrets/`, `*.db`/`*.sqlite*`, logs, caches, `data/*`, `backups/`) and `CLAUDE.md` (scope and safety rules) written. Private GitHub repository `lohit24a/ab-discovery-engine` connected; branch `main`.
- **Changes and reasons:** D-01 (Python 3.14.3). Git Credential Manager kept returning a different saved GitHub account, so this repository's `.git/config` uses the GitHub CLI credential helper for `lohit24a` (repo-local only; global settings and the other saved credential were left alone).
- **Verification:** `.venv\Scripts\python.exe --version` = 3.14.3. All later dependencies (`requirements.txt`: gspread 6.2.1, google-auth 2.62.0, python-dotenv 1.2.4, pytest 9.1.1 and their dependencies) installed without error; `pip check` reports no broken requirements; the full suite passes on 3.14.3.
- **Limitations:** none known. Dependencies planned for later days (JobSpy, rapidfuzz, cleanco, trafilatura, PyYAML...) have **not** been tested on 3.14 yet.

### Day 2 - Google Sheets authentication (Complete)

- **Objective:** Python can read the TEST spreadsheet with a dedicated identity.
- **Planned:** two spreadsheets with four tabs, Google Cloud project, Sheets/Drive APIs, service account, JSON key in `.secrets/`, `.env`, a read-only `doctor`.
- **Done:** Google Cloud project `ab-discovery-engine`, Sheets and Drive APIs enabled, service account `ab-discovery-sheets` (key stored at `.secrets/google-service-account.json`, git-ignored, never printed). Spreadsheets `A+B Discovery - TEST` and `A+B Discovery - LIVE`, each with tabs Shortlist, Posts, Companies, Runs, shared with the service account as Editor. `.env` holds the two sheet IDs and the credential path. `scripts/doctor_sheets.py` authenticates with the read-only scope, opens TEST, checks the four tabs and reads a range. `requirements.txt` pinned.
- **Changes and reasons:** D-02, D-07.
- **Verification:** doctor exit 0; confirmed no write calls (read-only scope). Commit `ca8d806`.
- **Limitations:** the LIVE sheet has never been accessed by Python.

### Day 3 - SQLite foundation (Complete)

- **Objective:** a local database that remembers companies, jobs, sightings and decisions across manual runs.
- **Planned:** project skeleton, eight tables, migrations, identity and replay tests.
- **Done (three steps):**
  - *Step 1* (`550d4d3`): `discovery/` package, `argparse` CLI, offline read-only `python -m discovery doctor`, `.env.example`, `.gitkeep` folders, 7 tests.
  - *Step 2* (`29173fb`): `schema.py` (versioned migrations via `PRAGMA user_version`), `database.py` (connection with foreign keys, WAL, busy timeout, explicit transactions, repository functions), `init-db` command, doctor database checks. Tables: `companies`, `jobs`, `sources`, `sightings`, `runs`, `source_checks`, `status_events`, `verdicts`. 31 tests.
  - *Step 3* (`6368458`): migration 2, `observe_company` / `observe_job`, URL normalization, content hashes, replay-safe runs and source checks, Not Seen/Closed only with a successful complete source check (enforced by a trigger), append-only status events, 67 tests.
- **Identity rules implemented:** companies match by **verified domain only**, or by a source's own ID/URL for provisional companies; never by name. Jobs match by (source, provider ID/URL) or by employer requisition ID within one company; same title/location alone never merges. Conflicting evidence raises `IdentityConflict` instead of merging. Failed, partial, skipped or unverified checks can never close a job. Verdicts are only changed by explicit `save_verdict` calls.
- **Changes and reasons:** D-03, D-04, D-05. The MCP/API evaluation backlog appendix was added in `9c0984a` (research backlog only).
- **Limitations:** merging two provisional companies later proved equivalent is manual and not built; sightings are not linked to the source-check scope that produced them; no job-closure policy exists; multi-source disagreement on a job's fields is not arbitrated.

### Day 4 - Google Sheets synchronization (Complete)

- **Objective:** a human can review in Sheets and decisions survive every sync.
- **Planned:** two-way sync keyed by record ID, verdict/notes protected, sort-safe, repeat-safe.
- **Done:** `discovery/sheets_schema.py` (column specification and ownership: key / machine / human), `discovery/sheets_sync.py` (read everything first, validate headers and IDs, reconcile human fields, plan, re-check IDs, import verdicts into SQLite, batched writes), CLI `sync --target test [--dry-run]`. Migration 3 allows a note with no verdict (`decision` NULL) and `save_verdict` keeps any field left unspecified. Human columns are only filled or restored, never replaced by blanks; machine columns flow only SQLite → sheet; rows are matched by `record_id` and columns by header name; duplicate or unknown IDs abort the sync before any write; conflicts (both sides changed) are reported and nothing is overwritten; a sync with conflicts is recorded as `partial`.
- **Verification:** 106 tests at `73244c0`, 116 at `ddcd72a`; TEST-only integration with fictional records (initial sync, verdict import, row sorting via the connector, repeated syncs with zero writes); the fictional rows were removed with approval; the real database was backed up before migration 3.
- **Changes and reasons:** D-06; Runs tab lists discovery/import runs only (sync bookkeeping runs are hidden so repeated syncs make no sheet changes); worksheet lookups are cached and HTTP 429 is retried because the first demo hit Google's per-minute read quota.
- **Limitations:** intentionally clearing a verdict or note from the sheet is not supported (a blank cell restores the stored value; Python can clear explicitly); Google Sheets has no atomic compare-and-swap so editing during a sync can still race (documented in `sheets_sync.py`); Shortlist stays empty until ranking exists.

### Day 5 - YC company discovery (Complete; see D-09)

- **Objective:** Stream B: discover YC companies whether or not they are hiring.
- **Planned:** YC company importer; companies stored with websites and source dates.
- **Done:** live audit of `yc-oss/api` recorded in `docs/source_audits/yc_oss.md` (unofficial dataset produced daily from YC's public search index; 6,311 companies matching `meta.json`). `discovery/adapters/yc_companies.py` fetches `meta.json` and `companies/all.json` (timeouts, bounded size, retries, schema-change detection, stale and count-mismatch detection, refusal of snapshots older than data already imported), normalizes records, classifies India evidence deterministically, and imports idempotently in transaction batches. CLI `import-yc --dry-run | --apply`.
- **Results (verified from the database and recorded runs on 2026-10-20):**
  - 6,311 source records → 6,311 canonical companies and 6,311 YC sightings (6,311 distinct YC IDs); 0 malformed, 0 identity conflicts, 0 duplicates. Two runs recorded (`b0bd05ba…` created 6,311; `a8f987c6…` created 0 / unchanged 6,311), both `success` with a complete source check. All companies are `provisional` with no verified domain; 0 jobs, 0 status events, 0 verdicts.
  - The YC hiring flag is stored only as `facts.yc.is_hiring_reported` ("YC-reported, unverified"). 1,494 companies report `true`; no company was excluded for `false`, including 173 India-based ones.
  - Source-reported websites are not verified domains (D-08). Two website hosts shared by two companies each were reported and not merged.
  - **All 6,311 companies are stored in SQLite; only the relevant subset is published to Google Sheets.** `facts.sheet_publish` is true for the 224 `india_based` companies, which were synchronized to the TEST Companies tab (224 rows, each once, IDs equal to SQLite, Domain blank, YC facts shown as "YC-reported"). Companies outside the subset reach the sheet only if they carry a human verdict or note.
  - Classification at import: `india_based` 224; `potentially_relevant` 1,124 (fully remote per YC, India eligibility **unconfirmed**; remote never implies India eligibility); `unverified` 1,957; `no_india_evidence_yet` 3,006 (first imported under the older name `explicitly_incompatible`; renamed in storage by a third import run, see D-09 and the next subsection).
  - Spot check of four companies against their public YC pages (DocVita, RocketSDR, Increase, 42Floors) matched on batch, location, website and status.
  - LIVE spreadsheet untouched. No YC job pages were collected.
- **Verification:** 159 tests at the first Day 5 review, 162 after the classification correction; `pip check` clean; doctor 18 PASS; repeated import and repeated sync produce no changes.
- **Source limitations:** unofficial dataset; depends on a key scraped from YC's own page and on a daily GitHub Action (could break or lag); only publicly launched companies; `isHiring` is unreliable (110 of the 159 Active India companies show `false` although some may hire); `changes/latest.json` covers only the latest day.

### Geographic classification terminology (Day 5 review, decision D-09)

- **Where calculated and used:** `classify_location` in `discovery/adapters/yc_companies.py` from YC's `all_locations` and `regions`. The result is persisted in `companies.facts.india_relevance` (`class`, `reason`) and drives only one boolean, `facts.sheet_publish` (true for `india_based`), which controls whether a *new* row is added to the review sheet. It is also shown in the Details text of published rows (all `india_based`). It is not used to filter, exclude, rank or close anything.
- **Problem:** `explicitly_incompatible` (3,006 companies; 2,586 are US-only) meant "locations listed only outside India and no remote hint in YC's data". That is a lack of India evidence, not an explicit restriction.
- **Decision:** rename to **`no_india_evidence_yet`**, reword the reason ("... no India evidence yet (not an eligibility restriction)"), and document that company geography is never a permanent job-eligibility ruling: the label is recomputed on every import (a company that later lists an Indian office moves to `india_based` and appears in the sheet; covered by a test). Remote-first/fully-remote stays `potentially_relevant`, never "India eligible".
- **Production-data change (approved and applied 2026-10-20):** after a dry-run preview ("stored facts to refresh: 3006", 0 new) and a verified backup (`backups/discovery_pre-label-refresh_*.db`), `import-yc --apply` ran as run `80a79aee…`: 3,006 companies refreshed, 3,305 unchanged, 0 created. Counts afterwards: `india_based` 224, `potentially_relevant` 1,124, `unverified` 1,957, `no_india_evidence_yet` 3,006; 6,311 companies and sightings, no duplicates; a following dry-run shows 0 facts to refresh. The 224-company TEST subset and its rows were not changed by the refresh (no TEST row displayed the old label). That run also moved the companies' last-seen time, so the next `sync --target test` will rewrite the Last Seen cell of the 224 rows and add the new import run to the Runs tab (verified by dry-run: only the Last Seen column differs); this sync had not been run at the end of Day 5.

### Day 6 - Feashliaa supporting job feed (Complete; commits `67d0964` and `126a930`)

- **Objective:** Stream A: import relevant jobs from the Feashliaa ATS dataset while protecting the database against stale, retained, duplicated or mis-identified rows.
- **Planned (section 9):** manifest + gzip chunks, stream-filter for Bengaluru/India/India-eligible remote, keep Feashliaa optional and provenance-aware, never treat retained rows as open, import twice without duplicates.
- **Audit (`docs/source_audits/feashliaa.md`, 2026-10-20):** repos moved to `Riley-Dorrington/job-board-aggregator` (HEAD `ba8cfa9d`) and `job-board-data` (HEAD `beb43849`, a single force-replaced commit). Live snapshot: manifest `last_updated` 2026-10-19T15:48:40Z, 58 chunks of 25,000 rows, `totalJobs` 1,436,282 which equals the rows actually present; Workday 636k, iCIMS 247k, Greenhouse 236k, Paylocity 103k, Lever 92k, BambooHR 63k, Ashby 58k. 47.8% of rows come from the latest collection run, the rest are retained up to 30 days. Open anomaly issue #53 (Ashby volume drop). No job IDs, no descriptions; `remote` is inferred from location text and absent for Ashby; `skill_level` is a title-keyword guess.
- **Implemented:** `discovery/adapters/feashliaa.py` (manifest validation, resumable chunk cache in git-ignored `data/cache/feashliaa/`, one-chunk-at-a-time streaming scan with early filtering, geography classifier, title prefilter from editable `config/feashliaa_prefilter.json`, freshness classifier, snapshot identity, import in transaction batches, source-check/run recording); CLI `import-feashliaa --dry-run | --apply`; migration 4; `observe_job`/`record_sighting` forward provider provenance; closure-evidence guard for this source (D-12); Posts publishing gate (D-13).
- **Rules implemented:** company identity = (ATS, board code) scoped to this source, always provisional with no domain, never auto-merged with YC (name matches are recorded in `facts.feashliaa.possible_yc_matches` for review; false merges are worse than duplicates); job identity = (ATS, board, normalized URL), never title; conflicting identity raises `IdentityConflict` and aborts that run. Import refuses (and records a `partial` check) on stale snapshot (>3 days), missing/corrupt chunk, row-count mismatch with the manifest, manifest republished during download, or a snapshot older than what was imported; the same snapshot is skipped (`skipped` check). Nothing is written until every chunk validated.
- **Dry-run results (live, 2026-10-20):** 58/58 chunks read, 1,436,282 rows scanned in 66 s, peak memory about 158 MB, 0 malformed. Geography of all rows: Bengaluru 14,248; India explicit 11,519; India inferred (city/state only) 11,228; remote without India 60,763; other 1,338,524. After the title prefilter 25,219 jobs from 3,872 boards would be stored (Bengaluru 4,901; India explicit 3,278; inferred 3,380; remote-unverified 13,660), of which 12,352 are `latest_collection` and 12,867 `provider_retained` (stored as Unknown). 171 boards have a possible YC name match; 6 board codes exist on more than one ATS.
- **Verification:** 221 automated tests (59 new in `tests/test_feashliaa.py`, covering the 28 required areas) all pass; `pip check` clean; spot check of 15 India/Bengaluru latest-collection jobs from Greenhouse/Lever/Ashby: all pages returned HTTP 200 and 12 contained the title words (3 redirected to employer career sites). An accessible page is not a confirmed open vacancy.
- **Changes and reasons:** D-10 to D-14. The prefilter keywords are a temporary volume filter seeded from the guide's example role names; they are not the final search profile.
- **Real import (approved, 2026-10-20):** verified backup `backups/discovery_pre-feashliaa_*.db`, `init-db` to schema version 4, then `import-feashliaa --apply` (run `f6d16c71…`, 18 s): 3,872 board companies and 25,219 jobs created, 12,858 retained jobs set to Unknown (12,361 stay New), 0 changed. A second `--apply` of the same snapshot was skipped as already processed (a `skipped` source check, no data change). Verified in SQLite: integrity ok, 0 foreign-key violations, 10,183 companies (6,311 YC + 3,872 board codes), 25,219 job sightings with 25,219 distinct IDs, 3,872 company sightings, no job or company without a sighting, no Active/Closed/Not Seen events, 0 verdicts lost, all 6,311 YC companies and YC sightings byte-identical to the backup, 224 publishable companies unchanged, no posted dates set, provider first-seen kept separately from ours. A dry-run TEST sync shows 0 new Posts rows, 0 new Companies rows (224 Last Seen cells and 3 Runs rows pending).
- **Small corrections made during import:** the CLI label for a skipped apply was misleading and a skipped attempt was recorded with run result `partial`; both fixed (the one earlier skipped run keeps result `partial`). The freshness counts in the printed metrics differ by a few rows from the stored ones (12,352/12,867 vs 12,361/12,858) because 30 duplicate rows were replaced by their newer copy.
- **Limitations / pending:** any publishing of jobs to TEST is awaiting an approved rule; the Posts publishing rule is undecided (see Outstanding Issues); sightings are still not linked to the collection scope; Paylocity rows are ambiguous because the published data lost their job IDs.

### Day 7 - Integrated pipeline and the single Skill (Complete, commit `f9b07e1`; Milestone 1 passed)

- **Objective:** connect the working components into one manually started operation with one consolidated report.
- **Planned (section 10):** `run --sources yc,feashliaa --sheet test`, `sync`, `report --last`, one user-invoked Skill, Milestone 1.
- **Implemented so far (code, tests and offline/dry-run checks only; no real-data execution yet):**
  - `discovery/pipeline.py`: validates sources and schema/integrity, creates a parent `runs` row (`run_type = 'discovery'`), runs only the named sources in a fixed order (a crash or failure in one never stops the other), previews the TEST sync (applied only with `--apply-sync`), computes the overall status (success / partial / failed / dry-run; `skipped` and `resumed` are not failures), stores a summary on the parent, and renders the report.
  - **Run identity, no schema change (decision D-15):** the YC and Feashliaa adapters still write their own `import` run and source check, unchanged. The pipeline links each child by writing `parent_run_id` and `source` into the child run's JSON summary; consolidated numbers come only from those linked children. `--resume RUN_ID` re-runs an interrupted/failed parent: sources already finished under it are not repeated. A true foreign-key parent column was not needed.
  - CLI: `run --sources ... --target test [--dry-run] [--apply-sync] [--resume ID]`, `report --last | --run-id ID`. `--target test` is the only target (LIVE is rejected by the parser and the pipeline); the original text's `--sheet` was not used, keeping one convention shared with `sync`. Nothing is scheduled and nothing runs unless named.
  - `yc_companies.execute()` was extracted from the CLI so the pipeline reuses it; the CLI behaves as before.
  - `.claude/skills/run-discovery/SKILL.md`: project-local Skill with `disable-model-invocation: true` (checked against the current Claude Code skills documentation: location `.claude/skills/<name>/SKILL.md`, fields `name`, `description`, `disable-model-invocation`, `argument-hint`, `$ARGUMENTS`). It runs doctor, a dry-run, asks before the real run, then separately before the TEST sync, shows `report --last`, and stops.
- **Restrictions kept:** Feashliaa jobs are never published (the Posts gate from D-13 is unchanged; New = first seen by us; provider freshness is shown in the report); the 171 YC name matches remain review hints; no ranking or relevance rules (Day 12).
- **Verification so far:** 242 automated tests pass (21 new in `tests/test_pipeline.py` covering the 18 required areas); offline integration with fixed fixtures: run 1 created 3 YC + 3 board companies and 3 jobs with 2 linked child runs, two identical replays created no companies, jobs, sightings or status events (Feashliaa `skipped`, YC unchanged); live read-only dry-run on 2026-10-20: YC 6,311 records (0 new, 0 to refresh), Feashliaa snapshot `878186386dd56df5` already processed so `skipped`, TEST preview = 224 Last Seen cells + 3 Runs rows, 0 job rows; the real database file was byte-identical afterwards.
- **Approved real execution (Test C, 2026-10-20):** verified backup `backups/discovery_pre-day7-run_*.db` (schema 4, 10,183 companies, 25,219 jobs), then `run --sources yc,feashliaa --target test --apply-sync`: parent run `20d89541…`, overall SUCCESS in about 16 s. YC: 6,311 records, 0 new, 6,311 unchanged, 0 malformed or conflicts. Feashliaa: snapshot `878186386dd56df5` (published 2026-10-19 15:48 UTC) was already processed, so it was recorded as `skipped` and did not count as a failure; no upstream refresh had happened yet. The previously pending TEST changes were applied: 224 Last Seen cells and the Runs rows (the Runs tab then held 8 rows).
- **Repeat (Test D):** a second identical `run ... --apply-sync` (parent `f99b93d7…`, SUCCESS) left companies (10,183), jobs (25,219), sightings (35,402), status events (38,077: 25,219 New + 12,858 Unknown) and verdicts (0) exactly unchanged, with new source-check rows only (feashliaa skipped x3, YC success x5) and each parent linked to its own YC child, Feashliaa child and sync run. The sheet matches SQLite: 224 company rows, each once, all India-based YC, Posts 0, Shortlist 0, no Feashliaa company or job on the sheet; the 226 cells synced were the 224 Last Seen values (our own last-seen moves every run by design) and 2 Runs-status cells. Because the upstream feed had not refreshed, idempotency for a genuinely new Feashliaa snapshot is proven by the frozen-fixture tests, not by this live repeat.
- **Frozen-data acceptance replay (post-commit verification, on a COPY of the real database; the real database and both spreadsheets untouched):** the saved YC snapshot (publisher time 2026-10-20T02:41:43Z) and the saved Feashliaa snapshot `878186386dd56df5` were served from local files. The Feashliaa success check was deleted in the copy so the snapshot was truly re-processed rather than skipped: YC 6,311 unchanged, Feashliaa 1,436,282 rows scanned, 25,219 candidates, **0 created, 0 changed, 25,219 unchanged**; companies 10,183, jobs 25,219, sightings 35,402, status events 38,077 (25,219 New + 12,858 Unknown) and job states (12,361 New, 12,858 Unknown) were identical before and after. Three fixture verdicts/notes (a job `No` with a note, a note-only company, a `Yes` on a non-published company) were identical, including timestamps, after two further replays. `report --last` reproduces the stored run: parent `f99b93d7…`, YC SUCCESS, Feashliaa SKIPPED, TEST sync APPLIED with 226 cells, integrity ok. LIVE was checked read-only: four tabs, original 1,000-row sheets, no headers or data.
- **Defect found and fixed during the real run:** the sync listed the discovery run that was still `running`, so its Runs row said "running" until the next sync. The Runs tab now lists a run only when it has finished (test added). The final parent row of the latest run therefore appears at the following sync (after the repeat, one Runs row was still to be added).
- **Still restricted or deferred:** no Feashliaa job publishing (Day 12 rules); no ranking; no LIVE; no scheduler; the 171 YC name matches remain review hints; `Last Seen` for YC rows is rewritten at every sync.
- **Next stage:** Day 8, JobSpy evaluation, starting with the mandatory source audit and a Python 3.14 compatibility check before any install.

### Day 8 - JobSpy integration (Complete, commit `8b9bdf9`; final scope Indeed India, Naukri, LinkedIn)

> **Reading note (2026-10-20):** this section is a chronological record. Statements below about Glassdoor, "four" restricted sources, terminal-typed confirmation codes, "pending" approvals or "no adapter yet" were true when written and are superseded by decisions D-22 (chat approval) and D-23 (Glassdoor retired). Current state: the three-source summary at the end of this section.

- **Objective:** evaluate JobSpy as an independent mainstream India source (Naukri, Indeed India).
- **Planned (section 11):** install a tested, pinned version, test Naukri and Indeed one at a time with small Bengaluru searches, add an adapter only for permitted sources, keep failures from closing jobs; "if a source is inaccessible or not authorized for automated retrieval, keep it disabled and use manual import".
- **Audit result (`docs/source_audits/jobspy.md`, 2026-10-20):**
  - The library is healthy: v1.4.0 released 2026-10-19, HEAD `8dd83cdd`, MIT, requires Python >=3.10 and lists 3.14. A real install in a throw-away Python 3.14.3 venv outside the project succeeded and `pip check` was clean; a dry-run into our `.venv` shows 15 new packages (pandas 3.0.6, numpy 2.5.4, curl_cffi 0.16.3, pydantic 2.14.1 and others) and no change to existing ones. **The project `.venv` was not modified.**
  - Access path: Indeed is reached through `apis.indeed.com/graphql` with the Indeed mobile app's embedded API key; Naukri through its private `jobapi/v3/search` with a client-generated token (HTTP 406 "recaptcha required" is an expected outcome). Both use TLS impersonation and optional proxies; the README advertises proxy use "to bypass blocking".
  - **Indeed is not permitted:** Indeed's Terms of Service (updated 2026-09-29, D.21) forbid access other than through public interfaces and any bots, scrapers or crawlers, and `apis.indeed.com/robots.txt` is `Disallow: /`. **Naukri's permission is Unknown:** its robots.txt and terms returned HTTP 403 to our fetch, and a direct request failed on a TLS certificate error that was not bypassed.
  - Consequently **no live JobSpy request has been sent to either board** and no adapter or experiment results exist (decision D-17). Unique-versus-Feashliaa overlap is unmeasured.
- **Recommendation recorded:** keep Indeed-via-JobSpy permanently disabled; keep Naukri disabled unless permission is established and you explicitly accept the risk; prefer official ATS APIs (Greenhouse, Lever, Ashby), manual link import (Day 12) and the Indeed MCP connector (backlog) as compliant routes. Installing JobSpy (15 packages) needs approval under `CLAUDE.md`.
- **Operator authorization (2026-10-20):** the operator confirmed authorization from Indeed and Naukri and approved installing and testing JobSpy; the audit's section 11 records what that changes and what remains: Indeed's path uses an API key embedded in the library rather than a credential issued to the operator, and Naukri's private API needs a provider-issued token or challenge process if one exists. Not verifiable by us.
- **Done after authorization:** `python-jobspy==1.4.0` (+14 dependencies) installed in the project `.venv`; `pip check` clean; `requirements.txt` re-pinned; 243 tests still pass; the earlier Naukri TLS error was diagnosed (missing GlobalSign Root R46 in the Windows store at that moment; verification stayed on; `certifi` fallback via `ca_cert` available).
- **Permission note:** the first live probe was stopped by the assistant environment's permission layer before any request left the machine; nothing was worked around. After the operator added a permission rule the probes ran.
- **Live results (2026-10-20, section 12 of the audit; no database, TEST or LIVE writes):** 12 small searches (6 queries x Indeed India and Naukri Bengaluru, 15 results each), all succeeded with no block, CAPTCHA, rate limit or certificate error; each took about 1 s. Indeed: 90 rows, descriptions on all, no experience field, median posting age 46 days. Naukri: 90 rows, experience range on all, descriptions only with an extra request per job (worked), median posting age 6 days. Direct employer URLs: Indeed 88/90, Naukri 50/90; 8 and 2 of them equal Feashliaa job URLs. About 76 of 84 (Indeed) and 87 of 89 (Naukri) title-relevant postings have no URL match in Feashliaa, so the boards mostly add employer career-site jobs; 8 sampled employer pages all returned HTTP 200.
- **Recommendation:** both boards are usable as supplementary sources behind the existing pipeline, disabled by default until an adapter and tests exist; Naukri first (fresher, experience data), Indeed second (descriptions, direct URLs, but older postings). Open items: a provider-issued credential for each board if one exists (the Indeed path uses a key embedded in the library), volume and rate limits beyond these small tests (the library has no retry/backoff, so the adapter must add bounded delays and 429 handling), Indeed's missing city and experience, and stale postings.
- **Interface decision (design only, `docs/source_audits/jobspy.md` sections 13-15):**
  - **Python API chosen**, through one thin module (`discovery/adapters/jobspy_source.py`, one board per `scrape_jobs` call), because it returns structured per-board `errors`/`warnings` and typed fields. The CLI hides partial failures (exit code 1 only when every board fails; unavailable fields are omitted) and the MCP server (extra `mcp` dependency and process, interactive `search_id` design, previews only) gives nothing for repeatable ingestion; **MCP not installed**. The Indeed/Naukri/LinkedIn scrapers are not rewritten.
  - Indeed and Naukri stay independent sources (`jobspy_indeed`, `jobspy_naukri`), each disabled by default in `config/sources.json` and runnable only when named in `--sources` and enabled; each search has its own source check with `complete = false` (a query is a window, not a full listing) and `config_ref = "no-closure-evidence"`.
  - Provenance kept distinct: board posting date -> `sightings.provider_first_seen_at` (not `jobs.posted_at`), our first/last seen untouched, original board URL as the sighting URL, `job_url_direct` as strong cross-source evidence (match to an existing sighting URL, never by title). New JobSpy jobs belong to provisional companies with `sheet_publish = false`, so nothing reaches Google Sheets until relevance rules exist (existing Posts gate).
  - Network proposal (decision D-18): proxy or VPN you control, configured in `.env` (`JOBSPY_PROXIES`, optional `JOBSPY_CA_CERT`), fail closed if it is unreachable, a visible `JOBSPY_ALLOW_DIRECT=true` choice for direct runs, never claim anonymity, no resume, cookies or credentials are collected or sent.
  - LinkedIn and Glassdoor are candidates for separate small validation tests only; the operator's authorization named Indeed and Naukri, so explicit confirmation for these two is required before any request.
- **Network privacy decision (audit section 16, decision D-19):** the operator's Surfshark Windows app (OS-level VPN) is the default network layer for live job-board requests instead of a separate JobSpy proxy. Verified on 2026-10-20 (read-only, no job board contacted, no IP address printed or stored): the VPN is **not connected yet** (only the Surfshark installer was running; only Wi-Fi is up), so Python's egress is currently a consumer ISP network and DNS uses the ISP's resolvers; there are no proxy variables, WinHTTP proxy or PAC script that would interfere; TLS verification untouched. New helper `scripts/check_vpn_egress.py` prints only the egress class and country and exits 0 only when the egress no longer looks like a consumer ISP; it must pass before any live board run. The operator still has to connect, enable the Kill Switch (Strict recommended), keep `python.exe`/PowerShell/`curl.exe` out of Bypasser, and compare Surfshark's DNS leak test with the VPN off and on. Not anonymity: the VPN operator and the boards still see traffic metadata; source limits, manual runs and access permissions are unchanged.
- **VPN re-check after connection (2026-10-20): verified VPN-routed.** Python 3.14.3 egress class hosting/vpn-like in SG (heuristic), same as Windows `curl.exe`; no proxy settings; `SurfsharkWireGuard` up with split IPv4 default routes and a lower interface metric than Wi-Fi; the VPN adapter's DNS servers differ from the ISP's and a resolver-echo query shows DNS leaving through a hosting/VPN network in SG (no DNS leak observed); no IPv6 default route or IPv6 path observed. Kill Switch and Bypasser are operator-confirmed and cannot be read from outside the app. The check must be re-run at the start of each live session.
- **Implementation (restricted-source controls, offline; see "Restricted Job Board Execution Policy" above):**
  - New files: `config/restricted_sources.json` (policy), `discovery/restricted_policy.py` (strict policy loader, plans, one-time interactive authorization), `discovery/network_guard.py` (VPN preflight; `scripts/check_vpn_egress.py` now runs it), `discovery/adapters/jobspy_source.py` (Python-API integration: transport-level request budget, collect snapshot, preview, import), `tests/test_jobspy.py` (32 tests). CLI additions: `restricted-policy`, `restricted-collect`, `restricted-import`; `run`/`run_pipeline` reject restricted names; the Skill and `CLAUDE.md` carry the permanent rules.
  - Design as approved: Indeed and Naukri are independent sources (`jobspy_indeed`, `jobspy_naukri`) with their own searches, request metrics, source checks and sightings; collection (Phase A) and import (Phase B) are separate approvals; LinkedIn and Glassdoor are blocked in the policy and have no live code path. Direct employer URLs are used as strong cross-source evidence (an existing sighting URL attaches the new sighting to that job; never title similarity); new companies are provisional with `sheet_publish = false`; board posting dates go to `provider_first_seen_at`; no schema change was needed.
  - Enforcement findings: JobSpy has no retries and no request limit of its own; every Indeed/Naukri request passes through `RotatingProxySession.request`, which the adapter wraps (verified offline against JobSpy's real `RequestsRotating` class); `results_wanted=10` does not limit the request: Indeed asks for up to 100 jobs and Naukri 20 per page, so only the stored cap is local.
  - Tests: 274 pass (243 before + 31 new in `tests/test_jobspy.py` covering the 25 required areas plus transport-budget, preflight, import and snapshot checks). All offline: sockets blocked, JobSpy replaced by fakes, temporary databases.
  - Not done and not allowed yet: no real restricted-source request has been made since the earlier small probes; the initial controlled test (two searches x 10 results per board) awaits the operator's interactive approval; `enabled` is still false for all four; no import has happened.
- **LinkedIn and Glassdoor preparation phase (2026-10-20; offline only, no request sent to either provider):**
  - Indeed and Naukri were not retested and their implementations and approved scopes are unchanged (a test asserts their policy values). The earlier small live probes (12 searches) remain the only Indeed/Naukri live evidence; the controlled test under the new controls is still pending the operator.
  - Audit: `docs/source_audits/jobspy.md` section 18. LinkedIn = one guest-endpoint GET per page (10 cards/page), `requests` transport, free-text location, no country parameter. Glassdoor = at least 2 requests (location autocomplete GET + GraphQL POST), curl_cffi transport that impersonates Chrome's TLS fingerprint, India domain `glassdoor.co.in`.
  - Authorization: **not established for either.** LinkedIn's User Agreement (8.2) and robots.txt prohibit automated access without express permission and say nothing about VPN egress; Glassdoor's robots.txt disallows `/graph` and `/autocomplete` and its Terms page could not be read (HTTP 403). No assumption that Surfshark egress is compliant.
  - Policy (`config/restricted_sources.json`): LinkedIn proposal (term `business analyst`, location `Bengaluru, India`, 1 search, 5 results, budget 1) and Glassdoor proposal (term `business analyst`, location `Bengaluru, Karnataka`, country India, 1 search, 5 results, budget 2 = 2 base requests); both `enabled=false`, `provider_authorization="not_established"`, `authorization_covers_vpn_egress=false`, no enrichment, pagination, retries, import or Sheets publication, 60 s limit. New fields on every source: `provider_authorization`, `authorization_covers_vpn_egress`, `authorization_reference`, `base_requests_per_search`.
  - Code: the loader refuses to enable a source unless authorization is `operator_confirmed` and covers VPN egress; LinkedIn and Glassdoor must each be authorized alone; budget must cover searches x base requests; `check_transport_enforceable()` rejects a scraper whose transport the budget cannot reliably cap (checked before the authorization is consumed); wire-level redirect hops count against the budget; 401/999/authwall/Cloudflare signals (in errors or warnings) stop the source like 403/429/CAPTCHA; the live call re-checks provider authorization.
  - Tests: 317 pass (274 before + 43 new in `tests/test_jobspy.py`: mocked LinkedIn/Glassdoor success, Bengaluru location, Glassdoor India country, missing descriptions/dates, stable IDs and duplicates, changed schemas, HTTP errors/blocks, transport budget per provider, redirect hops, runtime cap, unenforceable transport, failed VPN preflight, separate one-time authorization, direct-call refusal, Indeed/Naukri unchanged, no SQLite/Sheets writes). `pip check` clean.
  - Live test plans (Test A LinkedIn, Test B Glassdoor) are prepared in the final report of this phase; neither may start until the operator records provider authorization (including VPN egress), flips that source's policy entry, and types the one-time confirmation themselves. Requests sent to LinkedIn or Glassdoor so far: **0**.
- **Pre-live security audit for LinkedIn and Glassdoor (2026-10-20; offline; `docs/source_audits/jobspy.md` section 19):** operator stated authorization covers the access methods, VPN egress, endpoints and Glassdoor's transport (no reference number). Verified from code: no personal credentials/cookies/browser data used; request budgets enforced at the transport (LinkedIn 1, Glassdoor 2, redirect hops included); snapshots temporary, git-ignored, user-only ACL, 14 fields, production DB hash unchanged. One gap found and fixed: `requests` could honour ambient `.netrc`/proxy/CA variables; the budget wrapper now disables `trust_env` and refuses credential headers, auth, cookies and proxies. 336 tests pass. Live tests have NOT been run; they await the operator's explicit go-ahead and individual terminal confirmations.
- **Chat-approved workflow (2026-10-20, decision D-22; offline only):** added `restricted-plan`, `--approve-plan`, `--enable-for-this-run`, `--attest-provider-authorization` and `restricted_policy.authorize_from_chat`; 353 tests pass (17 new covering no run without approval, single-plan approval, replay, expiry, changed terms/geography/limits, LinkedIn cannot authorize Glassdoor, blocks and budgets unchanged, VPN failure after approval, no SQLite/Sheets writes, normal discovery excludes all four, untrusted text cannot approve, no terminal needed). The scratchpad launcher is no longer needed. No provider contacted.
- **Permission-layer diagnosis (2026-10-20; read-only, no provider contacted):** `restricted-collect --approve-plan ...` was denied twice with reason "Third-Party Attack" by Claude Code's **auto-mode classifier**, which evaluates each shell command before it runs and is separate from this application's chat approval. Findings: the command goes through the Bash tool (Git Bash); `~/.claude/settings.json` has no permission rules and only unrelated hooks (a PreToolUse hook on Write/Edit only; none on Bash); the project has no `.claude/settings*.json`; `~/.claude.json` lists no allowed tools for this project; no managed-settings directory exists at `C:\ProgramData\ClaudeCode`; so no deny rule, hook or organization policy is involved. The operator's earlier permission rule is not present in any settings file readable from here, so either it was not saved or it lives in the desktop app's own store. `/permissions` is a terminal dialog and is not available in this desktop session. Consequence: operator chat approval does not override an auto-mode denial; a native one-time prompt (permission mode that asks) or a narrow project allow rule is required in addition to the application's chat approval. The application's safeguards were not changed.
- **Controlled live tests of LinkedIn and Glassdoor (2026-10-20; chat-approved, one plan each, Surfshark preflight 8/8 before each; nothing written to SQLite, Sheets TEST or LIVE):**
  - **LinkedIn: SUCCESS.** `business analyst` / `Bengaluru, India` / 5 requested: 5 unique records, **1 HTTP request**, 3.6 s, no error or warning. All 5 had title, company, location (Bengaluru/Bangalore Urban, Karnataka), a posting date (1-13 October 2026; the freshest was 7 days old), a job URL `linkedin.com/jobs/view/<id>` and a LinkedIn company URL. Missing on all 5: direct employer URL, experience range, description (enrichment is off). Matches against existing jobs by URL: 0 (so 0 confirmed duplicates); name hints against YC/Feashliaa companies: 0. A URL non-match is not proof of uniqueness. Employers were large IT-services/consulting firms (TCS, EY, Consilio, Virtusa, Cognizant), which are not the early-stage/product companies the engine targets, so incremental value from this one query is low; its strength is freshness and breadth, its weakness is no employer URLs for dedupe. Snapshot kept (git-ignored), not imported.
  - **Glassdoor: BLOCKED.** `business analyst` / `Bengaluru, Karnataka` / India / 5 requested: **HTTP 403 ("blocked: use a proxy") on the first request** (the location lookup) after 1 request, 2.8 s, 0 records. The adapter stopped the source with no retry, no second request and no workaround; proxies, rotation and fingerprint changes are forbidden by policy. Glassdoor is therefore not usable through JobSpy under our rules at present; the next legitimate step is an official Glassdoor data/partner route or manual import, not a technical workaround. The access also contradicts the earlier robots.txt finding for `/autocomplete`.
  - Both sources returned to `enabled: false` (the enabling was in memory for the run only); both plan records were consumed; production DB hash unchanged; 353 tests pass; `pip check` clean; doctor 18/18; no scheduler or background task exists.
  - **Defect found and fixed:** the test suite had been appending fake runs to the real audit log `logs/restricted_runs.jsonl` (about 100 lines since Day 8 implementation). Tests now redirect the audit log to a temporary file. The contaminated file is kept as `logs/restricted_runs.test-contaminated.jsonl`; the live log now holds only the two real executions (LinkedIn 5 rows/1 request, Glassdoor blocked/1 request). The earlier real Indeed/Naukri probe entries pre-date the audit log.
  - **Recommendations:** LinkedIn - keep as an optional supplementary source behind explicit per-run approval; do not import yet (import needs its own approval and a backup; dedupe is weak without employer URLs). Glassdoor - do not run again; pursue official access.
- **Glassdoor 403 diagnosis (2026-10-20; offline; `docs/source_audits/jobspy.md` section 22):** the 403 came from the first request, `GET www.glassdoor.co.in/autocomplete/location` (location lookup); the search request was never sent. The URL, encoding, headers and country domain are correct (offline reproduction with the real scraper and a faked transport); installed JobSpy 1.4.0 already contains the upstream fixes #384 and #394 (upstream verified US and UK, not India); #347 was never merged. Root cause between provider-side WAF/IP-reputation/India-specific denial is INCONCLUSIVE because the first run recorded no response headers. No evidence-supported scraper fix exists, so none was made; user-agent, fingerprint, proxy and retry changes are excluded by policy. Added sanitized per-request diagnostics (stage, host, path, status, header names) to snapshots and the console, with 4 regression tests; 357 tests pass. **Glassdoor stays deferred and disabled; no retest is prepared** unless Glassdoor provides an authorized access path (official interface, provider-issued credential or allow-listed endpoint/address). Alternatives: ATS sources, manual import, LinkedIn.
- **Glassdoor access options (2026-10-20; research only; `docs/source_audits/jobspy.md` section 23):** JobSpy 1.4.0 is the newest release and HEAD, with no newer Glassdoor fix and no open issue for this 403. Public sources report that Glassdoor's job API and partner programme are closed to new developers (current access enterprise/sales-led; one vendor saw HTTP 410 from the legacy endpoint) and that Glassdoor has merged into Indeed (unverified). No credential, registration or allow-list route that would make the private autocomplete/graph endpoints sanctioned was found. Decision: no code or dependency change, no retest, Glassdoor stays deferred and disabled. What the operator can obtain: (1) written confirmation from Glassdoor, or via the Indeed relationship, of an approved route for job listings (API/feed credential, endpoint, or allow-listed static address); (2) optionally approval of a licensed third-party feed (new service, needs explicit approval); (3) meanwhile use LinkedIn, ATS boards and manual links (Day 12). A new live attempt is justified only after one of these exists and is verified offline.
- **Glassdoor removed; final JobSpy scope Indeed, Naukri, LinkedIn (2026-10-20, decision D-23; cleanup only, no live request):** removed Glassdoor from `config/restricted_sources.json`, `RESTRICTED_SOURCES`, solo-authorization list, the adapter's source ids/labels/country parameter, the 2-request base-budget ceiling (now 1) and Glassdoor-only tests; `glassdoor` and `jobspy_glassdoor` are rejected with an "unsupported source" error by the restricted commands and by the normal pipeline (`RETIRED` tuples are the only remaining code references). Kept: the shared transport budget, TLS-session budget test, credential guard, sanitized request trace, chat approval, VPN preflight and all other safeguards. Historical Glassdoor entries above and in the audit are preserved unchanged and are now superseded by this decision (retired source, not part of the active engine).
- **Day 8 close-out (2026-10-20):**
  - **Active JobSpy sources:** `jobspy_indeed`, `jobspy_naukri`, `jobspy_linkedin`, each disabled by default in `config/restricted_sources.json`. Glassdoor is retired and not executable (`glassdoor` / `jobspy_glassdoor` are rejected as unsupported).
  - **Validation:** 353 offline tests, `pip check`, local doctor and the read-only Sheets doctor pass. Live evidence: Indeed and Naukri returned listings in the earlier small probes (12 searches, before the restricted-source controls existed); LinkedIn returned 5 jobs with 1 request through the final chat-approved path. The controlled Indeed/Naukri re-run through the final path has not been performed.
  - **Approval-gated and not done:** importing the LinkedIn snapshot (`data/restricted/20261020092735_linkedin.json`) into SQLite; publishing any restricted-source job to Sheets (blocked by policy); any scheduled or automatic restricted run (does not exist).
  - **Known limits:** LinkedIn rows carry no employer URL, so cross-source dedupe is weak; "requested results" is not a request limit (enforced at the transport budget); chat approval cannot be authenticated by Python (D-22); the audit log `logs/restricted_runs.test-contaminated.jsonl` preserves earlier test pollution (git-ignored).
  - **Next:** Day 9 (not started).

### Current system snapshot (end of Day 8, 2026-10-20, commit `8b9bdf9`)

- **Runtime:** Python 3.14.3, `.venv`; python-jobspy 1.4.0 pinned in `requirements.txt`; SQLite schema version **4** (unchanged by Day 8); 353 automated tests passing.
- **Commands (added in Day 8):** `restricted-policy` (read-only), `restricted-plan`, `restricted-collect` (with `--approve-plan CODE`, `--enable-for-this-run`, `--attest-provider-authorization`), `restricted-import`; `scripts/check_vpn_egress.py`. Normal `run` rejects all restricted sources.
- **Data:** unchanged since Day 7 (10,183 companies, 25,219 jobs, 0 verdicts); no restricted-source job is in SQLite. TEST sheet unchanged by Day 8; LIVE untouched and blocked in code.
- **Local-only, git-ignored:** `data/discovery.db`, `backups/`, `data/restricted/` snapshots (LinkedIn, retired-source Glassdoor 403 record), `logs/`, `.env`, `.secrets/`.
- **Next planned step:** Day 9 (YC job pages); not started.

### Current system snapshot (verified at the start of Day 7, 2026-10-20, HEAD `126a930` = origin/main; historical)

- **Runtime:** Python 3.14.3, `.venv`; SQLite schema version **4** at `data/discovery.db` (git-ignored; integrity ok, no foreign-key violations); backups in `backups/` (git-ignored); 221 automated tests passing.
- **Commands:** `doctor`, `init-db`, `sync --target test [--dry-run]`, `import-yc --dry-run | --apply`, `import-feashliaa --dry-run | --apply`; `scripts/doctor_sheets.py` (read-only Google check). Day 7 adds `run` and `report` plus the Skill `/run-discovery`. No `import` (manual link import) or `backup` command yet.
- **CLI conventions actually used (they supersede the original Day 7 examples, which are kept above unchanged for traceability):** the spreadsheet is selected with `--target test` (the original text used `--sheet test`); only `test` is accepted and LIVE is blocked in code; source imports are `import-yc` and `import-feashliaa` with `--dry-run` / `--apply`.
- **Data:** 10,183 companies (6,311 YC + 3,872 Feashliaa provisional board-code companies), 25,219 Feashliaa jobs (12,361 `New`, 12,858 `Unknown` = provider-retained), 0 verdicts. Source runs recorded: three YC import runs, one Feashliaa import, one skipped Feashliaa attempt, one sync. TEST sheet: Companies 224 rows (YC India-based), Posts 0, Runs 2, Shortlist 0; a fresh dry-run shows only 224 Last Seen cells and 3 Runs rows pending, no Feashliaa job or company would be published.
- **After Day 7:** 243 tests; real runs `20d89541…` and `f99b93d7…` recorded; TEST sheet: Companies 224, Posts 0, Runs 10 (one more row pending), Shortlist 0.
- **Next planned step:** Day 8, JobSpy evaluation (source audit and Python 3.14 compatibility check first).

---

## Mandatory Source Repository and API Review Before Every New Integration

This is a required engineering step for **Day 6 onward** and for every future source (Feashliaa, JobSpy, YC job pages, investor boards, MCP/API candidates, and all others). No adapter code is written until the audit exists and has been reviewed.

Before implementing a source adapter, inspect the actual upstream repository, published dataset, source code or official API documentation, and for each source:

1. Confirm the official repository or provider and the exact URL.
2. Review the README, installation instructions and architecture.
3. Check recent commits, releases, open issues and maintenance activity.
4. Identify which code can be reused versus which live datasets can be consumed.
5. Inspect real sample responses or data files.
6. Verify field names, formats, identifiers, pagination and missing values.
7. Check source timestamps, freshness and historical retention behavior.
8. Identify access requirements, rate limits and potential usage restrictions.
9. Review whether the source is relevant to Bengaluru, India and India-eligible remote opportunities.
10. Identify overlapping coverage with our existing discovery sources.
11. Evaluate failure modes, incomplete data and potential identity conflicts.
12. Choose the simplest reliable integration: published feed, official API or adapted reader code.
13. Create an integration plan before implementing the adapter.
14. Create tests using representative fixtures.
15. Perform a dry run before any production database update.
16. Verify repeated runs do not create duplicates.
17. Record observed behavior, source version and important design decisions.

Findings go in `docs/source_audits/<source_name>.md` and must include: inspection date, source URLs, relevant commit/version, data schema, update behavior, integration choice, risks and test findings. `docs/source_audits/yc_oss.md` is the reference example.

Rules: if the source behaves substantially differently from this guide, explain the difference and obtain approval before changing the architecture. Do not assume a source is current because its GitHub repository exists. Do not add a source merely because it advertises millions of jobs; measure its contribution to our actual target opportunities.

---

## Outstanding Issues and Deferred Improvements

Legend: **KL** known limitation · **FI** future improvement · **REQ** requirement for a later implementation step · **BLOCKER** actually blocks progress. No item below is complete, and none is currently a BLOCKER.

| Item | Type | Detail / where it belongs |
|---|---|---|
| Company domain verification and manual identity reconciliation | KL + REQ | YC companies are `provisional`; `set_company_domain` raises `IdentityConflict` when a verified owner exists. A reviewed merge/reconciliation tool is needed before multi-source company overlap (Days 9-10). |
| Source sightings not linked to the collection scope | KL | `source_checks.scope` exists but sightings do not record which scope produced them; closure evidence is checked by source only. Link before building any closure/Not Seen logic. |
| Conflicting information from multiple job sources | KL + REQ | A job's fields are updated by whichever source reports a change last; arbitration rules are needed (Day 11). |
| Full job lifecycle / closure policy | REQ | Only the safety mechanism exists (Not Seen/Closed need a successful complete check). The policy itself, and moving jobs out of `Changed`, are undecided (Day 11). |
| Intentionally clearing verdicts or notes from Sheets | FI | Blank cells restore stored values. Python can clear explicitly. A sheet-side mechanism (e.g. a CLEAR token) needs an approved design. |
| Google Sheets concurrent-edit race | KL | No atomic compare-and-swap; documented in `sheets_sync.py`; do not edit during a sync. |
| LinkedIn snapshot import and cross-source dedupe | REQ | LinkedIn rows have no employer URL, so strong dedupe evidence is absent; import is separately approval-gated (backup first). Decide in Day 11/12 how restricted-source jobs are matched and when they may be published. |
| Controlled Indeed/Naukri re-run through the chat-approved path | REQ | Earlier probes predate the final controls; one explicitly approved plan per source would confirm the final path. |
| Restricted-source approval provenance | KL | Python cannot authenticate a chat message (D-22); safeguards rest on the Claude Code workflow and tool-permission prompts. |
| LIVE spreadsheet | REQ | Blocked in code; enabling needs the Day 15 checklist plus separate explicit authorization. |
| Review-sheet size for YC | FI | 224 India-based rows include 43 Inactive and 20 Acquired companies. Narrowing (for example Active/Public) is the operator's decision; not applied. |
| International companies needing verified India-eligible job evidence | REQ | `potentially_relevant` (1,124) and `no_india_evidence_yet` (3,006) stay in SQLite; their jobs must be judged on job-level India evidence once job sources exist. Company geography is never a permanent exclusion. |
| YC job-page discovery, including companies marked not hiring | REQ (Day 9) | Not started. `isHiring` must not decide which companies are examined. |
| Feashliaa, JobSpy, investor-board integrations | REQ (Days 6, 8, 10) | Not started; each needs the mandatory source audit first. JobSpy and other packages are untested on Python 3.14. |
| Search profile, ranking, Shortlist, filters, manual import, benchmark, `/run-discovery` Skill, README, `pyproject.toml`, backup command | REQ (Days 7, 12-16) | Not started. PyYAML would be added with the profile (needs approval). |
| MCP / external job API evaluation backlog | FI | Documented in the earlier appendix; nothing installed or validated. |
| YC source dependency risk | KL | Unofficial dataset relying on a key scraped from YC's website and a daily GitHub Action. |
| Provider-retained jobs must stay distinguishable | REQ (later steps) | Any view, sheet column, report or ranking that shows Feashliaa jobs must show provider freshness (`latest_collection` vs `provider_retained`) so retained rows are never mistaken for currently observed ones. 12,858 of the 25,219 imported jobs are retained. |
| Publishing Feashliaa jobs to the review sheet | REQ (needs approval) | No Feashliaa jobs reach TEST until more selective role and relevance rules are approved; a 25,219-job import must not flood the sheet (blocked by default, see D-13). Proposed starting rule (not applied): Bengaluru/India-explicit AND latest collection = 3,191 jobs from 606 boards (1,751 on Greenhouse/Lever/Ashby). No ranking weights invented. |
| Feashliaa freshness rule is inferred | KL | `latest_collection` = `scraped_at` within 6 h before the manifest time; observed run window about 1.5 h. Re-check on every audit. |
| Feashliaa board-code company names | KL | Company names are ATS board codes, not legal names; Paylocity rows lack job IDs. Reconciliation with YC companies is manual: 171 board-code companies have a normalized-name match to a YC company (`facts.feashliaa.possible_yc_matches`), and **no merge is allowed without strong identity evidence** (for example a verified shared domain or an ATS-confirmed link) reviewed by a human; a name match alone is only a hint. |
| Feed URL may move again | KL | The owner already changed once; the base URL is a constant in the adapter. |
| TEST sheet one sync behind | KL (trivial) | Pending and not yet applied: 224 Last Seen cells (after the YC label refresh) and 3 Runs rows (YC label refresh, Feashliaa import, skipped Feashliaa attempt). Needs its own safe `sync --target test` (dry-run first, then approval). Nothing else differs and no Feashliaa job or company would be added. |

---

## Documentation Update Rule

At the end of every implementation day or major milestone, **update this guide before committing and pushing.** The update must capture:

1. What was completed.
2. What was tested.
3. What changed from the original plan.
4. Why the change was necessary.
5. Any remaining technical debt or unresolved decision.
6. New or changed source assumptions.
7. The next planned step.

Keep the original architecture and decisions traceable: append to the Status table, add a per-day record and decision-log rows, and update "Outstanding Issues". Never silently change previously approved objectives or rewrite the original plan text. Day records must be based on actual files, Git history and test results.
