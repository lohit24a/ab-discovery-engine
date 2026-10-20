"""Integrated pipeline tests: offline fixtures only (YC feed, Feashliaa feed, fake spreadsheet)."""

import argparse
import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from discovery import cli, database as db, pipeline, sheets_sync
from discovery.adapters import feashliaa as fz, yc_companies as yc
from tests.test_feashliaa import Feed
from tests.test_sheets_sync import FakeBackend
from tests.test_yc_companies import india, make_fetch, rec

REPO = Path(__file__).resolve().parent.parent
PF = fz.Prefilter(["analyst", "associate", "operations"])


def stamp(hours_ago, z=True):
    t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000000Z") if z else t.isoformat()


def yc_fetch(records=None, hours_ago=1):
    records = records if records is not None else [rec(1), india(2), rec(3, "Hiring Co", isHiring=True)]
    return make_fetch(records, updated=stamp(hours_ago))


def job(i, scraped_h=2.2, **over):
    from tests.test_feashliaa import row
    return row(i, company=over.pop("company", f"co{i}"), scraped=stamp(scraped_h), first=stamp(500),
               title=over.pop("title", "Business Analyst"), **over)


def fz_feed(rows=None, hours_ago=2):
    rows = rows if rows is not None else [job(1), job(2, scraped_h=300), job(3, company="co3", location="Remote")]
    return Feed([rows], generated=stamp(hours_ago))


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "pipe.db"
    db.initialize_database(path)
    c = db.connect(path)
    yield c
    c.close()


def tmp_of(conn):
    return Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent


def go(conn, sources=("yc", "feashliaa"), *, fetchers=None, backend=None, **kw):
    fetchers = fetchers if fetchers is not None else {"yc": yc_fetch(), "feashliaa": fz_feed()}
    kw.setdefault("target", "test")
    return pipeline.run_pipeline(conn, sources=list(sources), fetchers=fetchers, cache_dir=tmp_of(conn) / "cache",
                                 prefilter=PF, backend_factory=(lambda t: backend) if backend is not None else
                                 (lambda t: FakeBackend()), **kw)


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def status_of(result, name):
    return next(o for o in result.outcomes if o.name == name)


def snapshot_db(conn):
    tables = ("companies", "jobs", "sightings", "runs", "source_checks", "status_events", "verdicts", "sources")
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}


def children(conn, parent):
    return conn.execute("SELECT * FROM runs WHERE json_extract(summary, '$.parent_run_id') = ?", (parent,)).fetchall()


# 1 / 9 / 10 --------------------------------------------------------------------------------------------------
def test_both_sources_succeed_with_linked_runs_and_accurate_counts(conn):
    result = go(conn)
    assert result.overall == "success" and [o.status for o in result.outcomes] == ["success", "success"]
    yc_out, fz_out = status_of(result, "yc"), status_of(result, "feashliaa")
    assert yc_out.metrics["new"] == 3 and yc_out.metrics["india_based"] == 1 and yc_out.metrics["records_scanned"] == 3
    assert fz_out.metrics["rows_scanned"] == 3 and fz_out.metrics["jobs_created"] == 3
    assert (fz_out.metrics["latest_collection"], fz_out.metrics["provider_retained"]) == (2, 1)
    assert count(conn, "companies") == 3 + 3 and count(conn, "jobs") == 3
    kids = children(conn, result.run_id)
    assert {json.loads(k["summary"])["source"] for k in kids} == {"yc", "feashliaa"}
    assert {k["run_id"] for k in kids} == {yc_out.child_run_id, fz_out.child_run_id}
    assert all(k["run_type"] == "import" and k["result"] == "success" for k in kids)
    parent = conn.execute("SELECT * FROM runs WHERE run_id = ?", (result.run_id,)).fetchone()
    assert parent["run_type"] == "discovery" and parent["result"] == "success" and parent["finished_at"]
    assert result.integrity == "ok"
    stored = pipeline.load_summary(conn)
    assert stored["run_id"] == result.run_id and stored["outcomes"][0]["metrics"]["new"] == 3
    text = pipeline.render(stored)
    assert "SUCCESS" in text and "provider_retained" in text and "NOT verified open" in text


def test_overall_status_rules():
    ok, bad, part, skip = (pipeline.SourceOutcome("yc", s) for s in ("success", "failed", "partial", "skipped"))
    assert pipeline._overall([ok, skip], None) == "success"
    assert pipeline._overall([ok, bad], None) == "partial" and pipeline._overall([ok, part], None) == "partial"
    assert pipeline._overall([bad, bad], None) == "failed" and pipeline._overall([skip, skip], None) == "success"
    assert pipeline._overall([ok], pipeline.SyncOutcome("write_failed")) == "partial"
    assert pipeline._overall([pipeline.SourceOutcome("yc", "dry-run")], None) == "dry-run"


# 2 / 3 / 4 -----------------------------------------------------------------------------------------------------
def test_yc_succeeds_when_feashliaa_fails(conn):
    broken = fz_feed()
    broken.set_manifest(totalJobs=...)
    result = go(conn, fetchers={"yc": yc_fetch(), "feashliaa": broken})
    assert result.overall == "partial"
    assert status_of(result, "yc").status == "success" and status_of(result, "feashliaa").status == "failed"
    assert conn.execute("SELECT COUNT(*) FROM companies WHERE json_extract(facts,'$.yc') IS NOT NULL").fetchone()[0] == 3
    assert count(conn, "jobs") == 0
    assert any("retry" in p for p in result.pending)
    check = conn.execute("SELECT outcome FROM source_checks WHERE source_id = 'feashliaa'").fetchone()[0]
    assert check == "failed"


def test_feashliaa_succeeds_when_yc_fails(conn):
    def dead(url):
        raise yc.SourceError("connection refused")

    result = go(conn, fetchers={"yc": dead, "feashliaa": fz_feed()})
    assert result.overall == "partial"
    assert status_of(result, "yc").status == "failed" and status_of(result, "feashliaa").status == "success"
    assert count(conn, "jobs") == 3
    assert conn.execute("SELECT outcome FROM source_checks WHERE source_id = 'yc_oss'").fetchone()[0] == "failed"
    assert not conn.execute("SELECT 1 FROM status_events WHERE new_state IN ('Closed','Not Seen')").fetchone()


def test_both_sources_failing_is_a_failed_run_with_nothing_lost(conn):
    go(conn)   # real data first
    before = (count(conn, "companies"), count(conn, "jobs"))

    def dead(url, **kw):
        raise yc.SourceError("down")

    class DeadFeed(Feed):
        def __call__(self, url, **kw):
            raise fz.SourceError("down")

    result = go(conn, fetchers={"yc": dead, "feashliaa": DeadFeed([[job(1)]])})
    assert result.overall == "failed" and {o.status for o in result.outcomes} == {"failed"}
    assert (count(conn, "companies"), count(conn, "jobs")) == before
    assert not conn.execute("SELECT 1 FROM status_events WHERE new_state IN ('Closed','Not Seen','Active')").fetchone()
    assert conn.execute("SELECT result FROM runs WHERE run_type = 'discovery' ORDER BY rowid DESC").fetchone()[0] == "failed"


# 5 / 6 / 7 -----------------------------------------------------------------------------------------------------
def test_already_imported_feashliaa_snapshot_is_skipped_not_failed(conn):
    fetchers = {"yc": yc_fetch(), "feashliaa": fz_feed()}
    go(conn, fetchers=fetchers)
    second = go(conn, fetchers=fetchers)
    skipped = status_of(second, "feashliaa")
    assert skipped.status == "skipped" and second.overall == "success"
    assert "SKIPPED (already processed, not a failure)" in pipeline.render(second.to_summary())
    assert count(conn, "jobs") == 3
    assert not [p for p in second.pending if "failed" in p or "partial" in p]   # a skipped source is not pending work
    assert not conn.execute("SELECT 1 FROM status_events WHERE new_state IN ('Closed','Not Seen')").fetchone()


def test_newer_feashliaa_snapshot_is_processed_without_duplicates(conn):
    go(conn, fetchers={"yc": yc_fetch(), "feashliaa": fz_feed()})
    newer_rows = [job(1, scraped_h=0.6, title="Senior Business Analyst"), job(2, scraped_h=0.6), job(4, scraped_h=0.6)]
    second = go(conn, fetchers={"yc": yc_fetch(hours_ago=0.5), "feashliaa": fz_feed(newer_rows, hours_ago=0.5)})
    m = status_of(second, "feashliaa").metrics
    assert second.overall == "success" and m["jobs_created"] == 1 and m["jobs_changed"] == 1
    assert count(conn, "jobs") == 4
    assert [r[0] for r in conn.execute("SELECT DISTINCT provider_freshness FROM sightings WHERE job_id IS NOT NULL")] \
        == ["latest_collection"]


def test_repeated_yc_import_creates_no_duplicates(conn):
    fetchers = {"yc": yc_fetch()}
    go(conn, ("yc",), fetchers=fetchers)
    before = (count(conn, "companies"), count(conn, "sightings"), count(conn, "status_events"))
    second = go(conn, ("yc",), fetchers=fetchers)
    assert (count(conn, "companies"), count(conn, "sightings"), count(conn, "status_events")) == before
    assert status_of(second, "yc").metrics["new"] == 0 and status_of(second, "yc").metrics["unchanged"] == 3


# 8 / 11 -----------------------------------------------------------------------------------------------------------
def test_interrupted_run_resumes_without_repeating_or_duplicating(conn, monkeypatch):
    calls = {"yc": 0}
    real_yc = yc_fetch()

    def counting_yc(url):
        calls["yc"] += 1
        return real_yc(url)

    fetchers = {"yc": counting_yc, "feashliaa": fz_feed()}
    real = fz.execute

    def dying(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(fz, "execute", dying)
    with pytest.raises(KeyboardInterrupt):
        go(conn, fetchers=fetchers)
    parent = conn.execute("SELECT run_id, result FROM runs WHERE run_type = 'discovery'").fetchone()
    assert parent["result"] == "interrupted" and count(conn, "jobs") == 0 and count(conn, "companies") == 3
    yc_calls = calls["yc"]
    monkeypatch.setattr(fz, "execute", real)
    result = go(conn, fetchers=fetchers, resume_run_id=parent["run_id"])
    assert result.run_id == parent["run_id"] and result.resumed and result.overall == "success"
    assert status_of(result, "yc").status == "resumed" and calls["yc"] == yc_calls   # YC not fetched again
    assert count(conn, "runs WHERE run_type = 'discovery'") == 1 and count(conn, "jobs") == 3
    assert conn.execute("SELECT COUNT(*) FROM source_checks WHERE source_id = 'yc_oss'").fetchone()[0] == 1
    with pytest.raises(pipeline.PipelineError, match="already finished"):
        go(conn, fetchers=fetchers, resume_run_id=parent["run_id"])
    with pytest.raises(pipeline.PipelineError, match="No discovery run"):
        go(conn, fetchers=fetchers, resume_run_id="nope")


# 12 / 13 -----------------------------------------------------------------------------------------------------------
def test_sheet_read_error_blocks_writes_but_keeps_collected_data(conn):
    backend = FakeBackend()
    backend.fail_read = "Posts"
    result = go(conn, backend=backend, apply_sync=True)
    assert result.sync.status == "read_error" and result.overall == "partial"
    assert backend.writes() == [] and count(conn, "jobs") == 3 and count(conn, "companies") == 6
    assert any("read error" in p for p in result.pending)


def test_runs_tab_never_lists_a_run_that_is_still_running(conn):
    backend = FakeBackend()
    go(conn, backend=backend, apply_sync=True)   # the discovery run is 'running' while it syncs
    listed = {r[0] for r in backend.data("Runs")}
    parent = conn.execute("SELECT run_id FROM runs WHERE run_type = 'discovery'").fetchone()[0]
    assert parent not in listed and len(listed) == 2   # only the two finished source runs
    go(conn, backend=backend, apply_sync=True)         # next sync lists the finished parent with its real status
    row = next(r for r in backend.data("Runs") if r[0] == parent)
    assert row[backend.header("Runs").index("Status")] == "success"


def test_sheet_write_failure_is_reported_and_sqlite_data_survives(conn):
    backend = FakeBackend()
    backend.fail_write_on_call = 0
    result = go(conn, backend=backend, apply_sync=True)
    assert result.sync.status == "write_failed" and result.overall == "partial"
    assert count(conn, "jobs") == 3 and count(conn, "companies") == 6
    assert conn.execute("SELECT result FROM runs WHERE run_type = 'discovery'").fetchone()[0] == "partial"
    backend.fail_write_on_call = None
    retry = go(conn, ("yc",), fetchers={"yc": yc_fetch()}, backend=backend, apply_sync=True)   # retryable
    assert retry.sync.status == "applied" and len(backend.data("Companies")) == 1


# 14 / 15 / 16 -------------------------------------------------------------------------------------------------------
def test_verdicts_and_notes_survive_repeated_runs_and_changes(conn):
    go(conn)
    jid = conn.execute("SELECT job_id FROM jobs LIMIT 1").fetchone()[0]
    cid = conn.execute("SELECT company_id FROM companies WHERE json_extract(facts,'$.yc.yc_id') = 2").fetchone()[0]
    db.save_verdict(conn, "No", "not for me", job_id=jid)
    db.save_verdict(conn, None, "check later", company_id=cid)
    go(conn, fetchers={"yc": yc_fetch([rec(1), india(2, one_liner="changed"), rec(3, "Hiring Co")], hours_ago=0.5),
                       "feashliaa": fz_feed([job(1, title="Senior Business Analyst"), job(2), job(3, location="Remote")], hours_ago=0.5)})
    v = db.get_verdict(conn, job_id=jid)
    assert (v["decision"], v["notes"]) == ("No", "not for me")
    c = db.get_verdict(conn, company_id=cid)
    assert (c["decision"], c["notes"]) == (None, "check later") and count(conn, "verdicts") == 2


def test_provider_retained_jobs_never_become_verified_open(conn):
    go(conn)
    states = dict(conn.execute("""SELECT s.provider_freshness, j.lifecycle_state FROM jobs j
                                   JOIN sightings s ON s.job_id = j.job_id"""))
    assert states["provider_retained"] == "Unknown" and states["latest_collection"] == "New"
    assert not conn.execute("SELECT 1 FROM status_events WHERE new_state IN ('Active','Closed','Not Seen')").fetchone()


def test_unapproved_feashliaa_jobs_never_reach_test(conn):
    backend = FakeBackend()
    result = go(conn, backend=backend, apply_sync=True)
    assert result.sync.status == "applied"
    assert backend.data("Posts") == []                                   # none of the 3 stored jobs
    names = [r[1] for r in backend.data("Companies")]
    assert names == ["Bharat Labs"]                                      # only the approved YC India subset
    assert result.sync.counts["posts_to_add"] == 0
    assert "unpublished" in pipeline.render(result.to_summary())


# 17 / dry-run / CLI -------------------------------------------------------------------------------------------------
def test_live_cannot_be_targeted(conn, monkeypatch):
    with pytest.raises(pipeline.PipelineError, match="LIVE"):
        go(conn, target="live")
    with pytest.raises(SystemExit):
        cli.main(["run", "--sources", "yc", "--target", "live"])
    with pytest.raises(sheets_sync.SyncError, match="LIVE"):
        sheets_sync.open_backend("live")
    with pytest.raises(pipeline.PipelineError):
        go(conn, dry_run=True, apply_sync=True)


def test_dry_run_changes_neither_sqlite_nor_the_sheet(conn):
    go(conn, ("yc",), fetchers={"yc": yc_fetch()})
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    before = snapshot_db(conn)
    backend = FakeBackend()
    ro = db.connect(path, readonly=True)
    try:
        result = pipeline.run_pipeline(ro, sources=["yc", "feashliaa"], target="test", dry_run=True,
                                       fetchers={"yc": yc_fetch(hours_ago=0.5), "feashliaa": fz_feed()},
                                       cache_dir=tmp_of(conn) / "cache", prefilter=PF, backend_factory=lambda t: backend)
    finally:
        ro.close()
    assert result.overall == "dry-run" and result.run_id.startswith("(dry run")
    assert [o.status for o in result.outcomes] == ["dry-run", "dry-run"]
    assert snapshot_db(conn) == before and backend.writes() == []
    assert status_of(result, "feashliaa").metrics["new_jobs_est"] == 3
    assert "DRY RUN" in pipeline.render(result.to_summary())


def test_unknown_sources_and_schema_problems_stop_before_any_change(conn):
    with pytest.raises(pipeline.PipelineError, match="Unknown source"):
        pipeline.parse_sources("yc,someboard")
    with pytest.raises(pipeline.PipelineError, match="restricted"):
        pipeline.parse_sources("yc,jobspy")
    with pytest.raises(pipeline.PipelineError, match="No sources"):
        pipeline.parse_sources("")
    assert pipeline.parse_sources("feashliaa,yc,yc") == ["yc", "feashliaa"]
    conn.execute("PRAGMA user_version = 3")
    before = snapshot_db(conn)
    with pytest.raises(pipeline.PipelineError, match="schema"):
        go(conn)
    assert snapshot_db(conn) == before


def test_cli_run_and_report_end_to_end(conn, monkeypatch, capsys):
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_of(conn))
    monkeypatch.setenv(db.DB_PATH_ENV, path)
    ns = argparse.Namespace(sources="yc,feashliaa", target="test", dry_run=False, apply_sync=False, resume=None)
    code = cli.cmd_run(ns, fetchers={"yc": yc_fetch(), "feashliaa": fz_feed()}, backend_factory=lambda t: FakeBackend(),
                       cache_dir=tmp_of(conn) / "cache", prefilter=PF)
    out = capsys.readouterr().out
    assert code == 0 and "Overall: SUCCESS" in out and "TEST sync: PREVIEW" in out and "TEST sync not applied" in out
    assert cli.cmd_report(argparse.Namespace(last=True, run_id=None)) == 0
    assert "Overall: SUCCESS" in capsys.readouterr().out
    bad = argparse.Namespace(sources="yc,nope", target="test", dry_run=False, apply_sync=False, resume=None)
    assert cli.cmd_run(bad) == 1


def test_report_with_no_runs_is_clear(conn, monkeypatch, capsys):
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_of(conn))
    monkeypatch.setenv(db.DB_PATH_ENV, path)
    assert cli.cmd_report(argparse.Namespace(last=True, run_id=None)) == 1
    assert "No discovery run recorded" in capsys.readouterr().out


def test_metrics_the_adapters_do_not_produce_show_as_not_available(conn):
    broken = fz_feed()
    broken.set_manifest(chunks=...)
    summary = go(conn, fetchers={"yc": yc_fetch(), "feashliaa": broken}).to_summary()
    text = pipeline.render(summary)
    failed = next(o for o in summary["outcomes"] if o["name"] == "feashliaa")
    assert failed["metrics"] == {} and "Not available" in text   # nothing is invented for a source that produced nothing
    assert "rows scanned: Not available" in text


# 18 the Skill ---------------------------------------------------------------------------------------------------------
def test_skill_is_user_invoked_only_and_has_no_scheduling_or_live():
    text = (REPO / ".claude" / "skills" / "run-discovery" / "SKILL.md").read_text(encoding="utf-8")
    front = re.match(r"^---\n(.*?)\n---\n", text, re.S).group(1)
    assert "name: run-discovery" in front and "disable-model-invocation: true" in front
    body = text.split("---", 2)[2]
    assert "--target test" in body and "dry-run" in body and "Never" in body
    for forbidden in ("--target live", "cron", "ScheduleWakeup", "CronCreate", "pip install", "init-db --"):
        assert forbidden not in body
    assert "Enable or touch LIVE, schedule future runs" in body and "install packages" in body
    parser = cli.build_parser()
    help_text = parser.format_help() + "".join(a.format_help() for a in
                                               [s for s in parser._subparsers._group_actions[0].choices.values()])
    assert "schedule" not in help_text.lower() and "daemon" not in help_text.lower()
