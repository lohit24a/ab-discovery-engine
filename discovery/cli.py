"""Command-line interface for the discovery engine.

Subcommands are registered in COMMANDS so that `run`, `import`, `sync` and
others can be added later without restructuring this module.
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PASS = "PASS"
WARNING = "WARNING"
FAIL = "FAIL"

EXPECTED_PYTHON = (3, 14)
REQUIRED_DIRS = [
    "discovery",
    "discovery/adapters",
    "config",
    "tests",
    "scripts",
    "data",
    "logs",
    "imports",
    "exports",
]
DEFAULT_DB_PATH = "data/discovery.db"


def run_checks(root: Path):
    """Run local-only health checks. Returns a list of (status, message)."""
    results = []

    def add(status, message):
        results.append((status, message))

    version = sys.version_info
    version_text = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) == EXPECTED_PYTHON:
        add(PASS, f"Python {version_text} (project runtime is 3.14)")
    else:
        add(WARNING, f"Python {version_text} is running; project runtime is 3.14.x")

    if sys.prefix != sys.base_prefix:
        add(PASS, "Running inside a virtual environment")
    else:
        add(WARNING, "Not running inside a virtual environment; use .venv\\Scripts\\python.exe")

    for rel in REQUIRED_DIRS:
        if (root / rel).is_dir():
            add(PASS, f"Folder present: {rel}/")
        else:
            add(FAIL, f"Required folder is missing: {rel}/ (create it under {root})")

    if (root / ".env.example").is_file():
        add(PASS, "Config template present: .env.example")
    else:
        add(FAIL, "Config template is missing: .env.example")

    if (root / ".env").is_file():
        add(PASS, "Local config present: .env (only DISCOVERY_DB_PATH is read)")
    else:
        add(WARNING, "Local config .env not found; copy .env.example to .env and fill it in")

    try:
        import discovery  # noqa: F401
        add(PASS, f"Package 'discovery' imports (version {discovery.__version__})")
    except Exception as exc:
        add(FAIL, f"Package 'discovery' failed to import: {type(exc).__name__}: {exc}")

    results.extend(check_database(root))
    return results


def check_database(root: Path):
    """Read-only database checks. Never creates or modifies the database."""
    from . import database
    from .schema import APP_TABLES, SCHEMA_VERSION

    try:
        db_path = database.resolve_db_path(root)
    except database.DatabaseConfigError as exc:
        return [(FAIL, f"Invalid database configuration: {exc}")]
    shown = os.path.relpath(db_path, root) if db_path.is_relative_to(root) else str(db_path)

    if db_path.is_dir():
        return [(FAIL, f"Database path is a folder, not a file: {shown}")]
    if not db_path.parent.is_dir():
        return [(FAIL, f"Database folder does not exist: {db_path.parent}")]
    if not db_path.is_file():
        return [(WARNING, f"SQLite database not initialized yet ({shown}); "
                          "run: python -m discovery init-db")]

    results = [(PASS, f"SQLite database found: {shown}")]
    try:
        conn = database.connect(db_path, readonly=True)
    except Exception as exc:
        return results + [(FAIL, f"Database cannot be opened: {type(exc).__name__}: {exc}")]
    try:
        version = database.get_schema_version(conn)
        if version == SCHEMA_VERSION:
            results.append((PASS, f"Schema version {version} is supported"))
        elif version < SCHEMA_VERSION:
            results.append((WARNING, f"Schema version {version} is older than {SCHEMA_VERSION}; "
                                     "run: python -m discovery init-db"))
        else:
            results.append((FAIL, f"Schema version {version} is newer than supported {SCHEMA_VERSION}"))

        tables = set(database.list_app_tables(conn))
        missing = sorted(set(APP_TABLES) - tables)
        if not missing:
            results.append((PASS, f"All {len(APP_TABLES)} application tables exist"))
        else:
            results.append((FAIL, f"Missing table(s): {', '.join(missing)}"))

        if conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1:
            results.append((PASS, "Foreign-key enforcement is enabled"))
        else:
            results.append((FAIL, "Foreign-key enforcement is NOT enabled"))
        violations = conn.execute("PRAGMA foreign_key_check").fetchall() if not missing else []
        if violations:
            results.append((FAIL, f"{len(violations)} foreign-key violation(s) found in stored data"))
    except Exception as exc:
        results.append((FAIL, f"Database check failed: {type(exc).__name__}: {exc}"))
    finally:
        conn.close()
    return results


def cmd_doctor(args) -> int:
    print("Discovery engine local doctor (no network, no Google access)")
    results = run_checks(PROJECT_ROOT)
    for status, message in results:
        print(f"[{status:<7}] {message}")
    fails = sum(1 for s, _ in results if s == FAIL)
    warns = sum(1 for s, _ in results if s == WARNING)
    print(f"\nSummary: {len(results) - fails - warns} passed, {warns} warning(s), {fails} failed")
    return 1 if fails else 0


def cmd_init_db(args) -> int:
    from . import database

    try:
        db_path = database.resolve_db_path(PROJECT_ROOT)
        version = database.initialize_database(db_path)
    except (database.DatabaseConfigError, database.SchemaVersionError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(f"[PASS] Database ready at schema version {version}: {db_path}")
    return 0


def _configure_sync(parser):
    # Only TEST is accepted. LIVE needs a separate, explicit authorization in a later step.
    parser.add_argument("--target", choices=["test"], required=True,
                        help="spreadsheet to synchronize (only 'test' is enabled)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report intended changes without modifying SQLite or the spreadsheet")


def cmd_sync(args) -> int:
    from . import database, sheets_sync
    from .schema import SCHEMA_VERSION

    try:
        db_path = database.resolve_db_path(PROJECT_ROOT)
        if not db_path.is_file():
            print("[FAIL] Database not initialized; run: python -m discovery init-db")
            return 1
        backend = sheets_sync.open_backend(args.target, PROJECT_ROOT)
        conn = database.connect(db_path, readonly=args.dry_run)
        try:
            if database.get_schema_version(conn) != SCHEMA_VERSION:
                print("[FAIL] Database schema is out of date; run: python -m discovery init-db")
                return 1
            report = sheets_sync.sync_to_sheets(conn, backend, dry_run=args.dry_run)
        finally:
            conn.close()
    except (sheets_sync.SyncError, database.DatabaseConfigError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    print("\n".join(report.lines()))
    return 2 if report.conflicts else 0


def _configure_import_yc(parser):
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="fetch and analyse the YC dataset; change nothing")
    mode.add_argument("--apply", action="store_true",
                      help="write the YC companies into SQLite (deliberate, manual)")


def cmd_import_yc(args, fetch=None) -> int:
    from . import database
    from .adapters import yc_companies as yc
    from .schema import SCHEMA_VERSION

    fetch = fetch or yc.fetch_json
    db_path = database.resolve_db_path(PROJECT_ROOT)
    db_ready = db_path.is_file()
    conn = None
    try:
        if args.apply:
            if not db_ready:
                print("[FAIL] Database not initialized; run: python -m discovery init-db")
                return 1
            conn = database.connect(db_path)
            if database.get_schema_version(conn) != SCHEMA_VERSION:
                print("[FAIL] Database schema is out of date; run: python -m discovery init-db")
                return 1
        elif db_ready:
            conn = database.connect(db_path, readonly=True)

        report = yc.execute(conn, apply=args.apply, fetch=fetch)
        if report.status == "failed":
            print(f"[FAIL] YC source unusable: {report.error}")
            if args.apply:
                print("       The failed attempt was recorded; no company data was changed.")
            return 1
        snapshot, analysis, pv = report.snapshot, report.analysis, report.preview
        print(("DRY RUN - nothing was changed" if args.dry_run else "APPLYING import") + " | source: yc-oss/api")
        print(f"Source last updated (publisher): {snapshot.source_updated_at}")
        print(f"Retrieved (this machine):        {snapshot.retrieved_at}")
        print(f"Records fetched: {len(snapshot.records)} (meta.json says {snapshot.meta_count}) | "
              f"importable: {len(analysis.companies)}")
        print("India relevance: " + ", ".join(f"{k}={v}" for k, v in analysis.classes.items()))
        print(f"Would publish to review sheet (india_based only): {analysis.publish_count}")
        print(f"New companies: {pv['new']} | already imported: {pv['existing']} "
              f"(changed at source: {pv['existing_changed']}; stored facts to refresh: {pv['would_refresh']})")
        print(f"YC-reported hiring flag true: {analysis.hiring_reported} (informational; no company excluded)")
        print(f"Malformed records: {len(analysis.malformed)} | identical duplicates dropped: "
              f"{analysis.duplicates_dropped} | identity conflicts: {len(analysis.conflicts)}")
        for line in (analysis.malformed + analysis.conflicts)[:10]:
            print(f"  ! {line}")
        for line in analysis.warnings[:10]:
            print(f"  ? {line}")
        if analysis.stale:
            print("  ! Snapshot is older than 3 days.")
        if args.dry_run:
            return 0 if analysis.clean else 2
        result = report.result
        print(f"Run {result.run_id}: {result.result} | created {result.created}, refreshed {result.refreshed}, "
              f"unchanged {result.unchanged}")
        return 0 if analysis.clean else 2
    except yc.SourceError as exc:
        print(f"[FAIL] {exc}")
        return 1
    finally:
        if conn:
            conn.close()


def cmd_import_feashliaa(args, fetch=None, cache_dir=None) -> int:
    from . import database
    from .adapters import feashliaa as fz
    from .schema import SCHEMA_VERSION

    fetch = fetch or fz.fetch_bytes
    cache_dir = cache_dir or (PROJECT_ROOT / "data" / "cache" / "feashliaa")
    db_path = database.resolve_db_path(PROJECT_ROOT)
    conn = None
    try:
        if args.apply:
            if not db_path.is_file():
                print("[FAIL] Database not initialized; run: python -m discovery init-db")
                return 1
            conn = database.connect(db_path)
            if database.get_schema_version(conn) != SCHEMA_VERSION:
                print("[FAIL] Database schema is out of date; back it up, then run: python -m discovery init-db")
                return 1
        elif db_path.is_file():
            conn = database.connect(db_path, readonly=True)
        try:
            report = fz.execute(conn, apply=args.apply, cache_dir=cache_dir, fetch=fetch)
        except fz.SourceError as exc:
            print(f"[FAIL] {exc}")
            return 1
        print("\n".join(report.lines()))
        return 0 if report.status in ("ok", "already_processed") else 1
    finally:
        if conn:
            conn.close()


def _configure_run(parser):
    parser.add_argument("--sources", required=True,
                        help="comma-separated sources to run, e.g. yc,feashliaa (nothing runs unless named)")
    parser.add_argument("--target", choices=["test"], required=True,
                        help="spreadsheet to preview/sync (only 'test' is enabled; LIVE is not authorized)")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and analyse everything; change neither SQLite nor the spreadsheet")
    parser.add_argument("--apply-sync", action="store_true",
                        help="also apply the TEST spreadsheet sync (default: only preview it)")
    parser.add_argument("--resume", metavar="RUN_ID",
                        help="re-run an interrupted/failed run: sources already finished under it are not repeated")


def cmd_run(args, fetchers=None, backend_factory=None, cache_dir=None, prefilter=None) -> int:
    from . import database, pipeline
    from .adapters import feashliaa as fz, yc_companies as yc

    db_path = database.resolve_db_path(PROJECT_ROOT)
    if not db_path.is_file():
        print("[FAIL] Database not initialized; run: python -m discovery init-db")
        return 1
    conn = database.connect(db_path, readonly=args.dry_run)
    try:
        sources = pipeline.parse_sources(args.sources)
        result = pipeline.run_pipeline(
            conn, sources=sources, target=args.target, dry_run=args.dry_run, apply_sync=args.apply_sync,
            fetchers=fetchers, backend_factory=backend_factory, cache_dir=cache_dir, prefilter=prefilter,
            resume_run_id=args.resume)
    except (pipeline.PipelineError, yc.SourceError, fz.SourceError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    finally:
        conn.close()
    print(pipeline.render(result.to_summary()))
    return {"success": 0, "dry-run": 0, "partial": 2, "failed": 1}[result.overall]


def _configure_report(parser):
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--last", action="store_true", help="show the most recent recorded discovery run")
    which.add_argument("--run-id", help="show a specific discovery run")


def cmd_report(args) -> int:
    from . import database, pipeline

    db_path = database.resolve_db_path(PROJECT_ROOT)
    if not db_path.is_file():
        print("[FAIL] Database not initialized; run: python -m discovery init-db")
        return 1
    conn = database.connect(db_path, readonly=True)
    try:
        summary = pipeline.load_summary(conn, None if args.last else args.run_id)
    finally:
        conn.close()
    if summary is None:
        print("No discovery run recorded yet. Start one with: python -m discovery run --sources yc,feashliaa --target test")
        return 1
    print(pipeline.render(summary))
    return 0


# Register new subcommands here: name -> (help text, handler, optional argument configurer)
COMMANDS = {
    "doctor": ("Run local project health checks (read-only)", cmd_doctor, None),
    "init-db": ("Create or upgrade the local SQLite database", cmd_init_db, None),
    "sync": ("Synchronize SQLite with the TEST Google Sheet", cmd_sync, _configure_sync),
    "import-yc": ("Import Y Combinator companies (Stream B)", cmd_import_yc, _configure_import_yc),
    "import-feashliaa": ("Import filtered jobs from the Feashliaa feed (Stream A)", cmd_import_feashliaa,
                         _configure_import_yc),
    "run": ("Run the requested sources and preview/apply the TEST sync (manual, one report)", cmd_run, _configure_run),
    "report": ("Show the consolidated report of a recorded discovery run", cmd_report, _configure_report),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="discovery", description="A+B Discovery Engine")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    for name, (help_text, handler, configure) in COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(handler=handler)
        if configure:
            configure(p)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)
