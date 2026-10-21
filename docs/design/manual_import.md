# Manual job import: design and test plan (Day 12, checkpoint 1)

Status: **implemented in Day 12 (final phase), not applied to production.** `discovery/imports.py`, command `import-manual`
(dry run by default), 27 tests, fictional frozen fixture `tests/fixtures/manual_import/`. `--apply` needs schema version 7
(migration 7, `manual_inputs`), which is NOT applied to production and needs the operator's approval. Decisions taken: migration
7 is justified (jobs have no description column and the supplied text must live in the backed-up database with its provenance);
URL-only rows are valid only when the URL attaches to exactly one existing job; manual jobs are held from the normal publication
gate and are published only if shortlisted (hand-imported jobs sort first among new discoveries); availability of a manual job
is always `unknown`. The text below is the original design and is kept for traceability.

## 1. What it must do

Let the operator add jobs the automated sources did not find, from material the operator already has permission to use:

1. **Job URLs** (a link the operator found, for example while signed in elsewhere) with optional hints.
2. **CSV rows** (`imports/links.csv`, the template from the implementation guide).
3. **Saved job-description text** (plain text the operator pasted or saved).
4. **Permitted exports** (a CSV/JSON a site lets the operator download for their own use).

Hard limits, inherited from `CLAUDE.md`:

* The importer **never opens a URL**. It reads local files only. No network, no cookies, no browser session, no
  login, no authenticated page, no proxy. A signed-in page can only enter as text the operator saved.
* It reads **only** the files named on the command line, and refuses any path outside the project `imports/` folder
  (no career-history files, no Brain, no `.env`, no `.secrets`).
* User-supplied text is **recorded as user-supplied and unverified**. It is never presented as employer-confirmed.
* A manual job is **not published to Sheets** until a publication rule is approved (same gate as other new sources).
* A manual source can **never be closure evidence**: it is registered with `config_ref = no-closure-evidence`.

## 2. Input formats

`imports/links.csv` (UTF-8, header required):

```csv
url,company_hint,job_title_hint,location_hint,experience_hint,source,notes
https://example.com/role-123,Example Co,AI Implementation Associate,Bengaluru,0-2 years,manual,Found while signed in
```

* `url` is required for a URL row; `company_hint`, `job_title_hint`, `location_hint`, `experience_hint`, `notes` are optional.
  `source` must be `manual` (the operator may add a short label in `notes`).
* A row with **no URL** is allowed only together with a description file (`description_file` column or
  `imports/descriptions/<name>.txt`) and both `company_hint` and `job_title_hint`.
* `imports/descriptions/*.txt`: plain text, size-capped (default 200 KB), UTF-8 with a clear error on other encodings.
* Exports: a mapping file `imports/export_mapping.yaml` names the columns that hold url/title/company/location for that
  export; anything not mapped is ignored. Unknown columns are never guessed.

## 3. Where it fits (smallest consistent extension)

| Concern | Reuse | New |
|---|---|---|
| Module | `discovery/imports.py` (the module the guide already plans) | one module, one CLI command `import-manual` |
| Company | `db.observe_company`; the domain is taken from the URL host **only if** `idkeys.host_class` says employer-controlled and the host is not a shared ATS; otherwise a provisional company from the hint (names never merge) | none |
| Job identity | `db.observe_job` with the manual source; `external_id = "manual\|" + key` where key is the scoped posting key (`idkeys.url_signature`) for a job-specific URL, else a hash of the normalised URL, else a hash of company+title+description | none |
| Attach to an existing job | same rule as board imports: only a **job-specific scoped key** that equals exactly one existing job, no known-location conflict, no negative decision; otherwise a separate job plus a review hint | one shared helper (lift `match_by_employer_url` into `identity`) |
| History | `observe_job` already records New / Changed events and refuses to repeat identical observations | none |
| Backup | `jobspy_source.verified_backup(label="pre-manual-import")` before `--apply` | none |
| Provenance of supplied text | a small additive table **`manual_inputs`** (`sighting_id`, `kind`, `sha256`, `text`, `user_supplied=1`, `verified=0`, `created_at`) | **migration 7 (additive; needs your approval)** |
| Ranking | description text feeds `filters.match_roles(description=...)` and experience extraction; explanations say "supplied by you on <date>, not independently verified" | evidence class `user_supplied` in the explanation only |

Alternative without a migration: keep the text in git-ignored `data/manual_imports/<sha256>.txt` and store the hash in the
sighting's `external_id`. It works but ties provenance to loose files; the table is cleaner.

## 4. Command behaviour

`python -m discovery import-manual --links imports/links.csv [--descriptions imports/descriptions] [--apply]`

* Dry run by default: parses, validates, resolves identities read-only and prints exactly what would be created,
  attached, changed or rejected. **Nothing is written** and nothing is fetched.
* `--apply` requires a verified, restore-tested backup first, then one transaction per row; a rejected row never stops
  the others, and the run records a `runs` row plus one `source_checks` row (`partial` if any row was rejected; never `complete`).
* Idempotent: importing the same file twice creates no new job, sighting or event.
* Human verdicts and notes are never read or written by the importer.

## 5. Test cases (written before implementation)

Parsing and validation
1. Valid row with all fields; valid minimal row (URL only).
2. Missing header, unknown column, extra whitespace, quoted commas and newlines in notes.
3. Row without URL and without description file is rejected with a line-numbered message; with description + hints is accepted.
4. Non-http(s) schemes (`file:`, `javascript:`, `ftp:`) and credentials in the URL (`user:pass@`) are rejected.
5. Duplicate rows inside one file import once.
6. File larger than the cap, binary file, wrong encoding: clear error, nothing imported.
7. Path outside `imports/`, a symlink out of it, `..` traversal, `.env`, `.secrets/`, a Brain path: refused before opening.

Safety (no network, no accounts)
8. The importer opens **no socket** (monkeypatch `socket`/`urllib` to fail) and uses no cookies or browser files.
9. A URL that looks authenticated (login or session parameters) is stored only as text; it is never requested.
10. Tokens or session parameters in a pasted URL are stripped from the stored comparison key but the raw URL is kept as supplied.

Identity and history
11. New URL, new company: provisional company, New job, one manual sighting, one status event.
12. Job-specific URL equal to exactly one existing job: manual sighting attaches; no new job; company untouched.
13. Same URL, generic careers page: separate job, review hint, never attached.
14. Same posting id on a different ATS tenant: not attached.
15. Matching posting but conflicting known location (Workday multi-location): separate, hint, not attached.
16. Company hint conflicts with the URL host's owner: employer host wins for the domain, hint kept as alias; no name-only merge.
17. Re-import identical file: no new rows, no events, `last_seen` refresh only.
18. Changed description text: one `Changed` event, hash updated, history kept.
19. Existing verdict and notes on the matched job are untouched.
20. A merged alias job: the manual row lands on the canonical job.

Lifecycle and publication
21. A manual source can never be closure evidence (database refuses Not Seen/Closed from it).
22. Manual jobs are held back from `load_publishable_job_ids` until a publication rule is approved.
23. `--apply` without a verified backup is refused; dry run writes nothing (file hash equal).
24. A rejected row yields a `partial` source check, never `complete`.

Ranking integration
25. A saved description raises role/responsibility evidence and is labelled user-supplied; with no description the job is ranked on title/location only.
26. A user-supplied "0-3 years" is shown as the supplied claim, not as verified employer data.
27. Missing location in a manual row is unknown (needs review), never approved.

## 6. Decisions needed before checkpoint 2

1. Approve additive **migration 7** (`manual_inputs`) or choose the file-store alternative.
2. Confirm the `links.csv` columns above (two columns were added to the guide's template: `location_hint`, `experience_hint`).
3. Confirm that manual jobs stay unpublished to Sheets until the publication rule is approved.
4. Confirm that exports are mapped explicitly (`export_mapping.yaml`) and never guessed.
