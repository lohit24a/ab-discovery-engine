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
def _configure_yc_jobs_collect(parser):
    parser.add_argument("--authorized-by-operator", action="store_true", required=True,
                        help="state that you hold YC's authorization for this automated retrieval (user-attested, "
                             "recorded as such; no reference number)")
    parser.add_argument("--classes", default="india_based",
                        help="India relevance classes to check (default india_based); the hiring flag never filters")
    parser.add_argument("--sample", type=int, help="check only a small representative sample of the selection")
    parser.add_argument("--newest-first", action="store_true", help="check the most recently launched companies first")
    parser.add_argument("--limit", type=int, help="check at most this many companies (after ordering and skipping)")
    parser.add_argument("--skip-checked-in", action="append", metavar="SNAPSHOT", default=[],
                        help="skip companies already requested in this saved snapshot (repeatable)")
    parser.add_argument("--max-requests", type=int, default=None, help="hard request cap for this run")
    parser.add_argument("--max-seconds", type=int, default=900, help="runtime cap in seconds")
    parser.add_argument("--plan", action="store_true", help="show the selection and request volume only; send nothing")
    parser.add_argument("--recent-days", type=int, default=90,
                        help="listings younger than this are 'recently observed, unverified'; older ones are historical "
                             "company-level evidence. A prioritization threshold only, never a closure rule")


def cmd_yc_jobs_collect(args, fetch=None, sleep=None, db_path=None, snapshot_dir=None) -> int:
    """Read-only collection of public YC job pages into a git-ignored snapshot. Writes nothing to SQLite or Sheets."""
    import time as _time

    from . import database
    from .adapters import yc_jobs as yj
    path_db = db_path or database.resolve_db_path(PROJECT_ROOT)
    if not path_db.is_file():
        print("[REFUSED] database not initialized; run: python -m discovery init-db")
        return 1
    conn = database.connect(path_db, readonly=True)
    try:
        classes = tuple(c.strip() for c in args.classes.split(",") if c.strip())
        companies = yj.select_companies(conn, classes)
    except yj.YCJobsError as exc:
        print(f"[REFUSED] {exc}")
        return 1
    finally:
        conn.close()
    counts = yj.selection_counts(companies)
    try:
        skipped = [slug for snap_path in args.skip_checked_in for slug in yj.slugs_in_snapshot(snap_path)]
    except yj.YCJobsError as exc:
        print(f"[REFUSED] {exc}")
        return 1
    chosen = yj.plan_batch(companies, newest_first=args.newest_first, limit=args.limit, skip_slugs=skipped)
    if args.sample:
        chosen = yj.representative_sample(chosen, args.sample)
    if skipped:
        print(f"Skipping {len(set(skipped) & {c['slug'] for c in companies})} companies already checked in earlier snapshots.")
    cap = min(args.max_requests or len(chosen), yj.MAX_REQUESTS_CEILING)
    print(f"Selected {counts['selected']} YC companies by relevance class {list(classes)} "
          f"(hiring flag true {counts['hiring_true']}, false {counts['hiring_false']}, unknown {counts['hiring_unknown']}); "
          f"checking {len(chosen)}. Expected requests <= {min(cap, len(chosen))}, about "
          f"{round(min(cap, len(chosen)) * (yj.MIN_DELAY_SECONDS + 1) / 60, 1)} minutes at one request every "
          f"{yj.MIN_DELAY_SECONDS:.0f}s.")
    print("Authorization basis: user-attested (no reference number); sequential, no retries, stops on 403/429.")
    if args.plan:
        print("PLAN ONLY: nothing was requested.")
        return 0
    snapshot = yj.collect(chosen, fetch=fetch or yj.fetch_page, sleep=sleep or _time.sleep,
                          max_requests=cap, max_seconds=args.max_seconds, recency_days=max(1, args.recent_days))
    path = yj.write_snapshot(snapshot, snapshot_dir)
    print("\n".join(_render_yc_jobs_summary(yj.summarize(snapshot))))
    print(f"\nSnapshot saved (temporary, git-ignored): {path}")
    print("Nothing was written to SQLite, Google Sheets TEST or LIVE. Import needs a separate approval: "
          "python -m discovery yc-jobs-import --snapshot <file> [--apply]")
    return 0 if not snapshot["stopped"] else 2


def _render_yc_jobs_summary(s):
    st = s["by_state"]
    lines = [f"Companies in snapshot: {s['companies_in_snapshot']} (hiring flag true {s['hiring_true']}, "
             f"false {s['hiring_false']}, unknown {s['hiring_unknown']})",
             f"Checked: {s['checked']} (with listings {st['checked_with_listings']}, no listings observed "
             f"{st['checked_no_listings']}) | partial {st['partial']} | blocked {st['blocked']} | failed {st['failed']} | "
             f"not checked {st['not_checked']}",
             f"Listings observed: {s['jobs_retrieved']} in {s['companies_with_listings']} companies | unique "
             f"{s['unique_jobs']} | evidence (recency threshold {s['recency_days']} days): recently observed, unverified "
             f"{s['evidence'].get('recently_observed_unverified', 0)}, historical/stale {s['evidence'].get('historical_stale', 0)} "
             f"(company-level evidence only), unknown age {s['evidence'].get('unknown', 0)}; companies with only historical "
             f"listings {s['companies_only_historical']}",
             f"Posting dates: ISO date {s['jobs_with_iso_posting_date']}, relative age text only "
             f"{s['jobs_with_relative_age_only']} (older than ~1 year: {s['jobs_older_than_1y']}, age unknown "
             f"{s['jobs_age_unknown']}) | direct employer apply URLs {s['direct_employer_apply_urls']} "
             "(YC's apply link is a login gate and is never followed)",
             f"Requests: {s['requests']} | runtime {s['seconds']} s" + (f" | STOPPED: {s['stopped']}" if s["stopped"] else ""),
             "Observed listings are NOT verified-open roles; zero listings never means 'not hiring'."]
    if "jobs_new_if_imported" in s:
        lines.append(f"If imported: new job rows {s['jobs_new_if_imported']}, already known {s['jobs_existing']} "
                     "(historical listings become company-level evidence, not job rows)")
    for slug, flag, state, n, kind in s["contradictions"]:
        lines.append(f"  contradiction: {slug} directory hiring={flag}, page {state} ({n} listings): {kind}")
    for slug, state, err in s["failed_or_blocked"]:
        lines.append(f"  {state}: {slug} - {err}")
    for slug, state, why in s["incomplete"]:
        lines.append(f"  incomplete ({state}): {slug} - {why}")
    return lines


def _configure_yc_jobs_report(parser):
    parser.add_argument("--snapshot", required=True, nargs="+", help="one or more snapshot files written by yc-jobs-collect")
    parser.add_argument("--csv", action="store_true", help="also write <snapshot>.listings.csv next to each snapshot (git-ignored)")


def cmd_yc_jobs_report(args) -> int:
    """Read-only coverage and freshness report over saved snapshots. Touches neither the network nor SQLite."""
    from .adapters import yc_jobs as yj
    try:
        snaps = [yj.load_snapshot(p) for p in args.snapshot]
    except yj.YCJobsError as exc:
        print(f"[REFUSED] {exc}")
        return 1
    merged = {"companies": [c for s in snaps for c in s["companies"]], "requests": sum(s["requests"] for s in snaps),
              "seconds": round(sum(s["seconds"] for s in snaps), 1), "stopped": next((s["stopped"] for s in snaps if s["stopped"]), None),
              "params": snaps[0].get("params", {})}
    print(f"Snapshots combined: {len(snaps)}")
    print("\n".join(_render_yc_jobs_summary(yj.summarize(merged))))
    if args.csv:
        for path, snap in zip(args.snapshot, snaps):
            out = yj.write_listings_csv(snap, str(path) + ".listings.csv")
            print(f"Listings CSV (git-ignored): {out}")
    return 0


def _configure_yc_jobs_import(parser):
    parser.add_argument("--snapshot", required=True, help="snapshot file written by yc-jobs-collect")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="show what would change (default)")
    group.add_argument("--apply", action="store_true", help="back up the database, then import (needs the operator's approval)")


def cmd_yc_jobs_import(args, db_path=None, backup_dir=None) -> int:
    from . import database
    from .adapters import jobspy_source as js
    from .adapters import yc_jobs as yj
    from .schema import SCHEMA_VERSION
    try:
        snapshot = yj.load_snapshot(args.snapshot)
    except yj.YCJobsError as exc:
        print(f"[REFUSED] {exc}")
        return 1
    path_db = db_path or database.resolve_db_path(PROJECT_ROOT)
    if not path_db.is_file():
        print("[REFUSED] database not initialized; run: python -m discovery init-db")
        return 1
    ro = database.connect(path_db, readonly=True)
    try:
        version = database.get_schema_version(ro)
        print("\n".join(_render_yc_jobs_summary(yj.summarize(snapshot, ro))))
    finally:
        ro.close()
    if not args.apply:
        print("\nDRY RUN: nothing was written. Re-run with --apply only after the operator approves the import.")
        return 0
    if version != SCHEMA_VERSION:
        print("[REFUSED] database schema is out of date; back it up and run init-db first")
        return 1
    backup = js.verified_backup(path_db, backup_dir, label="pre-yc-jobs-import")
    print(f"Verified backup: {backup}")
    conn = database.connect(path_db)
    try:
        counts = yj.import_snapshot(conn, snapshot)
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    print(f"Imported run {counts.run_id}: companies {counts.companies}, jobs created {counts.jobs_created}, changed "
          f"{counts.jobs_changed}, unchanged {counts.jobs_unchanged}, company errors {len(counts.company_errors)}; "
          f"integrity {'ok' if quick == 'ok' and not fk else 'PROBLEM'}")
    return 0 if counts.result == "success" else 2


def _configure_inv_collect(parser):
    parser.add_argument("--boards", required=True, help="boards to read, named one by one, e.g. accel,lightspeed")
    parser.add_argument("--scope", choices=["jobs", "companies", "both"], default="both")
    parser.add_argument("--location", help="apply this exact location value at the source (e.g. 'Bengaluru, Karnataka, India'); "
                                           "the scan is then complete for that filter scope only")
    parser.add_argument("--company-location", help="apply this exact location value to the company directory search too (only if the operator approved it; default: companies unfiltered)")
    parser.add_argument("--max-pages", type=int, help="lower the page cap for this run (cannot exceed the board's configured cap)")
    parser.add_argument("--max-requests", type=int, help="lower the request cap for this run (cannot exceed the configured cap)")
    parser.add_argument("--plan", action="store_true", help="show the exact request plan only; send nothing")
    parser.add_argument("--approved-by-operator", action="store_true",
                        help="state that the operator approved this live read (required unless --plan)")


def cmd_investor_boards_collect(args, http=None, sleep=None, snapshot_dir=None, config_path=None) -> int:
    """Read-only, bounded collection of investor-board jobs/companies into a git-ignored snapshot."""
    import dataclasses
    import time as _time

    from .adapters import investor_boards as ib
    try:
        boards = ib.select_boards(ib.load_boards(config_path or ib.CONFIG_PATH), args.boards)
    except ib.InvestorBoardError as exc:
        print(f"[REFUSED] {exc}")
        return 1
    scopes = ib.SCOPES if args.scope == "both" else (args.scope,)
    capped = []
    for b in boards:
        capped.append(dataclasses.replace(b, max_pages=min(b.max_pages, args.max_pages or b.max_pages),
                                          max_requests=min(b.max_requests, args.max_requests or b.max_requests)))
    boards = capped
    print("Investor-board read plan (nothing is sent unless --approved-by-operator and no --plan):")
    total_requests = 0
    for b in boards:
        reqs = b.max_requests * sum(1 for sc in scopes if sc == "jobs" or (b.platform == "getro" and b.company_directory == "next_data"))
        total_requests += reqs
        print(f"- {b.key}: {b.name} | platform {b.platform} | {b.jobs_url if 'jobs' in scopes else b.companies_url} | scopes {list(scopes)} | filter {('location = ' + args.location) if args.location else (b.location_filter and 'location = ' + b.location_filter) or 'none'} "
              f"| method {'GET board page, then POST cursor API with the page-issued token' if b.platform == 'consider' else ('POST client search API on ' + ', '.join(sorted(b.hosts - {b.url.split('/')[2]})) if b.api else 'GET board page(s) with embedded data')} "
              f"| <= {b.max_pages} pages and <= {b.max_requests} requests per scope | {b.min_delay_seconds:.0f}s between requests "
              f"| permission: {b.permission} | evidence: {b.evidence}")
    print(f"Maximum requests in total: {total_requests}; about {round(total_requests * 3 / 60, 1)} minutes at most. "
          "Writes: a git-ignored snapshot only (no SQLite, no Sheets).")
    if args.plan:
        print("PLAN ONLY: nothing was requested.")
        return 0
    if not args.approved_by_operator:
        print("[REFUSED] --approved-by-operator is required for a live read")
        return 1
    snapshot = ib.collect(boards, http=http, sleep=sleep or _time.sleep, scopes=scopes, location=args.location, company_location=getattr(args, "company_location", None))
    path = ib.write_snapshot(snapshot, snapshot_dir)
    print("\n".join(_render_inv_summary(ib.summarize(snapshot))))
    print(f"\nSnapshot saved (temporary, git-ignored): {path}")
    print("Nothing was written to SQLite, Google Sheets TEST or LIVE. Import needs a separate approval: "
          "python -m discovery investor-boards-import --snapshot <file> [--apply]")
    complete = all(s["complete"] for b in snapshot["boards"] for k, s in b["scopes"].items() if k == "jobs")
    return 0 if complete else 2


def _render_inv_summary(s):
    lines = []
    for b in s["boards"]:
        for scope, r in b["scopes"].items():
            tag = "COMPLETE" + (" (verified empty)" if r["verified_empty"] else "") if r["complete"] else r["status"].upper()
            lines.append(f"{b['key']} [{b['platform']}] {scope}: {tag} | records {r['records']} | reported total "
                         f"{r['reported_total'] if r['reported_total'] is not None else 'n/a'} | pages {r['pages_completed']}/"
                         f"{r['pages_requested']} | requests {r['requests']}"
                         + (f" | stopped: {r['stopped']}" if r["stopped"] else "") + (f" | error: {r['error']}" if r["error"] else ""))
            lines.extend(f"    anomaly: {a}" for a in r["anomalies"][:6])
    t = s["totals"]
    lines.append(f"Totals observed: {t['jobs']} jobs, {t['companies']} companies, {t['requests']} requests. A listing on a board is "
                 "NOT proof a job is open; a partial or failed scan is never evidence of absence.")
    if "identity" in s:
        i = s["identity"]
        lines.append(f"If imported: companies {i['company_resolution']} (possible name-only duplicates left unmerged: "
                     f"{i['companies_with_possible_name_duplicates']}); jobs new {i['jobs_new_if_imported']}, attach to an existing job by "
                     f"identical employer URL {i['jobs_attach_by_employer_url']} (ambiguous URL {i['jobs_ambiguous_url']}), already known "
                     f"{i['jobs_already_known']}; possible duplicate jobs across boards {i['possible_duplicate_jobs_across_boards']}")
    return lines


def _configure_ranking_preview(parser):
    parser.add_argument("--db", help="database to read (default: the project database, opened read-only); a disposable copy is fine")
    parser.add_argument("--as-of", help="reference time (ISO); default: the newest observation recorded in the database")
    parser.add_argument("--output", help="write the full machine-readable preview as JSON here (put it under exports/, which is Git-ignored)")
    parser.add_argument("--no-hints", action="store_true", help="skip the unresolved-identity review hints (faster)")
    parser.add_argument("--case-job-id", action="append", default=[], help="job id (prefix) to report individually in the preview; repeatable")
    parser.add_argument("--with-cached-upstream-attributes", action="store_true",
                        help="also read source-reported level / recruiter flags from the already-cached Feashliaa feed on disk (offline, no network)")


def cmd_ranking_preview(args, db_path=None) -> int:
    """Read-only: never writes to the database, never touches Sheets, never changes a lifecycle state."""
    import json as _json
    from . import candidates, database, profile as prof, ranking_preview
    path_db = Path(args.db) if getattr(args, "db", None) else (db_path or database.resolve_db_path(PROJECT_ROOT))
    if not path_db.is_file():
        print("[REFUSED] database not found")
        return 1
    try:
        pr, rk = prof.load_profile(), prof.load_ranking()
    except prof.ConfigError as exc:
        print(f"[REFUSED] configuration invalid: {exc}")
        return 1
    ro = database.connect(path_db, readonly=True)
    try:
        as_of = candidates.parse_when(args.as_of) if getattr(args, "as_of", None) else None
        upstream = None
        if getattr(args, "with_cached_upstream_attributes", False):
            from . import upstream_attrs
            upstream = upstream_attrs.load_feashliaa_attributes(ro)
        preview = ranking_preview.build_preview(ro, pr, rk, as_of=as_of, with_hints=not getattr(args, "no_hints", False),
                                                  case_job_ids=tuple(getattr(args, "case_job_id", []) or ()), upstream=upstream)
    finally:
        ro.close()
    print("\n".join(ranking_preview.render_text(preview)))
    if getattr(args, "output", None):
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(preview, indent=1, default=str), encoding="utf-8")
        print(f"\nFull preview written to {args.output}")
    return 0


def _configure_identity_report(parser):
    parser.add_argument("--examples", type=int, default=2, help="representative examples to keep per rule")
    parser.add_argument("--output", help="also write the full report as JSON to this file (read-only otherwise)")
    parser.add_argument("--review-output", help="write the detailed evidence review (tier B/D categories, promotion proposals, "
                                                "same-source repeats, title conflicts) as JSON to this file; put it under exports/ (Git-ignored)")


def cmd_identity_report(args, db_path=None) -> int:
    """Read-only: opens the database read-only and never proposes or applies a merge by itself."""
    import json as _json
    from . import database, identity
    path_db = db_path or database.resolve_db_path(PROJECT_ROOT)
    if not path_db.is_file():
        print("[REFUSED] database not initialized; run: python -m discovery init-db")
        return 1
    ro = database.connect(path_db, readonly=True)
    try:
        report = identity.build_report(ro, examples=max(0, args.examples))
    finally:
        ro.close()
    print("\n".join(identity.render_report(report)))
    if getattr(args, "review_output", None):
        from . import identity_review
        ro2 = database.connect(path_db, readonly=True)
        try:
            review = identity_review.build_review(ro2)
        finally:
            ro2.close()
        out = Path(args.review_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(review, indent=1, default=str), encoding="utf-8")
        print(f"Detailed review written to {args.review_output} ({len(review['promotion_proposals'])} promotion proposals need operator approval; nothing was applied)")
    if args.output:
        Path(args.output).write_text(_json.dumps(report, indent=1), encoding="utf-8")
        print(f"Full report written to {args.output}")
    return 0


def _configure_inv_report(parser):
    parser.add_argument("--snapshot", required=True, nargs="+", help="one or more snapshots written by investor-boards-collect")


def cmd_investor_boards_report(args) -> int:
    from .adapters import investor_boards as ib
    try:
        for path in args.snapshot:
            snap = ib.load_snapshot(path)
            print(f"== {path}")
            print("\n".join(_render_inv_summary(ib.summarize(snap))))
    except ib.InvestorBoardError as exc:
        print(f"[REFUSED] {exc}")
        return 1
    return 0


def _configure_inv_import(parser):
    parser.add_argument("--snapshot", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="show what would change (default)")
    group.add_argument("--apply", action="store_true", help="back up the database, then import (needs the operator's approval)")


def cmd_investor_boards_import(args, db_path=None, backup_dir=None) -> int:
    from . import database
    from .adapters import investor_boards as ib
    from .adapters import jobspy_source as js
    from .schema import SCHEMA_VERSION
    try:
        snapshot = ib.load_snapshot(args.snapshot)
    except ib.InvestorBoardError as exc:
        print(f"[REFUSED] {exc}")
        return 1
    path_db = db_path or database.resolve_db_path(PROJECT_ROOT)
    if not path_db.is_file():
        print("[REFUSED] database not initialized; run: python -m discovery init-db")
        return 1
    ro = database.connect(path_db, readonly=True)
    try:
        version = database.get_schema_version(ro)
        print("\n".join(_render_inv_summary(ib.summarize(snapshot, ro))))
    finally:
        ro.close()
    if not args.apply:
        print("\nDRY RUN: nothing was written. Re-run with --apply only after the operator approves the import.")
        return 0
    if version != SCHEMA_VERSION:
        print("[REFUSED] database schema is out of date; back it up and run init-db first")
        return 1
    backup = js.verified_backup(path_db, backup_dir, label="pre-investor-boards-import")
    print(f"Verified backup: {backup}")
    conn = database.connect(path_db)
    try:
        c = ib.import_snapshot(conn, snapshot)
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    print(f"Imported run {c.run_id}: companies created {c.companies_created}, linked by domain {c.companies_linked_by_domain}, "
          f"existing {c.companies_existing_sighting}, ambiguous (unlinked) {c.companies_ambiguous}; jobs created {c.jobs_created}, "
          f"attached to existing jobs {c.jobs_attached}, changed {c.jobs_changed}, unchanged {c.jobs_unchanged}; errors {len(c.errors)}; integrity "
          f"{'ok' if quick == 'ok' and not fk else 'PROBLEM'}")
    return 0 if c.result == "success" else 2


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
    "investor-boards-collect": ("Read investor/accelerator boards into a snapshot (bounded, read-only; operator-approved)",
                                cmd_investor_boards_collect, _configure_inv_collect),
    "ranking-preview": ("Read-only ranking preview with the proposed profile and weights; publishes and changes nothing", cmd_ranking_preview, _configure_ranking_preview),
    "identity-report": ("Read-only identity reconciliation report (duplicate evidence by tier); changes nothing", cmd_identity_report, _configure_identity_report),
    "investor-boards-report": ("Read-only report over saved investor-board snapshots", cmd_investor_boards_report, _configure_inv_report),
    "investor-boards-import": ("Import an investor-board snapshot after YOUR approval (dry-run by default)",
                               cmd_investor_boards_import, _configure_inv_import),
    "yc-jobs-collect": ("Collect public YC company job pages into a snapshot (read-only; user-attested authorization)",
                        cmd_yc_jobs_collect, _configure_yc_jobs_collect),
    "yc-jobs-report": ("Read-only coverage/freshness report over saved yc_jobs snapshots (optional CSV)",
                       cmd_yc_jobs_report, _configure_yc_jobs_report),
    "yc-jobs-import": ("Import a yc_jobs snapshot after YOUR approval (dry-run by default; --apply backs up first)",
                       cmd_yc_jobs_import, _configure_yc_jobs_import),
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
