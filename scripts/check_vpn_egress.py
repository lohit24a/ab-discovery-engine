"""VPN preflight for restricted job-board requests (Surfshark). Never prints or stores an IP address.

    .\\.venv\\Scripts\\python.exe scripts\\check_vpn_egress.py

Runs the same checks the restricted-source commands run immediately before any request
(discovery/network_guard.py): Surfshark adapter up, IPv4 routing via the VPN, egress class, proxy settings,
agreement with Windows curl.exe (split-tunnel hint), IPv6 exposure, DNS routing and TLS verification.
It contacts only neutral IP-information endpoints, no job board. Exit code 0 only if every critical check
passes; a pass is a snapshot, the network-owner class is a heuristic, and neither proves anonymity.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discovery.network_guard import run_preflight  # noqa: E402


def main() -> int:
    result = run_preflight()
    print("\n".join(result.lines()))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
