"""User-run SQLite backups: transaction-consistent, verified, never overwriting, never touching production.

* The copy is made with SQLite's online-backup API from a READ-ONLY connection (safe while the database is in WAL mode;
  a plain file copy is not).
* It is written under a temporary name, checked (integrity, foreign keys, schema version) and only then renamed into place,
  so an incomplete or corrupt copy never carries a real backup name.
* A manifest (`<backup>.manifest.json`: schema version, per-table row counts and content digests, file SHA-256) lets a later
  restore test prove the backup still equals the data it was taken from.
* Nothing here replaces or modifies the production database. Restoring is a deliberate manual procedure (see README.md).
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from . import database as db

LABEL_OK = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class BackupError(Exception):
    """The backup could not be made or verified; nothing was replaced."""


def _ro(path):
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def table_names(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def table_digests(conn):
    """{table: {"rows": n, "digest": first 16 hex of SHA-256 over the rows in primary-key order}}."""
    out = {}
    for t in table_names(conn):
        h = hashlib.sha256()
        n = 0
        for row in conn.execute(f'SELECT * FROM "{t}" ORDER BY 1'):
            h.update(repr(tuple(row)).encode("utf-8"))
            n += 1
        out[t] = {"rows": n, "digest": h.hexdigest()[:16]}
    return out


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_database(path):
    """-> (problems list, facts dict) for a database file, read-only."""
    problems, facts = [], {}
    try:
        conn = _ro(path)
    except sqlite3.Error as exc:
        return [f"cannot open: {type(exc).__name__}"], facts
    try:
        verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if verdict != "ok":
            problems.append(f"integrity check failed: {str(verdict)[:80]}")
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            problems.append(f"{len(fk)} foreign-key violation(s)")
        facts["schema_version"] = conn.execute("PRAGMA user_version").fetchone()[0]
        facts["tables"] = len(table_names(conn))
    except sqlite3.Error as exc:
        problems.append(f"check failed: {type(exc).__name__}")
    finally:
        conn.close()
    return problems, facts


def create_backup(db_path, backup_dir=None, label="manual", now=None):
    """Make one verified backup. -> Path of the backup. Raises BackupError (and leaves no half-written backup)."""
    db_path = Path(db_path)
    backup_dir = Path(backup_dir or db.PROJECT_ROOT / "backups")
    if not LABEL_OK.match(label or ""):
        raise BackupError("the label may use only letters, digits, '_' and '-' (up to 40 characters)")
    if not db_path.is_file():
        raise BackupError("the database file was not found; nothing was backed up")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", now or time.localtime())
    final = backup_dir / f"discovery_{label}_{stamp}.db"
    n = 1
    while final.exists() or final.with_suffix(".db.manifest.json").exists():     # never overwrite anything
        n += 1
        final = backup_dir / f"discovery_{label}_{stamp}-{n}.db"
    partial = backup_dir / (final.name + ".partial")
    src = dst = None
    try:
        src = _ro(db_path)
        dst = sqlite3.connect(partial)
        src.backup(dst)
        dst.close()
        dst = None
        problems, facts = check_database(partial)
        if problems:
            raise BackupError("the new copy failed verification (" + "; ".join(problems) + "); it was discarded")
        source_facts = db.get_schema_version(src)
        if facts["schema_version"] != source_facts:
            raise BackupError("the copy's schema version differs from the source; it was discarded")
        check = _ro(partial)
        try:
            digests = table_digests(check)
        finally:
            check.close()
        src_digests = table_digests(src)
        if digests != src_digests:
            raise BackupError("the copy does not match the source data; it was discarded")
        manifest = {"backup": final.name, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "schema_version": facts["schema_version"],
                    "tables": digests, "sha256": file_sha256(partial), "bytes": partial.stat().st_size}
    except BackupError:
        _cleanup(src, dst, partial)
        raise
    except sqlite3.Error as exc:
        _cleanup(src, dst, partial)
        raise BackupError(f"SQLite reported {type(exc).__name__}; nothing was kept") from exc
    except OSError as exc:
        _cleanup(src, dst, partial)
        raise BackupError(f"file error ({type(exc).__name__}); nothing was kept") from exc
    src.close()
    os.replace(partial, final)
    final.with_suffix(".db.manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return final


def _cleanup(src, dst, partial):
    for c in (src, dst):
        try:
            c and c.close()
        except sqlite3.Error:
            pass
    try:
        Path(partial).unlink(missing_ok=True)
    except OSError:
        pass


def manifest_path(backup):
    return Path(backup).with_suffix(".db.manifest.json")


def verify_backup(backup, restore_test=False):
    """Check an existing backup. -> (ok, lines). With restore_test it is copied into a disposable folder, opened, checked and its
    tables are compared with the manifest, then the copy is deleted. The production database is never opened for writing."""
    backup = Path(backup)
    lines = []
    if not backup.is_file():
        return False, [f"backup not found: {backup.name}"]
    problems, facts = check_database(backup)
    lines.append(f"{backup.name}: schema {facts.get('schema_version', '?')}, {facts.get('tables', '?')} tables, integrity/foreign keys "
                 + ("OK" if not problems else "FAILED: " + "; ".join(problems)))
    mpath = manifest_path(backup)
    manifest = None
    if mpath.is_file():
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        if file_sha256(backup) != manifest["sha256"]:
            problems.append("the file no longer matches its recorded SHA-256 (changed or damaged since it was made)")
        lines.append("manifest: found" + ("" if file_sha256(backup) == manifest["sha256"] else ", SHA-256 MISMATCH"))
    else:
        lines.append("manifest: none (older backup): content comparison is skipped")
    if restore_test and not problems:
        tmp = Path(tempfile.mkdtemp(prefix="restore_test_"))
        try:
            restored = tmp / "restored.db"
            shutil.copy2(backup, restored)
            p2, _ = check_database(restored)
            conn = _ro(restored)
            try:
                digests = table_digests(conn)
            finally:
                conn.close()
            if p2:
                problems.extend(p2)
            if manifest is not None:
                diff = [t for t in manifest["tables"] if digests.get(t) != manifest["tables"][t]]
                if diff:
                    problems.append("restored tables differ from the manifest: " + ", ".join(diff))
            lines.append("disposable restore: " + ("OK, " + str(sum(v["rows"] for v in digests.values())) + " rows in " + str(len(digests))
                                                   + " tables" + (", all tables equal the manifest" if manifest else "") if not problems else "FAILED"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        lines.append("disposable copy removed; production was not touched")
    return not problems, lines


def list_backups(backup_dir=None):
    d = Path(backup_dir or db.PROJECT_ROOT / "backups")
    return sorted(d.glob("discovery_*.db"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
