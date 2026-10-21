"""Day 14: READ-ONLY stratified descriptive sample (no labels are invented). Prints counts and 2 examples per stratum."""
import collections
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from discovery import database as db, profile as prof  # noqa: E402
from discovery.ranking_preview import rank_all  # noqa: E402

p, r = prof.load_profile(), prof.load_ranking()
conn = db.connect(db.resolve_db_path(db.PROJECT_ROOT), readonly=True)
jobs, cos, js, cs, as_of = rank_all(conn, p, r, with_hints=False)
rng = random.Random(14)
strata = {
    "top-ranked (score >= 85)": lambda x: x["classification"] == "confirmed_eligible" and x["score"] >= 85,
    "borderline qualified (45-60)": lambda x: x["classification"] == "confirmed_eligible" and x["score"] < 60,
    "weak-alias review (generic title)": lambda x: x["classification"] == "needs_review" and x["role_strength"] == "weak",
    "exploration (no family)": lambda x: x["classification"] == "exploration",
    "location incompatible": lambda x: x["hard_exclusion"] == "LOCATION_INCOMPATIBLE",
    "non-target role": lambda x: x["hard_exclusion"] == "NON_TARGET_ROLE",
    "provider-retained / historical": lambda x: x["availability"] == "historical" and x["classification"] != "excluded",
    "YC job pages": lambda x: "yc_jobs" in x["sources"],
    "India-remote explicit": lambda x: x["location_category"] == "india_remote_explicit" and x["classification"] != "excluded",
    "senior-flagged but strong role": lambda x: x["experience"] in ("senior", "executive") and x["role_strength"] == "strong" and x["classification"] != "excluded",
}
for name, f in strata.items():
    pool = sorted([x for x in js if f(x)], key=lambda x: x["id"])
    rng.shuffle(pool)
    miss = collections.Counter(m for x in pool for m in x["missing_evidence"] if m in ("NO_DESCRIPTION", "EXPERIENCE_UNKNOWN", "POSTING_DATE_UNKNOWN"))
    print(f"\n{name}: {len(pool)} jobs | missing evidence {dict(miss)} | families {dict(collections.Counter(x['family'] for x in pool).most_common(3))}")
    for x in pool[:2]:
        print(f"    {x['classification']:<18} {x['score']!s:>4} | {x['company'][:22]:<22} | {x['title'][:52]:<52} | {x['location_category']}")
cc = collections.Counter(c["classification"] for c in cs.values())
nojobs = [c for c in cs.values() if c["n_jobs"] == 0 and c["classification"] == "confirmed_eligible"]
print(f"\ncompanies: {dict(cc)} | qualified companies with no observed jobs: {len(nojobs)}")
