# Small TEST feedback round-trip: what you do, what I do, where I stop

Goal: prove on the real TEST sheet that your Yes / Maybe / No and Notes are read back by stable record ID, survive sorting and repeated
syncs, and that a rejected job leaves the active shortlist without a new batch being published. Nothing here is done for you: I never
type a verdict or a note, and I do not run the real sync until you say so.

## Your part (about 5 minutes, in the TEST sheet only)
1. Open the **Posts** tab (not Shortlist; the Shortlist tab is a generated view and anything typed there is ignored and overwritten).
2. Pick any three jobs that also appear on the Shortlist tab. In **Your Verdict** type exactly `Yes`, `Maybe`, `No` (one each) and in
   **Your Notes** write a short note for each. Use your real opinion; the examples in the review sample (`exports/calibration/day12_review_first.md`) can help.
3. Optional but useful: sort the Posts tab by **Company** (Data > Sort sheet). Do not insert, delete or rename columns or edit grey/machine columns.
4. Do **not** type in the Shortlist, Companies or Runs tabs for this test, and do not run any command.
5. Tell me "verdicts entered".

## My part after you tell me (read-only first)
1. Run `python -m discovery sync --target test --dry-run --with-shortlist` and show you the report. Expected, if you entered one of each:
   * `Verdicts/notes imported into SQLite: 3`, no conflicts, no warnings.
   * `Review session: CONTINUING 2e05066b ... 1 dropped` (the job you marked No is removed from the Shortlist view, **not replaced**).
   * Posts: 0 rows to add; the only cell updates are machine metadata corrections (explanation wording: 14 Posts + about 11 Shortlist cells)
     and clearing the last Shortlist row; no Verdict or Notes cell is changed.
2. **Stop and wait for your explicit approval of that specific plan.**
3. After your approval only: `python -m discovery sync --target test --with-shortlist` (continues the same session). A verified database backup first.
4. Verify by a second dry-run (expect zero changes), a read-only look at the sheet (your three verdicts and notes unchanged and on the right
   rows even after sorting) and a check that `verdicts` in SQLite holds exactly your three entries.

## Not part of this test
Starting the next review session (`--new-session`), importing manual jobs, applying migration 7, any LIVE write.
