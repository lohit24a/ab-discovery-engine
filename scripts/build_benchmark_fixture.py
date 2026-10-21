"""Writes tests/fixtures/benchmark_day13_independent_2026.csv from the research captured on 2026-10-21 (Day 13, revised methodology).

Every field below was read from the public posting page or board listing named in `evidence_reference`; nothing was copied from SQLite and
no database was opened to choose these records. Run once; the CSV is then frozen (the evaluator refuses a fixture whose digest changed).
"""
import csv
import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "benchmark_day13_independent_2026.csv"
FETCH_DATE = "2026-10-21"
COLUMNS = ["benchmark_id", "group", "employer", "exact_title", "original_discovery_source", "original_url", "direct_employer_or_ats_url", "stable_ats_identifier",
           "location_text", "remote_restriction_text", "experience_text", "expected_role_family", "availability_evidence", "availability_limits",
           "source_observation_date", "selection_rationale", "evidence_reference", "unresolved_or_missing"]

LEVER = "web search result (jobs.lever.co posting)"
GH = "employer greenhouse board (found via web search)"
POS, SEN, NEG = "A_positive", "B_family_match_but_senior", "N_negative_control"

R = [
    ("A01", POS, "Sprinto", "Forward Deployment Engineer", LEVER, "https://jobs.lever.co/Sprinto/9aaf7afb-2d4c-4f16-9458-d7b9a82dd9c3", "same (employer Lever page)",
     "lever:Sprinto:9aaf7afb-2d4c-4f16-9458-d7b9a82dd9c3", "Bengaluru; Remote", "page says '100% remote'; no country restriction stated", "none stated", "forward_deployed_solutions",
     "posting page loaded with 'Apply for this job' on 2026-10-21", "CONFLICT: Sprinto's own Lever board index, fetched later the same day (35 jobs), did not list this posting",
     "Customer-facing deployment role at an India-based security-compliance startup", "page fetch 2026-10-21; board fetch https://jobs.lever.co/Sprinto", "posting date not shown"),
    ("A02", POS, "Sprinto", "Revenue Operations Analyst - Customer Experience & Partner Operations", "employer Lever board (found via web search of the same employer)",
     "https://jobs.lever.co/Sprinto/56d536c2-812c-49be-8421-6ad63c42a273", "same", "lever:Sprinto:56d536c2-812c-49be-8421-6ad63c42a273", "Bengaluru; Remote",
     "page says '100% remote'; no country restriction stated", "3-5 years of experience in Revenue Operations", "gtm_engineering (weak 'revenue operations')",
     "posting page loaded with apply button 2026-10-21; listed on the employer's Lever board index 2026-10-21", "board listing is the employer's own index, not independent proof the vacancy is open",
     "Strategy & Operations team posting; tests a weak-alias family and mid experience", "page fetch and board fetch 2026-10-21", "posting date not shown"),
    ("A03", POS, "Prismforce", "Associate Product Manager", "web search result (employer Keka careers page)", "https://prismforce.keka.com/careers/jobdetails/152797", "same",
     "keka:prismforce:152797", "Bangalore / Bengaluru", "not stated", "1-3 years", "associate_product_ops", "page loaded with 'Apply for this job' on 2026-10-21",
     "no board index checked", "Associate product role at a Bengaluru SaaS company", "page fetch 2026-10-21", "posting date and remote policy not shown"),
    ("A04", POS, "Apna", "Product Operations Associate", "web search result (standout.work mirror of the employer posting)", "https://standout.work/jobs/d2ce3dea-fe36-439b-9bd5-8c72f3196f08",
     "https://apply.workable.com/apna/j/E75B63B3A7/apply (as quoted by the mirror page; the Workable page itself was NOT fetched)", "workable:apna:E75B63B3A7",
     "Bengaluru, Karnataka (Domlur)", "Workplace on_site; Work from Office 5 days/week", "0-2 years", "associate_product_ops",
     "mirror page shows 'Posted 2026-09-12' and an 'Apply at Apna' button on 2026-10-21", "availability inferred from a third-party mirror; employer page not fetched",
     "Early-career product operations role", "mirror page fetch 2026-10-21", "Workable page not verified directly"),
    ("A05", POS, "Sarvam AI", "Deployment Strategist", "web search result (employer careers page)", "https://www.sarvam.ai/careers/jobs/48c228f1-0762-4d4e-9d83-ae8f751ce19d", "same",
     "sarvam-careers:48c228f1-0762-4d4e-9d83-ae8f751ce19d", "Bengaluru", "On-Site; no country restriction", "'hiring across levels, from 4+ years'", "forward_deployed_solutions",
     "page loaded with 'Apply for this role' on 2026-10-21", "no board index checked", "Customer deployment at an Indian AI company", "page fetch 2026-10-21", "posting date not shown"),
    ("A06", POS, "Prodigal", "AI Deployment Lead (Bengaluru)", "web search result (employbl mirror) then employer greenhouse board", "https://job-boards.greenhouse.io/prodigal/jobs/5260521007", "same",
     "greenhouse:prodigal:5260521007", "Bengaluru", "not stated", "2+ years in any B2B program management role", "ai_implementation",
     "page loaded with Apply form on 2026-10-21; listed on the employer's board index the same day", "board index is the employer's own, not independent proof the vacancy is open",
     "AI implementation / deployment ownership", "page fetch and board fetch https://job-boards.greenhouse.io/prodigal 2026-10-21", "posting date not shown"),
    ("A07", POS, "Prodigal", "Strategy and Operations Analyst", GH, "https://job-boards.greenhouse.io/prodigal/jobs/5043755007", "same", "greenhouse:prodigal:5043755007",
     "Bengaluru (Koramangala)", "5 days in office", "no years stated ('relevant data analytics experience')", "founders_office (strategy & operations)",
     "page loaded with Apply form on 2026-10-21; listed on the employer's board index the same day", "board index is the employer's own", "Strategic operations analyst; second role family at the same employer",
     "page fetch and board fetch 2026-10-21", "posting date not shown"),
    ("A08", POS, "Emergent Labs", "Forward Deployed Engineer (FDE)", GH, "https://job-boards.greenhouse.io/emergentlabsinc/jobs/4433173009", "same", "greenhouse:emergentlabsinc:4433173009",
     "Bangalore", "Work From Office, 5 days/week", "'Senior (4+ years experience)'", "forward_deployed_solutions", "page loaded with Apply form on 2026-10-21; listed on the board index",
     "board index is the employer's own", "Customer-facing AI agent deployment", "page fetch and board fetch https://job-boards.greenhouse.io/emergentlabsinc 2026-10-21", "posting date not shown; experience wording says Senior 4+ years"),
    ("A09", POS, "Libra AI", "Associate Product Manager (AI)", "web search result (Built In Bengaluru listing)", "https://builtinbengaluru.in/job/associate-product-manager-ai/9566479", "NOT FOUND (the aggregator links only to its own company profile)",
     "none", "Bengaluru, Bengaluru Urban, Karnataka", "In-Office", "1-3 years (technical, product or analytics role; internships included)", "associate_product_ops",
     "aggregator page says 'Reposted 2 Months Ago'; no apply button; no closed notice", "AGGREGATOR-ONLY evidence; availability unverified", "Early-career AI product role", "page fetch 2026-10-21",
     "employer posting URL and any ATS id missing"),
    ("A10", POS, "Hevo Data", "Strategy & Ops (Founder's Office)", LEVER, "https://jobs.lever.co/hevodata/8d0ee4a7-1ecb-4129-af6b-14abc69b84e5", "same", "lever:hevodata:8d0ee4a7-1ecb-4129-af6b-14abc69b84e5",
     "Bangalore, India (HSR Layout)", "On-site", "2 to 8 years", "founders_office", "page loaded with 'Apply for this job' on 2026-10-21", "no board index checked",
     "Founder's office / strategy & operations", "page fetch 2026-10-21", "posting date not shown"),
    ("A11", POS, "Turing", "Product Manager", GH, "https://job-boards.greenhouse.io/turing/jobs/6227621004", "same", "greenhouse:turing:6227621004", "India - Remote",
     "'Remote'; no further country restriction stated beyond the India location", "2+ years product management and 2+ years software/data/design experience", "associate_product_ops (weak 'product manager')",
     "page loaded with Apply form on 2026-10-21; listed on the employer's board index (32 jobs) the same day", "board index is the employer's own", "Explicit India-remote case with a generic title",
     "page fetch and board fetch https://job-boards.greenhouse.io/turing/jobs/6116617004 (redirected to the board) 2026-10-21", "posting date shown only as a 'New' badge"),
    ("B01", SEN, "Gushwork", "Chief of Staff", "employer Lever board (found via web search of the employer)", "https://jobs.lever.co/gushwork/d9e8217c-24f8-41d9-bef1-5af93bb65685", "same",
     "lever:gushwork:d9e8217c-24f8-41d9-bef1-5af93bb65685", "Bengaluru", "Hybrid", "'7 or more years'", "founders_office", "page loaded with apply button 2026-10-21; on board index",
     "board index is the employer's own", "Family match but explicitly senior: tests experience handling; NOT in the recall denominator", "board fetch https://jobs.lever.co/gushwork", "posting date not shown"),
    ("B02", SEN, "Turing", "Forward-Deployed AI Engineer - Enterprise AI", GH, "https://job-boards.greenhouse.io/turing/jobs/6192725004", "same", "greenhouse:turing:6192725004",
     "Bengaluru, Karnataka, India (Bengaluru or Hyderabad)", "Hybrid, 3 days/week in office", "'8+ years'", "forward_deployed_solutions", "page loaded with Apply form 2026-10-21; on board index",
     "board index is the employer's own", "Family match but explicitly senior; NOT in the recall denominator", "page fetch 2026-10-21", "posting date not shown"),
    ("B03", SEN, "Emergent Labs", "Finance Manager", GH, "https://job-boards.greenhouse.io/emergentlabsinc/jobs/4419142009", "same", "greenhouse:emergentlabsinc:4419142009", "Bengaluru", "not stated",
     "'Chartered Accountant with 5-7 years of post-qualification experience'", "business_finance_ops (loosely; the duties are accounts payable and statutory compliance)",
     "page loaded with 'Apply Now' 2026-10-21; on board index", "board index is the employer's own", "Boundary case: accounting and senior; NOT in the recall denominator", "page fetch 2026-10-21", "posting date not shown"),
    ("N01", NEG, "Meltplan", "Senior Full Stack Engineer - Frontend Focused", "employer greenhouse board listing", "https://job-boards.greenhouse.io/meltplan/jobs/4146430009", "same", "greenhouse:meltplan:4146430009",
     "Bengaluru, India", "not stated", "not captured", "none (software engineering)", "listed on the employer's board index 2026-10-21", "listing only; the posting page was not fetched", "Clearly non-target control",
     "board fetch https://job-boards.greenhouse.io/meltplan 2026-10-21", "experience and posting date not captured"),
    ("N02", NEG, "Meltplan", "UI Engineering Lead", "employer greenhouse board listing", "https://job-boards.greenhouse.io/meltplan/jobs/4250926009", "same", "greenhouse:meltplan:4250926009", "Bengaluru, India", "not stated",
     "not captured", "none (software engineering)", "listed on the employer's board index 2026-10-21", "listing only", "Clearly non-target control", "board fetch 2026-10-21", "experience and posting date not captured"),
    ("N03", NEG, "Emergent Labs", "Motion Designer", "employer greenhouse board listing", "https://job-boards.greenhouse.io/emergentlabsinc/jobs/4438233009", "same", "greenhouse:emergentlabsinc:4438233009", "Bangalore", "not stated",
     "not captured", "none (design)", "listed on the employer's board index 2026-10-21", "listing only", "Clearly non-target control", "board fetch 2026-10-21", "experience and posting date not captured"),
    ("N04", NEG, "Emergent Labs", "Senior Software Engineer - Backend", "employer greenhouse board listing", "https://job-boards.greenhouse.io/emergentlabsinc/jobs/4030460009", "same", "greenhouse:emergentlabsinc:4030460009", "Bangalore", "not stated",
     "not captured", "none (software engineering)", "listed on the employer's board index 2026-10-21", "listing only", "Clearly non-target control", "board fetch 2026-10-21", "experience and posting date not captured"),
    ("N05", NEG, "Emergent Labs", "Legal Counsel", "employer greenhouse board listing", "https://job-boards.greenhouse.io/emergentlabsinc/jobs/4330481009", "same", "greenhouse:emergentlabsinc:4330481009", "Bengaluru", "not stated",
     "not captured", "none (legal)", "listed on the employer's board index 2026-10-21", "listing only", "Clearly non-target control", "board fetch 2026-10-21", "experience and posting date not captured"),
]

REJECTED = [
    ("Jobgether GTM Engineer (Lever)", "page returned HTTP 404"), ("Atomicwork Implementation Consultant (two ids)", "both pages returned HTTP 404; board also 404"),
    ("Stance Health Associate - Founder's Office (Antler board)", "page is a script-rendered portfolio index with no posting content"), ("Cashfree Manager FP&A (Kula)", "page returned HTTP 404"),
    ("Easyship Workflow Developer (Built In)", "page says the job was removed on 2025-12-15"), ("Gushwork GTM Engineer (Lever)", "page returned HTTP 404"),
    ("Sarvam AI Solution Specialist (Saarthi)", "page says Closed / no longer accepting applications"), ("Hevo Data Business Associate - Founder's Office (Lever)", "page returned HTTP 404"),
    ("Meltplan Forward Deployed Engineer / Applied AI Engineer", "no longer on the employer's board index"), ("Turing Strategy and Operations Associate (two ids)", "no longer on the employer's board index; page redirects to the board"),
    ("FamPay Business & Partnerships Associate (Lever)", "partnerships / business development role outside the eight families (3+ years)"),
    ("Observe.AI Engagement Manager", "board redirects off-site and the redirect was not followed (budget)"),
]


def main():
    created = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS + ["fixture_created_utc"])
        for r in R:
            row = list(r[:14]) + [FETCH_DATE] + list(r[14:])        # source_observation_date sits after availability_limits
            assert len(row) == len(COLUMNS), (row[0], len(row))
            w.writerow(row + [created])
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    meta = {"created_utc": created, "csv_sha256": digest, "searches_used": 28, "searches_cap": 30, "fetches_used": 37, "fetches_cap": 40,
            "rejected_before_matching": [{"candidate": c, "objective_reason": why} for c, why in REJECTED]}
    (ROOT / "exports").mkdir(exist_ok=True)
    (ROOT / "exports" / "day13_benchmark_research_log.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(created, digest, len(R), "records")


if __name__ == "__main__":
    main()
