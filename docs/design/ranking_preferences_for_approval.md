# Ranking preferences awaiting your approval

**APPROVED 2026-10-21:** the operator approved the recommended choice for items 1-12 (including demoting "special projects" to a weak alias and adding "mechanical" and "ic design" to the different-job list). Both configs now say `approved_by_operator_2026-10-21`. The text below is the proposal as presented.
Counts are from the frozen production data (29,959 canonical jobs, 10,982 companies); examples are in
`exports/calibration/day12_review_first.md` (about 30 items, judgment columns blank).

## What the proposal does today

| Area | Proposed rule | Effect on the shortlist |
|---|---|---|
| Role families | The eight agreed families. A **strong** title alias (e.g. "forward deployed", "associate product manager", "business operations") qualifies; a **weak** alias (e.g. "analyst", "program manager") only holds the job for review unless a corroborating term appears. | 250 qualified jobs; 5,534 weak-alias jobs held |
| Negative terms | "Product marketing", "test automation" etc. cancel a match when they overlap the matched words. | Removes false matches such as "Associate Product Marketing Manager" |
| Different job | ~74 titles ("software engineer", "devops", "recruiter", "payroll"...) are hidden when **no** family matched. Kept in SQLite. | 3,777 hidden |
| Location | Qualified only for Bengaluru or explicit India-remote (incl. "US or India"). Other Indian city, country-only "India", worldwide, regional and plain "remote" are held, never confirmed. Foreign-only is hidden. | 431 strong-role jobs in other Indian cities are held; 468 plain-remote held |
| Seniority | Senior titles and a source-reported "senior" hold the job; executive titles get a soft penalty (not hidden); mid-level words are ignored. Missing experience is neutral. | 582 strong-role senior jobs held, 72 executive |
| Internships | Held for your decision. | 15 held |
| Recruiter-posted | Held (needs the cached Feashliaa flags; preview-only today). | 29 fewer qualified if enabled |
| Freshness | Only a provider-reported creation date earns an age bonus (investor boards). An aggregator's first-seen date earns none. | |
| Company interests | AI-native, vertical AI, fintech, enterprise software (generic, cannot qualify alone), startup signals; YC Inactive/Acquired held. | 299 qualified companies |
| Diversity | Max 2 per employer, generic (no source quotas). Accel is 111 of 250 qualified jobs. | |
| Exploration lane | Titles with no family but a signal word ("operations", "strategy", "analyst", "ai"...), score >= 14, max 10, never qualified, **not placed in the Sheet**. | 1,960 candidates; precision is low |
| Session slots | new 10, changed 3, unreviewed 4, uncertain 3; unused slots go to further qualified new jobs. Your own imports sort first. | ~20 jobs + 5 company-only |

## Decisions that change the shortlist (my recommendation first)

1. **Other Indian cities** (431 strong-role jobs): keep held (recommended), or qualify Hyderabad/Pune/Chennai/Gurugram/Mumbai as plausible-but-listed?
2. **Internships and apprentices**: hold (recommended) / allow / hide.
3. **Senior titles** (582): hold (recommended) / hide. Executive titles: soft penalty (recommended) / hide.
4. **Worldwide / "Any location" remote**: hold (recommended) / allow as qualified.
5. **AI/ML Engineer roles** (195 in exploration): exploration (recommended) / needs review / hide.
6. **Exploration lane**: keep out of the Sheet until you judge the sample (recommended) / publish up to 10.
7. **Weak aliases** ("analyst", "program manager" alone): hold (recommended) / hide.
8. **Role-term corrections I would make**: demote "special projects" from strong to weak (it matches "Mechanical Design Engineer, Special Projects"); add "mechanical", "hardware", "ic design" to the different-job list. Approve?
9. **Company interests**: confirm AI-native, vertical AI, fintech; keep "enterprise software" generic.
10. **Weights** (`ranking.yaml`): role 40/24/8, Bengaluru 20, India-remote 18, other city 4, employer-confirmed open 20, in-latest-check 6, verdict Yes +25. Accept as a starting point?
11. **Session slots** and the rule that hand-imported jobs sort first.
12. **Per-employer cap** of 2: keep (recommended) or change.

Any later change to a number or term needs the operator's approval.
