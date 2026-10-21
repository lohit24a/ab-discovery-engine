"""Milestone 2, end to end, offline: frozen source fixtures -> pipeline (Stream A feed + Stream B directory) -> investor board import ->
manual import -> canonical identity -> approved ranking -> session shortlist -> fake Sheets, with human feedback and repeated runs.

No network. Disposable SQLite file. Fake spreadsheet. Nothing here can reach a real Sheet or the production database."""
import time
from pathlib import Path

import pytest

from discovery import database as db
from discovery import imports as imp
from discovery import pipeline, shortlist
from discovery import profile as prof
from discovery import sheets_sync as ss
from discovery.adapters import feashliaa as fz
from discovery.adapters import investor_boards as ib
from tests.test_feashliaa import Feed
from tests.test_feashliaa import row as fz_row
from tests.test_investor_boards import ACCEL, getro_job, getro_scans, make_snapshot
from tests.test_pipeline import PF, count, stamp, tmp_of
from tests.test_sheets_sync import FakeBackend
from tests.test_yc_companies import india, make_fetch

TITLES = ["Associate Product Manager", "Business Operations Associate", "Product Operations Associate", "Associate Program Manager"]
AI_TEXT = "AI-native agents for enterprise finance teams, built on artificial intelligence and large language model workflows. B2B SaaS startup."


@pytest.fixture(scope="module")
def profile():
    return prof.load_profile()


@pytest.fixture(scope="module")
def ranking():
    return prof.load_ranking()


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "m2e2e.db"
    db.initialize_database(path)
    c = db.connect(path)
    yield c
    c.close()


def yc_records():
    return [india(10 + i, f"Bharat AI {i}", long_description=AI_TEXT, one_liner="AI agents for finance", industries=["B2B", "Artificial Intelligence"],
                  team_size=8 + i, isHiring=False) for i in range(8)]


def feed(changed=None):
    rows = [fz_row(i, company=f"feedco{i}", title=TITLES[i % 4], scraped=stamp(2.2), first=stamp(500)) for i in range(14)]
    if changed:
        rows[changed]["location"] = "Bengaluru, India (Hybrid)"
    return Feed([rows], generated=stamp(2))


def run_pipeline(conn, changed=None):
    return pipeline.run_pipeline(conn, sources=["yc", "feashliaa"], fetchers={"yc": make_fetch(yc_records(), updated=stamp(1)), "feashliaa": feed(changed)},
                                 cache_dir=tmp_of(conn) / "cache", prefilter=PF, target="test", backend_factory=lambda t: FakeBackend())


def import_board(conn, n=12):
    now = int(time.time())
    jobs = [getro_job(i, org=f"boardco{i}", domain=f"boardco{i}.com", title=TITLES[i % 4], createdAt=now - 86400 * (i + 1)) for i in range(n)]
    ib.import_snapshot(conn, make_snapshot([(ACCEL, getro_scans(ACCEL, jobs))]))


def manual_import(conn, tmp_path):
    root = tmp_path / "imports"
    (root / "descriptions").mkdir(parents=True, exist_ok=True)
    (root / "descriptions" / "mine.txt").write_text("You will own the rollout of AI agents for customers; 0-2 years, new graduates welcome.", encoding="utf-8")
    (root / "links.csv").write_text("url,company_hint,job_title_hint,location_hint,description_file\n"
                                    "https://careers.mineco.example/job/778899,Mine Co,Forward Deployed Engineer,Bengaluru,mine.txt\n", encoding="utf-8")
    parsed = imp.parse_links(imp.read_text(root / "links.csv", imp.MAX_CSV_BYTES, root), root / "descriptions", root=root)
    return imp.apply(conn, imp.plan(conn, parsed))


def live(conn, profile, ranking, new_session=False):
    return lambda ov: shortlist.build(conn, profile, ranking, verdict_overrides=ov, new_session=new_session)


def fingerprint(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("companies", "jobs", "sightings", "status_events", "verdicts")}


def test_milestone_2_chain(conn, profile, ranking, tmp_path):
    # ---- Stream A (two independent job sources) + Stream B (company directory) + manual import
    r1 = run_pipeline(conn)
    assert {o.name for o in r1.outcomes} == {"yc", "feashliaa"} and not [o for o in r1.outcomes if str(o.status).upper() in ("FAILED", "PARTIAL")]
    import_board(conn)
    made = manual_import(conn, tmp_path)
    assert made["created"] == 1 and not made["errors"]
    assert count(conn, "jobs") >= 26 and conn.execute("SELECT COUNT(DISTINCT source_id) FROM sightings WHERE job_id IS NOT NULL").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM companies WHERE json_extract(facts,'$.yc') IS NOT NULL").fetchone()[0] == 8            # Stream B directory

    # ---- first review session: bounded A+B shortlist, factual and explainable, restricted sources never present
    backend = FakeBackend()
    sl = shortlist.build(conn, profile, ranking)
    assert sl.mode == "new" and 0 < len(sl.jobs) <= 20 and 0 < len(sl.companies) <= 5
    sources = {s for r in sl.jobs for s in r["sources"]}
    assert {"feashliaa", "inv_accel", "manual"} <= sources and not any(s.startswith("jobspy") for s in sources)
    assert sl.jobs[0]["user_supplied"]                                                       # a job you imported yourself is listed first
    assert all(r["n_jobs"] == 0 for r in sl.companies)                                      # company-only discoveries are independent of jobs
    assert all("not employer-verified" in r["explanation"] or r["user_supplied"] for r in sl.jobs)
    ss.sync_to_sheets(conn, backend, now=stamp(0.5, z=False), shortlist=live(conn, profile, ranking))
    posts, first_rows = {r[0] for r in backend.data("Posts")}, [r[1] for r in backend.data("Shortlist")]
    assert posts == {r["id"] for r in sl.jobs} and len(first_rows) == len(sl.rows)
    after_first = fingerprint(conn)

    # ---- the SAME source data again, twice: no duplicate observations, history events, rows or new recommendations
    for _ in range(2):
        run_pipeline(conn)
        import_board(conn)
        manual_import(conn, tmp_path)
        again = {k: v for k, v in fingerprint(conn).items() if k != "sightings"}
        assert again == {k: v for k, v in after_first.items() if k != "sightings"}
        assert fingerprint(conn)["sightings"] == after_first["sightings"]
        rep = ss.sync_to_sheets(conn, backend, dry_run=True, now=stamp(0.4, z=False), shortlist=live(conn, profile, ranking))
        assert rep.session_note.startswith("Review session: CONTINUING") and rep.planned["Posts"]["appends"] == [] and rep.planned["Shortlist"]["updates"] == []

    # ---- your feedback is read by record ID before anything is regenerated
    first_job, second_job = sl.jobs[1]["id"], sl.jobs[2]["id"]
    backend.set_cell("Posts", first_job, "Your Verdict", "No")
    backend.set_cell("Posts", first_job, "Your Notes", "wrong stage for me")
    backend.set_cell("Posts", second_job, "Your Verdict", "Yes")
    rows = backend.tabs["Posts"][1:]
    rows.reverse()                                                                           # you sorted the sheet
    backend.tabs["Posts"][1:] = rows
    ss.sync_to_sheets(conn, backend, now=stamp(0.3, z=False), shortlist=live(conn, profile, ranking))
    shown = [r[1] for r in backend.data("Shortlist") if r[1]]                                  # (a removed row is left as blank cells)
    assert first_job not in shown and len(shown) == len(first_rows) - 1                         # the No is removed and the gap is not refilled
    assert {r[0] for r in backend.data("Posts")} == posts                                                                                    # nothing new published
    assert backend.cell("Posts", backend.row_of("Posts", first_job), "Your Notes") == "wrong stage for me"
    assert dict(db.get_verdict(conn, job_id=second_job))["decision"] == "Yes"

    # ---- changed source data: one job moves; then a genuinely new session proposes the next set
    events = count(conn, "status_events")
    run_pipeline(conn, changed=3)
    import_board(conn)
    assert count(conn, "status_events") >= events                                            # real changes are history, never rewritten
    nxt = shortlist.build(conn, profile, ranking, new_session=True)
    assert nxt.mode == "new" and {r["id"] for r in nxt.jobs} - {r["id"] for r in sl.jobs}          # new recommendations exist
    assert first_job not in {r["id"] for r in nxt.jobs}                                          # a rejected record is not reintroduced by score
    assert second_job not in {r["id"] for r in nxt.jobs} or second_job in {r["id"] for r in nxt.lanes["changed"]}
    ss.sync_to_sheets(conn, backend, now=stamp(0.2, z=False), shortlist=live(conn, profile, ranking, new_session=True))
    assert len({r[0] for r in backend.data("Posts")}) == len(backend.data("Posts"))                      # no duplicate rows
    assert dict(db.get_verdict(conn, job_id=first_job))["notes"] == "wrong stage for me"
    assert conn.execute("SELECT COUNT(*) FROM identity_links WHERE status != 'confirmed'").fetchone()[0] == 0   # nothing was merged on a guess
