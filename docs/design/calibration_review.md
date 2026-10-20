# Day 12 calibration review (your judgments, not mine)

File: `exports/calibration/day12_calibration_sample.csv` (Git-ignored). 54 jobs, 8 per stratum: confirmed_eligible,
needs_review, exploration, low_priority, and three hard-exclusion reasons, so you can see what the ranking hides.

For each row fill **judgment** with one of `relevant`, `maybe`, `not_relevant`, `cannot_tell`; **reason** and **notes** are optional.
The code never fills or guesses these columns; blank stays blank and is counted as blank.

Evaluate with `calibration.evaluate(path)`: qualified precision, relevant jobs found in excluded/low priority (missed), and
agreement per stratum. Rows must carry a `day12-calibration-` batch id; the evaluator refuses anything else so this set stays
separate from the Day 13 15-post benchmark. Sample size is small: treat results as a smoke test, not a measurement.
