"""Command-line interface for the discovery engine.

Subcommands are registered in COMMANDS so that `run`, `import`, `sync` and
others can be added later without restructuring this module.
"""

import argparse
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
        add(PASS, "Local config present: .env (contents not read)")
    else:
        add(WARNING, "Local config .env not found; copy .env.example to .env and fill it in")

    try:
        import discovery  # noqa: F401
        add(PASS, f"Package 'discovery' imports (version {discovery.__version__})")
    except Exception as exc:
        add(FAIL, f"Package 'discovery' failed to import: {type(exc).__name__}: {exc}")

    if (root / DEFAULT_DB_PATH).is_file():
        add(PASS, f"SQLite database found: {DEFAULT_DB_PATH}")
    else:
        add(WARNING, f"SQLite database not initialized yet ({DEFAULT_DB_PATH}); expected at this stage")

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


# Register new subcommands here: name -> (help text, handler)
COMMANDS = {
    "doctor": ("Run local project health checks", cmd_doctor),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="discovery", description="A+B Discovery Engine")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    for name, (help_text, handler) in COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(handler=handler)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)
