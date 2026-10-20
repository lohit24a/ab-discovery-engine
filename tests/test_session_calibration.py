"""Day 12 audit: session lanes, calibration dataset/evaluator, and the 'every accepted setting is consumed' guard."""
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from discovery import calibration as cal
from discovery import profile as prof
from discovery import session as ses

ROOT = Path(__file__).resolve().parent.parent
AS_OF = datetime(2026, 10, 20, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def ranking():
    return prof.load_ranking()


def rec(i, cls="confirmed_eligible", score=60, first_days_ago=1, verdict=None, **over):
    r = {"kind": "job", "id": i, "company": "Co" + i, "title": "T" + i, "locations": ["Bengaluru"], "availability": "in_latest_check",
         "classification": cls, "score": score, "verdict": verdict, "hard_exclusion": None, "family": "x", "location_category": "bengaluru",
         "explanation": "e", "local_first_seen": (AS_OF - timedelta(days=first_days_ago)).isoformat()}
    r.update(over)
    return r


def test_lanes_are_exclusive_and_old_high_scores_do_not_take_new_slots(ranking):
    old = rec("old", score=99, first_days_ago=90)
    new = rec("new", score=50, first_days_ago=2)
    shown_same = rec("seen", score=80, first_days_ago=30)
    changed = rec("chg", score=70, first_days_ago=30)
    hist = {"seen": ses.fingerprint(shown_same), "chg": "different"}
    lanes = ses.assign_lanes([old, new, shown_same, changed], hist, AS_OF, ranking)
    assert [r["id"] for r in lanes["new_discovery"]] == ["new"]
    assert [r["id"] for r in lanes["changed"]] == ["chg"]
    assert [r["id"] for r in lanes["previously_unreviewed"]] == ["seen"]
    assert "old" not in {r["id"] for items in lanes.values() for r in items}      # never shown, old, confirmed: not a "new" item


def test_verdict_records_and_excluded_are_never_offered(ranking):
    lanes = ses.assign_lanes([rec("y", verdict="Yes"), rec("n", verdict="No"), rec("x", cls="excluded")], {}, AS_OF, ranking)
    assert all(not v for v in lanes.values())


def test_slots_cap_each_lane_and_never_pad(ranking):
    many = [rec(f"n{i}", score=50 + i) for i in range(30)]
    lanes = ses.assign_lanes(many, {}, AS_OF, ranking)
    assert len(lanes["new_discovery"]) == ranking["session"]["slots"]["new_discovery"] and lanes["new_discovery"][0]["score"] == 79
    assert lanes["changed"] == []


def test_history_loader_is_tolerant_and_read_only(tmp_path):
    import sqlite3
    c = sqlite3.connect(tmp_path / "x.db")
    c.execute("CREATE TABLE runs (run_id TEXT, started_at TEXT, summary TEXT)")
    c.executemany("INSERT INTO runs VALUES (?,?,?)", [("1", "a", '{"listed": {"j1": "f1"}}'), ("2", "b", "not json"), ("3", "c", None), ("4", "d", '{"x": 1}')])
    assert ses.load_history(c) == {"j1": "f1"}
    assert ses.load_history(sqlite3.connect(":memory:")) == {}


def test_calibration_sample_is_deterministic_blank_and_separate_from_day13(tmp_path):
    results = [rec(f"r{i}", cls=c) for i, c in enumerate(["confirmed_eligible"] * 8 + ["needs_review"] * 8)]
    a, b = cal.build_sample(results, per_stratum=3), cal.build_sample(results, per_stratum=3)
    assert a == b and len(a) == 6 and all(r["judgment"] == "" and r["reason"] == "" and r["notes"] == "" for r in a)
    assert all(r["batch_id"].startswith(cal.BATCH_PREFIX) for r in a)
    path = cal.write_csv(a, tmp_path / "s.csv")
    assert cal.evaluate(path)["judged"] == 0                                # blank stays blank; nothing is imputed
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    rows[0]["judgment"], rows[1]["judgment"] = "relevant", "not_relevant"
    cal.write_csv(rows, path)
    out = cal.evaluate(path)
    assert out["judged"] == 2 and out["blank"] == 4
    rows[0]["batch_id"] = "day13-benchmark-1"
    cal.write_csv(rows, path)
    with pytest.raises(ValueError):
        cal.evaluate(path)


def test_every_accepted_ranking_setting_is_consumed():
    """A knob that the code never reads is a silent lie in the config; each leaf name must appear in the scoring code."""
    raw = prof.load_ranking()
    code = "".join((ROOT / "discovery" / f).read_text(encoding="utf-8") for f in ("scoring.py", "session.py", "ranking_preview.py", "candidates.py"))
    unused = []

    def walk(d, path=()):
        for k, v in d.items():
            if isinstance(v, dict):
                if path[:1] == ("date_semantics",) or k == "date_semantics":
                    continue
                walk(v, path + (k,))
            elif path[:2] in (("jobs", "source_quality"), ("companies", "india_connection")):
                continue                   # looked up by the source type / India-relevance class found in the data (dynamic keys)
            elif k not in code and path[:1] not in (("reference",),) and k not in ("version", "status"):
                unused.append(".".join(path + (k,)))
    walk(raw)
    assert not unused, f"settings accepted but never consumed: {unused}"


def test_every_accepted_profile_setting_is_consumed():
    raw = prof.load_profile().raw
    code = "".join((ROOT / "discovery" / f).read_text(encoding="utf-8") for f in ("scoring.py", "filters.py", "candidates.py", "session.py", "ranking_preview.py"))
    dynamic = {"role_families", "company_interests", "role_types", "version", "status", "unknown_handling", "role_screening"}
    unused = []
    for top, section in raw.items():
        if top in dynamic or not isinstance(section, dict):
            continue
        # invariants: the loader REJECTS any other value (missing experience is never senior; notes are never modified)
        unused += [f"{top}.{k}" for k in section if k not in code and f"{top}.{k}" not in ("experience.unknown_treatment", "verdicts.never_modify_notes")]
    assert not unused, f"profile settings accepted but never consumed: {unused}"
