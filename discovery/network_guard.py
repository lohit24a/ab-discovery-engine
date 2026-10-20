"""VPN preflight for restricted job-board requests. Fails closed and never reveals an IP address.

run_preflight() must pass IMMEDIATELY before a restricted source is contacted (and is repeated between
searches). A check is 'pass', 'fail' or 'inconclusive'; any critical check that is not 'pass' blocks the run.
Nothing here switches networks, changes Windows settings, disables TLS verification or falls back to a direct
connection. Details contain only classes, countries, counts and yes/no facts - never an address.

Limits (also in docs/source_audits/jobspy.md): the egress/DNS "network owner" class is a heuristic from the owner
name returned by ipinfo.io; a pass is a snapshot and cannot guarantee the VPN will not drop mid-request (the
Surfshark Kill Switch is the operating-system protection for that); the Kill Switch and Bypasser settings
live inside the Surfshark app and are not readable from here.
"""

import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field

ENDPOINT = "https://ipinfo.io/json"
VPN_ADAPTER_PATTERN = re.compile(r"surfshark", re.I)
VPN_OWNER_WORDS = ("surfshark", "hosting", "vpn", "datacamp", "m247", "leaseweb", "digitalocean", "amazon", "google",
                   "ovh", "cloud", "colo", "choopa", "packet", "oracle")
PASS, FAIL, INCONCLUSIVE = "pass", "fail", "inconclusive"
QUICK_CHECKS = ("vpn_interface", "vpn_routing", "egress_class", "no_proxy")


class NetworkGuardError(Exception):
    """The VPN preflight did not pass. No restricted request may start."""


@dataclass
class Check:
    name: str
    status: str
    detail: str
    critical: bool = True


@dataclass
class Preflight:
    checks: list = field(default_factory=list)

    @property
    def ok(self):
        return bool(self.checks) and all(c.status == PASS for c in self.checks if c.critical)

    def failures(self):
        return [c for c in self.checks if c.critical and c.status != PASS]

    def lines(self):
        marks = {PASS: "PASS", FAIL: "FAIL", INCONCLUSIVE: "INCONCLUSIVE"}
        out = [f"[{marks[c.status]:12}] {c.name}: {c.detail}" + ("" if c.critical else " (advisory)") for c in self.checks]
        out.append("VPN preflight: " + ("PASSED" if self.ok else "BLOCKED (a critical check did not pass)"))
        return out


def classify_owner(org: str) -> str:
    o = (org or "").lower()
    if "surfshark" in o:
        return "surfshark"
    return "hosting/vpn-like" if any(w in o for w in VPN_OWNER_WORDS) else "consumer-isp-like"


def _digest(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()


class WindowsProbes:
    """Real probes. Each raises on failure; the caller turns exceptions into 'inconclusive'."""

    def _ps(self, script: str):
        if sys.platform != "win32":
            raise RuntimeError("Windows-only probe")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=40, encoding="utf-8")
        if out.returncode != 0:
            raise RuntimeError("PowerShell probe failed")
        text = out.stdout.strip()
        data = json.loads(text) if text else []
        return data if isinstance(data, list) else [data]

    def adapters(self):
        return self._ps("Get-NetAdapter | Where-Object Status -eq 'Up' | Select-Object Name,InterfaceDescription,"
                        "ifIndex | ConvertTo-Json -Compress")

    def routes(self):
        return self._ps(
            "Get-NetRoute -AddressFamily IPv4 | Where-Object { $_.DestinationPrefix -in '0.0.0.0/0','0.0.0.0/1','128.0.0.0/1' } "
            "| ForEach-Object { [pscustomobject]@{prefix=$_.DestinationPrefix; ifIndex=$_.InterfaceIndex; "
            "routeMetric=$_.RouteMetric; ifMetric=(Get-NetIPInterface -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4).InterfaceMetric} } "
            "| ConvertTo-Json -Compress")

    def ipv6_default_routes(self):
        # no ::/0 route at all is the good case; PowerShell reports that as an error, so treat it as an empty list
        return self._ps("$r = @(Get-NetRoute -DestinationPrefix '::/0' -ErrorAction SilentlyContinue); "
                        "if ($r.Count -eq 0) { '[]' } else { $r | Select-Object @{n='ifIndex';e={$_.InterfaceIndex}} | "
                        "ConvertTo-Json -Compress }; exit 0")

    def proxy_settings(self):
        env = [k for k in os.environ if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")]
        found = [f"env:{k}" for k in env] + [f"python:{k}" for k in urllib.request.getproxies()]
        out = subprocess.run(["netsh", "winhttp", "show", "proxy"], capture_output=True, text=True, timeout=20)
        if "Direct access" not in out.stdout:
            found.append("winhttp")
        key = self._ps("$k = Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings'; "
                       "[pscustomobject]@{enable=[int]$k.ProxyEnable; pac=[bool]$k.AutoConfigURL; server=[bool]$k.ProxyServer} "
                       "| ConvertTo-Json -Compress")[0]
        if key.get("enable") or key.get("pac") or key.get("server"):
            found.append("user-proxy")
        return found

    def egress(self):
        req = urllib.request.Request(ENDPOINT, headers={"User-Agent": "ab-discovery-engine vpn preflight", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:   # default context: certificate verification ON
            info = json.loads(resp.read().decode("utf-8"))
        return {"class": classify_owner(info.get("org")), "country": info.get("country"), "digest": _digest(info["ip"])}

    def curl_digest(self):
        curl = shutil.which("curl.exe")
        if not curl:
            return None
        out = subprocess.run([curl, "-s", "--max-time", "20", ENDPOINT], capture_output=True, text=True, timeout=30)
        return _digest(json.loads(out.stdout)["ip"])

    def resolver_class(self):
        out = subprocess.run(["nslookup", "-type=TXT", "o-o.myaddr.l.google.com"], capture_output=True, text=True, timeout=30).stdout
        ips = re.findall(r'"(\d{1,3}(?:\.\d{1,3}){3})"', out)
        if not ips:
            raise RuntimeError("no resolver echo")
        req = urllib.request.Request(f"https://ipinfo.io/{ips[0]}/json", headers={"User-Agent": "ab-discovery-engine vpn preflight"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        return {"class": classify_owner(info.get("org")), "country": info.get("country")}

    def tls_state(self):
        ctx = ssl.create_default_context()
        overrides = [k for k in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE") if os.environ.get(k)]
        return {"verify": ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname, "overrides": overrides}


def run_preflight(probes=None, *, quick: bool = False) -> Preflight:
    """Run the preflight. quick=True runs only the core checks (used between searches)."""
    probes = probes or WindowsProbes()
    result = Preflight()

    def attempt(name, fn, exc_critical=True):
        if quick and name not in QUICK_CHECKS:
            return None
        try:
            check = fn()
        except Exception as exc:
            check = Check(name, INCONCLUSIVE, f"could not be determined ({type(exc).__name__})", exc_critical)
        result.checks.append(check)
        return check

    vpn_index = {}

    def vpn_interface():
        ups = [a for a in probes.adapters() if VPN_ADAPTER_PATTERN.search(f"{a.get('Name', '')} {a.get('InterfaceDescription', '')}")]
        vpn_index["idx"] = {a["ifIndex"] for a in ups}
        return Check("vpn_interface", PASS if ups else FAIL, "Surfshark adapter is up" if ups else "no active Surfshark adapter")

    def vpn_routing():
        routes = probes.routes()
        vpn = vpn_index.get("idx", set())
        if not vpn:
            return Check("vpn_routing", FAIL, "no VPN adapter to route through")

        def best(prefixes):
            cands = [r for r in routes if r["prefix"] in prefixes]
            return min(cands, key=lambda r: (r["routeMetric"] or 0) + (r["ifMetric"] or 0)) if cands else None
        halves = [best({"0.0.0.0/1"}), best({"128.0.0.0/1"})]
        # more specific /1 routes win over the /0 route regardless of metric; fall back to the /0 route
        chosen = halves if all(halves) else [best({"0.0.0.0/0"})] * 2
        if not all(chosen):
            return Check("vpn_routing", FAIL, "no default IPv4 route found")
        ok = all(r["ifIndex"] in vpn for r in chosen)
        return Check("vpn_routing", PASS if ok else FAIL, "all IPv4 traffic is routed via the VPN adapter" if ok
                     else "default IPv4 route does not go through the VPN adapter")

    egress = {}

    def egress_class():
        e = probes.egress()
        egress.update(e)
        ok = e["class"] != "consumer-isp-like"
        return Check("egress_class", PASS if ok else FAIL, f"egress looks {e['class']} (country {e['country']}); heuristic, not proof of privacy")

    def no_proxy():
        found = probes.proxy_settings()
        return Check("no_proxy", FAIL if found else PASS, "proxy/PAC settings present: " + ", ".join(found) if found
                     else "no proxy variables, WinHTTP proxy or user proxy")

    def same_as_system():
        other = probes.curl_digest()
        if other is None:
            return Check("same_egress_as_windows_curl", INCONCLUSIVE, "curl.exe not available for the split-tunnel comparison",
                         critical=False)
        return Check("same_egress_as_windows_curl", PASS if other == egress.get("digest") else FAIL,
                     "Python and Windows curl.exe share one egress" if other == egress.get("digest")
                     else "Python and curl.exe leave from different addresses (possible split tunnelling)")

    def ipv6():
        stray = [r for r in probes.ipv6_default_routes() if r.get("ifIndex") not in vpn_index.get("idx", set())]
        return Check("ipv6_exposure", FAIL if stray else PASS, "an IPv6 default route exists outside the VPN adapter" if stray
                     else "no IPv6 default route outside the VPN adapter")

    def dns():
        d = probes.resolver_class()
        return Check("dns_routing", PASS if d["class"] != "consumer-isp-like" else FAIL,
                     f"DNS queries leave from a {d['class']} network (country {d['country']})")

    def tls():
        t = probes.tls_state()
        if not t["verify"]:
            return Check("tls_verification", FAIL, "certificate verification is not enforced")
        if t["overrides"]:
            return Check("tls_verification", FAIL, "a CA override variable is set: " + ", ".join(t["overrides"]))
        return Check("tls_verification", PASS, "certificate verification enforced, no CA overrides")

    attempt("vpn_interface", vpn_interface)
    attempt("vpn_routing", vpn_routing)
    attempt("egress_class", egress_class)
    attempt("no_proxy", no_proxy)
    attempt("same_egress_as_windows_curl", same_as_system, exc_critical=False)   # unavailable = advisory; a mismatch is critical
    attempt("ipv6_exposure", ipv6)
    attempt("dns_routing", dns)
    attempt("tls_verification", tls)
    return result


def require_vpn(probes=None, *, quick: bool = False) -> Preflight:
    """Raise NetworkGuardError unless every critical check passes."""
    result = run_preflight(probes, quick=quick)
    if not result.ok:
        bad = ", ".join(f"{c.name}={c.status}" for c in result.failures())
        raise NetworkGuardError(f"VPN preflight blocked the request ({bad})")
    return result
