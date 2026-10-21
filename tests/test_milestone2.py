"""Day 12 / Milestone 2: sources -> canonical identity -> manual import -> ranking -> session shortlist -> Sheets (fake).

Everything here is fictional and offline: a disposable SQLite file and an in-memory fake spreadsheet. No network, no real Sheet.
"""
import random

import pytest
from test_sheets_sync import FakeBackend

from discovery import database as db
from discovery import imports as imp
from discovery import profile as prof
from discovery import session, shortlist
from discovery import sheets_sync as ss

T0, T1, T2, T3, T4 = ("2026-10-18T10:00:00+00:00", "2026-10-19T10:00:00+00:00", "2026-10-20T10:00:00+00:00",
                      "2026-10-21T10:00:00+00:00", "2026-10-22T10:00:00+00:00")
TITLES = ["Associate Product Manager", "Business Operations Associate", "Forward Deployed Engineer", "Founders Office Associate",
          "Product Operations Associate"]
BLR = "Bengaluru, Karnataka, India"


@pytest.fixture(scope="module")
def profile():
    return prof.load_profile()


@pytest.fixture(scope="module")
def ranking():
    return prof.load_ranking()


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "m2.db"
    db.initialize_database(path)
    c = db.connect(path)
    db.register_source(c, "feashliaa", "Feashliaa", "ats_feed", config_ref=None, now=T0)
    db.register_source(c, "inv_testboard", "Investor board: Test", "investor_board", config_ref="investor-boards-config", now=T0)
    db.register_source(c, "jobspy_indeed", "Indeed", "aggregator", now=T0)
    yield c
    c.close()


def company(conn, name):
    return db.get_or_create_company(conn, name, None, facts={"sheet_publish": False}, now=T1)[0]


def job(conn, name, title, source="feashliaa", location=BLR, ext=None, now=T1, company_id=None, **kw):
    cid = company_id or company(conn, name)
    ext = ext or f"{source}-{name}-{title}"
    return db.observe_job(conn, source_id=source, source_url=f"https://jobs.example/{ext}", company_id=cid, title=title, external_id=ext,
                          location=location, posting_url=f"https://jobs.example/{ext}", now=now, **kw).job_id


def source_check(conn, source, started=T1):
    run = db.start_run(conn, "discovery", now=started)
    db.record_source_check(conn, run, source, "success", scope="jobs", complete=True, record_count=1, started_at=started, finished_at=started)
    db.finish_run(conn, run, "success", now=started)


def world(conn, n=30):
    ids = [job(conn, f"Co{i:02d}", TITLES[i % len(TITLES)]) for i in range(n)]
    source_check(conn, "feashliaa")
    return ids


def build(conn, profile, ranking, **kw):
    return shortlist.build(conn, profile, ranking, with_hints=False, **kw)


def live(conn, profile, ranking):
    """The way the CLI ranks inside a sync: after reading the sheet's verdicts (continues the current session)."""
    return lambda overrides: build(conn, profile, ranking, verdict_overrides=overrides)


def sync(conn, backend, sl, now):
    return ss.sync_to_sheets(conn, backend, now=now, shortlist=sl)


def ids_of(sl):
    return [r["id"] for r in sl.jobs]


# ---------------------------------------------------------------- shortlist shape and gates

def test_shortlist_is_bounded_deterministic_and_factual(conn, profile, ranking):
    world(conn, 40)
    a, b = build(conn, profile, ranking), build(conn, profile, ranking)
    assert ids_of(a) == ids_of(b) and [r["Type"] for r in a.rows] == [r["Type"] for r in b.rows]
    assert len(a.jobs) == 20 and len({r["employer_key"] for r in a.jobs}) == 20
    for r in a.jobs:
        e = r["explanation"]
        assert "not employer-verified" in e and "confirmed open" not in e and "currently open" not in e
    assert all(len(row["Explanation"]) <= 600 for row in a.rows)


def test_never_padded_when_few_qualify(conn, profile, ranking):
    world(conn, 4)
    job(conn, "Roadworks", "Truck Driver")                       # a different job: excluded, never padded in
    sl = build(conn, profile, ranking)
    assert len(sl.jobs) == 4 and all(r["title"] != "Truck Driver" for r in sl.jobs)


def test_per_employer_cap_and_large_source_dominance(conn, profile, ranking):
    for i in range(6):                                           # one big employer posting many matching roles
        job(conn, "BigCo", f"{TITLES[i % 5]} {i}", source="inv_testboard", ext=f"b{i}")
    for i in range(30):
        job(conn, f"Board{i:02d}", TITLES[i % 5], source="inv_testboard", ext=f"i{i}")
    for i in range(5):
        job(conn, f"Feed{i}", TITLES[i], source="feashliaa", ext=f"f{i}")
    source_check(conn, "inv_testboard")
    source_check(conn, "feashliaa")
    sl = build(conn, profile, ranking)
    per = {}
    for r in sl.jobs:
        per[r["employer_key"]] = per.get(r["employer_key"], 0) + 1
    assert max(per.values()) <= ranking["shortlist"]["max_per_employer"] and len(sl.jobs) <= 20
    assert any("feashliaa" in r["sources"] for r in sl.jobs) or len(sl.jobs) == 20             # no source-specific exclusion


def test_restricted_source_jobs_are_never_selected_or_published(conn, profile, ranking):
    job(conn, "Indeedco", "Associate Product Manager", source="jobspy_indeed", ext="x1")
    job(conn, "Fine Ltd", "Associate Product Manager")
    source_check(conn, "feashliaa")
    source_check(conn, "jobspy_indeed")
    sl = build(conn, profile, ranking)
    assert {r["company"] for r in sl.jobs} == {"Fine Ltd"} and sl.skipped_restricted == 1
    backend = FakeBackend()
    sync(conn, backend, sl, T2)
    assert "Indeedco" not in {r[1] for r in backend.data("Posts")}


def test_unknown_availability_is_stated_not_hidden(conn, profile, ranking):
    job(conn, "Quiet Co", "Associate Product Manager")             # no source check ever recorded
    res = shortlist.rank_all(conn, profile, ranking, with_hints=False)[2]
    quiet = next(x for x in res if x["company"] == "Quiet Co")
    assert quiet["availability"] == "unknown" and "availability is unknown" in quiet["explanation"]
    assert "AVAILABILITY_UNVERIFIED" in quiet["missing_evidence"]


def test_unresolved_possible_duplicates_are_never_merged_or_hidden(conn, profile, ranking):
    a = job(conn, "Twin Co", "Associate Product Manager", source="feashliaa", ext="t1")
    b = job(conn, "Twin Co", "Associate Product Manager", source="inv_testboard", ext="t2")
    source_check(conn, "feashliaa")
    source_check(conn, "inv_testboard")
    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    sl = build(conn, profile, ranking)
    assert a != b and conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM identity_links").fetchone()[0] == 0
    # Operator decision (final readiness): same employer + same title + same location compete for ONE slot in a new session. Neither
    # record is merged or deleted; the offered row names the other one, and the other stays in SQLite for later sessions.
    shown = {r["id"] for r in sl.jobs}
    assert len(shown) == 1 and shown <= {a, b}
    (kept,) = shown
    other = ({a, b} - shown).pop()
    assert sl.alternatives[kept] == [other] and "1 more posting(s)" in next(r for r in sl.rows if r["record_id"] == kept)["Explanation"]


# --------------------------------------------------------------------- sessions

def runs_state(conn):
    return (conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0], conn.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0])


def test_retries_of_one_session_are_idempotent_and_a_new_session_advances(conn, profile, ranking):
    world(conn, 40)
    backend = FakeBackend()
    s1 = build(conn, profile, ranking)
    assert s1.mode == "new" and len(s1.jobs) == 20
    # a DRY RUN never records anything, so it can be repeated forever without advancing
    state = runs_state(conn)
    for _ in range(3):
        rep = ss.sync_to_sheets(conn, backend, dry_run=True, now=T2, shortlist=live(conn, profile, ranking))
        assert rep.session_note.startswith("Review session: NEW")
    assert runs_state(conn) == state and backend.writes() == []
    sync(conn, backend, live(conn, profile, ranking), T2)                      # the first real sync records session 1
    first_rows, first_posts = [r[1] for r in backend.data("Shortlist")], {r[0] for r in backend.data("Posts")}
    assert first_posts == set(ids_of(s1))
    # retries of the same session: identical rows, no new Posts, no new batch, nothing duplicated
    for day in (T3, T4):
        again = live(conn, profile, ranking)({})
        assert again.mode == "continue" and ids_of(again) == ids_of(s1) and [r["record_id"] for r in again.rows] == first_rows
        rep = ss.sync_to_sheets(conn, backend, dry_run=True, now=day, shortlist=again)
        assert rep.planned["Posts"]["appends"] == [] and rep.planned["Shortlist"]["updates"] == [] and "CONTINUING" in rep.session_note
    sync(conn, backend, live(conn, profile, ranking), T3)
    sync(conn, backend, live(conn, profile, ranking), T4)
    assert {r[0] for r in backend.data("Posts")} == first_posts and len(backend.data("Posts")) == 20
    assert [r[1] for r in backend.data("Shortlist")] == first_rows
    # a genuinely NEW session, started on purpose, proposes the next bounded batch (previously listed records are deprioritised)
    s2 = build(conn, profile, ranking, new_session=True)
    assert s2.mode == "new" and len(s2.jobs) == 20
    assert len(set(ids_of(s2)) & set(ids_of(s1))) <= ranking["session"]["slots"]["previously_unreviewed"]
    sync(conn, backend, lambda ov: build(conn, profile, ranking, verdict_overrides=ov, new_session=True), T4)
    repeated = len(set(ids_of(s2)) & set(ids_of(s1)))
    assert len(backend.data("Posts")) == 20 + 20 - repeated and len({r[0] for r in backend.data("Posts")}) == len(backend.data("Posts"))
    assert ids_of(live(conn, profile, ranking)({})) == ids_of(s2)              # and now THAT is the current session


def test_a_changed_record_surfaces_in_the_next_session_only(conn, profile, ranking):
    ids = world(conn, 30)
    backend = FakeBackend()
    sync(conn, backend, live(conn, profile, ranking), T2)
    events = conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0]
    sightings = conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
    s1 = build(conn, profile, ranking)                                           # continuing session 1
    changed_id = ids_of(s1)[0]
    row = conn.execute("SELECT j.company_id, j.title, s.external_id FROM jobs j JOIN sightings s USING (job_id) WHERE j.job_id = ?", (changed_id,)).fetchone()
    db.observe_job(conn, source_id="feashliaa", source_url=f"https://jobs.example/{row['external_id']}", company_id=row["company_id"], title=row["title"],
                   external_id=row["external_id"], location="Bengaluru", now=T4)
    assert conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0] == events + 1          # only the real source change made an event
    assert conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == sightings
    assert changed_id in ids_of(build(conn, profile, ranking))                   # the current session keeps it (re-checked)
    s2 = build(conn, profile, ranking, new_session=True)
    assert changed_id in {r["id"] for r in s2.lanes["changed"]}
    assert len(ids_of(s2)) == len(set(ids_of(s2)))
    assert conn.execute("SELECT COUNT(*) FROM status_events").fetchone()[0] == events + 1          # ranking and syncing never add events


def test_human_decisions_apply_immediately_and_removal_does_not_backfill(conn, profile, ranking):
    world(conn, 30)
    backend = FakeBackend()
    sync(conn, backend, live(conn, profile, ranking), T2)
    s1 = build(conn, profile, ranking)
    yes, no, maybe = ids_of(s1)[0], ids_of(s1)[1], ids_of(s1)[2]
    backend.set_cell("Posts", yes, "Your Verdict", "Yes")
    backend.set_cell("Posts", no, "Your Verdict", "No")
    backend.set_cell("Posts", no, "Your Notes", "not for me")
    backend.set_cell("Posts", maybe, "Your Verdict", "Maybe")
    posts_before = {r[0] for r in backend.data("Posts")}
    rep = sync(conn, backend, live(conn, profile, ranking), T3)                  # reads the sheet's verdicts, then re-checks the session
    listed_now = [r[1] for r in backend.data("Shortlist") if r[1]]
    assert no not in listed_now and {yes, maybe} <= set(listed_now)              # a No is removed at once; Yes/Maybe stay attached to the session
    assert len(listed_now) == len(s1.rows) - 1                                   # the gap is NOT refilled with a new batch
    assert {r[0] for r in backend.data("Posts")} == posts_before                 # and nothing new is published
    assert "you marked it No" in rep.session_note
    assert backend.cell("Posts", backend.row_of("Posts", no), "Your Notes") == "not for me"          # the rejected row keeps its data
    s2 = build(conn, profile, ranking, new_session=True)
    assert not ({yes, no, maybe} & set(ids_of(s2)))                              # a later session does not re-offer decided records
    sync(conn, backend, lambda ov: build(conn, profile, ranking, verdict_overrides=ov, new_session=True), T4)
    for rid, decision, notes in ((yes, "Yes", ""), (no, "No", "not for me"), (maybe, "Maybe", "")):
        assert backend.cell("Posts", backend.row_of("Posts", rid), "Your Verdict") == decision
        assert backend.cell("Posts", backend.row_of("Posts", rid), "Your Notes") == notes


def test_the_first_sync_session_recorded_before_sessions_existed_is_continued(conn, profile, ranking):
    world(conn, 30)
    s1 = build(conn, profile, ranking)
    run = db.start_run(conn, "sync", now=T2)                                      # shaped like the real first TEST sync (only `listed`)
    db.finish_run(conn, run, "success", summary={"listed": s1.listed}, now=T2)
    cont = build(conn, profile, ranking)
    assert cont.mode == "continue" and cont.session_id == run and ids_of(cont) == ids_of(s1)
    assert [r["Type"] for r in cont.rows] == [r["Type"] for r in s1.rows]         # same labels, so the sheet does not change


def test_every_planned_change_reconciles_with_what_was_written(conn, profile, ranking):
    world(conn, 12)
    backend = FakeBackend()
    sl = live(conn, profile, ranking)
    plan = ss.sync_to_sheets(conn, backend, dry_run=True, now=T2, shortlist=sl)
    ss.sync_to_sheets(conn, backend, now=T2, shortlist=sl)
    written_rows = {(c[1], r[0]) for c in backend.calls if c[0] == "append" for r in c[2]}
    planned_rows = {(tab, a["record_id"]) for tab, d in plan.planned.items() for a in d["appends"]}
    assert written_rows == planned_rows
    written_cells = {(c[1], a1): v for c in backend.calls if c[0] == "write" for a1, v in c[2].items()}
    planned_cells = {(tab, u["cell"]): u["new"] for tab, d in plan.planned.items() for u in d["updates"]}
    missing = {k: (v, written_cells.get(k)) for k, v in planned_cells.items() if written_cells.get(k) != v}
    assert not missing, list(missing.items())[:3]
    assert all(a["admitted_by"] == "shortlist" for a in plan.planned["Posts"]["appends"])


def test_the_broad_job_gate_no_longer_adds_unqualified_jobs_when_a_shortlist_is_used(conn, profile, ranking):
    world(conn, 6)
    cid = db.get_or_create_company(conn, "Wide Co", None, facts={"sheet_publish": True}, now=T1)[0]       # a publishable company
    for i in range(4):
        db.observe_job(conn, source_id="feashliaa", source_url=f"https://jobs.example/wide{i}", company_id=cid, title=f"Senior Director {i}",
                       external_id=f"wide{i}", location=BLR, now=T1)                                             # unqualified jobs
    source_check(conn, "feashliaa")
    plain = FakeBackend()
    ss.sync_to_sheets(conn, plain, now=T2)
    assert any("Wide Co" == r[1] for r in plain.data("Posts"))                  # the older gate (no shortlist) still admits them
    backend = FakeBackend()
    rep = sync(conn, backend, live(conn, profile, ranking), T2)
    assert not any("Wide Co" == r[1] for r in backend.data("Posts"))
    assert "publication_gate" not in {a["admitted_by"] for a in rep.planned["Posts"]["appends"]}
    sync(conn, plain, live(conn, profile, ranking), T3)                         # rows admitted earlier are preserved, never deleted
    assert any("Wide Co" == r[1] for r in plain.data("Posts"))


def test_sorted_sheet_rows_keep_every_verdict_with_its_record(conn, profile, ranking):
    world(conn, 12)
    backend = FakeBackend()
    sync(conn, backend, build(conn, profile, ranking), T2)
    target = backend.tabs["Posts"][4][0]
    backend.set_cell("Posts", target, "Your Verdict", "Yes")
    body = backend.tabs["Posts"][1:]
    random.Random(7).shuffle(body)                                       # the person sorts the sheet
    backend.tabs["Posts"][1:] = body
    sync(conn, backend, live(conn, profile, ranking), T3)
    assert backend.cell("Posts", backend.row_of("Posts", target), "Your Verdict") == "Yes"
    ids = [r[0] for r in backend.tabs["Posts"][1:]]
    assert len(ids) == len(set(ids))                                     # no duplicate rows were appended
    rep = ss.sync_to_sheets(conn, backend, dry_run=True, now=T4, shortlist=build(conn, profile, ranking))
    assert rep.planned["Posts"]["appends"] == [] and rep.planned["Posts"]["updates"] == []     # repeat sync converges on a sorted sheet


def test_failed_verdict_read_stops_all_writes(conn, profile, ranking):
    world(conn, 6)
    backend = FakeBackend()
    sync(conn, backend, build(conn, profile, ranking), T2)
    backend.fail_read = "Posts"
    writes = len(backend.writes())
    with pytest.raises(ss.SyncError):
        sync(conn, backend, build(conn, profile, ranking), T3)
    assert len(backend.writes()) == writes
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE run_type = 'sync' AND finished_at LIKE '2026-10-21%'").fetchone()[0] == 0


def test_publication_is_limited_to_shortlisted_records(conn, profile, ranking):
    ids = world(conn, 30)
    backend = FakeBackend()
    sl = build(conn, profile, ranking)
    sync(conn, backend, sl, T2)
    published = {r[0] for r in backend.data("Posts")}
    assert published == set(ids_of(sl)) and len(published) == 20          # the other 10 qualified jobs stay unpublished
    assert {r[1] for r in backend.data("Shortlist")} == set(sl.listed)
    assert all(row[0].startswith(("Job", "Company")) for row in backend.data("Shortlist"))
    plain = FakeBackend()
    ss.sync_to_sheets(conn, plain, now=T2)                                # without a shortlist the normal gate is unchanged
    assert plain.data("Posts") == [] and plain.data("Shortlist") == []


def test_dry_run_reports_exact_cells_and_writes_nothing(conn, profile, ranking):
    world(conn, 6)
    backend = FakeBackend()
    sl = build(conn, profile, ranking)
    rep = ss.sync_to_sheets(conn, backend, dry_run=True, now=T2, shortlist=sl)
    assert backend.writes() == [] and rep.planned["Posts"]["appends"] and rep.planned["Shortlist"]["updates"]
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE run_type = 'sync'").fetchone()[0] == 0
    sync(conn, backend, sl, T2)
    rep2 = ss.sync_to_sheets(conn, backend, dry_run=True, now=T3, shortlist=sl)
    assert rep2.planned["Posts"]["updates"] == [] and rep2.planned["Shortlist"]["updates"] == []   # converged


def test_scores_fill_machine_columns_but_never_human_ones(conn, profile, ranking):
    ids = world(conn, 3)
    backend = FakeBackend()
    sl = build(conn, profile, ranking)
    sync(conn, backend, sl, T2)
    row = backend.row_of("Posts", ids[0])
    assert backend.cell("Posts", row, "Score") == str(sl.scores[ids[0]][0])
    assert "Qualified" in backend.cell("Posts", row, "Explanation")
    assert backend.cell("Posts", row, "Your Verdict") == "" and backend.cell("Posts", row, "Your Notes") == ""


# ---------------------------------------------------- manual import -> ranking -> sheet

def test_manual_import_feeds_ranking_and_is_labelled_user_supplied(conn, profile, ranking, tmp_path):
    root = tmp_path / "imports"
    (root / "descriptions").mkdir(parents=True)
    (root / "descriptions" / "d.txt").write_text("You will own AI agent rollout for customers; 0-2 years, new grads welcome.", encoding="utf-8")
    (root / "links.csv").write_text("url,company_hint,job_title_hint,location_hint,description_file\n"
                                    "https://careers.zedcorp.example/job/778899,Zed Corp,Forward Deployed Engineer,Bengaluru,d.txt\n"
                                    "https://careers.evil.example/job/778800,Evil Inc,=HYPERLINK(\"x\"),Bengaluru,\n", encoding="utf-8")
    parsed = imp.parse_links(imp.read_text(root / "links.csv", imp.MAX_CSV_BYTES, root), root / "descriptions", root=root)
    items = imp.plan(conn, parsed)
    assert imp.summarize(items) == {"create": 2}
    imp.apply(conn, items, now=T1)
    res = shortlist.rank_all(conn, profile, ranking, with_hints=False)[2]
    zed = next(r for r in res if r["company"] == "Zed Corp")
    assert "USER_SUPPLIED" in zed["reason_codes"] and "supplied by you" in zed["explanation"] and "employer" not in zed["explanation"].split("supplied by you")[1]
    assert zed["experience"] in ("early_career", "unknown")
    # manual jobs are held back from the normal gate; if shortlisted they are published, with formula text neutralised
    assert not (set(ss.load_publishable_job_ids(conn)) & {r["id"] for r in res if r["company"] in ("Zed Corp", "Evil Inc")})
    backend = FakeBackend()
    sl = build(conn, profile, ranking)
    sync(conn, backend, sl, T2)
    titles = {r[2] for r in backend.data("Posts")}
    assert "Forward Deployed Engineer" in titles                     # a job you imported by hand is listed first among new discoveries
    assert zed["availability"] == "unknown"                           # supplied by hand: never "listed in a source check"
    assert not any(t.startswith("=") for t in titles)
    assert not any(c.startswith("=") for row in backend.data("Shortlist") for c in row)
    assert conn.execute("SELECT lifecycle_state FROM jobs WHERE title LIKE 'Forward%'").fetchone()[0] == "New"


def test_manual_replay_after_ranking_keeps_history_and_verdicts(conn, profile, ranking, tmp_path):
    root = tmp_path / "imports"
    (root / "descriptions").mkdir(parents=True)
    (root / "links.csv").write_text("url,company_hint,job_title_hint,location_hint\nhttps://x.example/job/445566,Xco,Associate Product Manager,Bengaluru\n", encoding="utf-8")
    parse = lambda: imp.parse_links(imp.read_text(root / "links.csv", imp.MAX_CSV_BYTES, root), root / "descriptions", root=root)
    imp.apply(conn, imp.plan(conn, parse()), now=T1)
    jid = conn.execute("SELECT job_id FROM jobs").fetchone()[0]
    db.save_verdict(conn, "Yes", "mine", job_id=jid, now=T1)
    snap = [tuple(r) for r in conn.execute("SELECT * FROM sightings")]
    imp.apply(conn, imp.plan(conn, parse()), now=T2)
    build(conn, profile, ranking)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert dict(db.get_verdict(conn, job_id=jid))["notes"] == "mine"
    assert [r[:2] for r in snap] == [tuple(r)[:2] for r in conn.execute("SELECT * FROM sightings")]


def test_session_history_roundtrip_through_the_run_summary(conn):
    run = db.start_run(conn, "sync", now=T1)
    db.finish_run(conn, run, "success", summary={"listed": {"a": "f1"}}, now=T1)
    assert session.load_history(conn) == {"a": "f1"}


def test_merged_alias_is_never_published_and_the_canonical_row_keeps_its_verdict(conn, profile, ranking):
    from discovery import merge
    cid = company(conn, "Twin Co")
    j1 = job(conn, "Twin Co", "Associate Product Manager", source="feashliaa", ext="a1", company_id=cid)
    j2 = job(conn, "Twin Co", "Associate Product Manager", source="inv_testboard", ext="a2", company_id=cid)
    source_check(conn, "feashliaa")
    source_check(conn, "inv_testboard")
    merge.apply_job_merge(conn, j1, j2, rule="same_posting", evidence={"test": 1}, decided_by="test")
    backend = FakeBackend()
    sync(conn, backend, live(conn, profile, ranking), T2)
    assert {r[0] for r in backend.data("Posts")} == {j1} and {r[1] for r in backend.data("Shortlist")} == {j1}
    backend.set_cell("Posts", j1, "Your Verdict", "Maybe")
    backend.set_cell("Posts", j1, "Your Notes", "check later")
    sync(conn, backend, live(conn, profile, ranking), T3)
    assert dict(db.get_verdict(conn, job_id=j1))["notes"] == "check later" and db.get_verdict(conn, job_id=j2) is None
    assert db.get_job(conn, j2) is not None                                        # the alias row in SQLite is kept, never deleted


def test_duplicate_or_unknown_sheet_rows_abort_before_any_shortlist_write(conn, profile, ranking):
    world(conn, 5)
    backend = FakeBackend()
    sync(conn, backend, live(conn, profile, ranking), T2)
    backend.tabs["Posts"].append(list(backend.tabs["Posts"][1]))                     # a duplicated record id
    writes = len(backend.writes())
    with pytest.raises(ss.SyncError):
        sync(conn, backend, live(conn, profile, ranking), T3)
    assert len(backend.writes()) == writes
    backend.tabs["Posts"].pop()
    backend.tabs["Posts"].append(["not-a-real-id"] + [""] * (len(backend.tabs["Posts"][0]) - 1))
    with pytest.raises(ss.SyncError):
        sync(conn, backend, live(conn, profile, ranking), T3)
    assert len(backend.writes()) == writes


def test_test_and_live_stay_separate(conn):
    with pytest.raises(Exception):
        ss.open_backend("live")


import pytest as _pytest_iso


@_pytest_iso.fixture(autouse=True)
def _legacy_verdict_mechanics(monkeypatch):
    """These tests exercise the reconcile mechanics; production TEST syncs refuse verdict imports (see test_day16_verdict_isolation.py)."""
    from discovery import sheets_sync as _ss
    monkeypatch.setattr(_ss, "TEST_VERDICT_IMPORT_ENABLED", True)
