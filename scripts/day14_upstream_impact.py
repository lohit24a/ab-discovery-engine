"""Day 14: READ-ONLY. What would saved source attributes (Feashliaa skill_level / is_recruiter, Getro seniority) change in the ranking? Defaults are NOT changed."""
import collections
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from discovery import database as db  # noqa: E402
from discovery import profile as prof  # noqa: E402
from discovery import shortlist as sl_mod  # noqa: E402
from discovery import upstream_attrs as ua  # noqa: E402
from discovery.ranking_preview import rank_all  # noqa: E402

p, r = prof.load_profile(), prof.load_ranking()
conn = db.connect(db.resolve_db_path(db.PROJECT_ROOT), readonly=True)
fz, bd = ua.load_feashliaa_attributes(conn), ua.load_board_attributes()
print("attributes available offline: feashliaa", len(fz), "| investor boards", len(bd), "| combined", len(ua.merge(fz, bd)))
print("board levels:", dict(collections.Counter(v["source_level"] for v in bd.values())))
for label, up in (("baseline (stored fields only)", None), ("+ Feashliaa attrs", fz), ("+ Feashliaa + board seniority", ua.merge(fz, bd))):
    ranked = rank_all(conn, p, r, with_hints=False, upstream=up)
    js = ranked[2]
    cls = collections.Counter(x["classification"] for x in js)
    top = sl_mod.build(conn, p, r, history={}, new_session=True, ranked=ranked)
    print(f"\n{label}: qualified {cls['confirmed_eligible']} | needs_review {cls['needs_review']} | top-20 cutoff {top.jobs[-1]['score']} | top-20 sources {dict(collections.Counter(s for x in top.jobs for s in x['sources']))}")
    by = {x["id"]: x for x in js}
    if up is None:
        base = by
    else:
        moved = [(base[k]["classification"], by[k]["classification"]) for k in by if base[k]["classification"] != by[k]["classification"]]
        print("  classification changes vs baseline:", dict(collections.Counter(moved)))
        down = [by[k] for k in by if base[k]["classification"] == "confirmed_eligible" and by[k]["classification"] != "confirmed_eligible"]
        print("  qualified -> held examples:", [(x["company"], x["title"][:40], x["reason_codes"][-3:]) for x in down[:6]])
    for row in csv.DictReader(open(ROOT / "exports/day14_baseline_day13_results.csv", encoding="utf-8")):
        if row["canonical_job_ids"] and ";" not in row["canonical_job_ids"] and row["group"] != "N_negative_control":
            x = by[row["canonical_job_ids"]]
            print(f"    {row['benchmark_id']} {x['company'][:12]:<12} {x['classification']:<19} {x['score']:>3} experience={x['experience']:<12} src_level={[c for c in x['reason_codes'] if c.startswith('SRC_LEVEL')]}")
