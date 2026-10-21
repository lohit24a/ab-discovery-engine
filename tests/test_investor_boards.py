"""Investor / accelerator boards (Getro and Consider readers): pagination, completeness, identity, import safety.

Offline only (sockets blocked, JobSpy and Sheets forbidden). Payload shapes follow the public upstream descriptions
and are UNVERIFIED against live boards until the approved probe; fixtures must be re-checked afterwards.
"""

import argparse
import html
import json
import socket
from dataclasses import replace

import pytest

from discovery import cli, database as db, sheets_sync
from discovery.adapters import investor_boards as ib
from discovery.adapters import jobspy_source as js


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("network access attempted in an offline test")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(js, "_default_scrape", lambda **k: pytest.fail("JobSpy must not run for investor boards"))


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.initialize_database(path)
    c = db.connect(path)
    yield c
    c.close()


BOARDS = ib.load_boards()
# ACCEL / TECHSTARS here are variants whose ?page= parameter works, to exercise the page walk with fixtures. The shipped
# configs (page_param null) match what the live probe showed: Getro's server-rendered page ignores ?page=.
ACCEL = replace(BOARDS["accel"], api=None, page_param="page", location_filter=None, max_pages=10, max_requests=12)
TECHSTARS = replace(BOARDS["techstars"], api=None, page_param="page", location_filter=None)
SSR_LIVE = replace(BOARDS["accel"], api=None, location_filter=None, max_pages=10, max_requests=12)   # as observed live: no ?page=
API_ACCEL = replace(BOARDS["accel"], location_filter=None, max_pages=10, max_requests=12)             # validated client-API reader
LIGHTSPEED = replace(BOARDS["lightspeed"], location_filter=None, max_pages=10, max_requests=12)
CPAGE = b'<html><script>window.__C={"fixedBoard":"lightspeed","csrfToken":"tok-123"}</script></html>'


def CW(*responses):
    """Consider scans start with the board page (session + token), then the POSTs."""
    return Web([CPAGE, *responses])


# ------------------------------------------------------------------ fixtures

def getro_job(i, org="acme", org_id=None, domain="acme.com", **over):
    j = {"id": 5000 + i, "slug": f"role-{i}", "title": f"Product Analyst {i}", "url": f"https://boards.example/{org}/apply/{i}",
         "organization": {"id": org_id or abs(hash(org)) % 9000, "name": org.title(), "slug": org, "domain": domain},
         "locations": ["Bengaluru, India"], "searchableLocations": ["Bengaluru, Karnataka, India"], "workMode": "hybrid",
         "createdAt": 1760000000 + i, "seniority": "mid"}
    j.update(over)
    return j


def getro_company(i, name=None, domain=None, **over):
    c = {"id": 9000 + i, "name": name or f"Company {i}", "slug": f"company-{i}", "domain": domain, "locations": ["Pune, India"],
         "industryTags": ["AI"], "description": "Builds things", "headCount": 3, "jobsCount": 0}
    c.update(over)
    return c


def getro_html(items, total, key="jobs"):
    data = {"props": {"pageProps": {"initialState": {key: {"found": items, "total": total}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'.encode()


def consider_job(i, company="Acme", domain="acme.com", **over):
    j = {"jobId": f"c{i}", "title": f"Data Analyst {i}", "companyName": company, "companySlug": company.lower(),
         "companyDomain": domain, "locations": ["Bengaluru, India"], "remote": False, "hybrid": False,
         "timeStamp": "2026-10-10T08:00:00Z", "applyUrl": f"https://careers.example/{i}", "url": f"https://jobs.lsvp.com/jobs/c{i}"}
    j.update(over)
    return j


def consider_resp(jobs, total=None, cursor=None):
    body = {"jobs": jobs, "total": len(jobs) if total is None else total, "meta": {}}
    if cursor:
        body["meta"]["sequence"] = cursor
    return json.dumps(body).encode()


class Web:
    """Scripted responses per request; records every request."""

    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, board, req):
        self.calls.append(req)
        if not self.responses:
            raise AssertionError("unexpected extra request")
        r = self.responses.pop(0)
        return r if isinstance(r, tuple) else (200, r, None)


def run(board, web, scopes=("jobs",)):
    return ib.scan_board(board, web, scopes, sleep=lambda s: None)


# ------------------------------------------------------------------- config

def test_shipped_board_config_is_valid_unverified_and_complete_enough():
    assert set(BOARDS) == {"accel", "techstars", "space_capital", "lightspeed", "sequoia", "peakxv", "blume", "three_one4",
                           "nexus", "crane", "elevation"}
    assert {b.platform for b in BOARDS.values()} == {"getro", "consider", "unsupported"}
    assert all(b.permission == "unverified" and b.url.startswith("https://") for b in BOARDS.values())
    assert all(b.status in ib.STATUSES for b in BOARDS.values())
    assert all(b.min_delay_seconds >= 2 and b.max_requests <= ib.MAX_REQUESTS_CEILING for b in BOARDS.values())
    assert BOARDS["lightspeed"].board_slug == "lightspeed" and BOARDS["accel"].network_id == 8672
    # configured is not tested, and tested is not production-ready
    # Consider-hosted boards are disabled until the operator confirms Consider-level permission (final readiness, 2026-10-21)
    assert {k for k, b in BOARDS.items() if b.enabled} == {"accel", "three_one4", "techstars"}
    assert BOARDS["accel"].status == "validated_live" and BOARDS["lightspeed"].status == "validated_live"
    assert BOARDS["accel"].api == "getro_api" and BOARDS["accel"].hosts == {"jobs.accel.com", "api.getro.com"}
    assert {k for k, b in BOARDS.items() if b.status == "page_probed"} == {"blume", "nexus"}
    assert {k for k, b in BOARDS.items() if b.status == "validated_live"} == {"accel", "lightspeed", "three_one4", "peakxv", "techstars"}
    assert all(b.status == "configured" for k, b in BOARDS.items() if k in ("sequoia", "crane", "space_capital", "elevation"))
    assert BOARDS["peakxv"].board_slug == "sequoia-capital-india" and BOARDS["three_one4"].network_id == 51275 and BOARDS["techstars"].network_id == 89
    assert BOARDS["three_one4"].supported and BOARDS["three_one4"].api == "getro_api"
    assert BOARDS["peakxv"].companies_url == "https://careers.peakxv.com/companies" and BOARDS["nexus"].origin == "https://consider.com"
    assert not BOARDS["elevation"].supported


def test_board_selection_rejects_unknown_wildcard_and_forbidden(tmp_path):
    with pytest.raises(ib.InvestorBoardError):
        ib.select_boards(BOARDS, "")
    with pytest.raises(ib.InvestorBoardError):
        ib.select_boards(BOARDS, "*")
    with pytest.raises(ib.InvestorBoardError):
        ib.select_boards(BOARDS, "accel,nope")
    with pytest.raises(ib.InvestorBoardError, match="no supported reader"):
        ib.select_boards(BOARDS, "elevation")
    for name in ("blume", "nexus"):
        with pytest.raises(ib.InvestorBoardError, match="not enabled"):
            ib.select_boards(BOARDS, name)
    forbidden = dict(BOARDS, accel=replace(BOARDS["accel"], permission="forbidden"))
    with pytest.raises(ib.InvestorBoardError, match="forbidden"):
        ib.select_boards(forbidden, "accel")
    assert [b.key for b in ib.select_boards(BOARDS, "accel,accel,techstars")] == ["accel", "techstars"]
    for consider_board in ("lightspeed", "peakxv"):                       # no further Consider reads without confirmed permission
        with pytest.raises(ib.InvestorBoardError, match="not enabled"):
            ib.select_boards(BOARDS, consider_board)


def test_invalid_config_is_refused(tmp_path):
    base = json.loads(ib.CONFIG_PATH.read_text(encoding="utf-8"))
    for mutate in (lambda b: b.update(platform="weekday"), lambda b: b.update(url="http://x.example"),
                   lambda b: b.update(max_pages=10**6), lambda b: b.update(permission="maybe"),
                   lambda b: b.pop("max_requests")):
        data = json.loads(json.dumps(base))
        mutate(data["boards"][0])
        path = tmp_path / "b.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ib.InvestorBoardError):
            ib.load_boards(path)


def test_default_http_refuses_other_hosts():
    with pytest.raises(ib.InvestorBoardError):
        ib.default_http(ACCEL, {"method": "GET", "url": "https://evil.example/x"})


# ------------------------------------------------------------- normalization

def test_getro_job_normalization_keeps_investor_and_employer_apart():
    j = ib.normalize_getro_job(getro_job(1, org="acme", domain="https://www.Acme.com/about"), ACCEL)
    assert j["external_id"] == "5001" and j["title"] == "Product Analyst 1"
    assert j["url"] == "https://jobs.accel.com/companies/acme/jobs/role-1"            # the board's listing URL
    assert j["apply_url"] == "https://boards.example/acme/apply/1"                      # the employer's application URL
    assert j["company"]["name"] == "Acme" and j["company"]["domain"] == "acme.com"
    assert "accel" not in json.dumps(j["company"]).lower()                              # the investor is never the employer
    assert j["posted_date"] == "2025-10-09" and j["location"] == "Bengaluru, Karnataka, India" and j["remote_hint"] is False


def test_consider_job_normalization_and_missing_fields():
    j = ib.normalize_consider_job(consider_job(1), LIGHTSPEED)
    assert j["external_id"] == "c1" and j["company"] == {"name": "Acme", "slug": "acme", "board_company_id": None, "domain": "acme.com"}
    assert j["posted_date"] == "2026-10-10" and j["apply_url"] == "https://careers.example/1"
    bare = ib.normalize_consider_job({"jobId": "x", "title": "T", "applyUrl": "https://a.example/x"}, LIGHTSPEED)
    assert bare["company"]["name"] is None and bare["posted_date"] is None and bare["location"] is None
    assert ib.normalize_consider_job({"title": "no id"}, LIGHTSPEED) is None


@pytest.mark.parametrize("value,expected", [("https://www.acme.com/x", "acme.com"), ("ycombinator.com", None),
                                            ("jobs.lever.co", None), ("localhost", None), (None, None), (12, None)])
def test_reported_domain_rejects_shared_hosts(value, expected):
    assert ib.reported_domain(value) == expected


# --------------------------------------------------------- pagination (Getro)

def test_getro_multi_page_scan_is_complete_when_total_is_reached():
    pages = [getro_html([getro_job(1), getro_job(2)], 5), getro_html([getro_job(3), getro_job(4)], 5), getro_html([getro_job(5)], 5)]
    web = Web(pages)
    r = run(ACCEL, web)["jobs"]
    assert r.status == "complete" and r.complete and len(r.records) == 5 and r.pages_requested == 3 and r.requests == 3
    assert [c["url"] for c in web.calls] == ["https://jobs.accel.com/jobs", "https://jobs.accel.com/jobs?page=2", "https://jobs.accel.com/jobs?page=3"]
    assert r.reported_total == 5 and r.anomalies == []


def test_reported_400_but_20_retrieved_is_never_reported_complete():
    web = Web([getro_html([getro_job(i) for i in range(1, 11)], 400), getro_html([getro_job(i) for i in range(11, 21)], 400),
               getro_html([], 400)])
    r = run(ACCEL, web)["jobs"]
    assert len(r.records) == 20 and r.status == "partial" and not r.complete
    assert any("count mismatch: board reports 400, retrieved 20" in a for a in r.anomalies)


def test_page_parameter_ignored_by_the_board_is_detected_as_a_duplicate_page():
    same = getro_html([getro_job(1), getro_job(2)], 6)
    r = run(ACCEL, Web([same, same, same]))["jobs"]
    assert r.status == "partial" and not r.complete and len(r.records) == 2 and r.stopped == "duplicate page"


def test_premature_empty_page_is_partial_not_complete():
    r = run(ACCEL, Web([getro_html([getro_job(1)], 4), getro_html([], 4)]))["jobs"]
    assert r.status == "partial" and any("count mismatch" in a for a in r.anomalies) and len(r.records) == 1


def test_empty_but_complete_feed_is_verified_empty():
    r = run(ACCEL, Web([getro_html([], 0)]))["jobs"]
    assert r.status == "complete" and r.complete and r.verified_empty and r.records == []


def test_failed_first_request_is_failed_not_empty():
    r = run(ACCEL, Web([(None, None, "TimeoutError: timed out")]))["jobs"]
    assert r.status == "failed" and not r.complete and not r.verified_empty and r.error == "TimeoutError: timed out"


def test_network_error_on_a_later_page_keeps_earlier_records_as_partial():
    r = run(ACCEL, Web([getro_html([getro_job(1)], 3), (500, None, "HTTP 500")]))["jobs"]
    assert r.status == "partial" and len(r.records) == 1 and r.error == "HTTP 500" and not r.complete


def test_block_stops_immediately_without_retry():
    web = Web([(429, None, "HTTP 429"), getro_html([getro_job(1)], 1)])
    r = run(ACCEL, web)["jobs"]
    assert r.status == "blocked" and len(web.calls) == 1 and r.records == [] and "nothing retried" in r.error


def test_changed_page_schema_is_a_failed_scan():
    r = run(ACCEL, Web([b"<html>no embedded data</html>"]))["jobs"]
    assert r.status == "failed" and "no initialState.jobs.found" in r.error
    data = {"props": {"pageProps": {"initialState": {"jobs": {"found": "oops"}}}}}
    r = run(ACCEL, Web([f'<script id="__NEXT_DATA__">{json.dumps(data)}</script>'.encode()]))["jobs"]
    assert r.status == "failed"


def test_request_cap_and_page_cap_make_the_scan_partial():
    capped = replace(ACCEL, max_requests=2)
    pages = [getro_html([getro_job(i)], 50) for i in range(1, 4)]
    r = run(capped, Web(pages))["jobs"]
    assert r.status == "partial" and r.requests == 2 and "request cap reached" in r.anomalies
    pagecap = replace(ACCEL, max_pages=2)
    r = run(pagecap, Web([getro_html([getro_job(1)], 50), getro_html([getro_job(2)], 50)]))["jobs"]
    assert r.status == "partial" and r.stopped == "page cap"


def test_duplicate_records_inside_a_feed_are_collapsed_and_reported():
    r = run(ACCEL, Web([getro_html([getro_job(1), getro_job(1), getro_job(2)], 2)]))["jobs"]
    assert len(r.records) == 2 and r.complete and any("duplicate record" in a for a in r.anomalies)


# ------------------------------------------------------- pagination (Consider)

def test_consider_cursor_scan_walks_all_pages_and_matches_the_total():
    pages = [consider_resp([consider_job(1), consider_job(2)], 5, "cur1"), consider_resp([consider_job(3), consider_job(4)], 5, "cur2"),
             consider_resp([consider_job(5)], 5)]
    web = CW(*pages)
    r = run(LIGHTSPEED, web)["jobs"]
    assert r.complete and len(r.records) == 5 and r.pages_requested == 3 and r.requests == 4         # 1 session page + 3 result pages
    get, posts = web.calls[0], web.calls[1:]
    assert get["method"] == "GET" and get["url"] == "https://jobs.lsvp.com/jobs"
    bodies = [c["json"] for c in posts]
    assert "sequence" not in bodies[0]["meta"] and bodies[1]["meta"]["sequence"] == "cur1" and bodies[2]["meta"]["sequence"] == "cur2"
    assert posts[0]["url"] == "https://jobs.lsvp.com/api-boards/search-jobs" and bodies[0]["board"] == {"id": "lightspeed", "isParent": True}
    assert all(c["headers"] == {"X-CSRF-Token": "tok-123", "Accept": "application/json"} for c in posts)   # the board's own protocol
    assert r.filters == "none (all records on the board)"


def test_consider_location_filter_is_sent_to_the_source_and_recorded_in_the_scope():
    web = CW(consider_resp([consider_job(1)], 1))
    r = ib.scan_board(LIGHTSPEED, web, ("jobs",), sleep=lambda s: None, location="Bengaluru, Karnataka, India")["jobs"]
    assert web.calls[1]["json"]["query"] == {"locations": ["Bengaluru, Karnataka, India"]}
    assert r.complete and r.filters == "locations = Bengaluru, Karnataka, India"


def test_consider_without_the_page_token_is_a_failed_scan():
    r = run(LIGHTSPEED, Web([b"<html>no token here</html>"]))["jobs"]
    assert r.status == "failed" and not r.complete and "csrf token" in r.error
    r = run(LIGHTSPEED, Web([(500, None, "HTTP 500")]))["jobs"]
    assert r.status == "failed" and r.error == "HTTP 500"


def test_session_cookie_jar_starts_empty_and_only_holds_what_the_board_sets():
    sess = ib.Session()
    assert len(sess.jar) == 0


def test_repeated_cursor_is_detected():
    pages = [consider_resp([consider_job(1)], 9, "cur"), consider_resp([consider_job(2)], 9, "cur")]
    r = run(LIGHTSPEED, CW(*pages))["jobs"]
    assert r.status == "partial" and "repeated cursor" in r.anomalies and r.stopped == "repeated cursor"


def test_consider_total_mismatch_api_errors_and_unexpected_shapes():
    r = run(LIGHTSPEED, CW(consider_resp([consider_job(1)], 40), consider_resp([], 40)))["jobs"]
    assert r.status == "partial" and any("count mismatch" in a for a in r.anomalies)
    r = run(LIGHTSPEED, CW(json.dumps({"errors": ["boom"], "jobs": []}).encode()))["jobs"]
    assert r.status == "failed"
    r = run(LIGHTSPEED, CW(b"[]"))["jobs"]
    assert r.status == "failed" and "unexpected response" in r.error


def test_consider_has_no_company_directory_so_companies_are_derived_and_never_complete():
    web = CW(consider_resp([consider_job(1), consider_job(2, company="Other", domain="other.io")], 2))
    out = run(LIGHTSPEED, web, scopes=("jobs", "companies"))
    assert out["jobs"].complete and len(web.calls) == 2                                     # session page + 1 POST, nothing for companies
    comp = out["companies"]
    assert {c["name"] for c in comp.records} == {"Acme", "Other"} and not comp.complete and comp.status == "partial"
    assert "derived from job listings only" in comp.filters


def test_getro_company_directory_scan_for_stream_b():
    comps = [getro_company(1, "Quiet Co", "quiet.co"), getro_company(2, "Other Co")]
    r = run(ACCEL, Web([getro_html(comps, 2, key="companies")]), scopes=("companies",))["companies"]
    assert r.complete and [c["name"] for c in r.records] == ["Quiet Co", "Other Co"]
    assert r.records[0]["domain"] == "quiet.co" and r.records[0]["profile_url"] == "https://jobs.accel.com/companies/company-1"


# ------------------------------------------------------------ snapshot + import

def make_snapshot(boards_scans, **kw):
    out = []
    for board, scans in boards_scans:
        out.append({"key": board.key, "name": board.name, "platform": board.platform, "url": board.url, "permission": "unverified",
                    "scopes": {k: vars(v) for k, v in scans.items()}})
    import hashlib
    snap = {"format": ib.SNAPSHOT_FORMAT, "source": "investor_boards", "collected_at": "2026-10-20T12:00:00+00:00",
            "finished_at": "2026-10-20T12:01:00+00:00", "seconds": 1.0, "boards": out}
    snap["records_sha256"] = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    return snap


def getro_scans(board, jobs, companies=None, total=None):
    pages = [getro_html(jobs, len(jobs) if total is None else total)]
    resp = list(pages)
    scopes = ("jobs",)
    if companies is not None:
        resp.append(getro_html(companies, len(companies), key="companies"))
        scopes = ("jobs", "companies")
    return ib.scan_board(board, Web(resp), scopes, sleep=lambda s: None)


def add_yc_company(conn, yc_id, slug, domain="acme.com", name=None):
    facts = {"yc": {"yc_id": yc_id, "slug": slug, "website_domain_reported": domain, "is_hiring_reported": False},
             "india_relevance": {"class": "india_based"}, "sheet_publish": True}
    db.register_source(conn, "yc_oss", "YC", "yc")
    cid, _ = db.get_or_create_company(conn, name or slug.title(), None, facts=facts)
    db.record_sighting(conn, "yc_oss", f"https://www.ycombinator.com/companies/{slug}", company_id=cid, external_id=str(yc_id))
    return cid


def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("companies", "jobs", "sightings", "status_events", "verdicts")}


def test_import_creates_provisional_employer_with_investor_as_source_only(conn):
    scans = getro_scans(ACCEL, [getro_job(1, org="newco", domain="newco.io"), getro_job(2, org="newco", domain="newco.io")])
    c = ib.import_snapshot(conn, make_snapshot([(ACCEL, scans)]))
    assert (c.companies_created, c.jobs_created) == (1, 2) and c.result == "success"
    src = conn.execute("SELECT * FROM sources WHERE source_id = 'inv_accel'").fetchone()
    assert src["source_type"] == "investor_board" and src["url_or_identifier"] == "https://jobs.accel.com/"
    company = conn.execute("SELECT * FROM companies").fetchone()
    assert company["name"] == "Newco" and company["identity_status"] == "provisional" and company["domain"] is None
    facts = json.loads(company["facts"])
    assert facts["sheet_publish"] is False and facts["investor_boards"]["boards"]["accel"]["reported_domain"] == "newco.io"
    assert conn.execute("SELECT COUNT(*) FROM companies WHERE normalized_name LIKE '%accel%'").fetchone()[0] == 0
    j = conn.execute("SELECT * FROM jobs").fetchone()
    assert j["posting_url"].startswith("https://jobs.accel.com/companies/newco/jobs/") and j["lifecycle_state"] == "New"
    assert j["company_id"] == company["company_id"] and j["posted_at"] is None


def test_company_only_portfolio_entries_are_discovered_without_jobs(conn):
    scans = getro_scans(ACCEL, [], companies=[getro_company(1, "Quiet Co", "quiet.co"), getro_company(2, "Other Co")])
    c = ib.import_snapshot(conn, make_snapshot([(ACCEL, scans)]))
    assert c.companies_created == 2 and c.jobs_created == 0 and counts(conn)["jobs"] == 0
    quiet = json.loads(conn.execute("SELECT facts FROM companies WHERE name = 'Quiet Co'").fetchone()[0])["investor_boards"]["boards"]["accel"]
    assert quiet["industries"] == ["AI"] and quiet["description"] == "Builds things" and quiet["profile_url"].endswith("/companies/company-1")


def test_one_company_on_two_investor_boards_is_one_canonical_company_with_two_sightings(conn):
    a = getro_scans(ACCEL, [getro_job(1, org="sharedco", domain="shared.io")])
    t = getro_scans(TECHSTARS, [getro_job(2, org="sharedco-ts", domain="shared.io", id=7002)])
    ib.import_snapshot(conn, make_snapshot([(ACCEL, a), (TECHSTARS, t)]))
    assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 1
    sightings = conn.execute("SELECT source_id, COUNT(*) FROM sightings WHERE job_id IS NULL GROUP BY 1").fetchall()
    assert {r[0] for r in sightings} == {"inv_accel", "inv_techstars"}
    assert conn.execute("SELECT COUNT(DISTINCT company_id) FROM jobs").fetchone()[0] == 1
    facts = json.loads(conn.execute("SELECT facts FROM companies").fetchone()[0])["investor_boards"]
    assert set(facts["boards"]) == {"accel", "techstars"} and facts["reported_domains"] == ["shared.io"]


def test_board_company_links_to_the_canonical_yc_company_by_reported_domain(conn):
    yc = add_yc_company(conn, 100, "acme", "acme.com")
    before = counts(conn)["companies"]
    scans = getro_scans(ACCEL, [getro_job(1, org="acme-inc", domain="https://acme.com")])
    c = ib.import_snapshot(conn, make_snapshot([(ACCEL, scans)]))
    assert c.companies_linked_by_domain == 1 and c.companies_created == 0 and counts(conn)["companies"] == before
    assert conn.execute("SELECT company_id FROM jobs").fetchone()[0] == yc
    facts = json.loads(db.get_company(conn, yc)["facts"])
    assert facts["yc"]["website_domain_reported"] == "acme.com" and "investor_boards" in facts and facts["sheet_publish"] is True


def test_ambiguous_domain_or_shared_host_never_merges(conn):
    a = add_yc_company(conn, 1, "twin-a", "twin.com")
    b = add_yc_company(conn, 2, "twin-b", "twin.com")
    scans = getro_scans(ACCEL, [getro_job(1, org="twin", domain="twin.com"), getro_job(2, org="hosted", domain="ycombinator.com")])
    c = ib.import_snapshot(conn, make_snapshot([(ACCEL, scans)]))
    assert c.companies_ambiguous == 1 and any("matches several companies" in e[1] for e in c.errors) and c.result == "partial"
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE company_id IN (?, ?)", (a, b)).fetchone()[0] == 0
    assert c.companies_created == 1                                             # the shared-host company stays its own provisional record


def test_same_name_different_domain_is_not_merged_and_is_listed_as_a_possible_duplicate(conn):
    existing = add_yc_company(conn, 5, "acme", "acme.com", name="Acme")
    scans = getro_scans(ACCEL, [getro_job(1, org="acme-labs", domain="acmelabs.io", organization={
        "id": 1, "name": "Acme", "slug": "acme-labs", "domain": "acmelabs.io"})])
    c = ib.import_snapshot(conn, make_snapshot([(ACCEL, scans)]))
    assert c.companies_created == 1 and c.companies_linked_by_domain == 0
    new = conn.execute("SELECT company_id, facts FROM companies WHERE company_id <> ?", (existing,)).fetchone()
    assert json.loads(new["facts"])["investor_boards"]["boards"]["accel"]["possible_duplicates"] == [existing]


def test_replay_creates_no_duplicates_and_no_unnecessary_events(conn):
    add_yc_company(conn, 100, "acme", "acme.com")
    scans = getro_scans(ACCEL, [getro_job(1, org="acme", domain="acme.com"), getro_job(2, org="newco", domain="newco.io")],
                        companies=[getro_company(1, "Quiet Co")])
    snap = make_snapshot([(ACCEL, scans)])
    first = ib.import_snapshot(conn, snap)
    state = counts(conn)
    again = ib.import_snapshot(conn, snap)
    assert counts(conn) == state and again.jobs_created == 0 and again.companies_created == 0 and again.jobs_unchanged == 2
    assert first.jobs_created == 2 and conn.execute("SELECT COUNT(*) FROM source_checks WHERE source_id = 'inv_accel'").fetchone()[0] == 4


def test_source_checks_record_scope_completeness_counts_and_anomalies(conn):
    scans = ib.scan_board(ACCEL, Web([getro_html([getro_job(1)], 9), getro_html([], 9)]), ("jobs",), sleep=lambda s: None)
    ib.import_snapshot(conn, make_snapshot([(ACCEL, scans)]))
    row = conn.execute("SELECT * FROM source_checks WHERE source_id = 'inv_accel'").fetchone()
    assert row["scope"].startswith("jobs:none (all records on the board)") and row["outcome"] == "partial" and row["complete"] == 0
    assert row["record_count"] == 1 and "count mismatch: board reports 9, retrieved 1" in row["error_info"]
    conn2_scans = getro_scans(ACCEL, [getro_job(2)])
    run_id = ib.import_snapshot(conn, make_snapshot([(ACCEL, conn2_scans)])).run_id
    done = conn.execute("SELECT outcome, complete FROM source_checks WHERE run_id = ?", (run_id,)).fetchone()
    assert (done["outcome"], done["complete"]) == ("success", 1)


def test_partial_failed_or_empty_later_scans_never_close_or_erase_jobs(conn):
    ib.import_snapshot(conn, make_snapshot([(ACCEL, getro_scans(ACCEL, [getro_job(1), getro_job(2)]))]))
    for web in (Web([(500, None, "HTTP 500")]), Web([(429, None, "HTTP 429")]), Web([getro_html([], 0)])):
        scans = ib.scan_board(ACCEL, web, ("jobs",), sleep=lambda s: None)
        ib.import_snapshot(conn, make_snapshot([(ACCEL, scans)]))
    assert counts(conn)["jobs"] == 2
    assert {r[0] for r in conn.execute("SELECT lifecycle_state FROM jobs")} == {"New"}
    assert {r[0] for r in conn.execute("SELECT new_state FROM status_events")} <= {"New", "Changed"}


def test_changed_listing_content_is_a_changed_event_not_a_duplicate(conn):
    ib.import_snapshot(conn, make_snapshot([(ACCEL, getro_scans(ACCEL, [getro_job(1)]))]))
    c = ib.import_snapshot(conn, make_snapshot([(ACCEL, getro_scans(ACCEL, [getro_job(1, searchableLocations=["Remote, India"])]))]))
    assert c.jobs_changed == 1 and counts(conn)["jobs"] == 1


def test_existing_verdicts_notes_and_company_records_survive(conn):
    yc = add_yc_company(conn, 100, "acme", "acme.com")
    snap = make_snapshot([(ACCEL, getro_scans(ACCEL, [getro_job(1, org="acme", domain="acme.com")]))])
    ib.import_snapshot(conn, snap)
    job_id = conn.execute("SELECT job_id FROM jobs").fetchone()[0]
    db.save_verdict(conn, "Yes", "interesting", job_id=job_id)
    db.save_verdict(conn, "Maybe", "check later", company_id=yc)
    ib.import_snapshot(conn, snap)
    ib.import_snapshot(conn, make_snapshot([(ACCEL, ib.scan_board(ACCEL, Web([(500, None, "HTTP 500")]), ("jobs",), sleep=lambda s: None))]))
    assert db.get_verdict(conn, job_id=job_id)["notes"] == "interesting" and db.get_verdict(conn, company_id=yc)["decision"] == "Maybe"
    assert json.loads(db.get_company(conn, yc)["facts"])["yc"]["slug"] == "acme"


def test_duplicate_jobs_across_boards_stay_separate_but_are_reported_as_possible_duplicates(conn):
    a = getro_scans(ACCEL, [getro_job(1, org="dupco", domain="dup.io", title="Product Analyst", id=1, url="https://careers.dup.io/a/1")])
    t = getro_scans(TECHSTARS, [getro_job(1, org="dupco", domain="dup.io", title="Product Analyst", id=2, url="https://careers.dup.io/a/2")])
    snap = make_snapshot([(ACCEL, a), (TECHSTARS, t)])
    assert ib.preview_identity(conn, snap)["possible_duplicate_jobs_across_boards"] == 1
    ib.import_snapshot(conn, snap)
    assert counts(conn)["jobs"] == 2 and conn.execute("SELECT COUNT(DISTINCT company_id) FROM jobs").fetchone()[0] == 1


def test_investor_board_jobs_are_never_published_to_the_review_sheet(conn):
    cid = add_yc_company(conn, 100, "acme", "acme.com")
    ib.import_snapshot(conn, make_snapshot([(ACCEL, getro_scans(ACCEL, [getro_job(1, org="acme", domain="acme.com")]))]))
    direct = db.insert_job(conn, cid, "Directly entered job")
    job_id = conn.execute("SELECT job_id FROM jobs WHERE title = 'Product Analyst 1'").fetchone()[0]
    publishable = sheets_sync.load_publishable_job_ids(conn)
    assert job_id not in publishable and direct in publishable


def test_preview_reports_identity_outcomes_read_only(conn):
    add_yc_company(conn, 100, "acme", "acme.com")
    snap = make_snapshot([(ACCEL, getro_scans(ACCEL, [getro_job(1, org="acme", domain="acme.com"), getro_job(2, org="newco", domain="newco.io")]))])
    before = counts(conn)
    p = ib.summarize(snap, conn)
    assert p["identity"]["company_resolution"] == {"domain_match": 1, "new": 1} and p["identity"]["jobs_new_if_imported"] == 2
    assert p["totals"] == {"jobs": 2, "companies": 0, "requests": 1} and counts(conn) == before


def test_snapshot_round_trip_tamper_detection_and_git_ignore(conn, tmp_path):
    import subprocess
    from pathlib import Path
    snap = make_snapshot([(ACCEL, getro_scans(ACCEL, [getro_job(1)]))])
    path = ib.write_snapshot(snap, tmp_path)
    assert ib.load_snapshot(path)["boards"][0]["key"] == "accel"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["boards"][0]["scopes"]["jobs"]["records"][0]["title"] = "tampered"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ib.InvestorBoardError, match="hash"):
        ib.load_snapshot(path)
    repo = Path(ib.__file__).resolve().parents[2]
    rel = (ib.SNAPSHOT_DIR / "20261020120000_accel.json").relative_to(repo).as_posix()
    assert subprocess.run(["git", "check-ignore", "-q", rel], cwd=repo).returncode == 0


# --------------------------------------------------------------------- CLI

def inv_args(**kw):
    base = dict(boards="accel", scope="jobs", location=None, max_pages=None, max_requests=None, plan=False, approved_by_operator=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_cli_plan_sends_nothing_and_live_read_needs_the_operator_flag(tmp_path, capsys):
    calls = []
    web = lambda board, req: calls.append(req) or (200, getro_html([], 0), None)
    assert cli.cmd_investor_boards_collect(inv_args(plan=True), http=web, snapshot_dir=tmp_path) == 0
    out = capsys.readouterr().out
    assert "PLAN ONLY" in out and "https://jobs.accel.com/jobs" in out and "Maximum requests in total" in out and calls == []
    assert cli.cmd_investor_boards_collect(inv_args(approved_by_operator=False), http=web, snapshot_dir=tmp_path) == 1 and calls == []
    assert cli.cmd_investor_boards_collect(inv_args(boards="nope"), http=web, snapshot_dir=tmp_path) == 1
    assert cli.cmd_investor_boards_collect(inv_args(boards="*"), http=web, snapshot_dir=tmp_path) == 1


def test_cli_collect_then_dry_run_then_apply_with_backup(tmp_path, capsys):
    path = tmp_path / "c.db"
    db.initialize_database(path)
    web = Web([api_resp("jobs", [api_job(1, org="newco")], 1)])
    rc = cli.cmd_investor_boards_collect(inv_args(max_requests=3), http=web, sleep=lambda s: None, snapshot_dir=tmp_path / "snaps")
    out = capsys.readouterr().out
    assert rc == 0 and "COMPLETE" in out and "Nothing was written to SQLite" in out and len(web.calls) == 1
    snap_file = next((tmp_path / "snaps").glob("*.json"))
    rc = cli.cmd_investor_boards_import(argparse.Namespace(snapshot=str(snap_file), dry_run=True, apply=False), db_path=path, backup_dir=tmp_path / "bk")
    assert rc == 0 and "DRY RUN" in capsys.readouterr().out and not (tmp_path / "bk").exists()
    c = db.connect(path)
    assert counts(c)["jobs"] == 0
    c.close()
    rc = cli.cmd_investor_boards_import(argparse.Namespace(snapshot=str(snap_file), dry_run=False, apply=True), db_path=path, backup_dir=tmp_path / "bk")
    assert rc == 0 and list((tmp_path / "bk").glob("discovery_pre-investor-boards-import_*.db"))
    c = db.connect(path)
    assert counts(c)["jobs"] == 1 and counts(c)["companies"] == 1
    c.close()


def test_cli_partial_collection_exits_nonzero_and_reports_the_anomaly(tmp_path, capsys):
    web = Web([api_resp("jobs", [api_job(1)], 300), api_resp("jobs", [], 300)])
    rc = cli.cmd_investor_boards_collect(inv_args(), http=web, sleep=lambda s: None, snapshot_dir=tmp_path)
    out = capsys.readouterr().out
    assert rc == 2 and "PARTIAL" in out and "count mismatch: board reports 300, retrieved 1" in out


def test_module_never_touches_sheets_jobspy_or_credentials():
    source = open(ib.__file__, encoding="utf-8").read().lower()
    for forbidden in ("gspread", "sheets_sync", "jobspy", "restricted_policy", "dotenv", "environ", "verify=false", "proxies"):
        assert forbidden not in source.replace("no cookies/login/proxy", "").replace("no retries, no redirects", ""), forbidden
    assert '"cookie"' not in source and "set-cookie" not in source            # cookies come only from the scan's own jar


def test_investor_boards_are_not_part_of_the_default_pipeline_or_restricted_sources():
    from discovery import pipeline, restricted_policy as rp
    for name in ("investor_boards", "accel", "lightspeed", "inv_accel"):
        with pytest.raises(pipeline.PipelineError):
            pipeline.parse_sources(name)
    assert "investor_boards" not in pipeline.SOURCE_ORDER and set(rp.RESTRICTED_SOURCES) == {"indeed", "naukri", "linkedin"}
    assert "glassdoor" in rp.RETIRED_SOURCES


# ----------------------------------------- behaviours observed in the first live probes (Accel and Lightspeed)

def test_getro_server_rendered_page_holds_only_the_first_batch_so_the_scan_is_partial():
    live = SSR_LIVE                                         # page_param null, as observed live
    web = Web([getro_html([getro_job(i) for i in range(1, 21)], 45967)])
    r = run(live, web)["jobs"]
    assert r.status == "partial" and not r.complete and len(r.records) == 20 and r.reported_total == 45967
    assert len(web.calls) == 1 and web.calls[0]["url"] == "https://jobs.accel.com/jobs"          # no ?page= guessing
    assert r.stopped == "first batch only" and any("first 20 of 45967" in a for a in r.anomalies)


def test_getro_company_directory_first_batch_is_partial_but_carries_domain_for_identity():
    comps = [getro_company(i, f"Co {i}", f"co{i}.io") for i in range(1, 13)]
    r = run(SSR_LIVE, Web([getro_html(comps, 709, key="companies")]), scopes=("companies",))["companies"]
    assert r.status == "partial" and len(r.records) == 12 and r.reported_total == 709 and r.records[0]["domain"] == "co1.io"


def test_getro_small_board_that_fits_in_one_batch_is_complete_without_paging():
    r = run(SSR_LIVE, Web([getro_html([getro_job(1), getro_job(2)], 2)]))["jobs"]
    assert r.complete and r.status == "complete"


def test_consider_csrf_refusal_is_a_failed_scan_not_an_empty_one():
    web = CW((412, None, "HTTP 412: {\"error\":\"INVALID_CSRF\"}"))
    r = run(LIGHTSPEED, web)["jobs"]
    assert r.status == "failed" and not r.complete and not r.verified_empty and "INVALID_CSRF" in r.error and len(web.calls) == 2


def test_consider_board_id_is_read_from_the_page_when_not_configured():
    nexus = replace(BOARDS["nexus"], board_slug=None)
    page = b'<html><script>window.__C={"fixedBoard":"nexus-venture-partners","csrfToken":"x"}</script></html>'
    web = Web([page, consider_resp([consider_job(1)], 1)])
    r = run(nexus, web)["jobs"]
    assert r.complete and len(r.records) == 1 and r.pages_requested == 1 and r.requests == 2
    assert web.calls[0]["method"] == "GET" and web.calls[1]["json"]["board"]["id"] == "nexus-venture-partners"
    assert web.calls[1]["url"] == "https://consider.com/api-boards/search-jobs"
    failed = run(nexus, Web([b"<html>no board id</html>"]))["jobs"]
    assert failed.status == "failed" and "board id" in failed.error


def test_sequential_pacing_and_hard_ceilings_hold_for_every_configured_board():
    assert all(b.min_delay_seconds >= ib.MIN_DELAY_SECONDS and b.max_requests <= ib.MAX_REQUESTS_CEILING for b in BOARDS.values())
    sleeps = []
    ib.scan_board(ACCEL, Web([getro_html([getro_job(1)], 2), getro_html([getro_job(2)], 2)]), ("jobs",), sleep=sleeps.append)
    assert sleeps == [ACCEL.min_delay_seconds]


# ------------------------------------------------ Getro client API (validated live on Accel, 2026-10-20)

def api_resp(key, items, count):
    return json.dumps({"results": {key: items, "count": count}}).encode()


def api_job(i, org="acme", **over):
    j = {"id": 7000 + i, "slug": f"{7000 + i}-role-{i}", "title": f"Analyst {i}", "url": f"https://boards.greenhouse.io/{org}/jobs/{i}",
         "created_at": 1760000000 + i, "work_mode": "on_site", "locations": ["Bengaluru, Karnataka, India"],
         "searchable_locations": ["Bengaluru, Karnataka, India", "India"], "seniority": None,
         "organization": {"id": 300, "name": org.title(), "slug": org, "head_count": 3, "industry_tags": ["AI"], "stage": "seed", "topics": []}}
    j.update(over)
    return j


def test_getro_api_walks_zero_based_pages_of_20_until_the_count_is_reached():
    pages = [api_resp("jobs", [api_job(i) for i in range(1, 21)], 45), api_resp("jobs", [api_job(i) for i in range(21, 41)], 45),
             api_resp("jobs", [api_job(i) for i in range(41, 46)], 45)]
    web = Web(pages)
    r = run(API_ACCEL, web)["jobs"]
    assert r.complete and len(r.records) == 45 and r.pages_requested == 3 and r.reported_total == 45
    assert [c["json"]["page"] for c in web.calls] == [0, 1, 2]
    assert all(c["url"] == "https://api.getro.com/api/v2/collections/8672/search/jobs" and c["method"] == "POST" for c in web.calls)
    assert web.calls[0]["json"] == {"hitsPerPage": 20, "page": 0, "filters": {}, "query": ""}
    assert web.calls[0]["headers"] == {"Accept": "application/json"}
    first = r.records[0]
    assert first["company"]["slug"] == "acme" and first["apply_url"].startswith("https://boards.greenhouse.io/")
    assert first["url"] == "https://jobs.accel.com/companies/acme/jobs/7001-role-1" and first["posted_date"] == "2025-10-09"


def test_getro_api_location_filter_applies_at_the_source_and_scopes_the_completeness_claim():
    web = Web([api_resp("jobs", [api_job(1), api_job(2)], 2)])
    r = ib.scan_board(API_ACCEL, web, ("jobs",), sleep=lambda s: None, location="Bengaluru, Karnataka, India")["jobs"]
    assert web.calls[0]["json"]["filters"] == {"searchable_locations": ["Bengaluru, Karnataka, India"]}
    assert r.complete and r.filters == "searchable_locations = Bengaluru, Karnataka, India"


def test_getro_api_capped_run_of_a_large_filtered_scope_is_partial_not_complete():
    capped = replace(API_ACCEL, max_requests=2, max_pages=2)
    r = run(capped, Web([api_resp("jobs", [api_job(i) for i in range(1, 21)], 3065), api_resp("jobs", [api_job(i) for i in range(21, 41)], 3065)]))["jobs"]
    assert r.status == "partial" and not r.complete and len(r.records) == 40 and r.reported_total == 3065 and r.stopped == "page cap"


def test_getro_api_repeated_page_422_and_schema_changes_are_detected():
    same = api_resp("jobs", [api_job(1), api_job(2)], 10)
    r = run(API_ACCEL, Web([same, same]))["jobs"]
    assert r.status == "partial" and r.stopped == "duplicate page" and len(r.records) == 2
    r = run(API_ACCEL, Web([(422, None, 'HTTP 422: {"error":["filters: must be a hash"]}')]))["jobs"]
    assert r.status == "failed" and "must be a hash" in r.error
    r = run(API_ACCEL, Web([b'{"results": {"count": 3}}']))["jobs"]
    assert r.status == "failed" and not r.complete


def test_getro_api_block_is_not_retried_and_empty_filtered_scope_is_verified_empty():
    web = Web([(429, None, "HTTP 429"), api_resp("jobs", [api_job(1)], 1)])
    r = run(API_ACCEL, web)["jobs"]
    assert r.status == "blocked" and len(web.calls) == 1
    r = run(API_ACCEL, Web([api_resp("jobs", [], 0)]))["jobs"]
    assert r.complete and r.verified_empty


def test_getro_api_reads_the_network_id_from_the_page_when_not_configured():
    board = replace(API_ACCEL, network_id=None)
    web = Web([b"<html>no embedded data</html>", api_resp("jobs", [api_job(1)], 1)])
    r = run(board, web)["jobs"]
    assert r.status == "failed" and "network id" in r.error                  # a page without it cannot be guessed
    page = ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"network": {"id": "8672"}}}}) + '</script>').encode()
    web = Web([page, api_resp("jobs", [api_job(1)], 1)])
    r = run(board, web)["jobs"]
    assert r.complete and r.pages_requested == 1 and r.requests == 2
    assert web.calls[1]["url"].endswith("/collections/8672/search/jobs")


def test_getro_api_company_directory_is_unfiltered_and_carries_domains():
    comps = [{"id": 1, "name": "100ms", "slug": "100ms-2", "domain": "100ms.live", "description": "Video", "locations": ["Bengaluru, Karnataka, India"],
              "industry_tags": ["Audio"], "head_count": 3, "active_jobs_count": 4}]
    web = Web([api_resp("companies", comps, 1)])
    r = ib.scan_board(API_ACCEL, web, ("companies",), sleep=lambda s: None, location="Bengaluru, Karnataka, India")["companies"]
    assert web.calls[0]["json"]["filters"] == {} and web.calls[0]["url"].endswith("/search/companies")      # location not sent to the unprobed endpoint
    assert r.complete and r.records[0]["domain"] == "100ms.live" and r.records[0]["industries"] == ["Audio"] and r.records[0]["open_jobs_reported"] == 4


# ------------------------------------------- cross-source job identity by identical employer URL

def feashliaa_like_job(conn, url, company_name="Acme Boardco", title="Product Analyst"):
    db.register_source(conn, "feashliaa", "Feashliaa", "ats_feed")
    cid, _ = db.get_or_create_company(conn, company_name, None, facts={"sheet_publish": False})
    obs = db.observe_job(conn, source_id="feashliaa", source_url=url, company_id=cid, title=title, external_id="fz-1")
    return cid, obs.job_id


def test_board_job_with_the_same_employer_url_attaches_to_the_existing_job_and_company(conn):
    cid, job_id = feashliaa_like_job(conn, "https://boards.greenhouse.io/acme/jobs/1")
    snap = make_snapshot([(ACCEL, getro_scans(ACCEL, [getro_job(1, org="acmeco", domain="acme.com", url="https://boards.greenhouse.io/acme/jobs/1?gh_src=x")]))])
    before = counts(conn)
    preview = ib.preview_identity(conn, snap)
    assert preview["jobs_attach_by_employer_url"] == 1 and preview["jobs_new_if_imported"] == 0
    c = ib.import_snapshot(conn, snap)
    assert (c.jobs_attached, c.jobs_created, c.companies_created) == (1, 0, 0)
    assert counts(conn)["jobs"] == before["jobs"] and counts(conn)["companies"] == before["companies"]
    sources = {r[0] for r in conn.execute("SELECT source_id FROM sightings WHERE job_id = ?", (job_id,))}
    assert sources == {"feashliaa", "inv_accel"}                                    # one job, two independent sightings
    assert conn.execute("SELECT company_id FROM sightings WHERE source_id = 'inv_accel' AND job_id IS NULL").fetchone()[0] == cid
    assert json.loads(db.get_company(conn, cid)["facts"])["investor_boards"]["boards"]["accel"]["reported_domain"] == "acme.com"


def test_attached_jobs_replay_without_duplicates_or_events(conn):
    feashliaa_like_job(conn, "https://boards.greenhouse.io/acme/jobs/1")
    snap = make_snapshot([(ACCEL, getro_scans(ACCEL, [getro_job(1, org="acmeco", domain="acme.com", url="https://boards.greenhouse.io/acme/jobs/1")]))])
    ib.import_snapshot(conn, snap)
    state = counts(conn)
    again = ib.import_snapshot(conn, snap)
    third = ib.import_snapshot(conn, snap)
    assert counts(conn) == state and again.jobs_created == 0 and third.jobs_changed == 0 and again.errors == []


def test_second_investor_board_attaches_to_the_job_the_first_board_created(conn):
    a = getro_scans(ACCEL, [getro_job(1, org="dupco", domain="dup.io", url="https://boards.greenhouse.io/dupco/jobs/4567890")])
    t = getro_scans(TECHSTARS, [getro_job(2, org="dupco-t", domain="dup.io", id=7002, url="https://boards.greenhouse.io/dupco/jobs/4567890?gh_src=x")])
    ib.import_snapshot(conn, make_snapshot([(ACCEL, a)]))
    c = ib.import_snapshot(conn, make_snapshot([(TECHSTARS, t)]))
    assert c.jobs_attached == 1 and c.jobs_created == 0 and counts(conn)["jobs"] == 1 and counts(conn)["companies"] == 1


def test_url_match_that_contradicts_an_earlier_company_link_is_left_separate_and_reported(conn):
    other, _ = feashliaa_like_job(conn, "https://boards.greenhouse.io/acme/jobs/1", company_name="Different Co")
    add_yc_company(conn, 7, "acme", "acme.com", name="Acme")
    snap = make_snapshot([(ACCEL, getro_scans(ACCEL, [getro_job(1, org="acmeco", domain="acme.com", url="https://boards.greenhouse.io/acme/jobs/1")]))])
    c = ib.import_snapshot(conn, snap)
    assert c.url_conflicts == 1 and c.jobs_attached == 0 and c.jobs_created == 1 and c.result == "partial"
    assert any("another company" in e[1] for e in c.errors)


def test_url_shared_by_several_existing_jobs_is_ambiguous_and_never_merged(conn):
    feashliaa_like_job(conn, "https://boards.greenhouse.io/acme/jobs/1", company_name="One")
    db.register_source(conn, "yc_jobs", "YC", "yc")
    other, _ = db.get_or_create_company(conn, "Two", None, facts={})
    db.observe_job(conn, source_id="yc_jobs", source_url="https://boards.greenhouse.io/acme/jobs/1", company_id=other, title="Same", external_id="y1")
    snap = make_snapshot([(ACCEL, getro_scans(ACCEL, [getro_job(1, org="acmeco", domain="acme.com", url="https://boards.greenhouse.io/acme/jobs/1")]))])
    assert ib.preview_identity(conn, snap)["jobs_ambiguous_url"] == 1
    c = ib.import_snapshot(conn, snap)
    assert c.url_ambiguous == 1 and c.jobs_attached == 0 and c.jobs_created == 1


def test_titles_and_locations_alone_never_attach_a_board_job_to_an_existing_job(conn):
    feashliaa_like_job(conn, "https://boards.greenhouse.io/other/jobs/5", company_name="Acme Boardco", title="Product Analyst 1")
    snap = make_snapshot([(ACCEL, getro_scans(ACCEL, [getro_job(1, org="acmeco", domain="acme.com", url="https://boards.greenhouse.io/acme/jobs/1")]))])
    c = ib.import_snapshot(conn, snap)
    assert c.jobs_attached == 0 and c.jobs_created == 1 and counts(conn)["jobs"] == 2


def test_url_attachment_happens_before_the_company_directory_so_it_is_not_misread_as_a_conflict(conn):
    """Regression (3one4 import): the directory scope used to create the company first, turning URL evidence into a 'conflict'."""
    cid, job_id = feashliaa_like_job(conn, "https://boards.greenhouse.io/acme/jobs/1")
    org = {"id": 300, "name": "Acmeco", "slug": "acmeco", "domain": None}
    jobs = [getro_job(1, org="acmeco", url="https://boards.greenhouse.io/acme/jobs/1", organization=org)]
    companies = [getro_company(300, "Acmeco", None, id=300, slug="acmeco")]
    snap = make_snapshot([(ACCEL, getro_scans(ACCEL, jobs, companies=companies))])
    before = counts(conn)
    c = ib.import_snapshot(conn, snap)
    assert (c.jobs_attached, c.url_conflicts, c.errors) == (1, 0, [])
    assert counts(conn)["jobs"] == before["jobs"] and counts(conn)["companies"] == before["companies"]
    assert conn.execute("SELECT company_id FROM sightings WHERE source_id = 'inv_accel' AND job_id IS NULL AND external_id = '300'").fetchone()[0] == cid
    again = ib.import_snapshot(conn, snap)
    assert again.jobs_created == 0 and again.errors == [] and counts(conn)["jobs"] == before["jobs"]


def test_company_directory_is_filtered_only_by_an_explicit_company_location():
    comps = [{"id": 1, "name": "100ms", "slug": "100ms-2", "domain": "100ms.live", "locations": [], "industry_tags": []}]
    web = Web([api_resp("companies", comps, 1)])
    r = ib.scan_board(API_ACCEL, web, ("companies",), sleep=lambda s: None, company_location="Bengaluru, Karnataka, India")["companies"]
    assert web.calls[0]["json"]["filters"] == {"searchable_locations": ["Bengaluru, Karnataka, India"]}
    assert r.complete and "Bengaluru" in r.filters


def test_generic_employer_url_never_attaches_a_board_job_to_an_existing_job(conn):
    """Regression (Day 11): only a job-specific URL identifies a job; a shared careers page must not attach anything."""
    cid, job_id = feashliaa_like_job(conn, "https://careers.example.com/open-roles")
    jobs = [getro_job(1, org="acme-t", url="https://careers.example.com/open-roles")]
    before = counts(conn)
    c = ib.import_snapshot(conn, make_snapshot([(ACCEL, getro_scans(ACCEL, jobs))]))
    assert c.jobs_attached == 0 and c.jobs_created == 1 and counts(conn)["jobs"] == before["jobs"] + 1


def test_lever_source_variants_attach_by_scoped_posting_key_and_cross_tenant_ids_do_not(conn):
    url = "https://jobs.lever.co/meesho/974e9975-c536-4219-bdaa-9cd0f2d4d030"
    cid, job_id = feashliaa_like_job(conn, url)
    jobs = [getro_job(1, org="meesho-t", url=url + "?lever-source%5B%5D=careers"),
            getro_job(2, org="other", url="https://jobs.lever.co/other/974e9975-c536-4219-bdaa-9cd0f2d4d030")]
    c = ib.import_snapshot(conn, make_snapshot([(ACCEL, getro_scans(ACCEL, jobs))]))
    assert c.jobs_attached == 1 and c.jobs_created == 1                  # same tenant attaches; the same id on another tenant does not


def test_board_job_in_another_city_is_not_attached_to_an_existing_multi_location_posting(conn):
    url = "https://acme.wd5.myworkdayjobs.com/ext/job/Hyderabad/Analyst_R7654321"
    cid, job_id = feashliaa_like_job(conn, url)
    conn.execute("UPDATE jobs SET location = 'Hyderabad, India' WHERE job_id = ?", (job_id,))
    jobs = [getro_job(1, org="acme-t", url="https://acme.wd5.myworkdayjobs.com/ext/job/Hyderabad/Analyst_R7654321", locations=["Bengaluru, India"],
                      searchableLocations=["Bengaluru, Karnataka, India"])]
    c = ib.import_snapshot(conn, make_snapshot([(ACCEL, getro_scans(ACCEL, jobs))]))
    assert c.jobs_attached == 0 and c.url_ambiguous == 1 and c.jobs_created == 1
