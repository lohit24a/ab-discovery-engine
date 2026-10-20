# Day 12 ranking quality audit (offline)

Scope: offline audit and refinement of the Checkpoint 1 ranking. No production SQLite change (hash `a142517f…` unchanged,
schema 6), no Sheets, no network, no migration 7, nothing committed. Weights and career preferences remain **proposals**.

## What was wrong (objective defects, fixed)

| # | Defect | Effect | Fix |
|---|--------|--------|-----|
| 1 | Aggregator first-seen date (Feashliaa) used as listing age | "Fresh" bonus for records the aggregator merely noticed late | `ranking.yaml: date_semantics`; only `provider_reported_creation` (investor boards) earns age points; codes `DATE_AGGREGATOR_FIRST_SEEN_NO_BONUS`, `POSTING_AGE_UNKNOWN` |
| 2 | "Latest check" used company-directory scans for job availability, and ignored partial scans | Job availability implied by an unrelated scan | `lifecycle.latest_run_checks(conn, "jobs")` ignores `companies:` / `companies/` scopes, keeps partial scans with a `partial` flag; `SCAN_PARTIAL` reason + missing-evidence flag |
| 3 | "Associate Product Marketing Manager" matched `associate product manager` | False strong match | span-overlap negative veto in `match_roles` |
| 4 | Country-only "India" outranked a documented city | Wrong best location | `LOCATION_PRIORITY` puts documented cities first |
| 5 | Mixed "Remote (United States or India)" classed unknown | Literal India-allowed text discarded | now `india_remote_explicit` / confirmed / literal |
| 6 | "Country \| Remote" split as separate segments | `United States \| Remote` read as unqualified remote | `_split` keeps it one foreign-remote segment |
| 7 | Hard exclusion `no_role_signal` removed 9,072 jobs | Missed adjacent roles; no way to explore | replaced by `non_target_role` (explicit different job, no family) + `low_priority` + exploration lane |
| 8 | Unused / duplicate config keys, bools accepted as strings, optional PyYAML | Silent misconfiguration | strict validation, settings-audit tests, deterministic subset reader |
| 9 | Stream B over-qualification (575/948 generic-only; Inactive/Acquired ignored) | Weak company shortlist | generic-only sector evidence cannot qualify; operating-status penalties and `needs_review` |
| 10 | Feashliaa `skill_level` / `is_recruiter` not stored | 27 of 126 confirmed Feashliaa jobs are upstream-senior | optional offline `--with-cached-upstream-attributes` (see enrichment plan) |

## Before / after on the same frozen data (29,959 canonical jobs, 10,982 companies)

| Class | Before | After | | Company | Before | After |
|---|---|---|---|---|---|---|
| confirmed_eligible | 253 | 250 | | confirmed_eligible | 948 | 299 |
| needs_review | 4,225 | 3,852 | | needs_review | 6,077 | 5,534 |
| exploration | – | 1,960 | | low_priority | 3,955 | 5,147 |
| low_priority | 3,584 | 10,339 | | excluded | 2 | 2 |
| excluded | 21,897 | 13,558 | | | | |

- Recovered from hard exclusion: 9,072 (all `NO_ROLE_SIGNAL`): 7,145 now low-priority, 1,927 exploration.
- Newly excluded that were previously review/qualified: 241 (232 location, 9 non-target). A 25-row random sample of the 232 were
  all genuinely foreign-only (e.g. `Remote - Florida`, `Remote, Serbia`, `United States | Remote`, `Americas Remote`).
- Top-20 jobs: 16 unchanged; 4 internships/mid-level moved out or down (internship now held, mid-level penalised).
- Company top-5: 4 of 5 unchanged. 649 previously-qualified companies dropped (533 to review, 116 to low priority): generic-only
  wording, Inactive/Acquired status.
- Cached upstream attributes (offline) would move a further 29 qualified jobs to review (source-labelled senior or recruiter posted)
  and take Accel from 9 to 8 of the 20-item shortlist.

## Known weaknesses (not fixed; need your judgment)

- **Exploration lane precision is low.** `exploration_signals` such as `operations`, `strategy`, `analyst` pull in
  "Controls Engineer – Industrial Automation", "Procurement Operations Professional". The lane is capped at 10, never qualifies,
  and exists to be measured with the calibration sample, not trusted.
- Of 250 qualified jobs, all lack a description and employer-confirmed availability; 224 lack stated experience; 100 lack a posting
  date. These are shown as `Missing evidence:` in every explanation.
- 91 of 250 qualified jobs come from a partial scan.
- Investor sources supply ~75% of the confirmed pool (Accel 111). The generic per-employer cap (2) is retained; no source quota.

## YAML reader comparison and recommendation

PyYAML is **not installed**; installing a dependency needs your approval, so no empirical comparison was run.
Known semantic differences (documented from the YAML 1.1 spec behaviour of `yaml.safe_load`): `yes/no/on/off` become booleans,
bare dates become `date` objects, `0o`/leading-zero numbers change meaning, duplicate keys silently take the last value.
The shipped configs use none of these constructs, and the subset reader **rejects** them rather than guessing, so both readers
would agree on the shipped files.

Recommendation: keep the strict subset reader (zero dependencies, deterministic, line-numbered errors). Adopt PyYAML only if you
want richer YAML later; if so, pin `PyYAML==<version verified on Python 3.14>` in requirements, call `yaml.safe_load` only,
keep `profile.py` validation, and add a conformance test that both readers produce identical dicts for both config files.
`load_yaml` no longer changes behaviour if PyYAML happens to be installed.

## Lanes (session model)

`discovery/session.py`: `changed`, `new_discovery`, `previously_unreviewed`, `uncertain_valuable` with slots from
`ranking.yaml: session.slots`. History is read from `runs.summary["listed"]` if present; **nothing writes it today**, so
until a publish step records it every record is "never shown". Recording it needs no schema change, only a field added to the
run summary at publish time, which is outside this checkpoint.

## Calibration

`exports/calibration/day12_calibration_sample.csv` (Git-ignored): 54 rows, 8 per stratum, deterministic, judgment/reason/notes
blank. See `docs/design/calibration_review.md`. Separate from the Day 13 15-post benchmark (batch ids `day12-calibration-*`).

## Decisions that need you

Role terms and weights; exploration signals and AI-engineering placement; internship, mid-level, executive, other-Indian-city and
worldwide-remote treatments; generic company interests; session slots; whether PyYAML is wanted; migration 7 (enrichment
storage); what may be published and when.
