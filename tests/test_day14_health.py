"""Day 14: deterministic check ordering and the health report's attention summary."""
import pytest

from discovery import lifecycle, source_health
from tests.test_day14_recovery import check, conn, db_now  # noqa: F401  (fixtures and helpers)

T = "2026-10-20T10:00:00+00:00"


def test_two_runs_in_the_same_second_resolve_to_the_later_one_deterministically(conn):
    check(conn, "feed", "success", complete=True, n=10, stype="ats_feed", started=T)
    check(conn, "feed", "failed", err="HTTP 403: access restricted; nothing retried", stype="ats_feed", started=T)           # same second, recorded later
    assert next(r for r in source_health.source_health(conn, now=db_now()) if r["source_id"] == "feed")["state"] == "blocked"
    check(conn, "feed2", "failed", err="HTTP 429", stype="ats_feed", started=T)
    check(conn, "feed2", "success", complete=True, n=3, stype="ats_feed", started=T)
    assert next(r for r in source_health.source_health(conn, now=db_now()) if r["source_id"] == "feed2")["state"] == "complete"
    assert lifecycle.latest_run_checks(conn, "any")["feed2"]["complete"] == 1                                           # lifecycle picks the same (later) run
    for _ in range(3):                                                                                                  # and it is stable
        assert next(r for r in source_health.source_health(conn, now=db_now()) if r["source_id"] == "feed")["state"] == "blocked"


def test_the_health_report_names_every_source_that_needs_attention(conn):
    check(conn, "ok", "success", complete=True, scope="jobs:none", n=5, stype="ats_feed")
    check(conn, "bad", "failed", err="HTTP 429", stype="investor_board")
    check(conn, "half", "partial", n=3, err="count mismatch", stype="investor_board")
    last = source_health.render(source_health.source_health(conn, now=db_now()))[-1]
    assert last.startswith("Sources needing attention:") and "bad (blocked)" in last and "half (partial)" in last and "ok (" not in last
    only_ok = [r for r in source_health.source_health(conn, now=db_now()) if r["source_id"] == "ok"]
    assert source_health.render(only_ok)[-1].startswith("All sources")


def test_a_partial_run_is_never_presented_as_complete_or_able_to_close_jobs(conn):
    check(conn, "half", "partial", n=3, err="count mismatch", stype="investor_board")
    row = next(r for r in source_health.source_health(conn, now=db_now()) if r["source_id"] == "half")
    assert row["state"] != "complete" and row["completeness"] == "low" and row["may_close_jobs"] is False
