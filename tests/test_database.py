import sqlite3

import pytest

from discovery import cli, database as db
from discovery.schema import APP_TABLES, SCHEMA_VERSION


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    db.initialize_database(path)
    return path


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    yield c
    c.close()


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def make_job(conn, title="Fake Analyst", domain="fake-co.example"):
    company_id, _ = db.get_or_create_company(conn, "Fake Co", domain)
    return company_id, db.insert_job(conn, company_id, title)


# 1
def test_initialize_creates_eight_tables(db_path, conn):
    assert set(db.list_app_tables(conn)) == set(APP_TABLES)
    assert len(APP_TABLES) == 8
    assert db.get_schema_version(conn) == SCHEMA_VERSION


# 2
def test_reinitialize_is_idempotent_and_keeps_data(db_path, conn):
    company_id, job_id = make_job(conn)
    assert db.initialize_database(db_path) == SCHEMA_VERSION
    assert db.initialize_database(db_path) == SCHEMA_VERSION
    assert count(conn, "companies") == 1 and count(conn, "jobs") == 1
    assert db.get_job(conn, job_id)["company_id"] == company_id


def test_newer_schema_version_is_refused(db_path):
    raw = sqlite3.connect(db_path)
    raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    raw.close()
    with pytest.raises(db.SchemaVersionError):
        db.initialize_database(db_path)


# 3
def test_foreign_keys_reject_invalid_references(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_job(conn, "no-such-company", "Fake Analyst")
    db.register_source(conn, "fake_feed", "Fake Feed", "ats_feed")
    with pytest.raises(sqlite3.IntegrityError):
        db.record_sighting(conn, "fake_feed", "https://fake.example/1", job_id="no-such-job")
    assert count(conn, "jobs") == 0 and count(conn, "sightings") == 0


# 4
def test_same_verified_domain_gives_one_company(conn):
    first, created1 = db.get_or_create_company(conn, "Fake Co", "https://www.Fake-Co.example/careers")
    second, created2 = db.get_or_create_company(conn, "Fake Company Inc", "fake-co.example")
    assert first == second and created1 and not created2
    assert count(conn, "companies") == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO companies (company_id, name, normalized_name, domain, identity_status,
                                      created_at, updated_at)
               VALUES ('x', 'Dup', 'dup', 'fake-co.example', 'verified', 't', 't')""")


def test_companies_without_domain_are_not_merged_by_name(conn):
    a, _ = db.get_or_create_company(conn, "Same Name")
    b, _ = db.get_or_create_company(conn, "Same Name")
    assert a != b
    assert db.get_company(conn, a)["identity_status"] == "provisional"
    assert db.get_company(conn, a)["domain"] is None


# 5
def test_same_title_jobs_are_distinct(conn):
    company_id, job1 = make_job(conn, "Fake Analyst")
    job2 = db.insert_job(conn, company_id, "Fake Analyst")
    assert job1 != job2 and count(conn, "jobs") == 2


# 6
def test_multiple_sightings_for_one_job(conn):
    _, job_id = make_job(conn)
    db.register_source(conn, "feed_a", "Feed A", "ats_feed")
    db.register_source(conn, "board_b", "Board B", "investor_board")
    s1, _ = db.record_sighting(conn, "feed_a", "https://a.example/1", job_id=job_id, external_id="A-1")
    s2, _ = db.record_sighting(conn, "board_b", "https://b.example/9", job_id=job_id, external_id="B-9")
    s3, _ = db.record_sighting(conn, "board_b", "https://b.example/10", job_id=job_id, external_id="B-10")
    assert len({s1, s2, s3}) == 3
    assert count(conn, "sightings") == 3 and count(conn, "jobs") == 1


def test_sighting_must_reference_exactly_one_record(conn):
    company_id, job_id = make_job(conn)
    db.register_source(conn, "feed_a", "Feed A", "ats_feed")
    with pytest.raises(sqlite3.IntegrityError):
        db.record_sighting(conn, "feed_a", "https://a.example/1")
    with pytest.raises(sqlite3.IntegrityError):
        db.record_sighting(conn, "feed_a", "https://a.example/1", job_id=job_id, company_id=company_id)


# 7
def test_repeated_sighting_updates_last_seen_only(conn):
    _, job_id = make_job(conn)
    db.register_source(conn, "feed_a", "Feed A", "ats_feed")
    sid, created = db.record_sighting(conn, "feed_a", "https://a.example/1", job_id=job_id,
                                      external_id="A-1", now="2026-10-19T10:00:00+00:00")
    sid2, created2 = db.record_sighting(conn, "feed_a", "https://a.example/1", job_id=job_id,
                                        external_id="A-1", content_hash="abc",
                                        now="2026-10-20T10:00:00+00:00")
    assert created and not created2 and sid == sid2
    row = conn.execute("SELECT * FROM sightings WHERE sighting_id = ?", (sid,)).fetchone()
    assert row["first_seen_at"] == "2026-10-19T10:00:00+00:00"
    assert row["last_seen_at"] == "2026-10-20T10:00:00+00:00"
    assert row["content_hash"] == "abc"
    assert count(conn, "sightings") == 1
    assert db.get_job(conn, job_id)["last_seen_at"] == "2026-10-20T10:00:00+00:00"


def test_sightings_without_external_id_dedupe_by_url(conn):
    _, job_id = make_job(conn)
    db.register_source(conn, "manual", "Manual", "manual")
    db.record_sighting(conn, "manual", "https://m.example/1", job_id=job_id)
    db.record_sighting(conn, "manual", "https://m.example/1", job_id=job_id)
    assert count(conn, "sightings") == 1


# 8
def test_unchanged_observation_creates_no_status_event(conn):
    _, job_id = make_job(conn)
    assert count(conn, "status_events") == 1  # the initial 'New' event
    assert db.append_status_event(conn, "Active", "Seen in complete check", job_id=job_id)
    assert count(conn, "status_events") == 2
    assert db.append_status_event(conn, "Active", "Seen again", job_id=job_id) is None
    assert count(conn, "status_events") == 2
    assert db.get_job(conn, job_id)["lifecycle_state"] == "Active"


def test_status_events_are_append_only(conn):
    _, job_id = make_job(conn)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE status_events SET reason = 'edited'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM status_events")
    assert count(conn, "status_events") == 1


# 9
def test_failed_and_partial_checks_are_distinct_from_complete(conn):
    db.register_source(conn, "feed_a", "Feed A", "ats_feed")
    run_id = db.start_run(conn, "discovery")
    ok = db.record_source_check(conn, run_id, "feed_a", "success", scope="board:one",
                                complete=True, record_count=10)
    partial = db.record_source_check(conn, run_id, "feed_a", "partial", scope="board:two",
                                     record_count=3, error_info="page 2 failed")
    failed = db.record_source_check(conn, run_id, "feed_a", "failed", scope="board:three",
                                    error_info="timeout")
    rows = {r["check_id"]: r for r in conn.execute("SELECT * FROM source_checks")}
    assert (rows[ok]["outcome"], rows[ok]["complete"]) == ("success", 1)
    assert (rows[partial]["outcome"], rows[partial]["complete"]) == ("partial", 0)
    assert (rows[failed]["outcome"], rows[failed]["complete"]) == ("failed", 0)
    with pytest.raises(sqlite3.IntegrityError):  # a partial check can never be 'complete'
        db.record_source_check(conn, run_id, "feed_a", "partial", scope="board:four", complete=True)
    with pytest.raises(sqlite3.IntegrityError):  # same run/source/scope only once
        db.record_source_check(conn, run_id, "feed_a", "success", scope="board:one", complete=True)
    db.finish_run(conn, run_id, "partial", summary={"checks": 3})
    assert conn.execute("SELECT result FROM runs").fetchone()[0] == "partial"


# 10
def test_verdict_survives_rediscovery(conn):
    _, job_id = make_job(conn)
    db.register_source(conn, "feed_a", "Feed A", "ats_feed")
    db.save_verdict(conn, "No", "not interested", job_id=job_id)
    db.record_sighting(conn, "feed_a", "https://a.example/1", job_id=job_id, external_id="A-1")
    db.record_sighting(conn, "feed_a", "https://a.example/1", job_id=job_id, external_id="A-1")
    db.append_status_event(conn, "Active", "Seen", job_id=job_id)
    verdict = db.get_verdict(conn, job_id=job_id)
    assert (verdict["decision"], verdict["notes"]) == ("No", "not interested")
    assert count(conn, "verdicts") == 1
    db.save_verdict(conn, "Maybe", "changed my mind", job_id=job_id)  # explicit human change
    assert count(conn, "verdicts") == 1
    assert db.get_verdict(conn, job_id=job_id)["decision"] == "Maybe"


# 11
def test_invalid_values_are_rejected(conn):
    company_id, job_id = make_job(conn)
    with pytest.raises(sqlite3.IntegrityError):
        db.save_verdict(conn, "Definitely", job_id=job_id)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE jobs SET lifecycle_state = 'Vanished' WHERE job_id = ?", (job_id,))
    with pytest.raises(sqlite3.IntegrityError):
        db.append_status_event(conn, "Vanished", "bad", job_id=job_id)
    with pytest.raises(sqlite3.IntegrityError):
        db.register_source(conn, "bad", "Bad", "carrier_pigeon")
    with pytest.raises(sqlite3.IntegrityError):
        db.start_run(conn, "nonsense")
    assert db.get_job(conn, job_id)["lifecycle_state"] == "New"


# 12
def test_failed_transaction_rolls_back(conn):
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction(conn):
            db.get_or_create_company(conn, "Rolled Back Co", "rollback.example")
            conn.execute(
                """INSERT INTO jobs (job_id, company_id, title, normalized_title, first_seen_at,
                                     last_seen_at, created_at, updated_at)
                   VALUES ('j', 'missing-company', 't', 't', 'n', 'n', 'n', 'n')""")
    assert not conn.in_transaction
    assert count(conn, "companies") == 0 and count(conn, "jobs") == 0


# 13
def test_second_connection_reads_committed_data(db_path, conn):
    company_id, job_id = make_job(conn)
    other = db.connect(db_path)
    try:
        assert db.get_company(other, company_id)["name"] == "Fake Co"
        assert db.get_job(other, job_id)["title"] == "Fake Analyst"
        assert other.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        other.close()


def test_readonly_connection_cannot_write(db_path):
    ro = db.connect(db_path, readonly=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("DELETE FROM companies")
    finally:
        ro.close()


# --- doctor and configuration -------------------------------------------

def project_with_db(tmp_path):
    for rel in cli.REQUIRED_DIRS:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    (tmp_path / ".env.example").write_text("X=1\n")
    (tmp_path / ".env").write_text("X=1\n")
    return tmp_path


def test_doctor_reports_missing_database_without_creating_it(tmp_path, monkeypatch):
    monkeypatch.delenv(db.DB_PATH_ENV, raising=False)
    root = project_with_db(tmp_path)
    results = cli.run_checks(root)
    assert any(s == cli.WARNING and "not initialized" in m for s, m in results)
    assert not (root / "data" / "discovery.db").exists()


def test_doctor_passes_on_initialized_database(tmp_path, monkeypatch):
    monkeypatch.delenv(db.DB_PATH_ENV, raising=False)
    root = project_with_db(tmp_path)
    db.initialize_database(root / "data" / "discovery.db")
    results = cli.run_checks(root)
    messages = " | ".join(m for _, m in results)
    assert cli.FAIL not in [s for s, _ in results]
    assert f"Schema version {SCHEMA_VERSION} is supported" in messages
    assert "All 8 application tables exist" in messages
    assert "Foreign-key enforcement is enabled" in messages


def test_doctor_flags_missing_table_and_future_version(tmp_path, monkeypatch):
    monkeypatch.delenv(db.DB_PATH_ENV, raising=False)
    root = project_with_db(tmp_path)
    path = root / "data" / "discovery.db"
    db.initialize_database(path)
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE verdicts")
    raw.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    raw.commit()
    raw.close()
    results = cli.run_checks(root)
    assert any(s == cli.FAIL and "verdicts" in m for s, m in results)
    assert any(s == cli.FAIL and "newer than supported" in m for s, m in results)


def test_doctor_flags_invalid_database_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv(db.DB_PATH_ENV, "  ")
    results = cli.run_checks(project_with_db(tmp_path))
    assert any(s == cli.FAIL and "Invalid database configuration" in m for s, m in results)


def test_db_path_resolution_order(tmp_path, monkeypatch):
    monkeypatch.delenv(db.DB_PATH_ENV, raising=False)
    assert db.resolve_db_path(tmp_path) == tmp_path / "data" / "discovery.db"
    (tmp_path / ".env").write_text("DISCOVERY_DB_PATH=custom/x.db\nOTHER=secret\n")
    assert db.resolve_db_path(tmp_path) == tmp_path / "custom" / "x.db"
    monkeypatch.setenv(db.DB_PATH_ENV, "env/y.db")
    assert db.resolve_db_path(tmp_path) == tmp_path / "env" / "y.db"
