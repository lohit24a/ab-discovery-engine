"""Identity, deduplication, replay, safety and recovery tests. Fictional data only."""

import sqlite3

import pytest

from discovery import database as db

T1 = "2026-10-19T10:00:00+00:00"
T2 = "2026-10-20T10:00:00+00:00"
T3 = "2026-10-21T10:00:00+00:00"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "identity.db"
    db.initialize_database(path)
    return path


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    for sid, name, kind in [("yc", "YC", "yc"), ("investor", "Investor Portfolio", "investor_board"),
                            ("ats", "Employer ATS", "ats_feed"), ("board", "Job Board", "aggregator")]:
        db.register_source(c, sid, name, kind)
    yield c
    c.close()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def acme(conn, source="yc", ext="acme-1", name="Acme AI", now=T1):
    return db.observe_company(conn, source_id=source, source_url=f"https://{source}.example/c/{ext}",
                              name=name, external_id=ext, domain="acme.example", now=now)[0]


def apm(conn, company_id, source, url, ext=None, req="APM-101", now=T1, **kw):
    kw.setdefault("title", "Associate Product Manager")
    return db.observe_job(conn, source_id=source, source_url=url, company_id=company_id,
                          external_id=ext, requisition_id=req, now=now, **kw)


def events(conn, job_id):
    return [r["new_state"] for r in conn.execute(
        "SELECT new_state FROM status_events WHERE job_id = ? ORDER BY rowid", (job_id,))]


# ===================================================== company identity (A-E)

class TestCompanyIdentity:
    def test_a_same_domain_from_two_sources_is_one_company(self, conn):
        first = acme(conn, "yc", "yc-77")
        second = acme(conn, "investor", "inv-9")
        assert first == second
        assert count(conn, "companies") == 1
        assert count(conn, "sightings") == 2

    def test_b_name_variation_with_same_domain_is_one_company(self, conn):
        first = acme(conn, "yc", "yc-77", name="Acme AI")
        second = acme(conn, "investor", "inv-9", name="Acme AI Technologies")
        assert first == second and count(conn, "companies") == 1
        assert "Acme AI Technologies" in db.get_company(conn, first)["aliases"]

    def test_c_similar_names_different_domains_stay_separate(self, conn):
        a, _ = db.observe_company(conn, source_id="yc", source_url="https://yc.example/1", name="Acme AI",
                                  external_id="1", domain="acme.example")
        b, _ = db.observe_company(conn, source_id="yc", source_url="https://yc.example/2", name="Acme A.I.",
                                  external_id="2", domain="acme-ai.example")
        assert a != b and count(conn, "companies") == 2

    def test_d_provisional_company_is_stable_across_repeats(self, conn):
        ids = set()
        for when in (T1, T2, T3):
            cid, created = db.observe_company(conn, source_id="yc", source_url="https://yc.example/c/55",
                                              name="Nodomain Labs", external_id="yc-55", now=when)
            ids.add(cid)
        assert len(ids) == 1 and count(conn, "companies") == 1 and count(conn, "sightings") == 1
        company = db.get_company(conn, ids.pop())
        assert company["identity_status"] == "provisional" and company["domain"] is None
        sighting = conn.execute("SELECT * FROM sightings").fetchone()
        assert (sighting["first_seen_at"], sighting["last_seen_at"]) == (T1, T3)

    def test_d_similar_provisional_names_are_not_merged(self, conn):
        a, _ = db.observe_company(conn, source_id="yc", source_url="https://yc.example/1",
                                  name="Nodomain Labs", external_id="1")
        b, _ = db.observe_company(conn, source_id="investor", source_url="https://inv.example/9",
                                  name="Nodomain Labs", external_id="9")
        assert a != b and count(conn, "companies") == 2

    def test_e_domain_learned_later_upgrades_in_place(self, conn):
        cid, _ = db.observe_company(conn, source_id="yc", source_url="https://yc.example/c/55",
                                    name="Later Co", external_id="yc-55", now=T1)
        job = db.observe_job(conn, source_id="yc", source_url="https://yc.example/j/1", company_id=cid,
                             title="Operations Associate", external_id="j1", now=T1)
        db.save_verdict(conn, "Maybe", "check later", company_id=cid)
        again, _ = db.observe_company(conn, source_id="yc", source_url="https://yc.example/c/55",
                                      name="Later Co", external_id="yc-55", domain="later.example", now=T2)
        company = db.get_company(conn, cid)
        assert again == cid and company["domain"] == "later.example"
        assert company["identity_status"] == "verified"
        assert db.get_job(conn, job.job_id)["company_id"] == cid
        assert db.get_verdict(conn, company_id=cid)["notes"] == "check later"
        assert count(conn, "companies") == 1 and count(conn, "sightings") == 2

    def test_e_domain_owned_by_another_company_needs_review_not_auto_merge(self, conn):
        verified = acme(conn, "yc", "yc-77")
        provisional, _ = db.observe_company(conn, source_id="investor", source_url="https://inv.example/9",
                                            name="Acme AI", external_id="inv-9")
        db.save_verdict(conn, "No", "dup?", company_id=provisional)
        with pytest.raises(db.IdentityConflict):
            db.observe_company(conn, source_id="investor", source_url="https://inv.example/9",
                               name="Acme AI", external_id="inv-9", domain="acme.example")
        # Nothing was merged or lost; reconciling these two records is deferred (human review).
        assert count(conn, "companies") == 2 and verified != provisional
        assert db.get_company(conn, provisional)["domain"] is None
        assert db.get_verdict(conn, company_id=provisional)["decision"] == "No"


# ============================================================ job identity (F-I)

class TestJobIdentity:
    def test_f_one_job_three_urls_three_sightings(self, conn):
        cid = acme(conn)
        first = apm(conn, cid, "ats", "https://boards.ats.example/acme/apm-101", ext="ats-5501")
        second = apm(conn, cid, "yc", "https://yc.example/companies/acme/jobs/apm", ext="yc-88")
        third = apm(conn, cid, "investor", "https://jobs.investor.example/acme/apm-101", ext="inv-31")
        assert first.created and not second.created and not third.created
        assert first.job_id == second.job_id == third.job_id
        assert count(conn, "jobs") == 1
        assert conn.execute("SELECT COUNT(*) FROM sightings WHERE job_id = ?", (first.job_id,)).fetchone()[0] == 3
        assert db.get_job(conn, first.job_id)["company_id"] == cid

    def test_f_same_title_without_shared_identity_is_not_merged(self, conn):
        cid = acme(conn)
        a = apm(conn, cid, "ats", "https://ats.example/1", ext="1", req=None)
        b = apm(conn, cid, "yc", "https://yc.example/2", ext="2", req=None)
        assert a.job_id != b.job_id and count(conn, "jobs") == 2

    def test_g_same_title_different_requisitions_are_two_jobs(self, conn):
        cid = acme(conn)
        a = apm(conn, cid, "ats", "https://ats.example/1", ext="1", req="APM-101")
        b = apm(conn, cid, "ats", "https://ats.example/2", ext="2", req="APM-102")
        assert a.job_id != b.job_id and count(conn, "jobs") == 2

    def test_h_same_title_different_locations_are_preserved(self, conn):
        cid = acme(conn)
        a = apm(conn, cid, "ats", "https://ats.example/1", ext="1", req=None, location="Bengaluru")
        b = apm(conn, cid, "ats", "https://ats.example/2", ext="2", req=None, location="Mumbai")
        assert a.job_id != b.job_id
        assert {r["location"] for r in conn.execute("SELECT location FROM jobs")} == {"Bengaluru", "Mumbai"}

    def test_i_harmless_url_differences_do_not_duplicate(self, conn):
        cid = acme(conn)
        urls = ["https://Jobs.Example.com/acme/apm-101",
                "https://jobs.example.com/acme/apm-101/",
                "https://jobs.example.com/acme/apm-101?utm_source=newsletter&utm_medium=email",
                "https://jobs.example.com/acme/apm-101#apply"]
        ids = {apm(conn, cid, "ats", u, req=None).job_id for u in urls}
        assert len(ids) == 1 and count(conn, "jobs") == 1
        assert conn.execute("SELECT COUNT(*) FROM sightings WHERE job_id IS NOT NULL").fetchone()[0] == 1

    def test_i_meaningful_identifiers_in_urls_are_never_stripped(self, conn):
        assert db.normalize_url("https://x.example/jobs?gh_jid=4001&utm_source=a&gh_src=b") == \
            "https://x.example/jobs?gh_jid=4001"
        cid = acme(conn)
        a = apm(conn, cid, "ats", "https://ats.example/view?id=111&utm_source=x", req=None)
        b = apm(conn, cid, "ats", "https://ats.example/view?id=222&utm_source=x", req=None)
        assert a.job_id != b.job_id and count(conn, "jobs") == 2

    def test_conflicting_requisition_is_flagged_not_merged(self, conn):
        cid = acme(conn)
        apm(conn, cid, "ats", "https://ats.example/1", ext="1", req="APM-101")
        with pytest.raises(db.IdentityConflict):
            apm(conn, cid, "ats", "https://ats.example/1", ext="1", req="APM-999")

    def test_requisitions_are_per_company(self, conn):
        other, _ = db.observe_company(conn, source_id="yc", source_url="https://yc.example/c/2",
                                      name="Beta Works", external_id="2", domain="beta.example")
        a = apm(conn, acme(conn), "ats", "https://ats.example/1", ext="1", req="REQ-1")
        b = apm(conn, other, "ats", "https://ats.example/2", ext="2", req="REQ-1")
        assert a.job_id != b.job_id


# ================================================== sightings & run replay

class TestRunReplay:
    def observe_run(self, conn, run_id, when, location, scope="board:acme"):
        db.start_run(conn, "discovery", run_id=run_id, now=when)
        cid = acme(conn, "ats", "ats-co", now=when)
        obs = apm(conn, cid, "ats", "https://ats.example/apm-101", ext="ats-5501", now=when,
                  location=location, description=f"Role in {location}")
        check = db.record_source_check(conn, run_id, "ats", "success", scope=scope, complete=True,
                                       record_count=1)
        return cid, obs, check

    def test_three_runs_and_replay(self, conn):
        cid, run1, _ = self.observe_run(conn, "run-1", T1, "Bengaluru")
        job_id = run1.job_id
        assert run1.created and events(conn, job_id) == ["New"]

        _, run2, _ = self.observe_run(conn, "run-2", T2, "Bengaluru")
        job = db.get_job(conn, job_id)
        assert run2.job_id == job_id and not run2.created and not run2.changed
        assert (job["first_seen_at"], job["last_seen_at"]) == (T1, T2)
        assert count(conn, "companies") == 1 and count(conn, "jobs") == 1
        assert events(conn, job_id) == ["New"]  # no unnecessary event

        _, run3, _ = self.observe_run(conn, "run-3", T3, "Remote - India")
        job = db.get_job(conn, job_id)
        assert run3.job_id == job_id and run3.changed and count(conn, "jobs") == 1
        assert job["location"] == "Remote - India" and job["lifecycle_state"] == "Changed"
        assert events(conn, job_id) == ["New", "Changed"]

        before = {t: count(conn, t) for t in db.list_app_tables(conn)}
        _, replay, _ = self.observe_run(conn, "run-3", T3, "Remote - India")
        assert replay.job_id == job_id and not replay.changed and not replay.created
        assert {t: count(conn, t) for t in db.list_app_tables(conn)} == before

    def test_each_further_change_is_recorded_once(self, conn):
        cid = acme(conn)
        apm(conn, cid, "ats", "https://ats.example/1", ext="1", location="A", now=T1)
        for when, loc in ((T2, "B"), (T3, "C")):
            apm(conn, cid, "ats", "https://ats.example/1", ext="1", location=loc, now=when)
            apm(conn, cid, "ats", "https://ats.example/1", ext="1", location=loc, now=when)
        job_id = conn.execute("SELECT job_id FROM jobs").fetchone()[0]
        assert events(conn, job_id) == ["New", "Changed", "Changed"]

    def test_replayed_source_check_does_not_duplicate_and_conflicts_are_rejected(self, conn):
        run_id = db.start_run(conn, "discovery")
        first = db.record_source_check(conn, run_id, "ats", "success", scope="q:pm", complete=True, record_count=4)
        again = db.record_source_check(conn, run_id, "ats", "success", scope="q:pm", complete=True, record_count=4)
        assert first == again and count(conn, "source_checks") == 1
        assert db.start_run(conn, "discovery", run_id=run_id) == run_id and count(conn, "runs") == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.record_source_check(conn, run_id, "ats", "failed", scope="q:pm", error_info="late timeout")

    def test_sighting_history_is_updated_not_duplicated(self, conn):
        cid = acme(conn)
        for when in (T1, T2, T3):
            apm(conn, cid, "ats", "https://ats.example/1", ext="1", now=when)
        row = conn.execute("SELECT * FROM sightings WHERE job_id IS NOT NULL").fetchone()
        assert (row["first_seen_at"], row["last_seen_at"]) == (T1, T3)
        assert conn.execute("SELECT COUNT(*) FROM sightings WHERE job_id IS NOT NULL").fetchone()[0] == 1


# ============================================ status-event / failure safety

class TestStatusEventSafety:
    @pytest.fixture
    def seen_job(self, conn):
        cid = acme(conn)
        a = apm(conn, cid, "ats", "https://ats.example/1", ext="1", now=T1)
        apm(conn, cid, "board", "https://board.example/9", ext="9", now=T1)
        db.append_status_event(conn, "Active", "Seen in complete check", job_id=a.job_id, now=T1)
        return a.job_id

    @pytest.mark.parametrize("outcome,complete,info", [
        ("failed", False, "no response"),
        ("partial", False, "page 1 of 4 only"),
        ("success", False, "returned results but completeness unverified"),
        ("skipped", False, "disabled"),
    ])
    def test_incomplete_checks_can_never_close_a_job(self, conn, seen_job, outcome, complete, info):
        run_id = db.start_run(conn, "discovery")
        check = db.record_source_check(conn, run_id, "ats", outcome, scope="board:acme",
                                       complete=complete, error_info=info)
        for state in ("Closed", "Not Seen"):
            with pytest.raises(ValueError):
                db.append_status_event(conn, state, "absent", job_id=seen_job, source_check_id=check)
        assert db.get_job(conn, seen_job)["lifecycle_state"] == "Active"
        assert events(conn, seen_job) == ["New", "Active"]

    def test_closing_without_any_evidence_is_rejected(self, conn, seen_job):
        with pytest.raises(ValueError):
            db.append_status_event(conn, "Closed", "gone", job_id=seen_job)

    def test_database_itself_rejects_unevidenced_closure(self, conn, seen_job):
        with pytest.raises(sqlite3.IntegrityError):  # bypassing the repository still fails
            conn.execute("""INSERT INTO status_events (event_id, job_id, previous_state, new_state,
                                                       occurred_at, reason)
                            VALUES ('e', ?, 'Active', 'Closed', 't', 'bypass')""", (seen_job,))

    def test_empty_but_verified_complete_check_is_valid_evidence(self, conn, seen_job):
        # Mechanism only: no closure policy is implied or applied automatically.
        run_id = db.start_run(conn, "discovery")
        check = db.record_source_check(conn, run_id, "ats", "success", scope="board:acme",
                                       complete=True, record_count=0)
        assert db.append_status_event(conn, "Not Seen", "absent from complete empty check",
                                      job_id=seen_job, source_check_id=check)
        assert db.get_job(conn, seen_job)["lifecycle_state"] == "Not Seen"

    def test_evidence_must_come_from_a_source_that_sighted_the_job(self, conn, seen_job):
        run_id = db.start_run(conn, "discovery")
        check = db.record_source_check(conn, run_id, "yc", "success", scope="all", complete=True)
        with pytest.raises(ValueError):
            db.append_status_event(conn, "Not Seen", "yc never listed it", job_id=seen_job,
                                  source_check_id=check)

    def test_absence_from_one_source_while_present_in_another(self, conn, seen_job):
        run_id = db.start_run(conn, "discovery")
        db.record_source_check(conn, run_id, "ats", "failed", scope="board:acme", error_info="timeout")
        db.record_source_check(conn, run_id, "board", "success", scope="q:pm", complete=True, record_count=1)
        cid = db.get_job(conn, seen_job)["company_id"]
        apm(conn, cid, "board", "https://board.example/9", ext="9", now=T2)
        assert db.get_job(conn, seen_job)["lifecycle_state"] == "Active"
        assert db.get_job(conn, seen_job)["last_seen_at"] == T2

    def test_check_evidence_stays_attached_to_its_own_scope(self, conn):
        run_id = db.start_run(conn, "discovery")
        db.record_source_check(conn, run_id, "investor", "success", scope="board:one", complete=True, record_count=30)
        db.record_source_check(conn, run_id, "investor", "partial", scope="board:two", record_count=10,
                               error_info="page 2 of 5 failed")
        db.record_source_check(conn, run_id, "investor", "success", scope="board:three", complete=True, record_count=0)
        one, two, three = (db.get_source_check(conn, run_id, "investor", s)
                           for s in ("board:one", "board:two", "board:three"))
        assert (one["complete"], two["complete"], three["complete"]) == (1, 0, 1)
        assert three["record_count"] == 0 and two["error_info"].startswith("page 2")

    def test_company_lifecycle_events_are_not_supported(self, conn):
        cid = acme(conn)
        with pytest.raises(ValueError):
            db.append_status_event(conn, "Closed", "company closed?", company_id=cid)


# ======================================================== verdict preservation

class TestVerdictPreservation:
    def test_verdicts_survive_everything_including_restart(self, db_path):
        c = db.connect(db_path)
        db.register_source(c, "ats", "ATS", "ats_feed")
        db.register_source(c, "yc", "YC", "yc")
        cid = acme(c, "ats", "co-1")
        job = apm(c, cid, "ats", "https://ats.example/1", ext="1")
        db.save_verdict(c, "Yes", "love this role", job_id=job.job_id)
        db.save_verdict(c, "Maybe", "interesting team", company_id=cid)

        acme(c, "ats", "co-1", now=T2)                                              # rediscover
        apm(c, cid, "ats", "https://ats.example/1", ext="1", now=T2, location="Remote")  # update
        apm(c, cid, "yc", "https://yc.example/j/1", ext="y1", now=T2)               # another source
        run = db.start_run(c, "discovery", run_id="r1")
        db.start_run(c, "discovery", run_id=run)                                    # replay
        c.close()

        c2 = db.connect(db_path)  # application restart
        try:
            assert db.get_job(c2, job.job_id)["location"] == "Remote"
            jv, cv = db.get_verdict(c2, job_id=job.job_id), db.get_verdict(c2, company_id=cid)
            assert (jv["decision"], jv["notes"]) == ("Yes", "love this role")
            assert (cv["decision"], cv["notes"]) == ("Maybe", "interesting team")
            assert count(c2, "verdicts") == 2 and count(c2, "jobs") == 1 and count(c2, "companies") == 1
        finally:
            c2.close()

    def test_no_verdict_is_retained_and_only_explicit_update_changes_it(self, conn):
        cid = acme(conn)
        job = apm(conn, cid, "ats", "https://ats.example/1", ext="1")
        db.save_verdict(conn, "No", "not for me", job_id=job.job_id, now=T1)
        for when in (T2, T3):
            apm(conn, cid, "ats", "https://ats.example/1", ext="1", now=when, location="X" + when)
        verdict = db.get_verdict(conn, job_id=job.job_id)
        assert (verdict["decision"], verdict["notes"], verdict["created_at"]) == ("No", "not for me", T1)
        db.save_verdict(conn, "Maybe", "reconsidering", job_id=job.job_id, now=T3)
        assert db.get_verdict(conn, job_id=job.job_id)["decision"] == "Maybe"
        assert count(conn, "verdicts") == 1


# ======================================================== transaction recovery

class TestTransactionRecovery:
    def test_interrupted_operation_rolls_back_and_retry_is_clean(self, db_path):
        c = db.connect(db_path)
        db.register_source(c, "ats", "ATS", "ats_feed")
        cid = acme(c, "ats", "co-1")
        committed = count(c, "jobs")

        with pytest.raises(KeyboardInterrupt):
            with db.transaction(c):
                db.observe_job(c, source_id="ats", source_url="https://ats.example/1", company_id=cid,
                               title="Associate Product Manager", external_id="1", requisition_id="APM-101")
                raise KeyboardInterrupt  # simulated Ctrl-C mid-run
        assert not c.in_transaction
        assert count(c, "jobs") == committed and count(c, "sightings") == 1 and count(c, "status_events") == 0

        apm(c, cid, "ats", "https://ats.example/1", ext="1")
        apm(c, cid, "ats", "https://ats.example/1", ext="1")
        assert count(c, "jobs") == 1 and count(c, "status_events") == 1
        c.close()

    def test_process_death_with_uncommitted_work_loses_only_uncommitted_data(self, db_path):
        c = db.connect(db_path)
        db.register_source(c, "ats", "ATS", "ats_feed")
        cid = acme(c, "ats", "co-1")
        c.execute("BEGIN IMMEDIATE")
        c.execute("""INSERT INTO companies (company_id, name, normalized_name, identity_status,
                                            created_at, updated_at)
                     VALUES ('lost', 'Lost Co', 'lost co', 'provisional', 't', 't')""")
        c.close()  # connection dropped without COMMIT, as if the process died

        c2 = db.connect(db_path)
        try:
            assert db.get_company(c2, "lost") is None
            assert db.get_company(c2, cid)["domain"] == "acme.example"
            acme(c2, "ats", "co-1")
            assert count(c2, "companies") == 1
        finally:
            c2.close()

    def test_failed_batch_leaves_no_partial_records(self, conn):
        cid = acme(conn)
        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction(conn):
                apm(conn, cid, "ats", "https://ats.example/1", ext="1")
                db.save_verdict(conn, "Definitely", job_id=conn.execute("SELECT job_id FROM jobs").fetchone()[0])
        assert count(conn, "jobs") == 0 and count(conn, "verdicts") == 0


# ============================================================ migration

def test_upgrade_from_version_1_preserves_data(tmp_path):
    from discovery.schema import MIGRATIONS
    path = tmp_path / "v1.db"
    raw = sqlite3.connect(path)
    raw.executescript(MIGRATIONS[0])
    raw.execute("""INSERT INTO companies (company_id, name, normalized_name, identity_status,
                                          created_at, updated_at)
                   VALUES ('c1', 'Old Co', 'old co', 'provisional', 't', 't')""")
    raw.commit()
    raw.close()
    assert db.initialize_database(path) == 2
    conn = db.connect(path)
    try:
        assert db.get_company(conn, "c1")["name"] == "Old Co"
        assert "requisition_id" in [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]
    finally:
        conn.close()
