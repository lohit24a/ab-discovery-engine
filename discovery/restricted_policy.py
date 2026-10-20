"""Permanent policy for RESTRICTED job boards: Indeed, Naukri, LinkedIn.

These never run as part of normal discovery. Source of truth for the maximum scope is
config/restricted_sources.json. Every live run (and every import of collected data) needs a fresh, one-time,
interactive confirmation typed by the operator at a real terminal, bound to the exact sources and exact
search configuration shown. This module enforces that in code; prompts and Skill text are not relied on.

What an Authorization is NOT: it is never stored (no file, no .env, no database), it cannot be created by
constructing the class directly, it works for one execution only, and it dies with the process, on
cancellation, on expiry, or when the policy file / plan no longer matches.
"""

import hashlib
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, fields as dc_fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = PROJECT_ROOT / "config" / "restricted_sources.json"

RESTRICTED_SOURCES = ("indeed", "naukri", "linkedin")
# Investigated and permanently removed (decision D-23): never selectable, never authorizable. Kept only to give a clear error.
RETIRED_SOURCES = ("glassdoor", "jobspy_glassdoor")
NORMAL_SOURCES = ("yc", "feashliaa")
AUTH_TTL_SECONDS = 900
PLAN_TTL_SECONDS = 900                      # a displayed plan can be approved in chat for this long
PENDING_DIR = PROJECT_ROOT / "data" / "restricted" / "pending"     # git-ignored (data/*)

# Defense in depth: the config file cannot raise limits above these without a code change.
HARD_MAX_SEARCHES, HARD_MAX_RESULTS, HARD_MAX_PAGINATION = 5, 25, 2
HARD_MAX_ENRICHMENT, HARD_MAX_SECONDS, HARD_MAX_BUDGET = 0, 600, 20
ACCESS_METHOD = "jobspy_python_api"
HARD_MAX_BASE_REQUESTS = 1
AUTHORIZATION_STATES = ("operator_confirmed", "not_established")
# Providers whose test must be authorized on its own: never batched with another provider in one confirmation.
SOLO_SOURCES = ("linkedin",)
# Providers with no country parameter in their scraper (location text only).
NO_COUNTRY_SOURCES = ("linkedin",)

_WILDCARD = re.compile(r"[*?%]")
_GENERIC = {"all", "any", "everything", "*"}


class PolicyError(Exception):
    """A request or the policy file violates the restricted-source policy."""


class AuthorizationError(Exception):
    """No valid one-time operator authorization for this action."""


class NonInteractiveError(AuthorizationError):
    """There is no real interactive terminal, so the operator cannot confirm."""


@dataclass(frozen=True)
class SourcePolicy:
    name: str
    enabled: bool
    provider_authorization: str
    authorization_covers_vpn_egress: bool
    authorization_reference: str
    base_requests_per_search: int
    approved_search_terms: tuple
    approved_locations: tuple
    country: object
    max_searches_per_run: int
    max_results_per_search: int
    max_pagination_requests: int
    max_enrichment_requests: int
    request_budget: int
    max_duration_seconds: int
    min_delay_seconds: float
    access_method: str
    requires_vpn: bool
    allow_description_fetch: bool
    allow_import: bool
    allow_sheets_publication: bool
    retries_after_restriction: int

    def as_dict(self):
        return {f.name: (list(getattr(self, f.name)) if isinstance(getattr(self, f.name), tuple) else getattr(self, f.name))
                for f in dc_fields(self)}


_FIELD_NAMES = {f.name for f in dc_fields(SourcePolicy)} - {"name"}


def _check_int(name, key, value, hard_max):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PolicyError(f"{name}.{key} must be a non-negative integer")
    if value > hard_max:
        raise PolicyError(f"{name}.{key}={value} exceeds the hard ceiling {hard_max}")
    return value


def _check_strings(name, key, value):
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise PolicyError(f"{name}.{key} must be a list of non-empty strings")
    for v in value:
        if _WILDCARD.search(v) or v.strip().lower() in _GENERIC:
            raise PolicyError(f"{name}.{key} may not contain wildcards or generic entries ({v!r})")
    return tuple(v.strip() for v in value)


def load_policy(path=POLICY_PATH, run_override=None):
    """Load and strictly validate the policy. Raises PolicyError on anything unexpected.

    run_override ({source: {"attest": bool}}) enables named sources IN MEMORY for one execution; the file is never
    changed. It is applied to the raw values BEFORE validation, so every rule still applies. For a source whose
    provider authorization is 'not_established', attest=True records the operator's statement (made in chat) that
    the authorization exists and covers VPN egress; without it the source stays refused.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))["sources"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PolicyError(f"restricted-source policy unreadable ({type(exc).__name__})") from exc
    if not isinstance(raw, dict) or set(raw) != set(RESTRICTED_SOURCES):
        raise PolicyError(f"policy must define exactly these sources: {', '.join(RESTRICTED_SOURCES)}")
    for name, opts in (run_override or {}).items():
        if name not in RESTRICTED_SOURCES or not isinstance(raw.get(name), dict):
            raise PolicyError(f"cannot override unknown source {name!r}")
        raw[name] = dict(raw[name], enabled=True)
        if opts.get("attest"):
            raw[name].update(provider_authorization="operator_confirmed", authorization_covers_vpn_egress=True)
    out = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict) or set(cfg) != _FIELD_NAMES:
            raise PolicyError(f"{name}: unexpected or missing policy fields")
        for flag in ("enabled", "authorization_covers_vpn_egress", "requires_vpn", "allow_description_fetch", "allow_import", "allow_sheets_publication"):
            if not isinstance(cfg[flag], bool):
                raise PolicyError(f"{name}.{flag} must be true or false")
        if cfg["provider_authorization"] not in AUTHORIZATION_STATES:
            raise PolicyError(f"{name}.provider_authorization must be one of {', '.join(AUTHORIZATION_STATES)}")
        if not isinstance(cfg["authorization_reference"], str):
            raise PolicyError(f"{name}.authorization_reference must be text")
        base = _check_int(name, "base_requests_per_search", cfg["base_requests_per_search"], HARD_MAX_BASE_REQUESTS)
        if not cfg["requires_vpn"]:
            raise PolicyError(f"{name}.requires_vpn must stay true")
        if cfg["allow_sheets_publication"]:
            raise PolicyError(f"{name}.allow_sheets_publication must stay false until relevance rules are approved")
        if cfg["allow_description_fetch"]:
            raise PolicyError(f"{name}.allow_description_fetch must stay false at this stage")
        if cfg["access_method"] != ACCESS_METHOD:
            raise PolicyError(f"{name}.access_method must be {ACCESS_METHOD!r}")
        searches = _check_int(name, "max_searches_per_run", cfg["max_searches_per_run"], HARD_MAX_SEARCHES)
        results = _check_int(name, "max_results_per_search", cfg["max_results_per_search"], HARD_MAX_RESULTS)
        pagination = _check_int(name, "max_pagination_requests", cfg["max_pagination_requests"], HARD_MAX_PAGINATION)
        enrichment = _check_int(name, "max_enrichment_requests", cfg["max_enrichment_requests"], HARD_MAX_ENRICHMENT)
        budget = _check_int(name, "request_budget", cfg["request_budget"], HARD_MAX_BUDGET)
        seconds = _check_int(name, "max_duration_seconds", cfg["max_duration_seconds"], HARD_MAX_SECONDS)
        _check_int(name, "retries_after_restriction", cfg["retries_after_restriction"], 0)
        delay = cfg["min_delay_seconds"]
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay < 1:
            raise PolicyError(f"{name}.min_delay_seconds must be at least 1")
        terms = _check_strings(name, "approved_search_terms", cfg["approved_search_terms"])
        locations = _check_strings(name, "approved_locations", cfg["approved_locations"])
        country = cfg["country"]
        if country is not None and (not isinstance(country, str) or _WILDCARD.search(country)):
            raise PolicyError(f"{name}.country is invalid")
        if cfg["enabled"]:
            if cfg["provider_authorization"] != "operator_confirmed":
                raise PolicyError(f"{name} cannot be enabled: provider authorization is not established")
            if not cfg["authorization_covers_vpn_egress"]:
                raise PolicyError(f"{name} cannot be enabled: the provider authorization is not confirmed to cover VPN egress")
            if not (terms and locations and (country or name in NO_COUNTRY_SOURCES) and searches and results and seconds
                    and budget and base):
                raise PolicyError(f"{name} is enabled but has no complete approved scope")
            if searches > len(terms) * len(locations):
                raise PolicyError(f"{name}.max_searches_per_run exceeds the approved term/location combinations")
            if budget < searches * base:
                raise PolicyError(f"{name}.request_budget is below the base requests its searches need ({searches} x {base})")
        out[name] = SourcePolicy(name=name, enabled=cfg["enabled"], provider_authorization=cfg["provider_authorization"],
                                 authorization_covers_vpn_egress=cfg["authorization_covers_vpn_egress"],
                                 authorization_reference=cfg["authorization_reference"], base_requests_per_search=base,
                                 approved_search_terms=terms, approved_locations=locations,
                                 country=country, max_searches_per_run=searches, max_results_per_search=results,
                                 max_pagination_requests=pagination, max_enrichment_requests=enrichment,
                                 request_budget=budget, max_duration_seconds=seconds, min_delay_seconds=delay,
                                 access_method=cfg["access_method"], requires_vpn=True,
                                 allow_description_fetch=False, allow_import=cfg["allow_import"],
                                 allow_sheets_publication=False, retries_after_restriction=0)
    return out


# ------------------------------------------------------------------ plans

def _norm(text):
    return re.sub(r"\s+", " ", text.strip().casefold())


def parse_source_names(text):
    """Parse a --sources value for the restricted commands. Wildcards, 'all' and unknown names are rejected."""
    names = [n.strip().lower() for n in (text or "").split(",") if n.strip()]
    if not names:
        raise PolicyError("Name the restricted sources explicitly, e.g. --sources indeed,naukri")
    for n in names:
        if n in RETIRED_SOURCES:
            raise PolicyError(f"{n!r} is an unsupported source: it was retired from this project (decision D-23) and cannot be run")
        if _WILDCARD.search(n) or n in _GENERIC:
            raise PolicyError(f"{n!r} is not allowed: restricted sources must be named one by one")
        if n in NORMAL_SOURCES:
            raise PolicyError(f"{n!r} is a normal source; use 'run', not the restricted commands")
        if n not in RESTRICTED_SOURCES:
            raise PolicyError(f"unknown restricted source {n!r}; available: {', '.join(RESTRICTED_SOURCES)}")
    return [n for n in RESTRICTED_SOURCES if n in names]


@dataclass(frozen=True)
class SearchSpec:
    source: str
    term: str
    location: str
    country: object
    results: int


@dataclass(frozen=True)
class Plan:
    action: str                 # 'collect' or 'import'
    sources: tuple
    searches: tuple
    limits: tuple               # ((source, ((key, value), ...)), ...)
    extra: str = ""             # for 'import': hash of the snapshot being imported

    def canonical(self):
        return {"action": self.action, "sources": list(self.sources), "extra": self.extra,
                "searches": [vars(s) for s in self.searches], "limits": [[s, [list(kv) for kv in l]] for s, l in self.limits]}

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.canonical(), sort_keys=True).encode()).hexdigest()

    def searches_for(self, source):
        return [s for s in self.searches if s.source == source]


def _limits(p: SourcePolicy):
    return tuple(sorted(p.as_dict().items(), key=lambda kv: kv[0]))


def build_plan(policies, source_names, *, terms=None, results=None, action="collect", extra=""):
    """Turn a request into the exact scope that may be authorized. Anything outside policy raises PolicyError."""
    if action not in ("collect", "import"):
        raise PolicyError("unknown action")
    names = [n for n in RESTRICTED_SOURCES if n in source_names]
    if not names or len(names) != len(set(source_names)):
        raise PolicyError("Name each restricted source explicitly (indeed, naukri, linkedin)")
    solo = [n for n in names if n in SOLO_SOURCES]
    if solo and len(names) > 1:
        raise PolicyError(f"{', '.join(solo)} must be authorized on its own, separately from any other provider")
    searches, limits = [], []
    for name in names:
        p = policies[name]
        if not p.enabled:
            raise PolicyError(f"{name} is disabled in config/restricted_sources.json and cannot be authorized")
        if p.provider_authorization != "operator_confirmed" or not p.authorization_covers_vpn_egress:
            raise PolicyError(f"{name}: provider authorization (including VPN egress) is not established")
        if action == "import":
            if not p.allow_import:
                raise PolicyError(f"{name}: importing is not permitted by policy")
            limits.append((name, _limits(p)))
            continue
        chosen = list(p.approved_search_terms)
        if terms:
            wanted = [_norm(t) for t in terms]
            approved = {_norm(t): t for t in p.approved_search_terms}
            bad = [t for t in terms if _norm(t) not in approved]
            if bad:
                raise PolicyError(f"{name}: search term(s) not approved: {', '.join(repr(b) for b in bad)}")
            chosen = [approved[w] for w in dict.fromkeys(wanted)]
        n_results = p.max_results_per_search if results is None else results
        if isinstance(n_results, bool) or not isinstance(n_results, int) or not 1 <= n_results <= p.max_results_per_search:
            raise PolicyError(f"{name}: results per search must be between 1 and {p.max_results_per_search}")
        combos = [(t, loc) for t in chosen for loc in p.approved_locations]
        if len(combos) > p.max_searches_per_run:
            raise PolicyError(f"{name}: {len(combos)} searches requested, at most {p.max_searches_per_run} per run")
        if len(combos) * p.base_requests_per_search > p.request_budget:
            raise PolicyError(f"{name}: request budget {p.request_budget} cannot cover {len(combos)} searches "
                              f"x {p.base_requests_per_search} base requests")
        searches.extend(SearchSpec(name, t, loc, p.country, n_results) for t, loc in combos)
        limits.append((name, _limits(p)))
    return Plan(action, tuple(names), tuple(searches), tuple(limits), extra)


def describe_plan(plan, policies, override=None):
    """Human-readable scope shown before any confirmation."""
    lines = [f"ACTION: {plan.action.upper()} (one execution, one time)",
             "Providers that would be contacted: " + (", ".join(s.upper() for s in plan.sources) if plan.action == "collect"
                                                       else "none (import reads the saved snapshot only)")]
    for s in plan.sources:
        p = policies[s]
        lines.append(f"- {s.upper()}: access method {p.access_method}, VPN required, retries after restrictions: 0, "
                     f"provider authorization: {p.provider_authorization}"
                     + (f" (ref {p.authorization_reference})" if p.authorization_reference else ""))
        if override and s in override:
            lines.append(f"    execution-scoped: enabled for THIS single run only"
                         + (" on the operator's stated provider authorization (incl. VPN egress)" if override[s].get("attest") else "")
                         + "; the policy file stays disabled")
        if plan.action == "collect":
            for spec in plan.searches_for(s):
                lines.append(f"    search: term={spec.term!r} location={spec.location!r} country={spec.country!r} "
                             f"requested results={spec.results}")
            per = p.base_requests_per_search + p.max_pagination_requests + p.max_enrichment_requests
            n = len(plan.searches_for(s))
            lines.append(f"    max requests: {min(p.request_budget, n * per)} (budget {p.request_budget}; per search {per}) | "
                         f"max runtime {p.max_duration_seconds}s | min delay {p.min_delay_seconds}s | "
                         f"description enrichment: {'allowed' if p.allow_description_fetch else 'NOT allowed'}")
    if plan.action == "collect":
        lines += ["SQLite writes: none (results go to a temporary snapshot file; importing needs a separate approval)",
                  "Google Sheets publication: BLOCKED for these sources (TEST and LIVE untouched)"]
    else:
        lines += [f"SQLite writes: a verified backup, then jobs/companies/sightings/source checks from snapshot {plan.extra[:12]}",
                  "No provider is contacted. Google Sheets publication: BLOCKED"]
    return lines


# ------------------------------------------------------- authorization

_MINT = object()


class Authorization:
    """One-time permission for exactly one plan. Only authorize_interactively() creates one."""

    def __init__(self, mint, plan, clock=time.monotonic):
        if mint is not _MINT:
            raise AuthorizationError("an Authorization can only be created by the operator's interactive confirmation")
        self._clock = clock
        self._plan = plan
        self._fingerprint = plan.fingerprint()
        self._expires = clock() + AUTH_TTL_SECONDS
        self._state = "granted"        # granted -> running -> finished | cancelled

    @property
    def action(self):
        return self._plan.action

    @property
    def plan(self):
        return self._plan

    def begin(self, plan):
        """Start the single execution. Fails if used, cancelled, expired or the scope differs."""
        if self._state != "granted":
            raise AuthorizationError(f"this authorization is {self._state}; ask the operator for a new one")
        if self._clock() > self._expires:
            self._state = "cancelled"
            raise AuthorizationError("authorization expired; ask the operator for a new one")
        if plan.fingerprint() != self._fingerprint:
            self._state = "cancelled"
            raise AuthorizationError("the approved scope or policy changed since the confirmation; authorization voided")
        self._state = "running"

    def require_running(self, spec=None):
        if self._state != "running":
            raise AuthorizationError("no live authorization for this action")
        if self._clock() > self._expires:
            self._state = "cancelled"
            raise AuthorizationError("authorization expired")
        if spec is not None and spec not in self._plan.searches:
            raise AuthorizationError("this search is outside the authorized scope")

    def finish(self):
        self._state = "finished"

    def cancel(self):
        self._state = "cancelled"


# ------------------------------------------------- chat-approved plans (display now, approve later, once)
#
# What Python enforces here: a plan can only be approved by quoting the code of a plan that `restricted-plan`
# recorded earlier (separate process), the plan must be unchanged (the code is its fingerprint, recomputed from the
# policy file and request now), unexpired and unused (the record is atomically renamed before the run starts), and the
# VPN preflight must pass first. What Python CANNOT enforce: that a human actually typed "Run" in a chat. It cannot
# authenticate a chat message; that part rests on Claude Code's workflow and its tool-permission system.

def plan_code(plan):
    return plan.fingerprint()[:12]


def _pending_file(directory, plan):
    return Path(directory) / f"{'-'.join(plan.sources)}.json"


def save_pending(plan, override, *, directory=None, now=time.time):
    """Record a displayed plan awaiting the operator's chat approval. Replaces an older pending plan for the same sources."""
    directory = Path(directory or PENDING_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    record = {"code": plan_code(plan), "fingerprint": plan.fingerprint(), "sources": list(plan.sources),
              "override": override or {}, "created_at": now(), "expires_at": now() + PLAN_TTL_SECONDS}
    path = _pending_file(directory, plan)
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(record), encoding="utf-8")
    os.replace(tmp, path)
    return record


def pending_plans(directory=None):
    directory = Path(directory or PENDING_DIR)
    out = []
    for f in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            out.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def cancel_pending(directory=None):
    n = 0
    for f, _ in pending_plans(directory):
        f.unlink(missing_ok=True)
        n += 1
    return n


def authorize_from_chat(plan, override, code, *, directory=None, vpn_check=None, now=time.time, clock=time.monotonic):
    """Mint a one-time Authorization for a plan the operator approved in chat by quoting its code.

    Raises AuthorizationError unless exactly the displayed plan is approved; NetworkGuardError if the VPN preflight
    fails (the pending plan is then left untouched and unused).
    """
    code = (code or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{12}", code):
        raise AuthorizationError("approval needs the 12-character plan code shown with the plan")
    if plan.action != "collect":
        raise AuthorizationError("chat approval is only available for collection plans")
    candidates = [(f, r) for f, r in pending_plans(directory) if r.get("code") == code]
    if len(candidates) != 1:
        raise AuthorizationError("no displayed plan awaits approval under that code (never shown, replaced, expired or already used)")
    path, record = candidates[0]
    if now() > record.get("expires_at", 0):
        path.unlink(missing_ok=True)
        raise AuthorizationError("the displayed plan expired; ask for a new plan")
    if record.get("sources") != list(plan.sources):
        raise AuthorizationError("that code belongs to a different plan (other sources); it was not used")
    if record.get("fingerprint") != plan.fingerprint() or record.get("override") != (override or {}):
        path.unlink(missing_ok=True)
        raise AuthorizationError("the scope or policy changed since the plan was displayed; approval voided")
    from .network_guard import require_vpn
    (vpn_check or require_vpn)()                    # raises NetworkGuardError; the pending plan stays unused
    try:
        os.replace(path, path.with_suffix(".consumed"))      # atomic: a second approval cannot find the plan
    except OSError as exc:
        raise AuthorizationError("this plan was already used") from exc
    return Authorization(_MINT, plan, clock)


class RealConsole:
    """The operator's terminal. A pipe, redirect or tool-run process has no TTY, so it cannot confirm."""

    def is_interactive(self):
        return bool(sys.stdin and sys.stdout and sys.stdin.isatty() and sys.stdout.isatty())

    def write(self, text):
        print(text)

    def read(self, prompt):
        return input(prompt)


def _phrase(plan, nonce):
    verb = "RUN" if plan.action == "collect" else "IMPORT"
    return f"{verb} {' '.join(s.upper() for s in plan.sources)} {nonce}"


def authorize_interactively(plan, policies, *, console=None, vpn_check=None, clock=time.monotonic):
    """Show the exact scope, run the VPN preflight, then require the operator to type the confirmation phrase.

    The phrase contains a random code that only appears in the terminal. Raises NonInteractiveError without a
    real terminal; raises NetworkGuardError (before asking) when the VPN preflight does not pass.
    """
    console = console or RealConsole()
    if not console.is_interactive():
        raise NonInteractiveError("no interactive terminal: ask the operator to run this command in their own terminal "
                                  "and type the confirmation themselves; it cannot be supplied by an assistant or script")
    for line in describe_plan(plan, policies):
        console.write(line)
    if plan.action == "collect":
        from .network_guard import require_vpn   # imported lazily so offline modules never touch the network layer
        result = (vpn_check or require_vpn)()
        for line in getattr(result, "lines", lambda: [])():
            console.write(line)
    nonce = f"{secrets.randbelow(10000):04d}"
    expected = _phrase(plan, nonce)
    console.write("")
    reply = console.read(f"Type exactly '{expected}' to authorize this single execution (anything else cancels): ")
    if (reply or "").strip() != expected:
        raise AuthorizationError("not confirmed; nothing was run")
    return Authorization(_MINT, plan, clock)
