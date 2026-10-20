"""Human-review calibration dataset and evaluator (Day 12 audit). Offline, deterministic, writes only under exports/.

The dataset is a stratified, deterministic sample for the OPERATOR to judge. The judgment columns are written BLANK and are
never filled, guessed or inferred by this code. The evaluator reads a CSV the operator has filled in and reports how the
current ranking agrees with those judgments. It is kept separate from the Day 13 fifteen-post benchmark: a different
batch id prefix ("day12-calibration-") and a guard that refuses to evaluate anything else.
"""

import csv
import hashlib
from pathlib import Path

BATCH_PREFIX = "day12-calibration-"
JUDGMENT_COLUMNS = ("judgment", "reason", "notes")
JUDGMENTS = ("relevant", "maybe", "not_relevant", "cannot_tell")
FIELDS = ("batch_id", "sample_id", "stratum", "kind", "record_id", "company", "title", "locations", "classification", "score",
          "family", "location_category", "availability", "explanation") + JUDGMENT_COLUMNS


def _order(record_id, salt):
    return hashlib.sha256(f"{salt}|{record_id}".encode()).hexdigest()


def stratum(r):
    if r["classification"] == "excluded":
        return "excluded:" + str(r["hard_exclusion"])
    return r["classification"]


def build_sample(results, per_stratum=6, salt="day12", batch_id=None):
    """Deterministic stratified sample: the same data and salt always give the same rows. Judgment columns stay empty."""
    pools = {}
    for r in results:
        if r["kind"] == "job":
            pools.setdefault(stratum(r), []).append(r)
    batch_id = batch_id or BATCH_PREFIX + hashlib.sha256(salt.encode()).hexdigest()[:8]
    rows = []
    for name in sorted(pools):
        for i, r in enumerate(sorted(pools[name], key=lambda x: _order(x["id"], salt))[:per_stratum], 1):
            rows.append({"batch_id": batch_id, "sample_id": f"{name}#{i}", "stratum": name, "kind": r["kind"], "record_id": r["id"],
                         "company": r["company"], "title": r.get("title", ""), "locations": " | ".join(r.get("locations", [])),
                         "classification": r["classification"], "score": r["score"], "family": r.get("family") or "",
                         "location_category": r.get("location_category", ""), "availability": r.get("availability", ""),
                         "explanation": r["explanation"], "judgment": "", "reason": "", "notes": ""})
    return rows


def write_csv(rows, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return path


def evaluate(path):
    """Compare operator judgments with the ranking. Rows without a judgment are counted, never imputed."""
    with Path(path).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    bad = [r for r in rows if not (r.get("batch_id") or "").startswith(BATCH_PREFIX)]
    if bad:
        raise ValueError("refusing to evaluate: rows outside a day12-calibration batch (the Day 13 benchmark is evaluated separately)")
    judged = [r for r in rows if r["judgment"].strip()]
    unknown = [r["judgment"] for r in judged if r["judgment"].strip() not in JUDGMENTS]
    if unknown:
        raise ValueError(f"unrecognised judgment value(s): {sorted(set(unknown))}; allowed: {JUDGMENTS}")
    useful = [r for r in judged if r["judgment"] in ("relevant", "maybe", "not_relevant")]
    positive = lambda r: r["judgment"] in ("relevant", "maybe")
    surfaced = lambda r: r["classification"] in ("confirmed_eligible", "needs_review", "exploration")
    tp = sum(1 for r in useful if positive(r) and surfaced(r))
    fn = sum(1 for r in useful if positive(r) and not surfaced(r))
    fp = sum(1 for r in useful if not positive(r) and r["classification"] == "confirmed_eligible")
    qualified = [r for r in useful if r["classification"] == "confirmed_eligible"]
    return {"rows": len(rows), "judged": len(judged), "blank": len(rows) - len(judged), "cannot_tell": sum(1 for r in judged if r["judgment"] == "cannot_tell"),
            "qualified_precision": (sum(1 for r in qualified if positive(r)) / len(qualified)) if qualified else None,
            "missed_relevant_in_excluded_or_low": fn, "relevant_surfaced": tp, "qualified_but_not_relevant": fp,
            "by_stratum": {s: {"relevant": sum(1 for r in useful if r["stratum"] == s and positive(r)),
                               "not_relevant": sum(1 for r in useful if r["stratum"] == s and not positive(r))}
                           for s in sorted({r["stratum"] for r in useful})}}
