"""Manual job import (Day 12): jobs a person supplies by hand, from local files only.

Hard rules (CLAUDE.md): this module NEVER opens a URL or a socket; it reads only files inside the project's `imports/`
folder; everything it stores is user-supplied and unverified; it merges nothing by name or title; a manual source is
never closure evidence (`config_ref = no-closure-evidence`); human verdicts are never read or written here.

Flow: parse + validate (pure) -> plan (read-only, resolves identities against the database) -> apply (verified backup first,
one transaction per row, needs schema version 7 for the `manual_inputs` provenance table).

Inputs
  imports/links.csv             one job per row (url and/or description_file, plus hints)
  imports/descriptions/*.txt    saved plaintext job descriptions (referenced by a links.csv row)
  an export CSV + a mapping     columns named explicitly in imports/export_mapping.yaml; unmapped columns are ignored
"""

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import database as db
from . import lifecycle, yamlconfig
from .idkeys import host_class, host_of, registrable_domain, url_signature

SOURCE_ID = "manual"
SOURCE_NAME = "Manual import"
IMPORTS_DIR = db.PROJECT_ROOT / "imports"
LINK_COLUMNS = ("url", "company_hint", "job_title_hint", "location_hint", "experience_hint", "source", "notes", "description_file")
EXPORT_FIELDS = ("url", "title", "company", "location")
MAX_CSV_BYTES = 1_000_000
MAX_DESCRIPTION_BYTES = 200_000
MAX_ROWS = 500
MAX_FIELD = 2000
MAX_URL = 2000
ALLOWED_SUFFIX = {".csv", ".txt", ".yaml"}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ImportRejected(ValueError):
    """A file or row is unsafe or malformed; nothing from it is imported."""


# ------------------------------------------------------------------------------ safe file access

def safe_path(path, root=None):
    """Resolve `path` and require it to be a regular file inside `root` (default: the project's imports/ folder)."""
    root = Path(root or IMPORTS_DIR).resolve()
    raw = Path(path)
    candidate = raw if raw.is_absolute() else (Path.cwd() / raw)
    for part in raw.parts:
        if part in ("..",):
            raise ImportRejected(f"path traversal is not allowed: {path}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ImportRejected(f"only files inside {root.name}/ can be imported: {path}") from None
    cur = candidate
    while True:                                              # no symlink anywhere between the file and the folder
        if cur.is_symlink():
            raise ImportRejected(f"symbolic links are not allowed: {path}")
        if cur.parent == cur or cur.resolve() == root:
            break
        cur = cur.parent
    if any(p.startswith(".") for p in resolved.relative_to(root).parts) or resolved.suffix.lower() not in ALLOWED_SUFFIX:
        raise ImportRejected(f"unsupported or hidden file (allowed: {sorted(ALLOWED_SUFFIX)}): {path}")
    if not resolved.is_file():
        raise ImportRejected(f"file not found: {path}")
    return resolved


def read_text(path, cap, root=None):
    """Read a bounded UTF-8 text file. Other encodings, NUL bytes and oversize files are refused with a clear message."""
    p = safe_path(path, root)
    size = p.stat().st_size
    if size > cap:
        raise ImportRejected(f"{p.name} is {size} bytes; the limit is {cap}")
    data = p.read_bytes()
    if b"\x00" in data:
        raise ImportRejected(f"{p.name} looks binary (NUL byte)")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportRejected(f"{p.name} is not valid UTF-8; save it as UTF-8 and retry") from None


# ----------------------------------------------------------------------------------- validation

def validate_url(url):
    """-> cleaned URL. http(s) only, no credentials, no whitespace/control characters, bounded length. Never requested."""
    u = (url or "").strip()
    if not u:
        raise ImportRejected("empty URL")
    if len(u) > MAX_URL or _CONTROL.search(u) or re.search(r"\s", u):
        raise ImportRejected("URL is too long or contains whitespace/control characters")
    parts = urlsplit(u)
    if parts.scheme.lower() not in ("http", "https"):
        raise ImportRejected(f"only http and https links are accepted (got {parts.scheme or 'none'})")
    if not parts.hostname:
        raise ImportRejected("URL has no host")
    if parts.username or parts.password or "@" in parts.netloc:
        raise ImportRejected("URLs with embedded credentials are refused")
    return u


def _clean(value, name, cap=MAX_FIELD):
    v = (value or "").strip()
    if _CONTROL.search(v):
        raise ImportRejected(f"{name} contains control characters")
    if len(v) > cap:
        raise ImportRejected(f"{name} is longer than {cap} characters")
    return v


_EXP_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:\+?\s*)(?:years?|yrs?)", re.I)
_EXP_PLUS = re.compile(r"(\d+(?:\.\d+)?)\s*\+\s*(?:years?|yrs?)", re.I)


def parse_experience(text):
    """-> (min, max, text). Only numbers literally present are used; anything else stays text."""
    t = (text or "").strip()
    if not t:
        return None, None, None
    m = _EXP_RANGE.search(t)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo <= hi:
            return lo, hi, t
    m = _EXP_PLUS.search(t)
    if m:
        return float(m.group(1)), None, t
    return None, None, t


@dataclass
class Row:
    line: int
    url: str = ""
    company_hint: str = ""
    title_hint: str = ""
    location_hint: str = ""
    experience_hint: str = ""
    notes: str = ""
    description: str = ""
    origin: str = ""
    kind: str = "url_row"                  # url_row | description_text | export_row
    key: str = ""
    error: str = ""


@dataclass
class Parsed:
    rows: list = field(default_factory=list)
    rejected: list = field(default_factory=list)       # (line, reason)


def _row_key(row):
    """Stable identity of an imported row inside the manual source: the scoped posting key for a job-specific URL, else
    the hash of the normalised URL, else a hash of company + title + description."""
    if row.url:
        sig = url_signature(row.url)
        basis = "|".join(map(str, sig.key)) if sig.kind == "specific" else db.normalize_url(row.url)
    else:
        basis = "\x1f".join([row.company_hint.casefold(), row.title_hint.casefold(), row.description])
    return "manual|" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def parse_links(csv_text, descriptions_dir=None, *, origin="links.csv", root=None):
    """Validate an `imports/links.csv` text. Unknown columns reject the whole file; bad rows are rejected one by one."""
    out = Parsed()
    reader = csv.DictReader(io.StringIO(csv_text, newline=""), strict=True)
    names = [n.strip() for n in (reader.fieldnames or [])]
    if not names:
        raise ImportRejected("the CSV has no header row")
    unknown = [n for n in names if n not in LINK_COLUMNS]
    if unknown:
        raise ImportRejected(f"unknown column(s) {unknown}; allowed: {list(LINK_COLUMNS)}")
    if "url" not in names and "description_file" not in names:
        raise ImportRejected("the header needs a url column or a description_file column")
    seen = set()
    try:
        for n, raw in enumerate(reader, start=2):
            if n - 1 > MAX_ROWS:
                raise ImportRejected(f"more than {MAX_ROWS} rows")
            if None in raw or any(v is None for v in raw.values()):
                out.rejected.append((n, "row has the wrong number of columns"))
                continue
            try:
                r = {k.strip(): _clean(v, k, MAX_URL if k == "url" else MAX_FIELD) for k, v in raw.items()}
                src = r.get("source", "").lower()
                if src not in ("", "manual"):
                    raise ImportRejected(f"source must be 'manual' (got {src!r})")
                row = Row(line=n, company_hint=r.get("company_hint", ""), title_hint=r.get("job_title_hint", ""),
                          location_hint=r.get("location_hint", ""), experience_hint=r.get("experience_hint", ""),
                          notes=r.get("notes", ""), origin=f"{origin}:{n}")
                if r.get("url"):
                    row.url = validate_url(r["url"])
                if r.get("description_file"):
                    base = Path(descriptions_dir or IMPORTS_DIR / "descriptions")
                    row.description = read_text(base / r["description_file"], MAX_DESCRIPTION_BYTES, root).strip()
                    if _CONTROL.search(row.description):
                        raise ImportRejected("description contains control characters")
                    row.kind = "description_text"
                if not row.url and not (row.description and row.company_hint and row.title_hint):
                    raise ImportRejected("a row without a URL needs a description file plus company_hint and job_title_hint")
                row.key = _row_key(row)
                if row.key in seen:
                    raise ImportRejected("duplicate of an earlier row in this file")
                seen.add(row.key)
                out.rows.append(row)
            except ImportRejected as exc:
                out.rejected.append((n, str(exc)))
    except csv.Error as exc:
        raise ImportRejected(f"malformed CSV: {exc}") from None
    return out


def parse_export(csv_text, mapping, *, origin="export.csv"):
    """Validate a permitted export using an EXPLICIT column mapping {url,title,company,location -> column header}.
    Columns that are not mapped are ignored; a missing mapped column rejects the file; nothing is guessed."""
    if not isinstance(mapping, dict) or set(mapping) - set(EXPORT_FIELDS) or "title" not in mapping or "company" not in mapping:
        raise ImportRejected(f"the mapping must name columns for {list(EXPORT_FIELDS)} (title and company are required)")
    reader = csv.DictReader(io.StringIO(csv_text, newline=""), strict=True)
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [col for col in mapping.values() if col not in header]
    if missing:
        raise ImportRejected(f"mapped column(s) not in the export: {missing}")
    out, seen = Parsed(), set()
    try:
        for n, raw in enumerate(reader, start=2):
            if n - 1 > MAX_ROWS:
                raise ImportRejected(f"more than {MAX_ROWS} rows")
            try:
                get = lambda f: _clean(raw.get(mapping.get(f, ""), "") if mapping.get(f) else "", f)
                row = Row(line=n, company_hint=get("company"), title_hint=get("title"), location_hint=get("location"),
                          origin=f"{origin}:{n}", kind="export_row")
                if get("url"):
                    row.url = validate_url(get("url"))
                if not row.company_hint or not row.title_hint:
                    raise ImportRejected("company and title are required")
                row.key = _row_key(row) if row.url else "manual|" + hashlib.sha256(f"{row.company_hint}|{row.title_hint}|{row.location_hint}".casefold().encode()).hexdigest()[:32]
                if row.key in seen:
                    raise ImportRejected("duplicate of an earlier row in this file")
                seen.add(row.key)
                out.rows.append(row)
            except ImportRejected as exc:
                out.rejected.append((n, str(exc)))
    except csv.Error as exc:
        raise ImportRejected(f"malformed CSV: {exc}") from None
    return out


def load_mapping(path, root=None):
    data = yamlconfig.loads(read_text(path, 20_000, root))
    return data.get("columns") if isinstance(data, dict) else None


# ------------------------------------------------------------------------------------- planning

@dataclass
class PlanItem:
    row: Row
    action: str                       # create | attach | refresh | update_text | reject
    detail: str = ""
    job_id: str = ""
    company_id: str = ""
    company_action: str = ""          # existing | by_domain | new_provisional


def _existing_manual(conn, row):
    return db.find_sighting(conn, SOURCE_ID, row.url or f"manual:{row.key}", row.key, is_job=True)


def _latest_text_hash(conn, sighting_id, kind):
    r = conn.execute("SELECT sha256 FROM manual_inputs WHERE sighting_id = ? AND kind = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                     (sighting_id, kind)).fetchone() if _has_table(conn) else None
    return r[0] if r else None


def _has_table(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='manual_inputs'").fetchone() is not None


def text_hash(row):
    return hashlib.sha256((row.description or json.dumps([row.url, row.company_hint, row.title_hint, row.location_hint,
                                                         row.experience_hint, row.notes])).encode("utf-8")).hexdigest()


def plan(conn, parsed):
    """Read-only: what applying would do, row by row. Resolves identities against the database; writes nothing."""
    from .adapters.investor_boards import employer_url_index, match_by_employer_url
    index = employer_url_index(conn, SOURCE_ID)
    items = [PlanItem(Row(line=l), "reject", why) for l, why in parsed.rejected]
    for row in parsed.rows:
        existing = _existing_manual(conn, row)
        if existing:
            job = db.get_job(conn, existing["job_id"])
            same = _latest_text_hash(conn, existing["sighting_id"], row.kind) == text_hash(row)
            if _has_table(conn) and not same:
                items.append(PlanItem(row, "update_text", "same manual row, new text version", job["job_id"], job["company_id"], "existing"))
            else:
                items.append(PlanItem(row, "refresh", "already imported; only last-seen would be refreshed", job["job_id"], job["company_id"], "existing"))
            continue
        if row.url:
            state, match_job, match_company = match_by_employer_url(index, {"url": row.url, "apply_url": None, "location": row.location_hint})
            if state == "match":
                items.append(PlanItem(row, "attach", "job-specific URL equals exactly one existing job; a manual sighting is added",
                                      match_job, match_company, "existing"))
                continue
            if state in ("ambiguous", "location_conflict"):
                note = f"URL evidence is {state.replace('_', ' ')}; kept as a separate job"
            else:
                note = ""
        else:
            note = ""
        if not (row.company_hint and row.title_hint):
            items.append(PlanItem(row, "reject", "a new job needs company_hint and job_title_hint (nothing is fetched to fill them)"))
            continue
        company_id, company_action = "", "new_provisional"
        host = host_of(row.url) if row.url else ""
        if host and host_class(host) == "employer":
            hit = db.find_company_by_domain(conn, registrable_domain(host))
            if hit:
                company_id, company_action = hit["company_id"], "by_domain"
        items.append(PlanItem(row, "create", note or "new manual job", "", company_id, company_action))
    return items


def summarize(items):
    counts = {}
    for it in items:
        counts[it.action] = counts.get(it.action, 0) + 1
    return counts


def render(items):
    lines = [f"Manual import plan: {summarize(items)}"]
    for it in items:
        r = it.row
        lines.append(f"  line {r.line:>3} {it.action:<11} {r.title_hint[:40] or '-'} @ {r.company_hint[:25] or '-'} | {it.detail}"
                     + (f" | company: {it.company_action}" if it.company_action else ""))
    return lines


# ------------------------------------------------------------------------------------- applying

class ManualImportError(RuntimeError):
    pass


def apply(conn, items, *, now=None):
    """Write the plan. The caller must have taken a verified backup and have the operator's approval. Needs schema 7."""
    if not _has_table(conn):
        raise ManualImportError("the database is at schema version 6: apply migration 7 (manual_inputs) first. "
                                "That needs the operator's approval; this import wrote nothing")
    now = now or db.utc_now()
    db.register_source(conn, SOURCE_ID, SOURCE_NAME, "manual", "imports/", config_ref=lifecycle.NO_CLOSURE_EVIDENCE, now=now)
    run_id = db.start_run(conn, "import", now=now)
    counts = {"created": 0, "attached": 0, "refreshed": 0, "text_updated": 0, "rejected": 0, "errors": []}
    try:
        for it in items:
            if it.action == "reject":
                counts["rejected"] += 1
                continue
            try:
                _apply_one(conn, it, counts, now)
            except (db.IdentityConflict, ImportRejected) as exc:
                counts["errors"].append(f"line {it.row.line}: {exc}")
        outcome = "partial" if (counts["errors"] or counts["rejected"]) else "success"
        db.record_source_check(conn, run_id, SOURCE_ID, outcome, scope="manual-rows", complete=False,       # never complete
                               record_count=len(items), started_at=now, finished_at=now,
                               error_info="; ".join(counts["errors"])[:500] or None)
        db.finish_run(conn, run_id, outcome, now=db.utc_now(), summary={"source": "manual", **{k: v for k, v in counts.items() if k != "errors"},
                                                                         "errors": len(counts["errors"])})
    except BaseException as exc:
        db.finish_run(conn, run_id, "failed", error_detail=f"{type(exc).__name__}: {str(exc)[:200]}", now=db.utc_now(), summary={"source": "manual"})
        raise
    counts["run_id"] = run_id
    return counts


def _manual_only(conn, job_id):
    return conn.execute("SELECT COUNT(*) FROM sightings WHERE job_id = ? AND source_id <> ?", (job_id, SOURCE_ID)).fetchone()[0] == 0


def _store_input(conn, sighting_id, row, now):
    sha = text_hash(row)
    cur = conn.execute("""INSERT OR IGNORE INTO manual_inputs (input_id, sighting_id, kind, sha256, text, hints, origin, created_at)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       (db.new_id(), sighting_id, row.kind, sha, row.description or "",
                        json.dumps({"company_hint": row.company_hint, "job_title_hint": row.title_hint, "location_hint": row.location_hint,
                                    "experience_hint": row.experience_hint, "notes": row.notes, "url": row.url}, sort_keys=True),
                        row.origin, now))
    return cur.rowcount


def _apply_one(conn, it, counts, now):
    row = it.row
    url = row.url or f"manual:{row.key}"
    lo, hi, exp_text = parse_experience(row.experience_hint)
    with db.transaction(conn):
        if it.action in ("refresh", "update_text"):
            if it.action == "update_text" and row.title_hint and _manual_only(conn, it.job_id):
                # the job exists only through this manual row: a real change is recorded as one 'Changed' event
                obs = db.observe_job(conn, source_id=SOURCE_ID, source_url=url, company_id=it.company_id, title=row.title_hint, external_id=row.key,
                                     location=row.location_hint or None, experience_min_years=lo, experience_max_years=hi, experience_text=exp_text,
                                     description=row.description or None, posting_url=row.url or None, now=now)
                sid = obs.sighting_id
            else:
                sid, _ = db.record_sighting(conn, SOURCE_ID, url, job_id=it.job_id, external_id=row.key, content_hash=text_hash(row), now=now)
            if it.action == "update_text":
                _store_input(conn, sid, row, now)
                counts["text_updated"] += 1
            else:
                counts["refreshed"] += 1
            return
        if it.action == "attach":
            sid, _ = db.record_sighting(conn, SOURCE_ID, url, job_id=it.job_id, external_id=row.key, content_hash=text_hash(row), now=now)
            _store_input(conn, sid, row, now)
            counts["attached"] += 1
            return
        company_id = it.company_id
        if not company_id:
            company_id, _ = db.get_or_create_company(conn, row.company_hint, None, facts={"sheet_publish": False, "created_by": "manual_import"}, now=now)
        elif row.company_hint:
            db.update_company_details(conn, company_id, aliases_add=[row.company_hint], now=now)
        obs = db.observe_job(conn, source_id=SOURCE_ID, source_url=url, company_id=company_id, title=row.title_hint, external_id=row.key,
                             location=row.location_hint or None, experience_min_years=lo, experience_max_years=hi, experience_text=exp_text,
                             description=row.description or None, posting_url=row.url or None, now=now)
        _store_input(conn, obs.sighting_id, row, now)
        counts["created" if obs.created else "refreshed"] += 1
