"""Per-card-type SSRF guard (AC-PRIV-9).

The fetcher refuses:
  - any non-http(s) scheme (file:, gopher:, ftp:, data:, ...)
  - any host that resolves to a loopback / private / link-local / reserved IP
    (blocks 169.254.169.254 cloud-metadata, 127.0.0.1:<other> port-scans, 10.x/192.168.x)

Two policy modes:
  - ALLOWLIST  (team / markets cards): host must be in a strict pinned set (the §5 APIs).
  - PUBLIC     (headlines / user-added feeds): any host is allowed *provided* it resolves
    to a public IP only — so "add a feed" works without opening an SSRF hole.

The ONLY sanctioned bypass is the test-replay channel (clock/fixture), which never
makes a real socket call and is gated behind an explicit test flag in the fetcher.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Iterable, List, Set
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")


class SsrfBlocked(ValueError):
    """Raised when a URL is refused by the SSRF guard. Carries a short reason."""


@dataclass(frozen=True)
class SsrfPolicy:
    mode: str  # "allowlist" | "public"
    allowed_hosts: Set[str] = field(default_factory=set)

    @staticmethod
    def allowlist(hosts: Iterable[str]) -> "SsrfPolicy":
        return SsrfPolicy(mode="allowlist", allowed_hosts={h.lower() for h in hosts})

    @staticmethod
    def public() -> "SsrfPolicy":
        return SsrfPolicy(mode="public")


def _ip_is_public(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return not (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve(host: str) -> List[str]:
    """All A/AAAA addresses for host. Empty list => unresolvable."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    out: List[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in out:
            out.append(addr)
    return out


def _parent_domain(host: str) -> str:
    # Naive last-two-labels. Sufficient because every pinned source sits on a single-label
    # TLD (nhle.com, mlb.com, stooq.com, yahoo.com). If a source on a multi-part TLD is ever
    # pinned (e.g. example.co.uk), switch to a public-suffix-list parse — otherwise this would
    # over-match sibling domains under that suffix (b.co.uk would look "within" a.co.uk).
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _host_within_allowlist(host: str, allowed: Set[str]) -> bool:
    """True if host is an allowlisted host or a subdomain of an allowlisted host's parent
    domain (so a pinned API's same-org CDN redirect — api-web.nhle.com -> *.nhle.com — is
    permitted, but a redirect to an unrelated domain is not)."""
    for a in allowed:
        parent = _parent_domain(a)
        if host == a or host == parent or host.endswith("." + parent):
            return True
    return False


def check_redirect(url: str, original_policy: SsrfPolicy, *, resolver=_resolve) -> str:
    """Validate a redirect HOP. Always enforces scheme + public-IP; for an allowlist
    (team/markets) source it additionally keeps the hop within the pinned parent domain,
    so the interest token can't be redirected to an arbitrary public host (defends a
    hijacked/compromised pinned origin)."""
    check_url(url, SsrfPolicy.public(), resolver=resolver)  # scheme + public-IP, any host
    if original_policy.mode == "allowlist":
        host = (urlsplit(url).hostname or "").lower()
        if not _host_within_allowlist(host, original_policy.allowed_hosts):
            raise SsrfBlocked(f"redirect host '{host}' is outside the pinned domain")
    return url


def check_url(url: str, policy: SsrfPolicy, *, resolver=_resolve) -> str:
    """Validate a URL against the SSRF policy. Returns the URL or raises SsrfBlocked.

    `resolver` is injectable for tests (so a fixture host can be classified without
    real DNS). The shipped runtime uses the real DNS resolver.
    """
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise SsrfBlocked(f"scheme '{scheme or '(none)'}' not allowed")
    host = (parts.hostname or "").lower()
    if not host:
        raise SsrfBlocked("missing host")

    if policy.mode == "allowlist":
        if host not in policy.allowed_hosts:
            raise SsrfBlocked(f"host '{host}' not in pinned allowlist")
        # Even allowlisted hosts are IP-checked: defends against a poisoned/edited
        # allowlist or a hijacked pinned domain resolving to an internal address.

    # Literal-IP hosts: classify directly (covers 169.254.169.254, 127.0.0.1, 10.x...).
    # NB: SsrfBlocked subclasses ValueError, so we must NOT wrap the raise in a
    # try/except ValueError — detect the literal IP first, then act outside the try.
    literal_ip = True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        literal_ip = False
    if literal_ip:
        if not _ip_is_public(host):
            raise SsrfBlocked(f"host IP '{host}' is non-public")
        return url

    addrs = resolver(host)
    if not addrs:
        raise SsrfBlocked(f"host '{host}' does not resolve")
    for a in addrs:
        if not _ip_is_public(a):
            raise SsrfBlocked(f"host '{host}' resolves to non-public IP {a}")
    return url
