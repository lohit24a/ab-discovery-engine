# Source audit: yc-oss/api (Y Combinator company directory)

- **Inspected:** 2026-10-20 (UTC)
- **Repository:** https://github.com/yc-oss/api — commit `c0029dc3b25d6332f35a90fb8c7a0732ee94a6ad`
  (2026-10-20T02:41:51Z, "Update APIs: 2026-10-20")
- **Endpoints used:** `https://yc-oss.github.io/api/meta.json`, `.../companies/all.json`
  (optional later: `.../changes/latest.json`)
- **Status:** unofficial, not affiliated with Y Combinator. No license file in the repository.

## How the data is produced

`fetcher.ts` (Deno) reads the public Algolia key embedded in `ycombinator.com/companies`, queries YC's own
search index (`YCCompany_By_Launch_Date_production`) batch by batch, sorts by `id`, adds `url`
(`https://www.ycombinator.com/companies/<slug>`) and `api`, and writes `companies/all.json` plus per-batch /
industry / tag / flag lists. A GitHub Actions cron (`0 0 * * *`, daily) commits changes and publishes to
GitHub Pages. Consequences: the data is only as fresh as the last daily run, depends on a key scraped from
YC's page (could break without notice), and contains **publicly launched companies only** (README).
No reusable normalization logic exists (records are published as returned by Algolia, minus
`_highlightResult`/`objectID`); `changes.ts` only diffs snapshots.

## Maintenance and accessibility

- Last 5 workflow runs (Oct 16–20) all succeeded; 30 commits between 2026-09-21 and 2026-10-20 (daily cadence).
- Not archived. Two open issues, both feature requests (#1 application video URL, #11 founders details).
- Endpoints returned HTTP 200, `application/json`. `all.json` = 10.6 MB (~3 s); `meta.json` = 67 KB;
  `Last-Modified` 2026-10-20 02:42 GMT.

## Observed dataset (2026-10-20)

| Item | Value |
|---|---|
| `meta.last_updated` | `2026-10-20T02:41:43.163Z` (publisher time; stored separately from our retrieval time) |
| `meta.companies.all.count` | 6311 |
| Records in `all.json` | **6311 — matches meta** |
| `meta` sub-list counts | hiring 1494 (equals actual `isHiring == true`), top 91, nonprofit 42 |
| IDs | all int, unique, range 5–39117 |
| Slugs | all present and unique; `url` always `https://www.ycombinator.com/companies/<slug>` |
| `changes/latest.json` | keys `generated_at, summary, added, removed, updated`; today: 4 added, 0 removed, 47 updated |

## Field behaviour (of 6311 records)

| Field | Type | Empty / null | Notes |
|---|---|---|---|
| `id` | int | 0 | stable YC identifier — used as source identity |
| `name`, `slug` | str | 0 | names are **not unique** (92 case-insensitive duplicates, e.g. "Scout", "Apollo") |
| `former_names` | list[str] | 3217 empty | 3094 companies have previous names — usable as aliases |
| `website` | str | 39 `""` + 1 `null` | 1490 `http://`; 5 have **no scheme** (`picplum.com`); two hosts shared by >1 company (`ycombinator.com`, `producthunt.com`) |
| `url` | str | 0 | YC profile |
| `all_locations` | str | 150 empty | `;`-separated, each `City, Region, Country`; country token `USA` for the US; 41 are `Remote` only |
| `regions` | list[str] | 0 | mixes countries, areas (`South Asia`, `Europe`) and work-style (`Remote`, `Partly Remote`, `Fully Remote`); `Unspecified` for 119 |
| `one_liner` | str | 160 empty | |
| `long_description` | str | 422 empty + 29 `null` | |
| `industry`, `subindustry`, `industries`, `tags` | str / list | 0 / 0 / 0 / 854 empty tags | |
| `batch` | str | 0 | `"Winter 2022"` style (`Unspecified` possible) |
| `stage` | str | 0 | `Early` 5213, `Growth` 1098 |
| `status` | str | 0 | `Active` 4354, `Inactive` 1096, `Acquired` 838, `Public` 23 |
| `team_size` | int | 113 `null` (+136 zero) | max 10000 |
| `isHiring` | bool | 0 | **True 1494 / False 4817**. Informational only |
| `launched_at` | int (epoch s) | 0 | 2010-01-17 … 2026-10 |
| `top_company` | bool | 1 `null` | |

Extra fields we do not use: `small_logo_thumb_url`, `tags_highlighted`, `highlight_*`, `nonprofit`,
`app_video_public`, `demo_day_video_public`, `app_answers`, `question_answers`, `api`.
No U+FFFD encoding damage was found in this snapshot.

## India evidence

- 224 companies list India in `regions`; the same 224 list India in `all_locations` (243 India location
  entries because some have several offices). The two fields agree for every record.
- Of these 224: 159 Active, 43 Inactive, 20 Acquired, 2 Public; 51 flagged hiring, 173 not.
- Remote work: `Remote` 3084, `Fully Remote` 1157, `Partly Remote` 1927 companies. **None of this says whether
  India-based candidates are eligible.**
- 13 South-Asia companies are outside India (Pakistan, Nepal, Bangladesh).

## Known limitations and decisions

1. **Not all YC companies**: only publicly launched ones; unpublished/stealth companies are absent.
2. **`isHiring` is unreliable for our purpose** (YC-reported, 110 of 224 India companies that are Active show
   `false`); it is stored as a source-reported fact, never used to filter, and never creates a job status event.
3. **`website` is source-reported.** It is stored as a fact and is *not* written to `companies.domain` (which our
   schema treats as verified, with a unique index). Shared hosts, missing and scheme-less values exist.
4. **Names are not identities**; only `id` is. Name changes are handled via `former_names`/aliases.
5. The set can shrink (`removed` in the change feed); absence from a later snapshot must not delete or close
   anything locally.
6. Schema drift risk: the field list is produced by Algolia and can change; the adapter must validate required
   fields (`id`, `name`, `slug`) and ignore unexpected extras.
7. `changes/latest.json` only covers the most recent day, so it cannot serve as a durable incremental feed;
   for now we always process the full `all.json` (10.6 MB, cheap). It could later be used as a cross-check.
