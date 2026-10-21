# Day 13 benchmark report: independent discovery quality (revised methodology)

**This is a newly constructed, independent benchmark. It is NOT the original 18 October 2026 historical sample**, which could not be located and was not reconstructed
(operator-approved deviation, 2026-10-21). Results below say nothing about that sample.

## 1. Provenance and limits
* Built on 2026-10-21 from 28 web searches (cap 30) and 37 single-page fetches (cap 40), all public pages, no login, no database consulted.
* Fixture `tests/fixtures/benchmark_day13_independent_2026.csv` was frozen at **2026-10-21T07:26:38+00:00** (SHA-256 `a635b47e306fe0ae...`) **before** any comparison with SQLite.
* Sample: **11 positive postings** (target was 15; fewer than 15 grounded early-career Bengaluru/India-remote postings in the target families could be verified inside the approved budget),
  3 senior-experience boundary cases and 5 negative controls. The positives and boundary cases are kept out of each other's denominators; controls are never in the recall denominator.
* **Statistical limit:** with n = 11, 6/11 = 54.5% has a 95% interval of roughly 28-79%. Treat every rate as indicative, not precise.
* **Selection bias (important):** most positives were found on Greenhouse and Lever employer boards, which is the ATS space Feashliaa also reads. Recall for ATS-hosted employers is therefore probably overstated
  relative to employers on other platforms (only 2 of 11 positives, Keka and an aggregator, came from outside it). Role families with no verified posting in the sample: internal AI automation, business/strategic finance, venture studio.
* Availability: every "page loaded" statement is one GET on 2026-10-21. **No authoritative current-availability check was completed**; Sprinto's board index (fetched the same day) did not list A01 although its page loaded.
* 12 candidates were rejected for objective page reasons (404, closed, removed, script-only, not on the employer's board) before matching; they are listed in `exports/day13_benchmark_research_log.json`.

## 2. Frozen evaluation baseline
Git `09258af`, schema 7, database SHA-256 `b781c5f9fff3ee59...` (unchanged by this evaluation), integrity ok,
profile `2e0596c7d6f8`, ranking `645958b23aad` (approved 2026-10-21, **no rule, weight or identity decision was changed**),
frozen at 2026-10-21T07:13:15+00:00. Review session `2e05066b` (24 records), verdicts {'Maybe': 1, 'No': 1, 'Yes': 3}.

| Source | Type | Last sighting | Latest check |
|---|---|---|---|
| feashliaa | ats_feed | 2026-10-20T06:02 | skipped / not complete |
| inv_accel | investor_board | 2026-10-20T15:15 | partial / not complete |
| inv_lightspeed | investor_board | 2026-10-20T14:15 | partial / not complete |
| inv_peakxv | investor_board | 2026-10-20T14:28 | partial / not complete |
| inv_techstars | investor_board | 2026-10-20T14:58 | success / complete |
| inv_three_one4 | investor_board | 2026-10-20T14:27 | success / complete |
| yc_jobs | yc | 2026-10-20T12:36 | success / not complete |
| yc_oss | yc | 2026-10-20T06:38 | success / complete |

('skipped' means that run found its snapshot already imported and collected nothing; Feashliaa's last collecting check was a success.) All collections predate the fixture by about 16-25 hours, and posting dates are unknown for most items, so **absence cannot be separated from collection timing** (see section 6).

## 3. Per-item results (11 positives)
| ID | Posting | Identity | Automatic sources | Engine first saw | Actual ranking | Clean top-20 | If collected: rules say | Page evidence | Manual import (dry run) |
|---|---|---|---|---|---|---|---|---|---|
| A01 | Sprinto - Forward Deployment Engineer | no match | - | - | not in database | no | low priority 0 | conflicting | create |
| A02 | Sprinto - Revenue Operations Analyst - Customer Experi | confirmed exact | inv_accel | 2026-10-20T15:14 | confirmed eligible 65 | no | confirmed eligible 57 | employer page loaded 2026-10-21 | attach |
| A03 | Prismforce - Associate Product Manager | no match | - | - | not in database | no | confirmed eligible 70 | employer page loaded 2026-10-21 | create |
| A04 | Apna - Product Operations Associate | probable review | inv_lightspeed, inv_peakxv | 2026-10-20T14:14 | confirmed eligible 86 | yes | confirmed eligible 75 | mirror/aggregator only | create |
| A05 | Sarvam AI - Deployment Strategist | probable review | inv_lightspeed | 2026-10-20T14:14 | confirmed eligible 72 | no | confirmed eligible 60 | employer page loaded 2026-10-21 | create |
| A06 | Prodigal - AI Deployment Lead (Bengaluru) | confirmed exact | feashliaa, inv_accel | 2026-10-20T06:02 | needs review 69 | no | needs review 48 | employer page loaded 2026-10-21 | attach |
| A07 | Prodigal - Strategy and Operations Analyst | confirmed exact | feashliaa, inv_accel | 2026-10-20T06:02 | confirmed eligible 82 | no | confirmed eligible 63 | employer page loaded 2026-10-21 | attach |
| A08 | Emergent Labs - Forward Deployed Engineer (FDE) | confirmed exact | feashliaa | 2026-10-20T06:02 | confirmed eligible 70 | no | confirmed eligible 60 | employer page loaded 2026-10-21 | attach |
| A09 | Libra AI - Associate Product Manager (AI) | no match | - | - | not in database | no | confirmed eligible 70 | mirror/aggregator only | create |
| A10 | Hevo Data - Strategy & Ops (Founder's Office) | confirmed exact | feashliaa | 2026-10-20T06:02 | confirmed eligible 70 | no | confirmed eligible 70 | employer page loaded 2026-10-21 | attach |
| A11 | Turing - Product Manager | confirmed exact | feashliaa | 2026-10-20T06:02 | needs review 36 | no | needs review 36 | employer page loaded 2026-10-21 | attach |

## 4. Coverage metrics (denominator 11; no per-source sums are added)
| Measure | Count | % of 11 |
|---|---|---|
| Confirmed exact, discovered automatically | 6 | 54.5% |
| Probable (needs review; NOT counted as found) | 2 | 18.2% |
| No match (missed or not yet collected) | 3 | 27.3% |
| Unverifiable identity | 0 | 0% |
| Matched only through manual entries | 0 | 0% |
| Matched to the correct company (all confirmed) | 6 | 54.5% |
| Authoritative current-availability checks completed | 0 | 0% |
| Recoverable by the manual importer (dry run) | 11 | 100% |
| Recoverable **only** by manual import (not found automatically) | 3 | 27.3% |

**Per source (confirmed, overlapping):** Feashliaa 5 (A06, A07, A08, A10, A11); Accel 3 (A02, A06, A07).
YC job pages, Lightspeed, Peak XV, 3one4 and Techstars: 0 confirmed (Lightspeed and Peak XV hold the two probable items). **Combined unique confirmed: 6** (A06 and A07 are in both families).
**Unique contribution:** ATS feed alone: A08, A10, A11; investor boards alone: A02.
Exact identity rate 54.5%, ambiguous/probable 18.2%.

## 5. Ranking and shortlist recovery
* Of the 6 confirmed finds: **4 qualified, 2 held for review, 0 excluded or low priority**; **0 reached the clean top 20** (cutoff score 78); 0 are in the current review session. The one top-20 item is the probable match A04 (Apna, 86).
* No false exclusion among found positives. Two were held for review for objective reasons: A06 ('Lead' read as senior although 2+ years) and A11 (generic 'Product Manager' is a weak alias).
* If the 3 missed items had been collected: A03 and A09 would qualify (70) and reach the top 20 once ordinary availability and source points apply; A01 would be **low priority (no role signal)**.
* Experience is "unknown" for most stored jobs (descriptions are not stored), which costs up to 10 points and lets senior roles through: **2 of 3 senior-experience boundary cases qualified** (B01, B02), none reached the top 20.
* Negative controls: 0 of 5 matched in the database, 0 would reach the shortlist (4 excluded as non-target, 1 low priority).

### Senior-experience boundary cases (not in the recall denominator)
| ID | Posting | Stated experience | Actual ranking | Hypothetical (stated experience read) |
|---|---|---|---|---|
| B01 | Gushwork - Chief of Staff | '7 or more years' | confirmed eligible 70 | needs review |
| B02 | Turing - Forward-Deployed AI Engineer - Enterprise AI | '8+ years' | confirmed eligible 70 | needs review |
| B03 | Emergent Labs - Finance Manager | 'Chartered Accountant with 5-7 years of post-qualification experience' | needs review 38 | low priority |

### Negative controls
| ID | Posting | Identity | Rules say |
|---|---|---|---|
| N01 | Meltplan - Senior Full Stack Engineer - Frontend Focused | no_match | excluded:NON TARGET ROLE  |
| N02 | Meltplan - UI Engineering Lead | no_match | low priority 0 |
| N03 | Emergent Labs - Motion Designer | no_match | excluded:NON TARGET ROLE  |
| N04 | Emergent Labs - Senior Software Engineer - Backend | no_match | excluded:NON TARGET ROLE  |
| N05 | Emergent Labs - Legal Counsel | no_match | excluded:NON TARGET ROLE  |

## 6. Diagnosis of every non-confirmed positive (demonstrated vs hypothesis)
* **A01 - Sprinto, Forward Deployment Engineer: MISSED.** Employer is in Accel (22 jobs) but not in Feashliaa; this job is not stored. The title says 'Forward Deployment', which no role alias matches, so it would rank low_priority even if collected. Page-vs-board availability evidence conflicts.
* **A03 - Prismforce, Associate Product Manager: MISSED.** Employer (Prismforce, Keka ATS) is absent from every collected source. Would qualify (70) if collected.
* **A09 - Libra AI, Associate Product Manager (AI): MISSED.** Employer (Libra AI) is absent from every collected source; the only evidence is one aggregator page with no apply button or employer URL. Would qualify (70) if collected.
* **A04 - Apna, Product Operations Associate: UNVERIFIABLE (probable match, identity not provable).** Same employer + identical title on Lightspeed and Peak XV; the stored Workable URL has no tenant, so the key cannot be compared (the posting id E75B63B3A7 appears in it). Qualified 86, in the top 20.
* **A05 - Sarvam AI, Deployment Strategist: UNVERIFIABLE (probable match, identity not provable).** Same employer + identical title on Lightspeed only; no shared identifier. Qualified (72), not in the top 20.

Demonstrated: employer absent from all sources (A03, A09); employer present only in Accel (A01); probable items lack a comparable job-specific key. **Hypotheses (not proven):** A01 may be a collection miss or a timing effect and the data cannot tell
them apart (Accel's check is partial and scoped, Feashliaa does not hold Sprinto, posting dates are unknown); A03 and A09 exist because the employers' platforms (Keka, Built In) are not integrated.

## 7. Found items
* **A02 - Sprinto, Revenue Operations Analyst - Customer Experience & Partner Operations: FOUND (confirmed, automatic).** Held by Accel only (employer's Lever board). Qualified (65), not in the top 20 (cutoff 78).
* **A06 - Prodigal, AI Deployment Lead (Bengaluru): FOUND (confirmed, automatic).** Feashliaa + Accel, exact key. Title contains 'Lead', so the experience rule reads it as senior although the page asks for 2+ years; held for review (69).
* **A07 - Prodigal, Strategy and Operations Analyst: FOUND (confirmed, automatic).** Feashliaa + Accel, exact key. Qualified (82), not in the top 20.
* **A08 - Emergent Labs, Forward Deployed Engineer (FDE): FOUND (confirmed, automatic).** Feashliaa only. Qualified (70). The page says 'Senior (4+ years)'; the stored record has no experience, so the rule sees 'unknown'.
* **A10 - Hevo Data, Strategy & Ops (Founder's Office): FOUND (confirmed, automatic).** Feashliaa (+ Peak XV employer record). Qualified (70). Page says '2 to 8 years'; stored experience unknown.
* **A11 - Turing, Product Manager: FOUND (confirmed, automatic).** Feashliaa only. India-remote recognised; the generic title 'Product Manager' is a weak alias, so it is held for review (36).

## 8. Limitations
Small n; ATS-biased sampling; one-day, single-GET availability evidence; no prospective test (sources not re-run); descriptions and experience are absent from stored data, so ranking uses titles and locations only;
three role families unrepresented; Sprinto A01 availability conflict unresolved. No restricted JobSpy source, no authenticated page, no proxy and no CAPTCHA handling was used.

## 9. Day 14 handoff (ranked by expected unique relevant recall, then cost)
1. **Read the public board index of employers we already know to use Greenhouse/Lever/Ashby** (tenant is visible in stored URLs). Directly recovers A01-type misses (Sprinto: Accel holds 22 records for the employer, the employer's own board lists 35 jobs, and this posting is not stored) without a new crawler class. Impact: 1 of 11 here; likely larger on the full inventory. Cost: medium; needs a source audit and approval.
2. **Add description/experience evidence for shortlist candidates** (public ATS job JSON/page; see `docs/design/enrichment_plan.md`). Fixes the 'Lead' false-senior (A06), restores the 10 experience points (A08, A10), and stops 2 of 3 senior roles qualifying. Impact: 5 of 14 items touched. Cost: medium; bounded request ceiling.
3. **Role-alias variants:** 'forward deployment' (A01) and a review of generic 'Product Manager' with corroboration (A11). Impact: 2 items. Cost: low; needs the operator's approval of terms.
4. **Employers on platforms nobody reads** (Keka A03; aggregator-only A09). Impact: 2 of 11; access and permission uncertain; evaluate before building anything.
5. **Identity:** compare tenant-less Workable URLs by posting id after a source audit (A04). Impact: 1 item moves from probable to confirmed. Cost: low.
6. **Prospective pickup test:** re-run the approved sources once (separate approval) and re-evaluate A01/A03/A09 to separate timing from misses.
