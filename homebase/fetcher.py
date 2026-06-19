"""The portable fetcher abstraction — the single, instrumented egress chokepoint.

Everything outbound goes through a Fetcher. There are two implementations:

  - UrllibFetcher: the shipped runtime. stdlib urllib + ssl. TLS verification ON
    (fail closed, never verify=False). Minimal, static, non-identifying headers.
    NEVER sends Cookie/Referer; NEVER persists Set-Cookie (no cookie jar exists).
    Runs the SSRF guard on the initial URL and re-checks every redirect hop.

  - ReplayFetcher: test-only. Serves canned cassettes; makes NO real socket call.
    It STILL runs the SSRF guard (so AC-PRIV-9 is exercised), then returns fixtures.
    This is the sole sanctioned SSRF "exception" — gated behind tests, never shipped.

Both record every outbound request into an EgressRecorder — the in-process substitute
for a pcap used by the egress ACs (AC-PRIV-1/2/3/12). For the egress ACs to be sound,
the fetcher must be the ONLY thing that touches the network; the test harness enforces
that by blocking socket.socket so any rogue egress raises.

`Target E` (Cloudflare Worker) would supply a third Fetcher with the same interface —
the abstraction is why §2d is design-compatible without touching the rest of the app.
"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

from .clock import Clock, default_clock
from .ssrf import SsrfBlocked, SsrfPolicy, check_redirect, check_url

# A fixed, non-identifying UA. Same for every HomeBase install -> not a fingerprint.
DEFAULT_UA = "Mozilla/5.0 (compatible; HomeBase/1.0; personal start page; +http://localhost)"
DEFAULT_TIMEOUT = 12.0
MAX_REDIRECTS = 5

# Header names we must NEVER emit (the privacy guarantee, enforced structurally).
_FORBIDDEN_REQUEST_HEADERS = {"cookie", "referer", "authorization"}


class FetchError(Exception):
    """Any failure to obtain a good response (network, TLS, non-2xx, SSRF)."""

    def __init__(self, message: str, *, kind: str = "error"):
        super().__init__(message)
        self.kind = kind  # 'ssrf' | 'tls' | 'http' | 'network' | 'error'


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    headers: Dict[str, str]
    body: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")


@dataclass
class EgressRecord:
    method: str
    scheme: str
    host: str
    path: str
    header_names: List[str]
    at: float


class EgressRecorder:
    """In-process pcap substitute. Records every outbound request the fetcher makes."""

    def __init__(self) -> None:
        self.records: List[EgressRecord] = []

    def record(self, method: str, url: str, headers: Dict[str, str], at: float) -> None:
        parts = urlsplit(url)
        self.records.append(
            EgressRecord(
                method=method,
                scheme=(parts.scheme or "").lower(),
                host=(parts.hostname or "").lower(),
                path=parts.path or "/",
                header_names=[h.lower() for h in headers],
                at=at,
            )
        )

    def hosts(self) -> List[str]:
        return [r.host for r in self.records]

    def reset(self) -> None:
        self.records.clear()


def _clean_headers(extra: Optional[Dict[str, str]]) -> Dict[str, str]:
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "close",
    }
    if extra:
        for k, v in extra.items():
            if k.lower() in _FORBIDDEN_REQUEST_HEADERS:
                # Refuse to ever emit an identifying header, even if an adapter slips one in.
                continue
            headers[k] = v
    return headers


class Fetcher:
    """Abstract fetcher."""

    def __init__(self, recorder: Optional[EgressRecorder] = None, clock: Optional[Clock] = None):
        self.recorder = recorder
        self.clock = clock or default_clock()

    def _now(self) -> float:
        return self.clock.now_utc().timestamp()

    def get(
        self,
        url: str,
        policy: SsrfPolicy,
        *,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> FetchResult:
        raise NotImplementedError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Disable urllib's automatic redirect so we can SSRF-check every hop ourselves."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


class UrllibFetcher(Fetcher):
    """Shipped runtime fetcher. TLS-verify ON, no cookies, manual SSRF-checked redirects."""

    def __init__(self, recorder=None, clock=None, ssl_context: Optional[ssl.SSLContext] = None):
        super().__init__(recorder, clock)
        # create_default_context() verifies the cert chain AND the hostname. Fail closed.
        self._ssl = ssl_context or ssl.create_default_context()
        # No HTTPCookieProcessor is installed -> Set-Cookie is never stored or echoed back.
        self._opener = urllib.request.build_opener(
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=self._ssl),
        )

    def get(self, url, policy, *, headers=None, timeout=DEFAULT_TIMEOUT) -> FetchResult:
        # SSRF on the initial URL: full policy (scheme + IP + allowlist-host if applicable).
        try:
            check_url(url, policy)
        except SsrfBlocked as exc:
            raise FetchError(f"SSRF refused: {exc}", kind="ssrf") from exc

        req_headers = _clean_headers(headers)
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            req = urllib.request.Request(current, headers=req_headers, method="GET")
            if self.recorder is not None:
                self.recorder.record("GET", current, req_headers, self._now())
            try:
                resp = self._opener.open(req, timeout=timeout)
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308):
                    loc = exc.headers.get("Location")
                    if not loc:
                        raise FetchError(f"redirect without Location ({exc.code})", kind="http")
                    current = urllib.request.urljoin(current, loc)
                    # Every redirect hop is re-checked, carrying the ORIGINAL policy: scheme +
                    # public-IP always, and for a pinned (allowlist) source the hop must stay
                    # within the pinned parent domain — so the interest token can't be
                    # redirected off to an arbitrary public host.
                    try:
                        check_redirect(current, policy)
                    except SsrfBlocked as sexc:
                        raise FetchError(f"redirect SSRF refused: {sexc}", kind="ssrf") from sexc
                    continue
                # Other HTTP errors: return as a non-ok FetchResult so callers can decide.
                body = exc.read() if hasattr(exc, "read") else b""
                return FetchResult(url, current, exc.code, _lower_headers(exc.headers), body)
            except ssl.SSLError as exc:
                raise FetchError(f"TLS verification failed: {exc}", kind="tls") from exc
            except (urllib.error.URLError, OSError) as exc:
                # urllib wraps cert errors in URLError(SSLCertVerificationError) sometimes.
                if isinstance(getattr(exc, "reason", None), ssl.SSLError):
                    raise FetchError(f"TLS verification failed: {exc}", kind="tls") from exc
                raise FetchError(f"network error: {exc}", kind="network") from exc

            with resp:
                body = resp.read()
                return FetchResult(url, resp.geturl(), resp.status, _lower_headers(resp.headers), body)
        raise FetchError("too many redirects", kind="http")


def _lower_headers(headers) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        items = headers.items()
    except AttributeError:
        items = []
    for k, v in items:
        out[k.lower()] = v
    return out


# --------------------------------------------------------------------------------------
# Test-only replay fetcher.
# --------------------------------------------------------------------------------------

@dataclass
class Cassette:
    status: int
    body: bytes
    headers: Dict[str, str] = field(default_factory=dict)


class ReplayFetcher(Fetcher):
    """Test-only. Serves cassettes keyed by exact URL or by a (url -> Cassette) callable.

    Still runs the SSRF guard (with an optional injected resolver) so SSRF ACs are real,
    then returns canned bytes WITHOUT any socket call.
    """

    def __init__(self, routes=None, recorder=None, clock=None, resolver=None):
        super().__init__(recorder, clock)
        # routes: dict[str, Cassette] | callable(url) -> Cassette|None
        self.routes = routes if routes is not None else {}
        self._resolver = resolver  # inject to classify fixture hostnames without real DNS

    def _lookup(self, url: str) -> Optional[Cassette]:
        if callable(self.routes):
            return self.routes(url)
        if url in self.routes:
            return self.routes[url]
        # prefix match (Google rss/search etc. carry query strings)
        for key, cas in self.routes.items():
            if url.startswith(key):
                return cas
        return None

    def get(self, url, policy, *, headers=None, timeout=DEFAULT_TIMEOUT) -> FetchResult:
        try:
            if self._resolver is not None:
                check_url(url, policy, resolver=self._resolver)
            else:
                check_url(url, policy)
        except SsrfBlocked as exc:
            raise FetchError(f"SSRF refused: {exc}", kind="ssrf") from exc

        req_headers = _clean_headers(headers)
        if self.recorder is not None:
            self.recorder.record("GET", url, req_headers, self._now())

        cas = self._lookup(url)
        if cas is None:
            raise FetchError(f"no cassette for {url}", kind="network")
        if cas.status == -1:
            # sentinel: simulate a TLS failure (AC-PRIV-11)
            raise FetchError("TLS verification failed (fixture)", kind="tls")
        if cas.status == -2:
            # sentinel: simulate a network error / unreachable (AC-FRESH)
            raise FetchError("network error (fixture)", kind="network")
        return FetchResult(url, url, cas.status, dict(cas.headers), cas.body)
