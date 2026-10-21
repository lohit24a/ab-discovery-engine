# Day 14 gap analysis (offline; no live request was made)

## Operator decisions applied at closure (2026-10-21)
| # | Decision | What was done |
|---|---|---|
| 1 | Alias `forward deployment`: approved with conditions | Added as a **weak** alias of `forward_deployed_solutions` only. Alone it is a review lead; it needs corroboration in the title to count as `weak_corroborated`; it never reaches strong. Nine stored jobs are touched (none becomes qualified; three foreign-only jobs stay excluded by location; five move to review; one stays low priority). Generic "deployment engineer", release and infrastructure titles are not matched (tested). Location and experience rules unchanged |
| 2 | "Lead" seniority: precedence policy | Not removed globally. A reliable explicit employer-stated minimum below the senior threshold now takes precedence over a senior title word; the conflict stays visible (`EXP_TITLE_CONFLICT`); executive titles, missing evidence and user-supplied or aggregator figures are unchanged; a noisy `skill_level` label never overrides employer evidence. Only YC job pages carry such evidence today, so the effect on stored data is limited to that source until descriptions exist |
| 3 | Investor-board bonus: +3 cap | `jobs.company.investor_membership_cap: 3`. Membership (per listing board plus the investor-board source points when the job was sighted on a board) is capped at 3 per job. Feed sourcing, availability and provider dates are separate; feed-only jobs lose nothing. 535 board-only jobs lose 3 points; qualified count is unchanged (249) |
| 4 | Workable, Apna, Sarvam | No merges. The 58 Workable pairs, Apna and Sarvam remain Tier B review candidates; no identity link was written |
| 5 | Source audit and enrichment | Offline comparison approved; **live collection deferred** (the 90-request plan is not approved and is not part of Day 14); no further network request was made |
| 6 | Migration 8 | **Deferred.** Not written as code, not applied; schema stays 7 |

Priority for any future controlled improvement (documentation only): (1) generic board-index discovery for already known, permitted Greenhouse, Lever and Ashby tenants; (2) selective description and experience enrichment; (3) Keka and other demonstrated India-relevant gaps; (4) manual import for inaccessible or unsupported sources. Weekday and Saarthi stay unintegrated unless stronger evidence shows unique, useful, permitted coverage. Any live experiment needs its own exact host and endpoint list, access assessment, request ceiling, pacing, stop conditions and explicit approval.

Scope: explain the Day 13 findings with evidence already on disk (SQLite at schema 7, cached Feashliaa feed, saved investor-board and YC snapshots), fix what is objectively wrong,
and put every judgment call in front of the operator. Nothing here changes an approved rule, weight, identity decision or source permission.

## 1. Checkpoint and Day 13 carry-forward
* Git `a9205d0`, clean tree; schema 7, integrity ok; database SHA-256 `b781c5f9fff3...`; frozen fixture SHA-256 `a635b47e306fe0ae...` (unchanged); profile/ranking approved 2026-10-21 (unchanged).
* The Day 13 evaluator was re-run before any code change and reproduced the published results **byte for byte** (`51ef681638c9d818...`): 6 confirmed, 2 probable, 3 not found; 4 of 6 confirmed qualified; 0 of 6 in the clean top 20; 0 of 5 controls in the shortlist.
* Day 13 documentation already records all six required items (original benchmark unavailable and formally replaced; 11 / 3 / 5 composition; three unrepresented families; non-authoritative availability;
  indicative results because of sample size and selection bias; collection timing limits what counts as a demonstrated miss). **Day 13 handoff: complete; no documentation omission found.**

## 2. Why 0 of 6 confirmed matches reached the clean top 20
Not a ranking defect. Findings, in order of weight:

| Cause | Evidence | Class |
|---|---|---|
| The qualified band is dense and the list is short | 249 qualified jobs; 160 sit between 70 and 89 and **78 is the 20th score**. The four confirmed items that qualified rank 31, 118, 154 and 187 of 249 (the other two confirmed items are held for review) | Shortlist-size limitation (by design: later sessions reach lower ranks) |
| Feed-only jobs lack the corroboration points | 100 of 249 qualified jobs are feed-only: average score 67.1 versus 73.8 for jobs on an investor board. Feed-only jobs get no provider-reported date (60% of qualified have one) and no +3 investor-listing point | Intentional rule with a subjective side effect (needs the operator) |
| Experience is "unknown" for 223 of 249 qualified jobs | No stored job has a stated-years code. Unknown is neutral (0) while early-career evidence is worth up to +10 | Missing evidence (descriptions are not stored) |
| A07 (Prodigal S&O Analyst, 82, rank 31) | Provider date older than 180 days costs -6 (AGE_STALE) although the page loads | Intentional rule |
| Employer cap (2 per employer) | Not decisive for any benchmark item; two employers hit it in the top 20 (BestDoc, Niyo) | Intentional rule |
| Duplicates and sessions | No unresolved duplicate hint on any benchmark item; clean context ignores session history | Not a cause |

Expected placement by chance is 20/249 x 6 = 0.5 items, so 0 is unremarkable. **Are the jobs above them better supported?** On role and location, no: all score the same 40 + 20. They are better *corroborated*
(fresh provider date, investor listing, sector terms). Example: Wibmo "Product Strategy & Operations" (84: fresh date, listed, partial Accel scan) versus Hevo "Strategy & Ops (Founder's Office)" (70: strong family match, no date, no listing).

### Individual investigations
* **Prodigal, AI Deployment Lead (69, needs_review, TITLE_SENIOR -22).** Title-based false negative *given* the page ("2+ years"), reasonable uncertainty *given the stored data* (no description). Counterfactual: dropping `lead`
  from the senior title terms qualifies it (70) but also moves 15 jobs (this one included) to qualified and lifts 200 low-priority jobs to review/exploration ("Lead Product Manager" would stop being flagged), so the term should **not** be removed globally.
  Smallest correct fix, now **adopted as policy**: reliable employer-stated years outrank a senior title word (implemented for the evidence stored today); without such evidence the title warning stands.
* **Turing, Product Manager (36, needs_review).** Generic title is a weak alias by design; the page's responsibilities ("own roadmap, prioritization and execution") would corroborate it, but descriptions are not stored. Nothing to fix without description evidence.
* **Sprinto, Forward Deployment Engineer.** Alias `forward deployment` is a reliable variant: 12 stored titles use it; adding it changes only 6 jobs (1 to qualified, 5 to review) and does not touch generic "deployment engineer" (70 such titles, infrastructure/release roles, deliberately NOT proposed).
  A01 would then qualify (60). **Approved with conditions (weak alias only); applied.**
* **Senior boundary cases (B01, B02 qualify).** Cause: missing experience, not a rule. Options: (a) wait for descriptions (recommended); (b) source labels: Feashliaa `skill_level` agreed with the page for B01 and B03, **disagreed** for A06 and A11 (both "2+ years", labelled senior) and missed B02 (8+ years), so it is too noisy to use as a rule;
  (c) require evidence to qualify: would demote 223 of 249 qualified jobs, unacceptable. Recommendation: keep unknown honest; enrich the shortlist candidates.
* **Objective bugs found and fixed:** (1) the latest-check selection broke ties by random id, so two runs in the same second could pick the wrong one (`ORDER BY started_at, rowid`); (2) a scan whose reported total changed while it was read was called complete (now partial);
  (3) Feashliaa and YC-company fetchers retried HTTP 429 (now stop, like every other adapter); (4) tenant-less and tenant-qualified Workable URLs could never be compared (now a review candidate).

Counterfactuals are in `scripts/day14_counterfactual.py`; the approved YAML files are untouched.

## 3. The three missed benchmark jobs (classified, none is "an adapter failure")
| Job | Evidence | Classification | Smallest plausible fix |
|---|---|---|---|
| A01 Sprinto, Forward Deployment Engineer (Lever) | Sprinto is **not among the 28,543 companies in the cached Feashliaa feed**; Accel holds 20 Sprinto Bengaluru records (none is this job) but its scan is partial (2,988 of 2,993, count mismatch); the employer's own Lever index fetched on Day 13 listed 35 jobs and **not** this posting although its page loaded | Upstream feed omission (Feashliaa) + incomplete source coverage (Accel) + availability unverifiable; also a ranking limitation (no alias) | Alias (approval); read the known tenant's board index (separate network approval); otherwise retain as unresolved |
| A03 Prismforce, APM (Keka) | Employer in no source: not in Feashliaa (its seven platforms exclude Keka), not on any saved investor-board snapshot, no company record. Keka-hosted jobs of other employers do arrive through the boards (313 stored URLs) | Source not integrated for this employer | Manual import (dry run accepts it); a generic Keka reader only after an audit |
| A09 Libra AI, APM (Built In page) | Employer absent from every source; the only evidence is an aggregator page with no apply button and no employer URL | Source not integrated + unverifiable | Manual import once an employer URL is known |

Timing: every collection predates the fixture by 16 to 25 hours and posting dates are unknown, so none of the three can be called a *demonstrated collection miss*.

## 4. Identity: Apna, Sarvam, Workable
* **Apna (Workable).** Stored URL `apply.workable.com/j/E75B63B3A7` (tenant-less short link), fixture `apply.workable.com/apna/j/E75B63B3A7`. Same shortcode, same employer, same title, location consistent, no contradiction.
  Data on the shortcode namespace: 273 distinct 10-character codes in stored URLs, **none under two tenants**; 58 codes appear in both forms, all at **one company (Exponent Energy) between two boards** (Lightspeed tenant-less, 3one4 tenant-qualified), same company and title every time, currently 58 unmerged duplicate pairs.
  Evidence is real but narrow (one company), and no vendor document establishes global uniqueness. Outcome: **candidate for review, not an exact match.** New rule `workable_shortcode_tenantless_vs_tenant` (Tier B); a code under two tenants is Tier D (`workable_shortcode_tenant_conflict`).
  `exports/day14_workable_candidates.json` lists the 58 pairs. Promoting them to Tier A needs the operator's decision and a reviewed merge plan.
* **Sarvam (probable).** The stored Ashby job and the Sarvam careers URL carry the **same UUID** (`48c228f1-...`), same employer and title. The earlier "title only" label hid this. New rule `same_uuid_different_hosts` (Tier B). UUID collisions are not a realistic risk, but no stored pair on two hosts exists to validate the rule in the data, so it stays a candidate.
* Both benchmark items stay **probable** (denominators unchanged); the report now labels them `strong_identifier_unscoped`.

## 5. Candidate sources compared (smallest reusable improvement first)
Counts are stored job URLs by host (SQLite); everything about vendor APIs is from general knowledge and **unverified until a source audit**.

| Candidate | Unique value | Already via other sources? | Fields | Access / terms | Cost | Verdict |
|---|---|---|---|---|---|---|
| **Board-index reader for known Greenhouse / Lever / Ashby tenants** (generic, one reader per platform) | Jobs missing from the feed or partial boards; **plus descriptions, dates, experience** (the evidence the ranking lacks) | Employers already known: 87 tenants cover the 158 of 463 candidate jobs that sit on these platforms; only 14 tenants are seen *only* through boards | Documented public JSON boards (unverified) | Public documented interfaces; terms and robots to be checked in the audit | Medium; one reader pattern, bounded requests | **Recommended first** (needs a source audit and your network approval) |
| Keka careers (generic) | Prismforce-type employers; 313 stored Keka URLs show it is common among Indian startups | Only for employers that sit on a board we read | Unknown | Unverified | Medium; unknown API | Audit before deciding; manual import meanwhile |
| Zoho Recruit (194), Darwinbox (149), Kula (178), Sensehq (149), Instahyre (176), Workable (322) | India-popular platforms | Their jobs already arrive through the investor boards (84% of 4,998 board job URLs point outside Feashliaa's seven platforms) | Unknown per platform | Unverified | One reader per platform | Not justified yet; revisit by measured unique value |
| Weekday / Saarthi | Marketplace mirrors of employer roles | **No stored URL on either host** except 35 Weekday-own postings via Feashliaa; Day 13's Sarvam page on Saarthi mirrored an employer posting and showed it Closed | Title, location, experience (Saarthi) | Accounts / terms unknown; not scraped | High, low marginal value if mirrors | Do not build; **keep manual import**; estimate uncertain |
| Employer-owned careers pages (IBM 664, HPE 568, Google 133 ...) | Large employers outside the target profile | Already on boards | Varies | Per site | One reader per site | No |
| JobSpy (Indeed / Naukri / LinkedIn) | Mainstream listings | Not run | n/a | Restricted, separately approval-gated | n/a | Unchanged: not proposed |
| YC job pages, investor boards | Already integrated | n/a | n/a | n/a | n/a | Keep |

## 6. Enrichment feasibility (offline audit)
* **Present upstream and collected, but discarded in normalisation:** Feashliaa `skill_level`, `is_recruiter`, `salary` (10% of rows have one), `remote`; Getro `seniority` (97% of 3one4, 78% of Accel, 0% of Lightspeed/Peak XV), `work_mode`, `remote_hint`. All sit in local files already, so a backfill needs no request.
  Impact of using them (preview only, `--with-cached-upstream-attributes`): qualified 249 to 220 (Feashliaa labels) or 211 (plus board seniority); mixed accuracy on the benchmark (see section 2), so not adopted.
* **Present in YC job snapshots and stored:** `experience_min_years`, `experience_text`.
* **Never collected:** job descriptions and responsibilities for every source, minimum experience (except YC), employment type, last-updated date, employer-side visibility. These need a retrieval step.
* **Coverage of a targeted enrichment:** of 463 candidate jobs (249 qualified plus review jobs scoring 40 or more with a strong role and confirmed location) only **158 (34%)** sit on Greenhouse, Lever or Ashby (87 tenants: 49, 20, 18); the other 305 are on Workday, Workable, Keka, employer pages or boards. A tenant-board reader therefore helps one third of the candidates.
* **Design (not run):** see `docs/design/enrichment_plan.md`, Day 14 addendum, including the network approval checkpoint and the additive migration 8 that would store attributes with provenance.

## 7. Source health and recovery
See `docs/reports/day14_recovery_tests.md` (new `source-health` command, retry policy table, 56 new tests in total, 30 of them in the recovery file).

## 8. Proven versus hypothetical gaps
* **Proven:** 58 hidden Workable duplicate pairs; one stale tie-break; one mid-scan mutation hole; 429 retries in two fetchers; the alias gap (12 titles); the `lead` ambiguity (244 jobs flagged only by `lead`); the experience gap (223 of 249 unknown); feed-only score handicap (6.7 points); Sprinto, Prismforce and Libra AI absent from the relevant sources.
* **Hypothetical:** that A01 was ever open to a Bengaluru applicant on the benchmark date; that a tenant-board reader would find many *new employers* (only 14 board-only tenants today); that Keka or Weekday add unique relevant jobs; that board `seniority` or Feashliaa `skill_level` can be trusted (the benchmark says partly not).

## 9. Prioritised fixes (status after the operator's decisions)
1. Alias `forward deployment`: **applied as a weak alias** (9 jobs touched, none qualified).
2. Tenant-board reader audit and bounded enrichment run: **deferred; no live request approved.**
3. Experience precedence: **adopted** (employer-stated years outrank an ambiguous title word; conflicts stay visible).
4. Investor-board membership: **capped at +3 per job**; the average gap between board-only and feed-only qualified jobs fell from 5.1 to 2.1 points.
5. The 58 Workable candidates: **no merge; kept as review candidates** (`exports/day14_workable_candidates.json`, local only).
6. Manual import for Prismforce and Libra AI whenever you want them (dry run only so far). Migration 8 stays deferred.
