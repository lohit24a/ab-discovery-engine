"""Day 14: why did the Day 13 benchmark jobs rank where they did? READ-ONLY. Prints component breakdowns against the clean top-20."""
import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from discovery import benchmark as bm  # noqa: E402
from discovery import database as db  # noqa: E402
from discovery import profile as prof  # noqa: E402
from discovery import shortlist as sl_mod  # noqa: E402
from discovery.ranking_preview import rank_all  # noqa: E402

p, r = prof.load_profile(), prof.load_ranking()
conn = db.connect(db.resolve_db_path(db.PROJECT_ROOT), readonly=True)
ranked = rank_all(conn, p, r, with_hints=False)
jobs, cos, js, cs, as_of = ranked
by_id = {x["id"]: x for x in js}
clean = sl_mod.build(conn, p, r, history={}, new_session=True, ranked=ranked)
top = clean.jobs
cut = top[-1]["score"]
qual = sorted((x for x in js if x["classification"] == "confirmed_eligible"), key=lambda x: (-x["score"], x["id"]))
pos_of = {x["id"]: i + 1 for i, x in enumerate(qual)}
print(f"qualified jobs: {len(qual)}; clean top-20 cutoff score {cut}; top-20 scores {[x['score'] for x in top]}")
emp_in_top = collections.Counter(x["employer_key"] for x in top)
print("employers in top-20 (cap 2):", {k: v for k, v in emp_in_top.items() if v >= 2})
print("distribution of qualified scores:", dict(sorted(collections.Counter(x["score"] // 5 * 5 for x in qual).items(), reverse=True)))


def comps(x):
    return {k: v for k, v in x["components"].items() if v}


print("\n== top-20 component profile (what a typical top-20 record has) ==")
agg = collections.Counter()
for x in top:
    for k, v in x["components"].items():
        agg[k] += v
print({k: round(v / len(top), 1) for k, v in agg.items()})
print("source mix in top-20:", dict(collections.Counter(s for x in top for s in x["sources"])))
print("top-20 with provider-reported date:", sum(1 for x in top if x["posting_date"]), "of", len(top), "| with CO_ALSO_STREAM_B:", sum(1 for x in top if "CO_ALSO_STREAM_B" in x["reason_codes"]),
      "| in investor boards:", sum(1 for x in top if any(s.startswith("inv_") for s in x["sources"])))
print("qualified overall: share with provider-reported date", round(sum(1 for x in qual if x["posting_date"]) / len(qual), 2), "| ATS-feed-only", round(sum(1 for x in qual if x["sources"] == ["feashliaa"]) / len(qual), 2))
print("qualified feashliaa-only median score", sorted(x["score"] for x in qual if x["sources"] == ["feashliaa"])[len([1 for x in qual if x["sources"] == ["feashliaa"]]) // 2],
      "| investor-board median", sorted(x["score"] for x in qual if any(s.startswith("inv_") for s in x["sources"]))[len([1 for x in qual if any(s.startswith("inv_") for s in x["sources"])]) // 2])

print("\n== benchmark jobs ==")
rows = list(csv.DictReader(open(ROOT / "exports/day14_baseline_day13_results.csv", encoding="utf-8")))
deferred = {d["id"]: d["why"] for d in sl_mod.build(conn, p, r, history={}, new_session=True, ranked=ranked).__dict__.get("dropped", [])}
for row in rows:
    if row["group"] != "A_positive" or not row["canonical_job_ids"] or ";" in row["canonical_job_ids"]:
        continue
    jid = row["canonical_job_ids"]
    x = by_id[jid]
    better = [q for q in qual if q["score"] > x["score"]][:0]
    print(f"\n{row['benchmark_id']} {row['employer']} | {row['exact_title']} | {x['classification']} score {x['score']} rank {pos_of.get(jid, '-')} of {len(qual)} qualified | gap to cutoff {x['score'] - cut:+d}")
    print("   components:", comps(x))
    print("   codes:", ";".join(x["reason_codes"]))
    print("   missing:", x["missing_evidence"], "| employer count in top20:", emp_in_top.get(x["employer_key"], 0))
    # what a top-20 record of the same family has more of
    rivals = [t for t in top if t["family"] == x["family"]][:3]
    for t in rivals:
        diff = {k: t["components"].get(k, 0) - x["components"].get(k, 0) for k in set(t["components"]) | set(x["components"]) if t["components"].get(k, 0) != x["components"].get(k, 0)}
        print(f"   vs top-20 '{t['title'][:36]}' ({t['company']}, {t['score']}): component differences {diff}")
