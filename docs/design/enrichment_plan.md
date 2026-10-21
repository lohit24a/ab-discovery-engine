# Future bounded enrichment plan (design only, nothing here has been run)

Status: **proposal.** No request was made, no website fetched, no schema changed. Anything below that names an external
endpoint is written **from memory and unverified**; each needs a source audit (`docs/source_audits/<name>.md`, 17-step
checklist) and your approval before any live read.

## Step 0: free, offline, needs no approval beyond code review

Backfill `skill_level` and `is_recruiter` for Feashliaa jobs from the feed chunks already cached in `data/cache/feashliaa`
(`discovery/upstream_attrs.py`, already used by `ranking-preview --with-cached-upstream-attributes`). Persisting them needs the
additive storage below (migration 7); until then they are preview-only.

## What is missing today

Measured on the 250 qualified jobs: description 250/250 missing, employer-confirmed availability 250/250, stated experience
224/250, posting date 100/250. Stored upstream fields not kept: Feashliaa `skill_level`, `is_recruiter`, `updated_at`; Consider
`minYearsExp/maxYearsExp`, `jobTypes`, skills; Getro `seniority`, `work_mode`, `created_at`.

## Candidate selection (who would be enriched)

Only jobs that would change a decision: `confirmed_eligible` and `needs_review` jobs with score within 15 points of the
threshold, plus the exploration lane top 10, capped per run. Excluded and low-priority jobs are never enriched.

## Possible endpoint types (UNVERIFIED, from memory)

| Platform | Likely read | Gives |
|---|---|---|
| Greenhouse boards | public job-board JSON for a company board | description, posted/updated date, location |
| Lever | public postings JSON | description, categories, created time |
| Ashby | public job-board posting API | description, location, published date |

Request ceiling = number of distinct boards among candidates (not jobs); one request per board returning all its jobs where the
platform allows it. The distinct-board count for the current candidate set has **not been measured**; it is computed
at plan time and becomes the hard ceiling the operator approves. Sequential, >= 2 s apart, hard caps, no retries, stop on 403/429, no login/proxy/evasion, per the rules
already used for YC and investor boards.

## Fields and storage (proposed, additive migration 7, **not applied**)

`sighting_attributes(sighting_id, name, value, source_id, retrieved_at, evidence_type)`: append-only, source-reported values
flagged as such; `manual_inputs` for the operator's pasted descriptions (see `manual_import.md`). Never overwrites a human verdict
or a stored field; an unchanged value is idempotent (same sighting, name, value is a no-op).

## Failure handling

A failed, blocked, partial or empty enrichment read changes nothing: it never closes or downgrades a job and never counts as
"not hiring". Partial results are recorded as partial.

## Expected value

Cached Feashliaa attributes alone would reclassify 29 of 250 qualified jobs (11.6%). Descriptions would convert the 2,534
weak-alias holds into decidable cases; that value is unmeasured until a calibration sample exists.

## Permission checkpoints

1. Approve migration 7. 2. Approve each platform's source audit. 3. Approve each live enrichment run's exact board list and
caps. 4. Separately approve applying results (verified backup first). Dry-run by default.

---

## Day 14 addendum: bounded enrichment plan and network approval checkpoint (PROPOSAL ONLY. The operator deferred all live collection on 2026-10-21: no request in this section is approved, migration 8 is not written and not applied)

Measured basis (offline, SQLite and saved snapshots): 463 candidate jobs (249 qualified plus review jobs scoring 40 or more with a strong or corroborated role and a confirmed location). Only **158** of them have a Greenhouse, Lever or Ashby URL (**87 tenants**: 49 / 20 / 18); 305 sit on Workday, Workable, Keka, employer pages or investor boards and are out of reach of this plan. The benchmark's matched Greenhouse/Lever items (Prodigal, Emergent, Hevo, Turing, Sprinto) fall inside it.

### What would be retrieved, and why
Per tenant, one public board-index request returning every posting of that employer, with descriptions where the interface offers them: title, location(s), department, posting URL and id, publication or update date, and the description text. Purpose: stated minimum experience, responsibilities (to corroborate weak aliases), original posting date (feed-only jobs currently score no date points) and a second look at jobs missing from the feed or partial boards. A board listing says a posting is *published*; it is never evidence that a vacancy exists or is guaranteed open, and absence from an index is never closure evidence.

### Network approval checkpoint (to be answered by the operator before any request)
| Item | Proposal |
|---|---|
| Hosts and endpoints | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`, `api.lever.co/v0/postings/{site}?mode=json`, `api.ashbyhq.com/posting-api/job-board/{name}`. **Written from general knowledge; unverified.** A source audit (`docs/source_audits/ats_board_apis.md`, 17-step checklist, documentation read only) must confirm each endpoint, field names and terms first |
| Access and terms | Public, unauthenticated, documented job-board interfaces as far as is known; robots.txt and terms to be read in the audit. If an interface is undocumented or restricted for this use, that platform is dropped and manual import remains |
| Request ceiling | at most **90 GETs**: 87 tenant requests (ordered by candidate count, all tenants in the deterministic list `exports/day14_enrichment_tenants.json`) plus 3 audit probes. No tenant is requested twice. Retries: none. Redirect hops count toward the ceiling; a cross-host redirect is not followed |
| Runtime and pacing | sequential, at least 2 s between requests (about 3 minutes in total), 15 s timeout, 5 MB body cap |
| Stop conditions | any 403, 429, CAPTCHA, login wall or terms notice stops the whole host for the run; two consecutive failures on a host stop that host; a changed response shape stops that platform; the ceiling is hard |
| SQLite writes during collection | **none**: the output is a Git-ignored snapshot with URLs, retrieval timestamps and a digest. Import is a separate dry-run-first step needing your approval |
| Sheets | no write of any kind |
| Standing permission | Day 13's consumed request budget is not permission for this; each run needs fresh approval of the exact list |

### Storage (DEFERRED: proposed design only, no code or migration exists; needs its own approval and a prerequisite of real collected evidence)
Smallest additive migration 8, tested on disposable databases only: `source_attributes(attribute_id, sighting_id -> sightings, name, value, sha256, source_id, retrieved_at, evidence_type, created_at)`, append-only, unique on (sighting_id, name, sha256) so a replay inserts nothing, with `evidence_type = 'source_reported'`. User-supplied text stays in `manual_inputs`; the two are never mixed. Ranking would read `source_attributes` for stated experience, description text and posting date, and every explanation would label them source-reported and unverified. No lifecycle rule would read this table.

### Order of work after approval
1. Source audit (documentation only). 2. Your approval of the exact tenant list and ceiling. 3. Collection into a snapshot. 4. Dry-run import report (matches by exact posting key only, no speculative attachment). 5. Your approval of migration 8 and the import. 6. Re-run the benchmark as a **prospective** measurement, reported separately from the frozen Day 13 baseline.
