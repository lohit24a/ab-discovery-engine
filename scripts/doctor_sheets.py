"""Read-only health check for Google Sheets access (TEST spreadsheet only).

Run with the project virtual environment:
    .\\.venv\\Scripts\\python.exe scripts\\doctor_sheets.py

Never writes to any spreadsheet and never prints credentials.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_TABS = ["Shortlist", "Posts", "Companies", "Runs"]
READONLY_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def ok(msg):
    print(f"[ OK ] {msg}")


def fail(msg, hint=None):
    print(f"[FAIL] {msg}")
    if hint:
        print(f"       Fix: {hint}")
    return 1


def main():
    print(f"Python {sys.version.split()[0]} ({sys.executable})")

    try:
        from dotenv import load_dotenv
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        return fail(f"Missing package: {exc.name}",
                    "install gspread, google-auth and python-dotenv into .venv")

    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return fail(f"Config file not found: {env_path}", "create .env from .env.example")
    load_dotenv(env_path)
    ok(".env loaded")

    sheet_id = os.getenv("GOOGLE_SHEET_ID_TEST", "").strip()
    cred_rel = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not sheet_id:
        return fail("GOOGLE_SHEET_ID_TEST is not set in .env")
    if not cred_rel:
        return fail("GOOGLE_SERVICE_ACCOUNT_JSON is not set in .env")

    cred_path = (PROJECT_ROOT / cred_rel).resolve()
    if not cred_path.is_file():
        return fail(f"Credential file not found at {cred_rel}",
                    "place the service-account JSON there (do not commit it)")
    ok(f"Credential file found ({cred_rel})")

    try:
        creds = Credentials.from_service_account_file(str(cred_path), scopes=READONLY_SCOPES)
        client = gspread.authorize(creds)
        ok(f"Service account loaded: {creds.service_account_email}")
    except Exception as exc:
        return fail(f"Could not load credentials: {type(exc).__name__}",
                    "check the JSON is a valid service-account key")

    try:
        spreadsheet = client.open_by_key(sheet_id)
        ok(f"Opened TEST spreadsheet: '{spreadsheet.title}'")
    except gspread.exceptions.SpreadsheetNotFound:
        return fail("TEST spreadsheet not found or not shared with the service account",
                    "share it with the service-account email as Editor and check the ID")
    except gspread.exceptions.APIError as exc:
        status = getattr(exc.response, "status_code", "?")
        return fail(f"Google API error {status} opening the spreadsheet",
                    "enable Google Sheets API (and Drive API) in the Cloud project; "
                    "403 usually means API disabled or sheet not shared")
    except Exception as exc:
        return fail(f"Unexpected error opening spreadsheet: {type(exc).__name__}")

    tabs = [ws.title for ws in spreadsheet.worksheets()]
    if sorted(tabs) == sorted(REQUIRED_TABS) and len(tabs) == len(REQUIRED_TABS):
        ok(f"Exactly four tabs present: {', '.join(tabs)}")
    else:
        missing = [t for t in REQUIRED_TABS if t not in tabs]
        extra = [t for t in tabs if t not in REQUIRED_TABS]
        return fail(f"Tab mismatch. Found {tabs}; missing {missing}; unexpected {extra}")

    try:
        rows = spreadsheet.worksheet("Posts").get("A1:D5")
        ok(f"Read Posts!A1:D5 successfully ({sum(1 for r in rows if any(r))} non-empty row(s))")
    except Exception as exc:
        return fail(f"Could not read Posts tab: {type(exc).__name__}",
                    "confirm the service account has access to the sheet")

    print("\nAll checks passed. No data was modified (read-only scope used).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
