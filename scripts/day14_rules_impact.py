"""Day 14: before/after impact of the operator-approved refinements (weak 'forward deployment' alias, investor-membership cap, experience precedence)
on the frozen database. READ-ONLY. `before` is a state dump taken with the pre-change configuration; `after` is computed now."""
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from discovery import database as db  # noqa: E402
from discovery import identity, profile as prof, shortlist as sl_mod  # noqa: E402
from discovery.ranking_preview import rank_all  # noqa: E402
from discovery.scoring import select_shortlist  # noqa: E402

before = json.load(open(sys.argv[1], encoding="utf-8"))
p, r = prof.load_profile(), prof.load_ranking()
conn = db.connect(db.resolve_db_path(db.PROJECT_ROOT), readonly=True)
ranked = rank_all(conn, p, r, with_hints=False)
after = {x["id"]: x for x in ranked[2]}


def top20(state):
    rows = [{"id": k, "classification": v["classification"], "score": v["score"], "company": v["company"], "title": v["title"], "sources": v["sources"],
             "employer_key": identity.name_key(v["company"])} for k, v in state.items() if v["classification"] == "confirmed_eligible"]
    sel, _, _ = select_shortlist(rows, 20, r["shortlist"]["minimum_score_to_list_jobs"], r["shortlist"]["max_per_employer"])
    return sel


tb, ta = top20(before), top20(after)
cls = lambda s: dict(collections.Counter(v["classification"] for v in s.values()))
print("classification before:", cls(before))
print("classification after :", cls(after))
moved = [(before[k]["classification"], after[k]["classification"]) for k in after if before[k]["classification"] != after[k]["classification"]]
print("class changes:", dict(collections.Counter(moved)))
sc = [(after[k]["score"] or 0) - (before[k]["score"] or 0) for k in after if after[k]["score"] is not None and before[k]["score"] is not None]
print("score deltas:", dict(collections.Counter(sc)))
print("top-20 cutoff before/after:", tb[-1]["score"], ta[-1]["score"], "| overlap:", len({x["id"] for x in tb} & {x["id"] for x in ta}))
mix = lambda t: dict(collections.Counter("board-only" if all(s.startswith("inv_") for s in x["sources"]) else ("feed+board" if any(s.startswith("inv_") for s in x["sources"]) and "feashliaa" in x["sources"] else "feed/other") for x in t))
print("top-20 mix before:", mix(tb), "| after:", mix(ta))
print("top-20 sources before:", dict(collections.Counter(s for x in tb for s in x["sources"])))
print("top-20 sources after :", dict(collections.Counter(s for x in ta for s in x["sources"])))
new = [x for x in ta if x["id"] not in {y["id"] for y in tb}]
out = [x for x in tb if x["id"] not in {y["id"] for y in ta}]
print("entered top-20:", [(x["company"], x["title"][:34], x["score"]) for x in new])
print("left top-20   :", [(x["company"], x["title"][:34], x["score"]) for x in out])
q = lambda s: [v for v in s.values() if v["classification"] == "confirmed_eligible"]
avg = lambda xs: round(sum(v["score"] for v in xs) / len(xs), 1)
fe = lambda s: [v for v in q(s) if v["sources"] == ["feashliaa"]]
bo = lambda s: [v for v in q(s) if all(x.startswith("inv_") for x in v["sources"])]
print("qualified avg score: feed-only", avg(fe(before)), "->", avg(fe(after)), "| board-only", avg(bo(before)), "->", avg(bo(after)), "| gap feed-only vs all-board", round(avg(fe(before)) - avg(bo(before)), 1), "->", round(avg(fe(after)) - avg(bo(after)), 1))
fd = [(before[k]["classification"], after[k]["classification"], after[k]["company"], after[k]["title"][:40], after[k]["location_category"]) for k in after if "forward" in after[k]["title"].lower() and "deploy" in after[k]["title"].lower() and before[k]["classification"] != after[k]["classification"] or (before[k]["family"] != after[k]["family"])]
print("alias-affected jobs:", len(fd))
for x in fd: print("   ", x)
summary = {
    "classification_before": cls(before), "classification_after": cls(after), "class_changes": {f"{a} -> {b}": n for (a, b), n in collections.Counter(moved).items()},
    "score_delta_counts": {str(k): v for k, v in sorted(collections.Counter(sc).items())}, "top20_cutoff": [tb[-1]["score"], ta[-1]["score"]],
    "top20_overlap": len({x["id"] for x in tb} & {x["id"] for x in ta}), "top20_mix_before": mix(tb), "top20_mix_after": mix(ta),
    "top20_sources_before": dict(collections.Counter(s_ for x in tb for s_ in x["sources"])), "top20_sources_after": dict(collections.Counter(s_ for x in ta for s_ in x["sources"])),
    "entered_top20": [[x["company"], x["title"], x["score"]] for x in new], "left_top20": [[x["company"], x["title"], x["score"]] for x in out],
    "avg_feed_only": [avg(fe(before)), avg(fe(after))], "avg_board_only": [avg(bo(before)), avg(bo(after))], "alias_affected": [list(map(str, x)) for x in fd],
}
json.dump(summary, open(ROOT / "exports/day14_rules_impact.json", "w", encoding="utf-8"), indent=1)
