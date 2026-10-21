# A+B Job and Company Discovery Engine - everyday guide (Windows)

You start every run by hand. The engine finds **jobs (A)** and **companies (B)**, ranks them with fixed rules you approved (no AI), remembers history in a local database, and puts a short list into Google Sheets for you to mark **Yes / Maybe / No**. It never applies to jobs, never contacts employers and never runs by itself.

Everything below uses commands that exist today. If you use Claude Code you rarely type them: the `/run-discovery` Skill does it and stops for your approval before anything is written.

## 1. Open the project

* Folder: `C:\Users\lohit\Projects\ab-discovery-engine` (keep it outside OneDrive/Dropbox).
* **Claude Code Desktop:** open the app, choose the *Code* tab, open that folder. Type `/run-discovery`.
* **Terminal instead:** open PowerShell in that folder and prefix every command below with `.\.venv\Scripts\python.exe -m discovery`, for example `.\.venv\Scripts\python.exe -m discovery doctor`. (Below it is written as `discovery <command>`.)

## 2. The routine

**Every time you want new jobs and companies:**

1. Open the project in Claude Code and type `/run-discovery existing` (stored data, no website contacted). Read the health and source-health lines.
2. Say whether you want a **new batch**. If you do not, you get the same current batch, re-checked. If you do, say "start the next review session".
3. Claude shows a read-only LIVE plan and **stops**. Read it. Reply "Approved, publish this plan" only if you accept it.
4. Review in the LIVE sheet and type Yes / Maybe / No (section 4).

Details:

1. Open the project and type **`/run-discovery existing`**. This uses the data already stored, contacts **no** website, and walks through: health check, source health, backups, the ranked shortlist for the TEST sheet.
2. Read the *source health* lines (section 6). Approve or decline each step in chat. Nothing is written without your clear yes.
3. When you want the LIVE sheet updated, say so. Claude shows a read-only plan (`discovery live-publish --plan-output exports/live_plan.json`), then **stops**. Only your new message approving *that plan* lets it make a backup and run the write.
4. Open the LIVE Google Sheet, review, type your verdicts (section 4).

To collect fresh data from the public YC and Feashliaa datasets, name them: `/run-discovery yc,feashliaa`. That contacts those two sources only. Investor boards, YC job pages and Indeed/Naukri/LinkedIn are never part of it (separate manual commands with their own approvals; Glassdoor is retired).

## 3. TEST and LIVE

* **TEST sheet** is the practice copy: `discovery sync --target test --with-shortlist` (add `--dry-run` to preview). `--target test` is the only target `sync` accepts.
* **LIVE sheet** is the real one. It is written **only** by `discovery live-publish`, in two steps, and never by `sync`:
  * Plan (read-only, safe any time): `discovery live-publish --plan-output exports/live_plan.json`
  * Write (needs all of): a plan identical to a fresh one, row limits respected (`config/live_publication.json`: Shortlist 25, Posts 30, Companies 30, Runs 2 rows in total), a verified backup made in the last 2 hours, and the flag `--authorized-first-live-trial`, which records **your** approval. Claude uses the flag only after you approve the displayed plan in a new message. Your Day 15 approval covered the first trial only; every later LIVE write needs your approval again. If a limit is reached, the plan says so and only you can approve a larger number.
* If the plan says a limit would be exceeded (the next new batch will need more than 30 Posts and 30 Companies), nothing is written. Preview exactly what is needed with `discovery live-limits --next-session` (read-only). Only you can approve larger numbers; then `discovery live-limits --set Posts=45 Companies=50 Runs=6 --operator-approved --note "approved <date>"` records them. Limits never rise on their own, never drop below the rows LIVE already holds, and old rows are never deleted or reset to make room.
* LIVE shows **only the shortlist**: Posts = the listed jobs, Companies = their companies plus up to five company-only picks, Runs = one row per LIVE publication. It never deletes a row and never copies verdicts into your cells.

## 4. Reading and using the sheets

| Tab | What it is |
|---|---|
| **Shortlist** | The current review batch (about 20 jobs plus up to 5 company-only discoveries) with score and the reasons. Rewritten by position: do not sort or type in it. |
| **Posts** | One row per listed job: title, company, link, where it was seen, score. **Your decision goes here.** |
| **Companies** | Companies of listed jobs plus company-only picks (B). A company is listed even if it has no open job. |
| **Runs** | One row per LIVE publication, saying it used stored data and whether any source needs attention. |

* **Enter Yes / Maybe / No in `Your Verdict`, and anything you like in `Your Notes`** (Posts and Companies tabs). Spelling must be Yes, Maybe or No; anything else is ignored with a warning.
* **LIVE is the one place for decisions.** While a single database store still serves both sheets, TEST is for rehearsing publication only: a verdict or note typed in TEST is **ignored with a warning** and saved nowhere, and nothing you decide in LIVE is ever written into TEST. Older decisions from before LIVE stay in the database (a job you rejected earlier stays rejected) and are **never copied into your blank LIVE cells**. If you type something in LIVE that differs from an older decision, LIVE wins and the sync report says it "replaces earlier review memory". Changing a decision later means typing the new value in LIVE.
* **Never edit** any other column, the `record_id` column, or the header row; never delete or rename the four tabs. You may sort or filter Posts and Companies (rows are matched by `record_id`, not position). A blank cell never erases a saved verdict; to change one, type another value.
* Your verdicts reach the database the next time a sync runs. A job you mark **No** is dropped from the current shortlist and is not proposed again.
* A score is a transparent rule result from `config/ranking.yaml`, not a prediction. "Held for review" means a rule is unsure.
* **A listed job is not necessarily open.** Explanations say "observed on source, not employer-verified". A job board or dataset listing is an observation; only an employer's own page could confirm it is open, and the engine does not claim that.

## 5. Review sessions

* A **session** is one batch of listed records. A normal sync **continues the current session**: same records, re-checked, rejected ones dropped, nothing new added. Running a sync again does not advance it.
* To deliberately get the **next batch**, ask for it by name: `discovery sync --target test --with-shortlist --new-session` (preview with `--dry-run`). This deprioritises records you have already been shown. Do this only when you want new items; ask Claude to show the plan first.
* Sessions are shared by TEST and LIVE. A new batch also needs LIVE row capacity (section 3, `live-limits --next-session`).

## 6. Source health: what the words mean

Run `discovery source-health` (read-only) at any time.

| State | Meaning |
|---|---|
| **complete** | The newest scan covered everything it asked for. |
| **complete_filtered** | Complete only for a filter such as `locations = Bengaluru`. It says nothing about other places. |
| **partial** | Only part was read (for example Accel: 2,988 of 2,993). Absence from it proves nothing. |
| **stale** | Last checked too long ago to trust as current. |
| **failed / blocked** | The source errored or refused. Nothing was learned; nothing is closed. |
| **skipped** | The data was unchanged since last time, so nothing new was collected. Not an error. |
| **incomplete / unverified zero** | Some parts returned nothing without proof that nothing exists. |

No state ever closes a job or marks it gone. Only shortlisted records appear in LIVE regardless of source health; the Runs row quotes the sources needing attention.

## 7. Search preferences and ranking

* `config/search_profile.yaml` - role types, title terms, exclusions, locations, shortlist size.
* `config/ranking.yaml` - the point weights. Both are marked approved by you; the files reject unknown keys, so a typo is an error. Change them only deliberately, then run `discovery ranking-preview` (read-only) to see the effect before any sync.
* `config/live_publication.json` - the LIVE row limits (yours to change).

## 8. Adding a job by hand

Put your own URL/CSV/text in the `imports` folder, never anywhere else:

1. Copy `imports\links.example.csv` to `imports\links.csv` and fill in rows (`url,company_hint,job_title_hint,location_hint,experience_hint,source,notes,description_file`). To keep a job description, save it as plain text in `imports\descriptions\` and name it in `description_file`.
2. Preview: `discovery import-manual` (nothing is written, no website is opened).
3. After you approve: make a backup, then `discovery import-manual --apply`.

Manual jobs are your unverified input: their availability is "unknown" and they never close or merge anything.

## 9. Backups and restoring

* **Make a backup** (safe while nothing else is writing; takes a few seconds):
  `discovery backup` (optionally `--label before-import`). It writes `backups\discovery_<label>_<date-time>.db` plus a `.manifest.json`, uses SQLite's online-backup method, checks integrity and foreign keys, refuses to overwrite anything, and never changes production.
* **List / check:** `discovery backup-list`, `discovery backup-verify --latest`.
* **Prove it restores** (disposable copy, deleted afterwards): `discovery backup-verify --latest --restore-test`.
* **Restore for real** (only if the live database is damaged; it is deliberately manual, never automatic):
  1. Close Claude Code, every terminal and anything else using the project, so nothing holds `data\discovery.db`.
  2. The database is `data\discovery.db` unless `DISCOVERY_DB_PATH` in `.env` says otherwise.
  3. Keep the damaged files for diagnosis: rename `data\discovery.db` to `data\discovery.FAILED-<date>.db` (and any `discovery.db-wal` / `-shm` beside it the same way). Never delete them and never "start fresh".
  4. Check the backup you will use: `discovery backup-verify backups\<file>.db --restore-test`.
  5. Copy that file to `data\discovery.db`.
  6. Run `discovery doctor`, then `discovery source-health`.
  7. **A restore does not undo Google Sheets.** Run `discovery sync --target test --with-shortlist --dry-run` and `discovery live-publish --plan-output exports\check.json` and read what they would change before approving anything.

## 10. If a run is interrupted

* Close the window; do not start another run on top. Nothing is half-written to your verdicts. Check health: `discovery doctor`.
* Collection runs can be resumed: `discovery run --sources <same sources> --target test --resume <RUN_ID>` (the id is in `discovery report --last`). Sources already finished are not repeated.
* A sheet sync that stopped part-way is safe to run again: it re-reads the sheet, matches by `record_id`, adds only what is missing and never deletes. For LIVE, make a fresh plan with `live-publish` and approve it again; never replay an old plan.
* If anything looks wrong, make a backup first (`discovery backup`), then ask Claude to investigate read-only.

## 11. Troubleshooting

| Symptom | What to do |
|---|---|
| `doctor` shows FAIL | Read the line; do not run `init-db` or edit code. Ask Claude with the output. |
| "LIVE synchronization is not enabled" | Expected for `sync`. Use `live-publish` (section 3). |
| `[REFUSED] ... plan differs / limits / backup` | A safety gate worked. Make a new plan and a fresh backup; if a limit was reached, it needs your decision. |
| Sheet permission or "not found" error | Check `.env` ids and that the service account is an editor of that sheet. Never paste the key anywhere. |
| "quota" / rate-limit message | Wait a minute and run again; the sync is safe to repeat. |
| `live-publish` says a limit would be exceeded | Normal after the first batch. Run `discovery live-limits --next-session`, then decide whether to approve larger numbers (section 3). |
| A source says partial/failed/skipped | See section 6. Not a reason to change anything. |
| `pip check` complains | Do not upgrade packages. `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` restores the tested set. |
| Rebuilding the environment | Python 3.14.3 is the project runtime. In the project folder: `py -3.14 -m venv .venv`, then `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`, then `discovery doctor`. Packages are pinned in `requirements.txt` (python-jobspy 1.4.0 included). |

## 12. Getting help from Claude Code safely

* Open Claude Code in this folder (it reads `CLAUDE.md`, the project rules). Ask in plain words, for example "run `/run-discovery existing`" or "why is Accel partial?".
* Keep Claude on its default permission prompts: approve read-only commands freely; read what any write command says before you allow it. Do not switch on "bypass permissions".
* Claude must stop and ask before: writing the database, TEST or LIVE sheet, contacting any website, using Indeed/Naukri/LinkedIn, changing configuration or committing to Git. A truncated, unclear or tool-generated message is never approval; say `Run` or `Approved` for one displayed plan.
* Never paste `.env`, the service-account key or sheet ids into chat.

## 13. What the engine will not do

No automatic runs, no applications or outreach, no AI ranking, no logins or CAPTCHA tricks, no deleting your verdicts, no closing a job because a source failed. Known limits (coverage gaps, Consider-only rows, shared verdict store) are in `docs/KNOWN_LIMITATIONS.md`; the full design and decision history is in `AB_Discovery_Engine_Implementation_Guide.md`.
