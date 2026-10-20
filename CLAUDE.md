# CLAUDE.md — A+B Job and Company Discovery Engine

Source of truth: `AB_Discovery_Engine_Implementation_Guide.md` (v1.0). Follow it one day/stage at a time, with tests and acceptance gates. Do not skip ahead.

## Objective
When the user manually starts a run, the system finds relevant **jobs (A)** and promising **companies (B)** from independent sources, links them, de-duplicates, remembers history, and puts roughly 20–30 worthwhile new/changed items into Google Sheets for human review (Yes / Maybe / No).

## Runtime and technology
- **Python 3.14.3** (project runtime; do not install or switch Python versions). Virtual environment: `.venv` — invoke `.\.venv\Scripts\python.exe` directly.
- **SQLite** (`data/discovery.db`, built-in `sqlite3`) is the system memory.
- **Google Sheets** (via `gspread`) is the review UI: tabs `Shortlist`, `Posts`, `Companies`, `Runs`. Use the TEST sheet during development; never write to LIVE until the guide's gates pass.
- Filtering and ranking: deterministic YAML rules + Python (`config/search_profile.yaml`, `config/ranking.yaml`, `config/sources.yaml`).
- **No new dependency or technology** (packages, runtimes, services, Node, Docker, etc.) without first explaining why and getting explicit user approval. Guide-listed package families are only candidates until approved at the relevant stage.
- If a dependency is incompatible with Python 3.14, report it — never change Python versions automatically.

## Architecture (target layout)
`discovery/` package (cli, pipeline, db, entities, dedupe, lifecycle, filters, scoring, sheets_sync, imports, backups, `adapters/`), plus `config/`, `tests/`, `data/`, `imports/`, `exports/`, `logs/`, `backups/`, and a single user-invoked Skill `.claude/skills/run-discovery/SKILL.md`. Python is the authority; Claude is only the operator. Eight SQLite tables: companies, jobs, sources, sightings, runs, source_checks, status_events, verdicts.

## Operating rules
- **Runs are manually initiated only.** No scheduler, background jobs, or automatic triggers.
- **No connection to the user's personal Brain** or career-history files; only the explicit `search_profile.yaml`.
- **No automated applications, outreach, recruiter messaging, scheduling, or email alerts.** No resume writing or interview prep.
- **No AI enrichment or AI-based ranking in v1** (no Gemini or other model calls).
- **No login automation or restricted-site scraping**, no proxy rotation or block evasion. Use public/permitted interfaces; otherwise use manual import.
- **Human verdicts and historical records are never overwritten or deleted.** Read `Your Verdict`/`Your Notes` from Sheets before any write; sync by immutable `record_id`, never row number; never delete/recreate Posts or Companies tabs; if reading verdicts fails, abort the sync. `status_events` are append-only.
- A failed, partial or empty source check must **never** cause Not Seen/Closed states. Prefer leaving a possible duplicate unmerged over a wrong merge.
- Secrets (`.env`, `.secrets/`, keys) stay local: never print, commit, or paste them.
- Don't touch the LIVE sheet before the guide's pre-LIVE checklist passes; source-parser fixes need tested confirmation before LIVE runs.
- Keep the project outside synced folders (OneDrive/Dropbox); never delete the SQLite DB to "start fresh".

## Source audits (mandatory)
Before implementing any new source adapter (Feashliaa, JobSpy, YC job pages, investor boards, MCP/API sources...), inspect the real upstream repository/dataset/API and write `docs/source_audits/<source_name>.md` first. Follow the 17-step checklist in the guide section "Mandatory Source Repository and API Review Before Every New Integration". If the source differs materially from the guide, explain it and get approval before changing the architecture. Do not assume a source is current because its repository exists, and measure contribution to our target opportunities, not advertised volume.

## Documentation rule
At the end of every implementation day or major milestone, update `AB_Discovery_Engine_Implementation_Guide.md` (Status and Decision Log, Outstanding Issues) BEFORE committing and pushing: what was completed and tested, what changed from the plan and why, remaining debt, new source assumptions, next step. Keep the original plan text and approved objectives intact and traceable. Geography labels are descriptive only: never treat company location as a permanent job-eligibility exclusion, and never treat "remote" as proof of India eligibility.

## Boundaries
Do not add custom web apps, hosting, cloud databases, or commercial infrastructure. Do not expand scope or redesign without approval — refer back to this file and the guide.
