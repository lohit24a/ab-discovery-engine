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


# Register new subcommands here: name -> (help text, handler, optional argument configurer)
COMMANDS = {
    "doctor": ("Run local project health checks (read-only)", cmd_doctor, None),
    "init-db": ("Create or upgrade the local SQLite database", cmd_init_db, None),
    "sync": ("Synchronize SQLite with the TEST Google Sheet", cmd_sync, _configure_sync),
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
