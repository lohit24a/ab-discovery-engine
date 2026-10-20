"""Column specification for the four Google Sheets tabs (version-controlled).

Ownership of every column:

  key      - the immutable record identifier. Written once when a row is created, never changed.
  machine  - generated from SQLite. Python may rewrite these cells; hand edits are overwritten.
  human    - 'Your Verdict' and 'Your Notes' (Posts and Companies only). Python never replaces a
             human value; it only imports it into SQLite, or restores a blank cell from the
             value already stored in SQLite.

Posts and Companies are matched to SQLite by record_id (the SQLite UUID), never by row number,
so sorting, filtering or reordering columns in the sheet cannot move a verdict to another record.
Columns are located by header name. Extra columns added by the user are left untouched.

Fields we have not implemented yet (score, explanation, unique/new record counts, shortlisted
counts) are written blank rather than invented.
"""

from dataclasses import dataclass

KEY, MACHINE, HUMAN = "key", "machine", "human"

VERDICT_COLUMN = "Your Verdict"
NOTES_COLUMN = "Your Notes"
VERDICT_VALUES = ("Yes", "Maybe", "No")  # blank = not yet reviewed

SHEET_SCHEMA_VERSION = 1
TAB_ORDER = ("Shortlist", "Posts", "Companies", "Runs")
KEY_COLUMN = {"Posts": "record_id", "Companies": "record_id", "Runs": "Run ID", "Shortlist": "record_id"}


@dataclass(frozen=True)
class Column:
    name: str
    owner: str


def _cols(key, machine, human=()):
    return ([Column(key, KEY)] + [Column(n, MACHINE) for n in machine] + [Column(n, HUMAN) for n in human])


TAB_COLUMNS = {
    "Posts": _cols(
        "record_id",
        ["Company", "Job Title", "Location", "Remote Eligibility", "Experience", "State",
         "First Seen", "Last Seen", "Job Link", "Sources", "Score", "Explanation"],
        [VERDICT_COLUMN, NOTES_COLUMN]),
    "Companies": _cols(
        "record_id",
        ["Company", "Domain", "Location", "Details", "Sources", "Hiring Evidence",
         "First Seen", "Last Seen", "Score", "Explanation"],
        [VERDICT_COLUMN, NOTES_COLUMN]),
    # Generated view only: decisions belong in Posts and Companies, never here.
    "Shortlist": [Column(n, KEY if n == "record_id" else MACHINE) for n in
                  ["Type", "record_id", "Name / Title", "Company", "Score", "Explanation", "Link"]],
    "Runs": _cols(
        "Run ID",
        ["Started", "Finished", "Status", "Type", "Sources Succeeded", "Sources Failed/Partial/Skipped",
         "Records Processed", "Unique Records", "New Records", "Shortlisted", "Errors"]),
}


def column_names(tab):
    return [c.name for c in TAB_COLUMNS[tab]]


def owner_of(tab, name):
    return next(c.owner for c in TAB_COLUMNS[tab] if c.name == name)
