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


BOARD_DIR = db.PROJECT_ROOT / "data" / "investor_boards"
GETRO_LEVEL = {"internship": "intern", "entry_level": "entry", "associate": "entry", "mid_senior": "mid", "senior": "senior",
               "director": "senior", "vice_president": "senior", "cxo": "senior"}


def load_board_attributes(board_dir=BOARD_DIR):
    """{normalised employer/listing URL: {"source_level": ...}} from the NEWEST saved snapshot of each investor board (offline, read-only).

    Getro reports a coarse `seniority` for most Bengaluru listings; the importer keeps title, location and dates but not this field. It is a machine-derived
    label from the board, not an employer statement, and 'mid_senior' is deliberately NOT turned into a signal. Returns {} when no snapshot exists."""
    root = Path(board_dir)
    if not root.is_dir():
        return {}
    newest = {}
    for fn in sorted(root.glob("*.json")):
        try:
            data = json.loads(fn.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for b in data.get("boards", []):
            scope = (b.get("scopes") or {}).get("jobs")
            if scope and scope.get("records"):
                newest[b["key"]] = scope["records"]
    out = {}
    for records in newest.values():
        for r in records:
            level = GETRO_LEVEL.get((r.get("seniority") or "").lower())
            url = r.get("apply_url") or r.get("url")
            if level and url:
                out.setdefault(db.normalize_url(url), {"source_level": level, "recruiter_posted": False})
    return out


def merge(*sources):
    """Combine attribute maps. Where two sources disagree the more cautious reading wins (senior over anything, then entry)."""
    order = {"senior": 3, "entry": 2, "intern": 2, "mid": 1, None: 0}
    out = {}
    for src in sources:
        for url, attrs in (src or {}).items():
            cur = out.get(url)
            if cur is None:
                out[url] = dict(attrs)
            else:
                if order.get(attrs.get("source_level"), 0) > order.get(cur.get("source_level"), 0):
                    cur["source_level"] = attrs["source_level"]
                cur["recruiter_posted"] = bool(cur.get("recruiter_posted") or attrs.get("recruiter_posted"))
    return out
