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


def _configure_restricted_policy(parser):
    pass


def cmd_restricted_policy(args) -> int:
    from . import restricted_policy as rp

    try:
        policies = rp.load_policy()
    except rp.PolicyError as exc:
        print(f"[FAIL] {exc}")
        return 1
    print("Restricted job boards (source of truth: config/restricted_sources.json). They NEVER run in normal discovery;")
    print("each live execution needs your own interactive confirmation of the exact scope below.")
    for name, p in policies.items():
        state = "ENABLED (still needs approval per run)" if p.enabled else "disabled"
        print(f"- {name}: {state} | terms {list(p.approved_search_terms)} | locations {list(p.approved_locations)} | "
              f"<= {p.max_searches_per_run} searches x {p.max_results_per_search} results | request budget {p.request_budget} | "
              f"<= {p.max_duration_seconds}s | delay >= {p.min_delay_seconds}s | enrichment {'on' if p.allow_description_fetch else 'off'} "
              f"| Sheets publication {'on' if p.allow_sheets_publication else 'blocked'}")
    return 0


def _configure_restricted_collect(parser):
    parser.add_argument("--sources", required=True, help="restricted sources to contact, named one by one, e.g. indeed,naukri")
    parser.add_argument("--search", action="append", metavar="TERM",
                        help="narrow to an approved search term (repeatable); anything not approved is rejected")
    parser.add_argument("--results", type=int, help="requested results per search (cannot exceed the policy maximum)")
    parser.add_argument("--enable-for-this-run", action="store_true",
                        help="enable the named source in memory for this one execution (the policy file stays disabled)")
    parser.add_argument("--attest-provider-authorization", action="store_true",
                        help="record that the operator states the provider authorization exists and covers VPN egress "
                             "(needed for sources marked not_established)")
    parser.add_argument("--approve-plan", metavar="CODE",
                        help="run the plan the operator approved in chat, identified by the code `restricted-plan` printed")


def _run_override(args):
    if getattr(args, "attest_provider_authorization", False) and not getattr(args, "enable_for_this_run", False):
        from . import restricted_policy as rp
        raise rp.PolicyError("--attest-provider-authorization only applies together with --enable-for-this-run")
    if not getattr(args, "enable_for_this_run", False):
        return {}
    from . import restricted_policy as rp
    return {n: {"attest": bool(args.attest_provider_authorization)} for n in rp.parse_source_names(args.sources)}


def _configure_restricted_plan(parser):
    _configure_restricted_collect(parser)
    parser.add_argument("--cancel", action="store_true", help="discard every plan awaiting approval")


def cmd_restricted_plan(args, vpn_check=None, policy_path=None, pending_dir=None) -> int:
    """Display the exact plan, run the VPN preflight, and record it as awaiting the operator's approval in chat.

    Contacts no provider and writes nothing but the pending-plan record. Approval happens in the chat and is then
    passed back to `restricted-collect --approve-plan CODE`.
    """
    from . import network_guard, restricted_policy as rp
    from .adapters import jobspy_source as js
    if getattr(args, "cancel", False):
        print(f"Discarded {rp.cancel_pending(pending_dir)} pending plan(s).")
        return 0
    try:
        names = rp.parse_source_names(args.sources)
        unsupported = [n for n in names if n not in js.IMPLEMENTED]
        if unsupported:
            raise rp.PolicyError(f"{', '.join(unsupported)}: no approved live implementation or scope yet")
        override = _run_override(args)
        policies = rp.load_policy(policy_path or rp.POLICY_PATH, override)
        plan = rp.build_plan(policies, names, terms=args.search, results=args.results)
        print("\n".join(rp.describe_plan(plan, policies, override)))
        result = (vpn_check or network_guard.require_vpn)()
        for line in getattr(result, "lines", lambda: [])():
            print(line)
    except network_guard.NetworkGuardError as exc:
        print(f"[BLOCKED] {exc}. No provider was contacted and no plan was recorded.")
        return 5
    except rp.PolicyError as exc:
        print(f"[REFUSED] {exc}")
        return 1
    record = rp.save_pending(plan, override, directory=pending_dir)
    flags = (" --enable-for-this-run" if override else "") + (" --attest-provider-authorization" if any(
        v.get("attest") for v in override.values()) else "")
    for opt, val in (("--search", t) for t in (args.search or [])):
        flags += f' {opt} "{val}"'
    if args.results is not None:
        flags += f" --results {args.results}"
    print(f"\nPLAN CODE: {record['code']}  (valid {rp.PLAN_TTL_SECONDS // 60} minutes, single use)")
    print("Nothing has been sent to any provider. Waiting for the operator's explicit approval message in the chat.")
    print(f"After approval: python -m discovery restricted-collect --sources {args.sources}{flags} --approve-plan {record['code']}")
    return 0


def cmd_restricted_collect(args, console=None, vpn_check=None, scrape=None, policy_path=None, snapshot_dir=None,
                           sleep=None, db_path=None, pending_dir=None) -> int:
    """Phase A: needs the operator's interactive confirmation; writes only a temporary snapshot file."""
    import time as _time

    from . import database, network_guard, restricted_policy as rp
    from .adapters import jobspy_source as js

    try:
        names = rp.parse_source_names(args.sources)
        unsupported = [n for n in names if n not in js.IMPLEMENTED]
        if unsupported:
            raise rp.PolicyError(f"{', '.join(unsupported)}: no approved live implementation or scope yet")
        override = _run_override(args)
        policies = rp.load_policy(policy_path or rp.POLICY_PATH, override)
        plan = rp.build_plan(policies, names, terms=args.search, results=args.results)
        if getattr(args, "approve_plan", None):
            auth = rp.authorize_from_chat(plan, override, args.approve_plan, directory=pending_dir, vpn_check=vpn_check)
        else:
            auth = rp.authorize_interactively(plan, policies, console=console, vpn_check=vpn_check)
        policies = rp.load_policy(policy_path or rp.POLICY_PATH, override)   # reload: a changed policy voids the authorization
        plan = rp.build_plan(policies, names, terms=args.search, results=args.results)
        snapshot = js.collect_snapshot(plan, auth, policies, scrape=scrape, vpn_check=vpn_check or network_guard.require_vpn,
                                       sleep=sleep or _time.sleep)
    except rp.NonInteractiveError as exc:
        print(f"[BLOCKED] {exc}")
        return 3
    except rp.AuthorizationError as exc:
        print(f"[BLOCKED] {exc}")
        return 4
    except network_guard.NetworkGuardError as exc:
        print(f"[BLOCKED] {exc}. No provider was contacted.")
        return 5
    except rp.PolicyError as exc:
        print(f"[REFUSED] {exc}")
        return 1
    path = js.write_snapshot(snapshot, snapshot_dir)
    js.append_audit({"ts": snapshot["finished_at"], "action": "collect", "sources": snapshot["sources"],
                     "plan": snapshot["plan_fingerprint"][:16],
                     "approval": "chat" if getattr(args, "approve_plan", None) else "terminal",
                     "run_override": bool(getattr(args, "enable_for_this_run", False)), "outcomes": [(o["source"], o["status"], o["rows"], o["requests_used"])
                                                                              for o in snapshot["outcomes"]]})
    conn = None
    try:
        path_db = db_path or database.resolve_db_path(PROJECT_ROOT)
        if path_db.is_file():
            conn = database.connect(path_db, readonly=True)
        print("\n".join(js.preview(conn, snapshot)))
        for o in snapshot["outcomes"]:
            for t in o.get("trace", []):
                print(f"    request {t['stage']}: {t['method']} {t['host']}{t['path']} -> HTTP {t['status']}"
                      + (f" server={t['server']}" if t.get("server") else ""))
    finally:
        if conn:
            conn.close()
    print(f"\nSnapshot saved (temporary, git-ignored): {path}")
    print("Nothing was written to SQLite, Google Sheets TEST or LIVE. Importing needs a separate approval: "
          "python -m discovery restricted-import --snapshot <file>")
    return 0 if all(o["status"] in ("success", "skipped") for o in snapshot["outcomes"]) else 2


def _configure_restricted_import(parser):
    parser.add_argument("--snapshot", required=True, help="snapshot file written by restricted-collect")
    parser.add_argument("--dry-run", action="store_true", help="show the preview and expected database changes only")


def cmd_restricted_import(args, console=None, policy_path=None, db_path=None, backup_dir=None) -> int:
    """Phase B: no provider is contacted; needs its own interactive confirmation (not for --dry-run)."""
    from . import database, restricted_policy as rp
    from .adapters import jobspy_source as js
    from .schema import SCHEMA_VERSION

    try:
        snapshot = js.load_snapshot(args.snapshot)
        policies = rp.load_policy(policy_path or rp.POLICY_PATH)
        plan = rp.build_plan(policies, snapshot["sources"], action="import", extra=snapshot["records_sha256"])
        path_db = db_path or database.resolve_db_path(PROJECT_ROOT)
        if not path_db.is_file():
            raise rp.PolicyError("database not initialized; run: python -m discovery init-db")
        ro = database.connect(path_db, readonly=True)
        try:
            print("\n".join(js.preview(ro, snapshot)))
            version = database.get_schema_version(ro)
        finally:
            ro.close()
        if args.dry_run:
            print("\nDRY RUN: no confirmation asked, nothing written.")
            return 0
        if version != SCHEMA_VERSION:
            raise rp.PolicyError("database schema is out of date; back it up and run init-db first")
        auth = rp.authorize_interactively(plan, policies, console=console)
        policies = rp.load_policy(policy_path or rp.POLICY_PATH)
        plan = rp.build_plan(policies, snapshot["sources"], action="import", extra=snapshot["records_sha256"])
        auth.begin(plan)
    except rp.NonInteractiveError as exc:
        print(f"[BLOCKED] {exc}")
        return 3
    except rp.AuthorizationError as exc:
        print(f"[BLOCKED] {exc}")
        return 4
    except rp.PolicyError as exc:
        print(f"[REFUSED] {exc}")
        return 1
    try:
        backup = js.verified_backup(path_db, backup_dir)
        conn = database.connect(path_db)
        try:
            counts = js.import_snapshot(conn, snapshot)
        finally:
            conn.close()
    finally:
        auth.finish()
    js.append_audit({"ts": database.utc_now(), "action": "import", "sources": snapshot["sources"],
                     "snapshot": snapshot["records_sha256"][:16], "created": counts.jobs_created})
    print(f"Verified backup: {backup.name}")
    print(f"Imported: jobs created {counts.jobs_created}, attached to existing jobs {counts.attached_to_existing}, "
          f"already seen {counts.already_seen}, companies created {counts.companies_created}, ambiguous skipped "
          f"{counts.skipped_identity}. Nothing was published to Google Sheets.")
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
    "restricted-policy": ("Show the restricted job-board policy (read-only)", cmd_restricted_policy, _configure_restricted_policy),
    "restricted-plan": ("Display a restricted-source plan and record it for chat approval (contacts nothing)",
                        cmd_restricted_plan, _configure_restricted_plan),
    "restricted-collect": ("Phase A: collect from restricted boards after YOUR interactive approval", cmd_restricted_collect,
                           _configure_restricted_collect),
    "restricted-import": ("Phase B: import a saved restricted snapshot after YOUR interactive approval", cmd_restricted_import,
                          _configure_restricted_import),
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
