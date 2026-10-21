"""Post-implementation audit: end-to-end offline acceptance on a frozen multi-source dataset and a disposable database.

Real code paths: the CLI in fresh Python processes (init-db, doctor, source-health, ranking-preview, backup, backup-verify), the real
ranking/shortlist with the approved configuration, and the real live-publish plan/apply gates. Only Google Sheets is replaced by an
in-memory fake, and the network is blocked for the whole in-process part. Nothing touches the project database or a real sheet."""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from discovery import backups, database as db, live_preflight as lp, live_publish as lpub, sheets_sync as ss
from discovery.sheets_schema import TAB_ORDER
from tests.test_sheets_sync import FakeBackend

ROOT = Path(__file__).resolve().parent.parent
T0, T1 = "2026-10-10T10:00:00+00:00", "2026-10-20T10:00:00+00:00"
LATER = ["2026-10-20T11:00:00+00:00", "2026-10-20T12:00:00+00:00", "2026-10-20T13:00:00+00:00", "2026-10-20T14:00:00+00:00"]


class FakeLive(FakeBackend):
    title = "A+B Discovery - LIVE (fake)"


class FakeReadOnly:
    """What live_preflight.open_live_readonly returns, over the fake: reads only."""

    def __init__(self, fake):
        self._f, self.title, self.spreadsheet_id = fake, fake.title, "fake-live"

    def tab_names(self):
        return self._f.tab_names()

    def read_tab(self, tab):
        return self._f.read_tab(tab)

    def write_cells(self, *a):
        raise lp.ReadOnlyError("read-only")

    def append_rows(self, *a):
        raise lp.ReadOnlyError("read-only")


def cli_run(args, dbp):
    env = {**os.environ, "DISCOVERY_DB_PATH": str(dbp), "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, "-m", "discovery", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)


def check(conn, source, outcome, *, started, complete=False, scope="jobs:none", n=0, err=None):
    run = db.start_run(conn, "discovery", now=started)
    db.record_source_check(conn, run, source, outcome, scope=scope, complete=complete, record_count=n, started_at=started, finished_at=started, error_info=err)
    db.finish_run(conn, run, "success" if outcome == "success" else "partial", now=started)


def populate(dbp):
    """A frozen multi-source world: complete, partial, failed and stale sources; cross-source duplicates; an unresolved look-alike;
    Bengaluru, India-remote, foreign, senior and non-target jobs; Stream B companies without jobs; legacy TEST verdicts."""
    c = db.connect(dbp)
    for sid, name, typ in (("feed_ok", "Complete feed", "ats_feed"), ("board_b", "Investor board B", "investor_board"),
                           ("feed_bad", "Failing feed", "ats_feed"), ("yc", "YC directory", "yc")):
        db.register_source(c, sid, name, typ, now=T0)
    co = {}
    for key, name, src, loc, facts in (
            ("acme", "Acme AI", "feed_ok", "Bengaluru", None),
            ("gamma", "Gamma Ltd", "feed_ok", "London", None),
            ("delta", "Delta Corp", "feed_ok", "Bengaluru", None),
            ("echo", "Echo Labs", "feed_bad", "Bengaluru", None),
            ("fox", "Foxtrot Pay", "feed_ok", "Bengaluru", None),
            ("zeta", "Zeta AI", "yc", "Bengaluru, KA, India", {"yc": {"batch": "Summer 2022", "status": "Active", "industries": ["B2B"], "team_size": 12,
                                                                      "one_liner": "AI-native B2B platform with AI agents for enterprises",
                                                                      "locations": "Bengaluru, KA, India"}, "india_relevance": {"class": "india_based"}}),
            ("omega", "Omega Agents", "yc", "Bengaluru, KA, India", {"yc": {"batch": "Winter 2023", "status": "Active", "industries": ["B2B"], "team_size": 8,
                                                                            "one_liner": "AI agents and large language model automation for B2B finance teams",
                                                                            "locations": "Bengaluru, KA, India"}, "india_relevance": {"class": "india_based"}})):
        cid, _ = db.observe_company(c, source_id=src, source_url=f"https://{key}.example", name=name, external_id=key, location=loc, now=T0)
        if facts:
            db.update_company_details(c, cid, facts_patch=facts, now=T0)
        co[key] = cid
    jobs = {}

    def job(key, company, title, loc, src, ext, now, **kw):
        jobs[key] = db.observe_job(c, source_id=src, source_url=f"https://{src}.example/{ext}", company_id=co[company], title=title, external_id=ext,
                                   location=loc, now=now, **kw).job_id
    check(c, "yc", "success", started=T0, complete=True, scope="companies:none", n=2)        # stale by T1 (> 168 h)
    job("echo_fo", "echo", "Founder's Office Associate", "Bengaluru, Karnataka, India", "feed_bad", "e1", T0)
    check(c, "feed_bad", "success", started=T0, complete=True, n=1)
    job("apm", "acme", "Associate Product Manager", "Bengaluru, Karnataka, India", "feed_ok", "a1", T1, requisition_id="APM-1")
    job("apm_b", "acme", "Associate Product Manager", "Bengaluru, Karnataka, India", "board_b", "b1", T1, requisition_id="APM-1")   # same vacancy
    job("apm_lookalike", "acme", "Associate Product Manager", "Bengaluru, Karnataka, India", "board_b", "b2", T1)                  # no shared id
    job("fde", "acme", "Forward Deployed Engineer", "Remote - India", "feed_ok", "a2", T1)
    job("driver", "acme", "Truck Driver", "Bengaluru, Karnataka, India", "feed_ok", "a3", T1)
    job("london", "gamma", "Product Manager", "London, United Kingdom", "feed_ok", "g1", T1)
    job("senior", "delta", "Senior Director, Strategy", "Bengaluru, Karnataka, India", "feed_ok", "d1", T1)
    job("bizops", "delta", "Business Operations Associate", "Bengaluru, Karnataka, India", "feed_ok", "d2", T1)
    job("rejected", "fox", "Strategy & Operations Analyst", "Bengaluru, Karnataka, India", "feed_ok", "f1", T1)
    check(c, "feed_ok", "success", started=T1, complete=True, n=8)
    check(c, "board_b", "partial", started=T1, n=2, err="count mismatch: 2 of 5")
    check(c, "feed_bad", "failed", started=T1, err="HTTP 403: access restricted; nothing retried")
    db.save_verdict(c, "Yes", "legacy TEST note", job_id=jobs["bizops"], now=T0)                # legacy TEST review memory
    db.save_verdict(c, "Maybe", "", job_id=jobs["fde"], now=T0)
    db.save_verdict(c, "No", "not for me", job_id=jobs["rejected"], now=T0)
    c.close()                                                                                  # checkpoint before any fingerprint
    return co, jobs


def rows(fake, tab):
    data = fake.tabs[tab]
    if not data:
        return []
    h = data[0]
    return [dict(zip(h, r + [""] * (len(h) - len(r)))) for r in data[1:] if any(str(x).strip() for x in r)]


@pytest.fixture
def no_network(monkeypatch):
    def blocked(*a, **k):
        raise RuntimeError("network access attempted during an offline acceptance test")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


def test_offline_acceptance_end_to_end(tmp_path, monkeypatch, no_network):
    dbp, bk = tmp_path / "e2e.db", tmp_path / "bk"
    assert cli_run(["init-db"], dbp).returncode == 0                                          # fresh process, isolated database
    co, jobs = populate(dbp)
    assert "0 failed" in cli_run(["doctor"], dbp).stdout

    live, test = FakeLive(), FakeBackend()
    monkeypatch.setattr(lpub.lp, "open_live_readonly", lambda root=None: FakeReadOnly(live))
    monkeypatch.setattr(lpub.lp, "open_live_write", lambda root=None: live)
    monkeypatch.setattr(lpub, "LIMITS", {"Shortlist": 25, "Posts": 30, "Companies": 30, "Runs": 10})   # an operator-approved wider Runs cap
    backup = backups.create_backup(dbp, bk, "e2e")

    def publish(new_session=False, expect_refusal=None):
        plan = lpub.make_plan(ROOT, dbp, new_session=new_session)
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        if expect_refusal:
            with pytest.raises((ss.SyncError, lpub.PublishRefused), match=expect_refusal):
                lpub.apply(path, backup, authorized=True, db_path=dbp, out=lambda *_: None)
            return plan, None
        assert plan["violations"] == [], plan["violations"]
        return plan, lpub.apply(path, backup, authorized=True, db_path=dbp, out=lambda *_: None)

    def session_id():
        c = db.connect(dbp, readonly=True)
        try:
            from discovery import shortlist as sm
            return (sm.current_session(c) or {}).get("id")
        finally:
            c.close()

    # 1. First LIVE publication: shortlist only, Stream A and Stream B, legacy verdicts not copied.
    plan1, rep1 = publish()
    sl = rows(live, "Shortlist")
    listed = {r["record_id"] for r in sl}
    job_rows = [r for r in sl if r["Type"].startswith("Job")]
    company_rows = [r for r in sl if r["Type"] == "Company"]
    assert jobs["apm"] in listed and company_rows, sl         # Stream A and an independent Stream B pick
    assert jobs["apm_b"] == jobs["apm"]                                                       # same requisition id: one canonical vacancy
    # the unresolved look-alike (same employer, title, location; no shared id) stays a separate record but does not take a second slot
    assert jobs["apm_lookalike"] not in listed
    apm_row = next(r for r in sl if r["record_id"] == jobs["apm"])
    assert apm_row["Explanation"].startswith("[1 more posting(s) at this employer with the same title and location")
    assert {jobs["driver"], jobs["london"], jobs["rejected"]}.isdisjoint(listed)
    fo = next(r for r in sl if r["record_id"] == jobs["echo_fo"])                            # its source's newest attempt failed (HTTP 403)
    assert "newest attempt (2026-10-20) failed" in fo["Explanation"]
    assert {r["record_id"] for r in rows(live, "Posts")} == {r["record_id"] for r in job_rows}
    published_cos = {r["record_id"] for r in rows(live, "Companies")}
    assert {r["record_id"] for r in company_rows} <= published_cos and co["gamma"] not in published_cos
    assert all(not r["Your Verdict"] and not r["Your Notes"] for t in ("Posts", "Companies") for r in rows(live, t))   # no TEST copy
    # legacy TEST Yes/Maybe are history only: offered as unreviewed in LIVE, no points, and the reason says so; legacy No stays suppressed
    assert jobs["bizops"] in listed                                                          # (fde loses its slot to Acme's per-employer cap of 2)
    expl = next(r for r in sl if r["record_id"] == jobs["bizops"])["Explanation"]
    assert "an earlier TEST-sheet review said Yes (history only, not counted" in expl and "stored verdict" not in expl
    from discovery import profile as prof, ranking_preview
    rc = db.connect(dbp, readonly=True)
    scored = {r["id"]: r for r in ranking_preview.rank_all(rc, prof.load_profile(), prof.load_ranking(), with_hints=False)[2]}
    rc.close()
    assert scored[jobs["fde"]]["verdict"] is None and scored[jobs["fde"]]["verdict_legacy"] == "Maybe"
    assert "VERDICT_MAYBE" not in scored[jobs["fde"]]["reason_codes"] and "VERDICT_LEGACY_TEST" in scored[jobs["fde"]]["reason_codes"]
    assert scored[jobs["rejected"]]["hard_exclusion"] == "HUMAN_NO" and "entered in the TEST sheet" in scored[jobs["rejected"]]["explanation"]
    assert all("employer-verified" not in r["Explanation"] or "not employer-verified" in r["Explanation"] for r in rows(live, "Posts"))
    assert not any("confirmed open" in r["Explanation"].lower() for r in rows(live, "Posts"))
    assert len(rows(live, "Runs")) == 1 and "no source was contacted" in rows(live, "Runs")[0]["Errors"]
    assert "board_b (partial)" in rows(live, "Runs")[0]["Errors"] and "feed_bad (blocked)" in rows(live, "Runs")[0]["Errors"]
    first_session = session_id()

    # 2. Replay: nothing changes, no new batch.
    again, _ = lpub.make_plan(ROOT, dbp), None
    assert not any(v for v in again["counts"]["appended"].values()) and not any(again["counts"]["cells_updated"].values())
    assert session_id() == first_session

    # 3. The person reviews in LIVE: a No and a note. The No leaves the shortlist without replacement; nothing is deleted.
    live.set_cell("Posts", jobs["apm"], "Your Verdict", "No")
    live.set_cell("Posts", jobs["echo_fo"], "Your Notes", "LIVE note")
    before = len(rows(live, "Shortlist"))
    _, rep3 = publish()
    assert len(rep3.verdicts_imported) == 2
    c = db.connect(dbp, readonly=True)
    assert db.get_verdict(c, job_id=jobs["apm"])["decision"] == "No"
    assert dict(db.get_verdict(c, job_id=jobs["echo_fo"]))["notes"] == "LIVE note"
    c.close()
    assert jobs["apm"] not in {r["record_id"] for r in rows(live, "Shortlist")} and len(rows(live, "Shortlist")) == before - 1
    assert jobs["apm"] in {r["record_id"] for r in rows(live, "Posts")}                        # row kept, with the person's No
    assert session_id() == first_session

    # 4. TEST never receives LIVE feedback and never imports.
    tconn = db.connect(dbp)
    ss.sync_to_sheets(tconn, test, now=LATER[0])
    for key in ("apm", "echo_fo"):
        r = next(x for x in rows(test, "Posts") if x["record_id"] == jobs[key])
        assert r["Your Verdict"] == "" and r["Your Notes"] == ""                                  # LIVE feedback never reaches TEST
    legacy = next(x for x in rows(test, "Posts") if x["record_id"] == jobs["bizops"])
    assert legacy["Your Verdict"] == "Yes" and legacy["Your Notes"] == "legacy TEST note"        # legacy TEST memory still shown in TEST
    test.set_cell("Posts", jobs["echo_fo"], "Your Verdict", "Yes")
    test.set_cell("Posts", jobs["bizops"], "Your Verdict", "No")
    report = ss.sync_to_sheets(tconn, test, now=LATER[1])
    assert not report.verdicts_imported and len(report.warnings) >= 2
    assert db.get_verdict(tconn, job_id=jobs["bizops"])["decision"] == "Yes"                    # TEST cannot change a decision
    assert db.get_verdict(tconn, job_id=jobs["echo_fo"])["decision"] is None
    tconn.close()

    # 5. An interrupted LIVE write keeps the person's input and is completed, recorded once.
    live.set_cell("Posts", jobs["bizops"], "Your Verdict", "Maybe")                          # a LIVE decision over a legacy TEST Yes
    original = live.append_rows

    def lose_runs(tab, data):
        if tab == "Runs":
            raise TimeoutError("simulated lost response")
        return original(tab, data)
    live.append_rows = lose_runs
    plan5 = lpub.make_plan(ROOT, dbp)
    p5 = tmp_path / "plan5.json"
    p5.write_text(json.dumps(plan5), encoding="utf-8")
    with pytest.raises(ss.SyncError):
        lpub.apply(p5, backup, authorized=True, db_path=dbp, out=lambda *_: None)
    live.append_rows = original
    c = db.connect(dbp, readonly=True)
    assert db.get_verdict(c, job_id=jobs["bizops"])["decision"] == "Maybe"
    from discovery import sheets_sync as _ss
    assert jobs["bizops"] in _ss.live_decision_ids(c)                                          # provenance kept although the write failed
    c.close()
    n_runs = len(rows(live, "Runs"))
    publish()
    assert len(rows(live, "Runs")) == n_runs + 1 and "Completes the publication interrupted" in rows(live, "Runs")[-1]["Errors"]

    # 6. The next review session only when asked for; then replays are stable again.
    publish(new_session=True)
    second_session = session_id()
    assert second_session != first_session
    for _ in range(2):
        p = lpub.make_plan(ROOT, dbp)
        assert not any(p["counts"]["appended"].values()) and not any(p["counts"]["cells_updated"].values()) and p["mode"] == "continue"
    assert session_id() == second_session
    for tab in ("Posts", "Companies", "Runs"):
        ids = [r[list(r)[0]] for r in rows(live, tab)]
        assert len(ids) == len(set(ids)), tab

    # 7. With the shipped first-trial limits the next publication would be refused (no silent widening).
    monkeypatch.setattr(lpub, "LIMITS", lpub.load_limits())
    shipped = lpub.make_plan(ROOT, dbp, new_session=True)
    assert any(v.startswith("Runs:") and "exceeds the approved maximum of 2" in v for v in shipped["violations"]), shipped["violations"]

    # 8. Integrity: nothing closed, nothing merged, no Not Seen; the failed source's job is untouched.
    c = sqlite3.connect(dbp)
    assert c.execute("SELECT COUNT(*) FROM jobs WHERE lifecycle_state IN ('Closed', 'Not Seen')").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM status_events WHERE new_state IN ('Closed', 'Not Seen')").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM identity_links").fetchone()[0] == 0
    assert c.execute("SELECT job_id FROM jobs WHERE job_id = ?", (jobs["apm_lookalike"],)).fetchone() is not None
    from discovery import sheets_sync as _ss2
    assert _ss2.live_decision_ids(c) == {jobs["apm"], jobs["bizops"]}                        # only decisions typed in LIVE count as LIVE
    verdicts = sorted(tuple(r) for r in c.execute("SELECT job_id, decision, notes FROM verdicts"))
    c.close()

    # 9. Backup through the CLI, proven restore, restart in a fresh process on the restored copy.
    made = cli_run(["backup", "--label", "e2e-final", "--dir", str(bk)], dbp)
    assert made.returncode == 0 and "[OK] Backup created and verified" in made.stdout
    proof = cli_run(["backup-verify", "--latest", "--restore-test", "--dir", str(bk)], dbp)
    assert proof.returncode == 0 and "all tables equal the manifest" in proof.stdout
    restored = tmp_path / "restored.db"
    shutil.copy2(backups.list_backups(bk)[-1], restored)
    health = cli_run(["source-health"], restored)
    assert health.returncode == 0
    for word in ("board_b", "partial", "feed_bad", "blocked", "yc"):
        assert word in health.stdout, word
    preview = cli_run(["ranking-preview", "--db", str(restored), "--no-hints"], restored)
    assert preview.returncode == 0 and "Ranking preview (read-only" in preview.stdout and "PROPOSALS" not in preview.stdout
    r = sqlite3.connect(restored)
    assert sorted(tuple(x) for x in r.execute("SELECT job_id, decision, notes FROM verdicts")) == verdicts
    assert r.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    r.close()
