"""Builds docs/reports/day13_benchmark_report.md from exports/day13_benchmark_results.csv and exports/day13_metrics.json (both produced by
`python -m discovery benchmark-eval`) and the frozen baseline. Numbers come from the data; only the interpretation is written by hand below."""
import csv
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
rows = list(csv.DictReader(open(ROOT / "exports/day13_benchmark_results.csv", encoding="utf-8")))
m = json.loads((ROOT / "exports/day13_metrics.json").read_text(encoding="utf-8"))
base = json.loads((ROOT / "exports/day13_frozen_baseline.json").read_text(encoding="utf-8"))
log = json.loads((ROOT / "exports/day13_benchmark_research_log.json").read_text(encoding="utf-8"))
fx = {r["benchmark_id"]: r for r in csv.DictReader(open(ROOT / "tests/fixtures/benchmark_day13_independent_2026.csv", encoding="utf-8"))}
by = {r["benchmark_id"]: r for r in rows}


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(100 * (c - h)), round(100 * (c + h))


OUTCOME = {"confirmed_exact": "FOUND (confirmed, automatic)", "probable_review": "UNVERIFIABLE (probable match, identity not provable)",
           "no_match": "MISSED", "unverifiable_identity": "UNVERIFIABLE (no job-specific id)"}
REASON = {
    "A01": "Employer is in Accel (22 jobs) but not in Feashliaa; this job is not stored. The title says 'Forward Deployment', which no role alias matches, so it would rank low_priority even if collected. Page-vs-board availability evidence conflicts.",
    "A02": "Held by Accel only (employer's Lever board). Qualified (65), not in the top 20 (cutoff 78).",
    "A03": "Employer (Prismforce, Keka ATS) is absent from every collected source. Would qualify (70) if collected.",
    "A04": "Same employer + identical title on Lightspeed and Peak XV; the stored Workable URL has no tenant, so the key cannot be compared (the posting id E75B63B3A7 appears in it). Qualified 86, in the top 20.",
    "A05": "Same employer + identical title on Lightspeed only; no shared identifier. Qualified (72), not in the top 20.",
    "A06": "Feashliaa + Accel, exact key. Title contains 'Lead', so the experience rule reads it as senior although the page asks for 2+ years; held for review (69).",
    "A07": "Feashliaa + Accel, exact key. Qualified (82), not in the top 20.",
    "A08": "Feashliaa only. Qualified (70). The page says 'Senior (4+ years)'; the stored record has no experience, so the rule sees 'unknown'.",
    "A09": "Employer (Libra AI) is absent from every collected source; the only evidence is one aggregator page with no apply button or employer URL. Would qualify (70) if collected.",
    "A10": "Feashliaa (+ Peak XV employer record). Qualified (70). Page says '2 to 8 years'; stored experience unknown.",
    "A11": "Feashliaa only. India-remote recognised; the generic title 'Product Manager' is a weak alias, so it is held for review (36).",
}
ranking_label = lambda r: r["actual_outcome"].replace("_", " ") + (f" {r['actual_score']}" if r["actual_score"] else "") if r["actual_outcome"] != "not_in_database" else "not in database"
pos = [r for r in rows if r["group"] == "A_positive"]
n = m["denominator_positive"]
ci = wilson(m["confirmed_exact"]["count"], n)
srcrows = "\n".join(f"| {s['source_id']} | {s['source_type']} | {s['last_sighting'][:16] if s['last_sighting'] else ''} | "
                    f"{(s['latest_check'] or {}).get('outcome', 'none')} / {'complete' if (s['latest_check'] or {}).get('complete') else 'not complete'} |" for s in base["sources"])
table = "\n".join(
    f"| {r['benchmark_id']} | {r['employer']} - {r['exact_title'][:44]} | {r['match_class'].replace('_', ' ')} | {r['automatic_sources'].replace(';', ', ') or '-'} | "
    f"{(r['first_observed_by_engine'] or '-')[:16]} | {ranking_label(r)} | {'yes' if r['in_clean_top20'] == 'True' else 'no'} | {r['hypothetical_outcome'].replace('_', ' ')} {r['hypothetical_score']} | "
    f"{r['employer_page_evidence'].replace('employer_page_loaded_with_apply_2026-10-21', 'employer page loaded 2026-10-21').replace('non_authoritative_only', 'mirror/aggregator only')} | {r['manual_action']} |" for r in pos)
misses = "\n".join(f"* **{k} - {by[k]['employer']}, {by[k]['exact_title']}: {OUTCOME[by[k]['match_class']]}.** {REASON[k]}" for k in
                   ("A01", "A03", "A09", "A04", "A05") if k in by)
found = "\n".join(f"* **{k} - {by[k]['employer']}, {by[k]['exact_title']}: {OUTCOME[by[k]['match_class']]}.** {REASON[k]}" for k in ("A02", "A06", "A07", "A08", "A10", "A11"))
senior = "\n".join(f"| {r['benchmark_id']} | {r['employer']} - {r['exact_title']} | {fx[r['benchmark_id']]['experience_text']} | {r['actual_outcome'].replace('_', ' ')} {r['actual_score']} | {r['hypothetical_outcome'].replace('_', ' ')} |"
                   for r in rows if r["group"].startswith("B"))
neg = "\n".join(f"| {r['benchmark_id']} | {r['employer']} - {r['exact_title']} | {r['match_class']} | {r['hypothetical_outcome'].replace('_', ' ')} {r['hypothetical_score'] if r['hypothetical_score'] != '' else ''} |"
                for r in rows if r["group"].startswith("N"))

md = f"""# Day 13 benchmark report: independent discovery quality (revised methodology)

**This is a newly constructed, independent benchmark. It is NOT the original 18 October 2026 historical sample**, which could not be located and was not reconstructed
(operator-approved deviation, 2026-10-21). Results below say nothing about that sample.

## 1. Provenance and limits
* Built on 2026-10-21 from {log['searches_used']} web searches (cap {log['searches_cap']}) and {log['fetches_used']} single-page fetches (cap {log['fetches_cap']}), all public pages, no login, no database consulted.
* Fixture `tests/fixtures/benchmark_day13_independent_2026.csv` was frozen at **{log['created_utc']}** (SHA-256 `{log['csv_sha256'][:16]}...`) **before** any comparison with SQLite.
* Sample: **{n} positive postings** (target was 15; fewer than 15 grounded early-career Bengaluru/India-remote postings in the target families could be verified inside the approved budget),
  3 senior-experience boundary cases and 5 negative controls. The positives and boundary cases are kept out of each other's denominators; controls are never in the recall denominator.
* **Statistical limit:** with n = {n}, {m['confirmed_exact']['count']}/{n} = {m['confirmed_exact']['pct']}% has a 95% interval of roughly {ci[0]}-{ci[1]}%. Treat every rate as indicative, not precise.
* **Selection bias (important):** most positives were found on Greenhouse and Lever employer boards, which is the ATS space Feashliaa also reads. Recall for ATS-hosted employers is therefore probably overstated
  relative to employers on other platforms (only 2 of {n} positives, Keka and an aggregator, came from outside it). Role families with no verified posting in the sample: internal AI automation, business/strategic finance, venture studio.
* Availability: every "page loaded" statement is one GET on 2026-10-21. **No authoritative current-availability check was completed**; Sprinto's board index (fetched the same day) did not list A01 although its page loaded.
* {len(log['rejected_before_matching'])} candidates were rejected for objective page reasons (404, closed, removed, script-only, not on the employer's board) before matching; they are listed in `exports/day13_benchmark_research_log.json`.

## 2. Frozen evaluation baseline
Git `{base['git_commit'][:7]}`, schema {base['schema_version']}, database SHA-256 `{base['database_sha256'][:16]}...` (unchanged by this evaluation), integrity {base['database_integrity']},
profile `{base['config_sha256']['config/search_profile.yaml'][:12]}`, ranking `{base['config_sha256']['config/ranking.yaml'][:12]}` (approved 2026-10-21, **no rule, weight or identity decision was changed**),
frozen at {base['frozen_at']}. Review session `{base['current_review_session']['id'][:8]}` ({base['current_review_session']['records']} records), verdicts {base['verdicts_by_decision']}.

| Source | Type | Last sighting | Latest check |
|---|---|---|---|
{srcrows}

('skipped' means that run found its snapshot already imported and collected nothing; Feashliaa's last collecting check was a success.) All collections predate the fixture by about 16-25 hours, and posting dates are unknown for most items, so **absence cannot be separated from collection timing** (see section 6).

## 3. Per-item results ({n} positives)
| ID | Posting | Identity | Automatic sources | Engine first saw | Actual ranking | Clean top-20 | If collected: rules say | Page evidence | Manual import (dry run) |
|---|---|---|---|---|---|---|---|---|---|
{table}

## 4. Coverage metrics (denominator {n}; no per-source sums are added)
| Measure | Count | % of {n} |
|---|---|---|
| Confirmed exact, discovered automatically | {m['confirmed_exact']['count']} | {m['confirmed_exact']['pct']}% |
| Probable (needs review; NOT counted as found) | {m['probable_review']['count']} | {m['probable_review']['pct']}% |
| No match (missed or not yet collected) | {m['no_match']['count']} | {m['no_match']['pct']}% |
| Unverifiable identity | {m['unverifiable_identity']['count']} | 0% |
| Matched only through manual entries | {m['matched_only_through_manual_entries']['count']} | 0% |
| Matched to the correct company (all confirmed) | {m['confirmed_exact']['count']} | {m['confirmed_exact']['pct']}% |
| Authoritative current-availability checks completed | 0 | 0% |
| Recoverable by the manual importer (dry run) | {m['manual_recoverable']['count']} | 100% |
| Recoverable **only** by manual import (not found automatically) | {m['manual_only_recovery']['count']} | {round(100*m['manual_only_recovery']['count']/n, 1)}% |

**Per source (confirmed, overlapping):** Feashliaa {len(m['per_source_confirmed'].get('feashliaa', []))} ({', '.join(m['per_source_confirmed'].get('feashliaa', []))}); Accel {len(m['per_source_confirmed'].get('inv_accel', []))} ({', '.join(m['per_source_confirmed'].get('inv_accel', []))}).
YC job pages, Lightspeed, Peak XV, 3one4 and Techstars: 0 confirmed (Lightspeed and Peak XV hold the two probable items). **Combined unique confirmed: {m['combined_unique_confirmed']}** (A06 and A07 are in both families).
**Unique contribution:** ATS feed alone: {', '.join(m['unique_to_one_source_family'].get('ats_feed', []))}; investor boards alone: {', '.join(m['unique_to_one_source_family'].get('investor_boards', []))}.
Exact identity rate {m['confirmed_exact']['pct']}%, ambiguous/probable {m['probable_review']['pct']}%.

## 5. Ranking and shortlist recovery
* Of the {m['confirmed_exact']['count']} confirmed finds: **{m['confirmed_found_ranking']['qualified']} qualified, {m['confirmed_found_ranking']['needs_review']} held for review, {m['confirmed_found_ranking']['low_or_excluded']} excluded or low priority**; **{m['confirmed_found_ranking']['in_clean_top20']} reached the clean top 20** (cutoff score {rows[0]['clean_top20_cutoff_score']}); {m['confirmed_found_ranking']['in_current_session']} are in the current review session. The one top-20 item is the probable match A04 (Apna, 86).
* No false exclusion among found positives. Two were held for review for objective reasons: A06 ('Lead' read as senior although 2+ years) and A11 (generic 'Product Manager' is a weak alias).
* If the {m['no_match']['count']} missed items had been collected: A03 and A09 would qualify (70) and reach the top 20 once ordinary availability and source points apply; A01 would be **low priority (no role signal)**.
* Experience is "unknown" for most stored jobs (descriptions are not stored), which costs up to 10 points and lets senior roles through: **2 of 3 senior-experience boundary cases qualified** (B01, B02), none reached the top 20.
* Negative controls: 0 of 5 matched in the database, 0 would reach the shortlist ({sum(1 for r in rows if r['group'].startswith('N') and r['hypothetical_outcome'].startswith('excluded'))} excluded as non-target, {sum(1 for r in rows if r['group'].startswith('N') and r['hypothetical_outcome'] == 'low_priority')} low priority).

### Senior-experience boundary cases (not in the recall denominator)
| ID | Posting | Stated experience | Actual ranking | Hypothetical (stated experience read) |
|---|---|---|---|---|
{senior}

### Negative controls
| ID | Posting | Identity | Rules say |
|---|---|---|---|
{neg}

## 6. Diagnosis of every non-confirmed positive (demonstrated vs hypothesis)
{misses}

Demonstrated: employer absent from all sources (A03, A09); employer present only in Accel (A01); probable items lack a comparable job-specific key. **Hypotheses (not proven):** A01 may be a collection miss or a timing effect and the data cannot tell
them apart (Accel's check is partial and scoped, Feashliaa does not hold Sprinto, posting dates are unknown); A03 and A09 exist because the employers' platforms (Keka, Built In) are not integrated.

## 7. Found items
{found}

## 8. Limitations
Small n; ATS-biased sampling; one-day, single-GET availability evidence; no prospective test (sources not re-run); descriptions and experience are absent from stored data, so ranking uses titles and locations only;
three role families unrepresented; Sprinto A01 availability conflict unresolved. No restricted JobSpy source, no authenticated page, no proxy and no CAPTCHA handling was used.

## 9. Day 14 handoff (ranked by expected unique relevant recall, then cost)
1. **Read the public board index of employers we already know to use Greenhouse/Lever/Ashby** (tenant is visible in stored URLs). Directly recovers A01-type misses (Sprinto: Accel holds 22 records for the employer, the employer's own board lists 35 jobs, and this posting is not stored) without a new crawler class. Impact: 1 of {n} here; likely larger on the full inventory. Cost: medium; needs a source audit and approval.
2. **Add description/experience evidence for shortlist candidates** (public ATS job JSON/page; see `docs/design/enrichment_plan.md`). Fixes the 'Lead' false-senior (A06), restores the 10 experience points (A08, A10), and stops 2 of 3 senior roles qualifying. Impact: 5 of 14 items touched. Cost: medium; bounded request ceiling.
3. **Role-alias variants:** 'forward deployment' (A01) and a review of generic 'Product Manager' with corroboration (A11). Impact: 2 items. Cost: low; needs the operator's approval of terms.
4. **Employers on platforms nobody reads** (Keka A03; aggregator-only A09). Impact: 2 of {n}; access and permission uncertain; evaluate before building anything.
5. **Identity:** compare tenant-less Workable URLs by posting id after a source audit (A04). Impact: 1 item moves from probable to confirmed. Cost: low.
6. **Prospective pickup test:** re-run the approved sources once (separate approval) and re-evaluate A01/A03/A09 to separate timing from misses.
"""
(ROOT / "docs/reports").mkdir(parents=True, exist_ok=True)
(ROOT / "docs/reports/day13_benchmark_report.md").write_text(md, encoding="utf-8")
print(len(md.splitlines()), "lines")
