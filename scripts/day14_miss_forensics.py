"""Day 14: READ-ONLY forensics on the three Day 13 misses using ONLY local data (SQLite, cached Feashliaa chunks, saved investor-board snapshots).
No network. Prints facts; interpretation lives in docs/reports/day14_gap_analysis.md."""
import collections
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from discovery import database as db  # noqa: E402

NAMES = {"Sprinto": ["sprinto"], "Prismforce": ["prismforce"], "Libra AI": ["libra"]}
conn = db.connect(db.resolve_db_path(db.PROJECT_ROOT), readonly=True)

print("== Feashliaa cached feed ==")
cache = sorted((ROOT / "data/cache/feashliaa").iterdir())
print("snapshot dirs:", [c.name for c in cache])
latest = cache[-1]
rows = []
for f in sorted(latest.glob("jobs_chunk_*.json.gz")):
    rows += json.load(gzip.open(f, "rt", encoding="utf-8"))
print("latest snapshot rows:", len(rows), "| files:", len(list(latest.glob('jobs_chunk_*.json.gz'))), "| manifest:", [p.name for p in latest.iterdir() if "manifest" in p.name])
ats = collections.Counter(r.get("ats") for r in rows)
print("ATS platforms in the feed:", dict(ats.most_common()))
cos = collections.Counter(r.get("company") for r in rows)
print("distinct companies in feed:", len(cos))
for label, keys in NAMES.items():
    hit = [r for r in rows if any(k in (r.get("company") or "").lower() or k in (r.get("url") or "").lower() for k in keys)]
    print(f"  {label}: feed rows {len(hit)}", [(r['company'], r['title'][:40], r.get('location')) for r in hit[:3]])
bl = [r for r in rows if "bengal" in (r.get("location") or "").lower() or "bangal" in (r.get("location") or "").lower()]
print("feed rows located in Bengaluru:", len(bl), "| by ATS:", dict(collections.Counter(r.get("ats") for r in bl).most_common()))
print("feed rows with 'scraped_at' range:", min(r["scraped_at"] for r in rows)[:16], "->", max(r["scraped_at"] for r in rows)[:16])

print("\n== investor-board snapshots ==")
for f in sorted((ROOT / "data/investor_boards").glob("*.json")):
    data = json.loads(f.read_text(encoding="utf-8"))
    for b in data["boards"]:
        for scope, s in b["scopes"].items():
            names = " ".join(json.dumps(r.get("company", r.get("name", ""))) for r in s["records"]).lower()
            hits = {label: sum(names.count(k) for k in keys) for label, keys in NAMES.items()}
            print(f"  {f.name[:30]:<30} {b['key']:<10} {scope:<9} status={s['status']:<9} complete={s['complete']} records={len(s['records']):>5} filters={s['filters']!r:.60} hits={hits}")

print("\n== SQLite: companies and ATS tenants ==")
for label, keys in NAMES.items():
    c = conn.execute("SELECT company_id, name, domain, facts FROM companies WHERE lower(name) LIKE ? OR lower(name) LIKE ?", (f"%{keys[0]}%", f"%{keys[0]}%")).fetchall()
    srcs = collections.Counter()
    for r in c:
        for s in conn.execute("SELECT source_id FROM sightings WHERE company_id = ? OR job_id IN (SELECT job_id FROM jobs WHERE company_id = ?)", (r["company_id"], r["company_id"])):
            srcs[s[0]] += 1
    print(f"  {label}: companies {[(x['name'], x['domain']) for x in c]} sightings {dict(srcs)}")
print("  tenant check: stored URLs containing 'sprinto':", conn.execute("SELECT source_id, COUNT(*) FROM sightings WHERE lower(source_url) LIKE '%sprinto%' GROUP BY 1").fetchall())
print("  stored URLs containing 'keka.com':", conn.execute("SELECT COUNT(*) FROM sightings WHERE source_url LIKE '%keka.com%'").fetchone()[0], "| 'workable.com':", conn.execute("SELECT COUNT(*) FROM sightings WHERE source_url LIKE '%workable.com%'").fetchone()[0])
print("  host classes of all stored job URLs (top 12):", conn.execute("""SELECT substr(source_url, instr(source_url,'//')+2, instr(substr(source_url, instr(source_url,'//')+2),'/')-1) h, COUNT(*) n FROM sightings
                                                                  WHERE job_id IS NOT NULL GROUP BY h ORDER BY n DESC LIMIT 12""").fetchall())
print("  Workable URL forms stored:", conn.execute("SELECT COUNT(*) FROM sightings WHERE source_url LIKE 'https://apply.workable.com/j/%'").fetchone()[0], "tenant-less;",
      conn.execute("SELECT COUNT(*) FROM sightings WHERE source_url LIKE 'https://apply.workable.com/%/j/%'").fetchone()[0], "tenant-qualified")
