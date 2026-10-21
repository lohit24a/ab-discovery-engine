"""Freeze the evaluation baseline BEFORE any benchmark job is chosen (Day 13). READ-ONLY: metadata only, no job content is examined.

Usage: python scripts/freeze_baseline.py [output.json]
"""
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from discovery import database as db  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(out):
    prod = db.resolve_db_path(ROOT)
    conn = db.connect(prod, readonly=True)
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    sources = []
    for s in conn.execute("SELECT source_id, name, source_type, enabled, config_ref FROM sources ORDER BY source_id"):
        row = conn.execute("""SELECT COUNT(*) AS n, MIN(first_seen_at) AS first, MAX(last_seen_at) AS last FROM sightings WHERE source_id = ?""", (s["source_id"],)).fetchone()
        check = conn.execute("""SELECT started_at, outcome, complete, scope FROM source_checks WHERE source_id = ? ORDER BY started_at DESC LIMIT 1""", (s["source_id"],)).fetchone()
        sources.append({**dict(s), "sightings": row["n"], "first_sighting": row["first"], "last_sighting": row["last"],
                        "latest_check": dict(check) if check else None})
    runs = {r[0]: r[1] for r in conn.execute("SELECT run_type, COUNT(*) FROM runs GROUP BY run_type")}
    last_sync = conn.execute("SELECT run_id, started_at, summary FROM runs WHERE run_type='sync' AND result IN ('success','partial') ORDER BY started_at DESC LIMIT 1").fetchone()
    session = json.loads(last_sync["summary"]).get("session", {}) if last_sync else {}
    baseline = {
        "frozen_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git("rev-parse", "HEAD"), "git_dirty_paths": len([l for l in git("status", "--short").splitlines() if l.strip()]),
        "schema_version": db.get_schema_version(conn),
        "database_sha256": sha(prod), "database_integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_violations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "row_counts": {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
                       ("companies", "jobs", "sightings", "status_events", "verdicts", "identity_links", "runs", "source_checks", "manual_inputs")},
        "config_sha256": {p: sha(ROOT / p) for p in ("config/search_profile.yaml", "config/ranking.yaml", "config/restricted_sources.json", "config/investor_boards.json")
                          if (ROOT / p).is_file()},
        "sources": sources, "run_counts_by_type": runs,
        "latest_sync_run": dict(last_sync) if last_sync else None, "current_review_session": {"id": session.get("id"), "records": len(session.get("order", []))},
        "verdicts_by_decision": {r[0]: r[1] for r in conn.execute("SELECT decision, COUNT(*) FROM verdicts GROUP BY decision")},
    }
    baseline["latest_sync_run"] = {k: v for k, v in (baseline["latest_sync_run"] or {}).items() if k != "summary"}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(baseline, indent=1, default=str), encoding="utf-8")
    return baseline


if __name__ == "__main__":
    b = main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "exports" / "day13_frozen_baseline.json")
    print(json.dumps({k: v for k, v in b.items() if k != "sources"}, indent=1, default=str))
    for s in b["sources"]:
        print(f"  {s['source_id']:<16} {s['source_type']:<14} enabled={s['enabled']} cfg={s['config_ref']} sightings={s['sightings']} last={s['last_sighting']} check={s['latest_check'] and (s['latest_check']['started_at'][:16], s['latest_check']['outcome'], s['latest_check']['complete'])}")
