"""Day 14: READ-ONLY counterfactuals for rule changes that need the operator's approval. NOTHING is applied: the approved YAML files are untouched and each
variant lives only in memory. Prints what each proposal would change on the stored data and on the frozen Day 13 items."""
import collections
import copy
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from discovery import benchmark as bm  # noqa: E402
from discovery import database as db  # noqa: E402
from discovery import profile as prof  # noqa: E402
from discovery import shortlist as sl_mod  # noqa: E402
from discovery.ranking_preview import rank_all  # noqa: E402

base_p, rk = prof.load_profile(), prof.load_ranking()
conn = db.connect(db.resolve_db_path(db.PROJECT_ROOT), readonly=True)
rows = bm.load_fixture(ROOT / "tests/fixtures/benchmark_day13_independent_2026.csv")


def variant(edit):
    raw = copy.deepcopy(base_p.raw)
    edit(raw)
    return prof.build_profile(raw)


def add_alias(raw):
    raw["role_families"]["forward_deployed_solutions"]["strong_aliases"].append("forward deployment")


def drop_lead(raw):
    raw["experience"]["senior_title_terms"] = [t for t in raw["experience"]["senior_title_terms"] if t != "lead"]


VARIANTS = {"approved (baseline)": base_p, "A. add alias 'forward deployment'": variant(add_alias), "B. 'lead' no longer a senior title term": variant(drop_lead),
            "A+B": variant(lambda raw: (add_alias(raw), drop_lead(raw)))}
base_res = None
for name, p in VARIANTS.items():
    ranked = rank_all(conn, p, rk, with_hints=False)
    js = ranked[2]
    by = {x["id"]: x for x in js}
    cls = collections.Counter(x["classification"] for x in js)
    top = sl_mod.build(conn, p, rk, history={}, new_session=True, ranked=ranked)
    print(f"\n== {name}: qualified {cls['confirmed_eligible']} | review {cls['needs_review']} | low {cls['low_priority']} | excluded {cls['excluded']} | top-20 cutoff {top.jobs[-1]['score']}")
    if base_res is None:
        base_res = by
    else:
        ch = collections.Counter((base_res[k]["classification"], by[k]["classification"]) for k in by if base_res[k]["classification"] != by[k]["classification"])
        print("   classification changes:", dict(ch))
        up = [by[k] for k in by if base_res[k]["classification"] != by[k]["classification"] and by[k]["classification"] in ("confirmed_eligible", "needs_review")]
        print("   examples:", [(x["company"][:14], x["title"][:40], x["classification"], x["score"]) for x in up[:6]])
    for r in rows:
        if r["benchmark_id"] in ("A01", "A06", "A08", "B01", "B02"):
            hyp = bm.hypothetical(r, p, rk, ranked[4])
            print(f"   {r['benchmark_id']} {r['employer']:<14} hypothetical: {hyp['classification']:<18} {hyp['score']!s:>4} family={hyp['family']} exp={hyp['experience']}")
