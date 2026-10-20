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
