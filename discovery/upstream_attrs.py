"""Evidence the sources already publish but the importers do not store (Day 12 audit). OFFLINE and READ-ONLY.

The Feashliaa feed carries `skill_level` (entry / mid / senior / intern) and `is_recruiter` for every row; the importer
keeps neither. This module reads them back from the CACHED feed chunks already on disk (no network) so the preview can
show what that evidence would change. Both values are SOURCE-REPORTED, machine-derived labels, not employer facts.
"""

import gzip
import json
from pathlib import Path

from . import database as db

CACHE_DIR = db.PROJECT_ROOT / "data" / "cache" / "feashliaa"
LEVELS = ("entry", "mid", "senior", "intern")


def load_feashliaa_attributes(conn, cache_dir=CACHE_DIR):
    """{normalised stored URL: {"source_level": ..., "recruiter_posted": bool}} for the Feashliaa jobs we already hold.
    Returns {} when no cache exists. Never writes anything."""
    wanted = {r[0] for r in conn.execute("SELECT source_url FROM sightings WHERE source_id = 'feashliaa' AND job_id IS NOT NULL")}
    root = Path(cache_dir)
    if not wanted or not root.is_dir():
        return {}
    dirs = sorted(p for p in root.iterdir() if p.is_dir())
    out = {}
    for d in dirs[-1:]:
        for f in sorted(d.glob("jobs_chunk_*.json.gz")):
            try:
                rows = json.load(gzip.open(f, "rt", encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for r in rows:
                url = r.get("url")
                if not url:
                    continue
                key = db.normalize_url(url)
                if key in wanted:
                    level = r.get("skill_level")
                    out[key] = {"source_level": level if level in LEVELS else None, "recruiter_posted": str(r.get("is_recruiter")).lower() == "true"}
    return out
