# Day 14 benchmark comparison (frozen Day 13 baseline versus the approved Day 14 ranking refinements)

**Fixture unchanged:** SHA-256 `a635b47e306fe0ae...` (same 11 positives, 3 senior-experience boundary cases, 5 negative controls, same labels and denominators). **Database unchanged:** SHA-256 `b781c5f9fff3...`.
The baseline was reproduced byte for byte before any change (`51ef681638c9d818...`).

**Two kinds of change, kept apart:**
* **A. Ranking-policy changes (approved by the operator, offline):** a weak "forward deployment" alias, a cap on the investor-board membership bonus, and the explicit-employer-experience precedence policy.
* **B. Collection improvements: none.** No network request was made, no source record was added, no benchmark URL created a sighting. The 6 of 11 confirmed discoveries are the same 6 and are not a new result; there are no prospective discoveries.

## A. Approved refinements and their effect
1. `forward deployment` added to `forward_deployed_solutions` as a **weak** alias (needs corroboration in the title to count as more than a review lead).
2. `jobs.company.investor_membership_cap: 3` in `config/ranking.yaml`: the points for a job's investor-board membership (per listing board, plus the investor-board source points when the job itself was sighted on a board) are capped at 3 per job. Feed sourcing, availability and provider dates are separate and untouched; a feed-only job loses nothing.
3. Reliable employer-stated minimum experience (YC job pages only, today) takes precedence over a senior title word; the conflict is shown; executive titles, missing evidence and user-supplied or aggregator figures are unchanged.

### Benchmark measures (denominator 11 positives)
| Measure | Day 13 | After refinements |
|---|---|---|
| Confirmed exact, automatic | 6 (54.5%) | 6 (54.5%) |
| Probable (not counted as found) | 2 | 2 (both strong identifier, unscoped) |
| Not found | 3 | 3 |
| Confirmed finds that qualified / held / excluded | 4 / 2 / 0 | 4 / 2 / 0 |
| Confirmed finds in the clean top 20 | 0 | 1 (A07 Prodigal, because board-only competitors lost 3 points) |
| Negative controls reaching the shortlist | 0 of 5 | 0 of 5 |
| Senior boundary cases (stated experience read) | low priority 1, needs review 2 | low priority 1, needs review 2 |
| If collected, the rules would give (outcomes) | confirmed eligible 8, low priority 1, needs review 2 | confirmed eligible 8, needs review 3 |
| Authoritative availability checks | 0 | 0 |

Field-level movements (10):
* A01 hypothetical_outcome: 'low_priority' -> 'needs_review'
* A01 hypothetical_score: '0' -> '28'
* A01 hypothetical_collected_score: '0' -> '38'
* A01 hypothetical_family: '' -> 'forward_deployed_solutions'
* A02 actual_score: '65' -> '62'
* A04 actual_score: '86' -> '83'
* A04 in_next_session_top20: 'True' -> 'False'
* A05 actual_score: '72' -> '69'
* A07 in_clean_top20: 'False' -> 'True'
* A07 in_next_session_top20: 'False' -> 'True'

No new false positive appeared among the benchmark items. A01 (Sprinto) would now be held for review instead of ignored if it were collected (weak alias, score 28 / 38 if collected normally); it is still not in the database, so it is still a miss.

### Per-item view (before -> after)
| ID | Posting | Group | Identity | Actual ranking | Clean top-20 | If collected: rules say |
|---|---|---|---|---|---|---|
| A01 | Sprinto - Forward Deployment Engineer | A | no match (none) | not in database  -> not in database  | no -> no | low priority 0 -> needs review 28 |
| A02 | Sprinto - Revenue Operations Analyst - Customer  | A | confirmed exact (scoped ats key) | confirmed eligible 65 -> confirmed eligible 62 | no -> no | confirmed eligible 57 -> confirmed eligible 57 |
| A03 | Prismforce - Associate Product Manager | A | no match (none) | not in database  -> not in database  | no -> no | confirmed eligible 70 -> confirmed eligible 70 |
| A04 | Apna - Product Operations Associate | A | probable review (strong identifier unscoped) | confirmed eligible 86 -> confirmed eligible 83 | yes -> yes | confirmed eligible 75 -> confirmed eligible 75 |
| A05 | Sarvam AI - Deployment Strategist | A | probable review (strong identifier unscoped) | confirmed eligible 72 -> confirmed eligible 69 | no -> no | confirmed eligible 60 -> confirmed eligible 60 |
| A06 | Prodigal - AI Deployment Lead (Bengaluru) | A | confirmed exact (scoped ats key) | needs review 69 -> needs review 69 | no -> no | needs review 48 -> needs review 48 |
| A07 | Prodigal - Strategy and Operations Analyst | A | confirmed exact (scoped ats key) | confirmed eligible 82 -> confirmed eligible 82 | no -> yes | confirmed eligible 63 -> confirmed eligible 63 |
| A08 | Emergent Labs - Forward Deployed Engineer (FDE) | A | confirmed exact (scoped ats key) | confirmed eligible 70 -> confirmed eligible 70 | no -> no | confirmed eligible 60 -> confirmed eligible 60 |
| A09 | Libra AI - Associate Product Manager (AI) | A | no match (none) | not in database  -> not in database  | no -> no | confirmed eligible 70 -> confirmed eligible 70 |
| A10 | Hevo Data - Strategy & Ops (Founder's Office) | A | confirmed exact (scoped ats key) | confirmed eligible 70 -> confirmed eligible 70 | no -> no | confirmed eligible 70 -> confirmed eligible 70 |
| A11 | Turing - Product Manager | A | confirmed exact (scoped ats key) | needs review 36 -> needs review 36 | no -> no | needs review 36 -> needs review 36 |
| B01 | Gushwork - Chief of Staff | B | confirmed exact (scoped ats key) | confirmed eligible 70 -> confirmed eligible 70 | no -> no | needs review 35 -> needs review 35 |
| B02 | Turing - Forward-Deployed AI Engineer - Enterpr | B | confirmed exact (scoped ats key) | confirmed eligible 70 -> confirmed eligible 70 | no -> no | needs review 35 -> needs review 35 |
| B03 | Emergent Labs - Finance Manager | B | confirmed exact (scoped ats key) | needs review 38 -> needs review 38 | no -> no | low priority 3 -> low priority 3 |

### Negative controls
| ID | Posting | Rules say |
|---|---|---|
| N01 | Meltplan - Senior Full Stack Engineer - Frontend Fo | excluded:NON TARGET ROLE -> excluded:NON TARGET ROLE |
| N02 | Meltplan - UI Engineering Lead | low priority -> low priority |
| N03 | Emergent Labs - Motion Designer | excluded:NON TARGET ROLE -> excluded:NON TARGET ROLE |
| N04 | Emergent Labs - Senior Software Engineer - Backend | excluded:NON TARGET ROLE -> excluded:NON TARGET ROLE |
| N05 | Emergent Labs - Legal Counsel | excluded:NON TARGET ROLE -> excluded:NON TARGET ROLE |

## Effect on the whole frozen database (29,959 jobs; `scripts/day14_rules_impact.py`)
* Classification: low priority 10316, needs review 3846, exploration 1959, excluded 13589, confirmed eligible 249 -> low priority 10328, needs review 3835, exploration 1959, excluded 13588, confirmed eligible 249.
* needs_review -> low_priority: 16 jobs
* low_priority -> needs_review: 4 jobs
* excluded -> needs_review: 1 jobs
* Score changes: -3 535, 0 15829, 8 1, 16 1, 18 1, 20 1, 22 1, 38 1 (the -3 are board-only jobs under the cap; the positive values are the nine forward-deployment jobs).
* Average qualified score: feed-only 67.1 -> 67.1; board-only 72.2 -> 69.2 (the gap between them fell from 5.1 to 2.1 points).
* Score-ranked top 20 (qualified only, cap 2 per employer): cutoff 83 -> 82; 13 of 20 unchanged. Composition by sourcing board-only 14, feed+board 6 -> feed+board 13, board-only 7; by source inv peakxv 5, feashliaa 6, inv accel 10, inv three one4 4, inv lightspeed 5 -> feashliaa 13, inv accel 11, inv peakxv 5, inv three one4 3, inv lightspeed 5.
  Entered: Meesho - Deputy Manager - Business Finance (82); celonis - Chief Of Staff (82); ethoslife - CX Product Operations Analyst (82); fictiv - Financial Analyst - FP&A-US shift (82); anthropic - Applied AI Architect (82); anthropic - Applied AI Architect, Partnerships (82); ethoslife - Strategy & Operations Manager (82). Left: Google - Technical Program Manager, AI Products (84); Lokal - Product Analyst II (84); Wibmo - Product Strategy & Operations (Bengalu (84); BestDoc - Associate Product Manager (83); Brightmoney - APM/PM Frontend (83); BestDoc - Associate Product Manager (83); Niyo - Associate Product Manager (83).
  This is a different view from the benchmark's clean top 20 (which includes the uncertain lane), so the cutoffs differ.

### The nine jobs touched by the alias (none became qualified; all stay subject to location and experience rules)
| Company | Title | Location class | Class change |
|---|---|---|---|
| nice | Forward Deployment Engineer | foreign_remote | excluded -> excluded |
| picarroinc | Forward Deployment Engineer - Oil, Gas,  | foreign_remote | excluded -> excluded |
| weekdayworks | Forward Deployment Engineer | bengaluru | low_priority -> needs_review |
| rhombuspower | Assistant Forward Deployment Data Engine | india_other_city | excluded -> needs_review |
| aptiagroup | Forward Deployment Engineer | india_country_only | low_priority -> low_priority |
| ryansg | AI Forward Deployment Engineer – Agentic | india_other_city | low_priority -> needs_review |
| sambanovasystems | Forward Deployment Engineer | bengaluru | low_priority -> needs_review |
| sambanovasystems | Forward Deployment Engineer | foreign_remote | excluded -> excluded |
| jazzx-ai | Staff Forward Deployment Engineer | bengaluru | low_priority -> needs_review |

## C. Not applied (counterfactuals only)
| Proposal | Result on stored data |
|---|---|
| Drop `lead` from the senior title terms | 15 jobs to qualified, 200 low-priority jobs lifted: **rejected by policy**; the precedence rule above is used instead |
| Use saved Feashliaa `skill_level` / `is_recruiter` | qualified 249 to 220; right for B01 and B03, wrong for A06 and A11, misses B02: not adopted (the policy forbids a noisy label overriding employer evidence) |
| Also use saved Getro `seniority` | qualified 249 to 211: coarse, not adopted |

## Reading these numbers honestly
* The ranking changes moved scores, not discovery. Recall (6 of 11) is unchanged because no source was run.
* The top-20 effect on A07 is a side effect of lowering board-only jobs by 3 points, not evidence that A07 is better; the benchmark is far too small to say the cap improves overall quality.
* Timing and availability limits stand: every collection predates the fixture by 16-25 hours, posting dates are mostly unknown, and no employer-open check was made.
