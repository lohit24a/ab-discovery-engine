"""Day 14: READ-ONLY impact analysis for the rule changes the benchmark suggests. Nothing is changed; this only counts what each proposal would touch."""
import collections
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from discovery import database as db  # noqa: E402
from discovery import profile as prof  # noqa: E402
from discovery.ranking_preview import rank_all  # noqa: E402

p, r = prof.load_profile(), prof.load_ranking()
conn = db.connect(db.resolve_db_path(db.PROJECT_ROOT), readonly=True)
jobs, cos, js, cs, as_of = rank_all(conn, p, r, with_hints=False)
live = [x for x in js if x["classification"] != "excluded"]

print("== 1. alias proposal: 'forward deployment' ==")
pat = re.compile(r"\bforward[- ]deploy(ment|ed|ing)?\b", re.I)
hits = [x for x in js if pat.search(x["title"])]
print("titles containing a forward-deploy* variant:", len(hits), dict(collections.Counter(pat.search(x["title"]).group(0).lower() for x in hits)))
for x in [x for x in hits if "deployment" in x["title"].lower()][:12]:
    print("   ", x["classification"], x["score"], "|", x["company"], "|", x["title"], "|", x["family"])
generic = [x for x in js if re.search(r"\bdeployment engineer\b|\bdeployment (manager|specialist|lead)\b", x["title"], re.I)]
print("generic 'deployment engineer/manager/specialist/lead' titles (NOT proposed; infra/release collateral):", len(generic))
for x in generic[:10]:
    print("   ", x["classification"], x["score"], "|", x["company"], "|", x["title"])

print("\n== 2. 'Lead' as the only senior term ==")
lead_only = []
for x in live:
    t = x["title"].lower()
    if "TITLE_SENIOR" in x["reason_codes"] and re.search(r"\blead\b", t) and not re.search(r"\b(senior|sr|principal|staff|head|director|avp|vp|associate director)\b", t):
        lead_only.append(x)
print("not-excluded jobs whose senior flag comes only from 'lead':", len(lead_only), "| strong role + Bengaluru-confirmed:", sum(1 for x in lead_only if x["role_strength"] in ("strong", "weak_corroborated") and x["location_eligibility"] == "confirmed"))
for x in [x for x in lead_only if x["role_strength"] == "strong" and x["location_eligibility"] == "confirmed"][:12]:
    print("   ", x["classification"], x["score"], "|", x["company"], "|", x["title"])

print("\n== 3. unknown experience among qualified ==")
qual = [x for x in js if x["classification"] == "confirmed_eligible"]
print("qualified:", len(qual), "| experience unknown:", sum(1 for x in qual if x["experience"] == "unknown"), "| early_career:", sum(1 for x in qual if x["experience"] == "early_career"),
      "| with a stated-years code:", sum(1 for x in qual if any(c.startswith("EXP_STATED") for c in x["reason_codes"])))

print("\n== 4. investor-board corroboration effect on qualified scores ==")
inv = [x for x in qual if any(s.startswith("inv_") for s in x["sources"])]
feed = [x for x in qual if x["sources"] == ["feashliaa"]]
avg = lambda xs: round(sum(x["score"] for x in xs) / len(xs), 1) if xs else None
print("investor-board qualified:", len(inv), "avg", avg(inv), "| feed-only qualified:", len(feed), "avg", avg(feed))
print("points available to investor-board listings and not to feed-only: company_investor_listed +3, provider-reported age -6..+6 (feed-only: none)")
print("posting-date coverage among qualified:", sum(1 for x in qual if x["posting_date"]), "of", len(qual))
