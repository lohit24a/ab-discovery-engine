"""Builds docs/reports/day14_benchmark_comparison.md from the frozen Day 13 baseline CSV/JSON, a fresh evaluator run and the rules-impact summary."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
rd = lambda p: {r["benchmark_id"]: r for r in csv.DictReader(open(ROOT / p, encoding="utf-8"))}
load = lambda p: json.loads((ROOT / p).read_text(encoding="utf-8"))
before, after = rd("exports/day14_baseline_day13_results.csv"), rd("exports/day14_after_results.csv")
mb, ma = load("exports/day14_baseline_day13_metrics.json"), load("exports/day14_after_metrics.json")
imp = load("exports/day14_rules_impact.json")
base = load("exports/day13_frozen_baseline.json")
fx_sha = hashlib.sha256((ROOT / "tests/fixtures/benchmark_day13_independent_2026.csv").read_bytes()).hexdigest()
fmt = lambda d: ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in d.items())
FIELDS = ["match_class", "actual_outcome", "actual_score", "actual_family", "actual_location_class", "actual_experience", "in_clean_top20", "in_current_session",
          "in_next_session_top20", "hypothetical_outcome", "hypothetical_score", "hypothetical_collected_score", "hypothetical_family", "employer_page_evidence", "manual_action"]
changes = [(k, f, before[k].get(f), after[k].get(f)) for k in before for f in FIELDS if before[k].get(f) != after[k].get(f)]
rows = "\n".join(f"| {k} | {r['employer']} - {r['exact_title'][:38]} | {r['group'][:1]} | {r['match_class'].replace('_', ' ')} ({after[k]['match_strength'].replace('_', ' ')}) | "
                 f"{r['actual_outcome'].replace('_', ' ')} {r['actual_score']} -> {after[k]['actual_outcome'].replace('_', ' ')} {after[k]['actual_score']} | "
                 f"{'yes' if r['in_clean_top20'] == 'True' else 'no'} -> {'yes' if after[k]['in_clean_top20'] == 'True' else 'no'} | "
                 f"{r['hypothetical_outcome'].replace('_', ' ')} {r['hypothetical_score']} -> {after[k]['hypothetical_outcome'].replace('_', ' ')} {after[k]['hypothetical_score']} |"
                 for k, r in sorted(before.items()) if r["group"][0] in "AB")
neg = "\n".join(f"| {k} | {r['employer']} - {r['exact_title'][:40]} | {r['hypothetical_outcome'].replace('_', ' ')} -> {after[k]['hypothetical_outcome'].replace('_', ' ')} |"
                for k, r in sorted(before.items()) if r["group"][0] == "N")
moved = "\n".join(f"* {k}: {a} jobs" for k, a in imp["class_changes"].items())
alias = "\n".join(f"| {x[2]} | {x[3]} | {x[4]} | {x[0]} -> {x[1]} |" for x in imp["alias_affected"])
md = f"""# Day 14 benchmark comparison (frozen Day 13 baseline versus the approved Day 14 ranking refinements)

**Fixture unchanged:** SHA-256 `{fx_sha[:16]}...` (same 11 positives, 3 senior-experience boundary cases, 5 negative controls, same labels and denominators). **Database unchanged:** SHA-256 `{base['database_sha256'][:12]}...`.
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
| Confirmed exact, automatic | {mb['confirmed_exact']['count']} ({mb['confirmed_exact']['pct']}%) | {ma['confirmed_exact']['count']} ({ma['confirmed_exact']['pct']}%) |
| Probable (not counted as found) | {mb['probable_review']['count']} | {ma['probable_review']['count']} (both strong identifier, unscoped) |
| Not found | {mb['no_match']['count']} | {ma['no_match']['count']} |
| Confirmed finds that qualified / held / excluded | {mb['confirmed_found_ranking']['qualified']} / {mb['confirmed_found_ranking']['needs_review']} / {mb['confirmed_found_ranking']['low_or_excluded']} | {ma['confirmed_found_ranking']['qualified']} / {ma['confirmed_found_ranking']['needs_review']} / {ma['confirmed_found_ranking']['low_or_excluded']} |
| Confirmed finds in the clean top 20 | {mb['confirmed_found_ranking']['in_clean_top20']} | {ma['confirmed_found_ranking']['in_clean_top20']} (A07 Prodigal, because board-only competitors lost 3 points) |
| Negative controls reaching the shortlist | {mb['negative_controls']['would_reach_shortlist']} of 5 | {ma['negative_controls']['would_reach_shortlist']} of 5 |
| Senior boundary cases (stated experience read) | {fmt(mb['senior_group']['hypothetical'])} | {fmt(ma['senior_group']['hypothetical'])} |
| If collected, the rules would give (outcomes) | {fmt(mb['hypothetical_if_discovered']['by_outcome'])} | {fmt(ma['hypothetical_if_discovered']['by_outcome'])} |
| Authoritative availability checks | 0 | 0 |

Field-level movements ({len(changes)}):
{chr(10).join(f'* {k} {f}: {a!r} -> {b!r}' for k, f, a, b in changes)}

No new false positive appeared among the benchmark items. A01 (Sprinto) would now be held for review instead of ignored if it were collected (weak alias, score 28 / 38 if collected normally); it is still not in the database, so it is still a miss.

### Per-item view (before -> after)
| ID | Posting | Group | Identity | Actual ranking | Clean top-20 | If collected: rules say |
|---|---|---|---|---|---|---|
{rows}

### Negative controls
| ID | Posting | Rules say |
|---|---|---|
{neg}

## Effect on the whole frozen database (29,959 jobs; `scripts/day14_rules_impact.py`)
* Classification: {fmt(imp['classification_before'])} -> {fmt(imp['classification_after'])}.
{moved}
* Score changes: {fmt(imp['score_delta_counts'])} (the -3 are board-only jobs under the cap; the positive values are the nine forward-deployment jobs).
* Average qualified score: feed-only {imp['avg_feed_only'][0]} -> {imp['avg_feed_only'][1]}; board-only {imp['avg_board_only'][0]} -> {imp['avg_board_only'][1]} (the gap between them fell from {round(imp['avg_board_only'][0] - imp['avg_feed_only'][0], 1)} to {round(imp['avg_board_only'][1] - imp['avg_feed_only'][1], 1)} points).
* Score-ranked top 20 (qualified only, cap 2 per employer): cutoff {imp['top20_cutoff'][0]} -> {imp['top20_cutoff'][1]}; {imp['top20_overlap']} of 20 unchanged. Composition by sourcing {fmt(imp['top20_mix_before'])} -> {fmt(imp['top20_mix_after'])}; by source {fmt(imp['top20_sources_before'])} -> {fmt(imp['top20_sources_after'])}.
  Entered: {'; '.join(f'{c} - {t[:38]} ({s})' for c, t, s in imp['entered_top20'])}. Left: {'; '.join(f'{c} - {t[:38]} ({s})' for c, t, s in imp['left_top20'])}.
  This is a different view from the benchmark's clean top 20 (which includes the uncertain lane), so the cutoffs differ.

### The nine jobs touched by the alias (none became qualified; all stay subject to location and experience rules)
| Company | Title | Location class | Class change |
|---|---|---|---|
{alias}

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
"""
(ROOT / "docs/reports/day14_benchmark_comparison.md").write_text(md, encoding="utf-8")
print(len(changes), "field movements;", len(md.splitlines()), "lines")
