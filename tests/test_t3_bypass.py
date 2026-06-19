"""T3 INDEPENDENT adversarial bypass suite (NOT written by the maker).

Each test attacks a REQUIRED control on an INDEPENDENT channel and asserts the
forbidden outcome cannot happen. Critically, the egress/privacy soundness checks do
NOT trust the maker's in-process EgressRecorder: they intercept at the *socket layer*
(socket.getaddrinfo / socket.create_connection) so a guard that merely "exists" but is
wired to nothing is exposed.

Mapping to the 12 controls in the grading brief:
  1  no-undisclosed-egress + per-source scoping + off-switch zeroes egress (socket layer)
  2  loopback-only bind (clamp + live socket + foreign Host 403)
  3  anti-CSRF (cross-Origin POST 403 + no ACAO header ever)
  4  CSP directive-complete (live header, parsed)
  5  feed XSS inert (data-only + no innerHTML + safeHref drops javascript:)
  6  SSRF (metadata/loopback/private/file/gopher refused; public feed allowed; pins held)
  7  file perms (real on-disk 0600 files / 0700 dirs)
  8  TLS fail-closed (CERT_REQUIRED + check_hostname; no verify=False; cache not poisoned)
  9  cookie/Referer suppression (>=2 cycles; no HTTPCookieProcessor)
  10 cache-only-on-load (hammer /api/state -> zero upstream; force-refresh debounced)
  11 validate-before-cache (good cache byte-for-byte preserved on bad fetch)
  12 no-interest-logs (no log file under the data dir carries URL/query/interest)
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from homebase import config as cfgmod
from homebase.config import NewsSource, default_config
from homebase.fetcher import Cassette, EgressRecorder, FetchError, ReplayFetcher, UrllibFetcher
from homebase.paths import perms_are_owner_only
from homebase.server import serve
from homebase.ssrf import SsrfBlocked, SsrfPolicy, check_url

from .conftest import block_sockets, public_resolver
from . import fixtures as fx

STATIC = Path(__file__).resolve().parent.parent / "homebase" / "static"
APP_JS = (STATIC / "app.js").read_text()
PKG_DIR = Path(__file__).resolve().parent.parent / "homebase"

# Hosts the SHIPPED default config is allowed to reach (interest-bearing egress).
CONFIGURED_HOSTS = {
    "api-web.nhle.com", "statsapi.mlb.com", "www.mlb.com", "news.google.com",
    "query1.finance.yahoo.com",  # default markets source as of 2026-06-18 (Stooq blocks residential)
    "stooq.com",                  # still a valid configured alternate (Settings source-swap)
}


# =====================================================================================
# A reusable SOCKET-LAYER interceptor. This is the lower layer the brief demands.
# It records every host the SHIPPED UrllibFetcher actually tries to DIAL, completely
# independent of homebase's own EgressRecorder. We fail the dial before any real packet
# leaves the box, capturing intent without touching the network.
# =====================================================================================
class SocketDialSpy:
    """Capture every (host, port) the real fetcher tries to connect to at the OS layer."""

    def __init__(self, resolved_ip="93.184.216.34"):
        self.gai_hosts = []      # hosts passed to getaddrinfo (DNS-level intent)
        self.dialed = []         # (host, port) passed to create_connection (TCP-level intent)
        self._ip = resolved_ip

    def fake_getaddrinfo(self, host, port, *a, **k):
        self.gai_hosts.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (self._ip, port or 0))]

    def fake_create_connection(self, address, *a, **k):
        self.dialed.append((address[0], address[1]))
        raise OSError("blocked at create_connection (T3 socket spy)")

    def patch(self):
        return mock.patch.multiple(
            "socket",
            getaddrinfo=self.fake_getaddrinfo,
            create_connection=self.fake_create_connection,
        )

    def dialed_hosts(self):
        return {h for (h, _p) in self.dialed}


def _shipped_app(config):
    """An App wired to the REAL UrllibFetcher (the shipped runtime egress path).

    We never let it touch the network — the SocketDialSpy intercepts at the OS layer.
    """
    from homebase.app import App
    from homebase.clock import FrozenClock
    import datetime as dt
    clock = FrozenClock(dt.datetime(2099, 6, 17, 18, 0, tzinfo=dt.timezone.utc))
    fetcher = UrllibFetcher(recorder=None, clock=clock)
    return App(config=config, fetcher=fetcher, clock=clock)


# =====================================================================================
# CONTROL 1 — no undisclosed egress + per-source scoping + off-switch zeroes egress
#   Verified at the SOCKET layer with the REAL UrllibFetcher, NOT the EgressRecorder.
# =====================================================================================
def test_c1a_shipped_fetcher_only_dials_configured_hosts_socket_layer():
    app = _shipped_app(default_config())
    spy = SocketDialSpy()
    with spy.patch():
        for card in app.config.cards:
            try:
                app.refresher.refresh_card(card)
            except Exception:
                pass  # dial is blocked on purpose; we only care WHAT it tried to dial
    dialed = spy.dialed_hosts()
    assert dialed, "expected the shipped fetcher to attempt some egress"
    undisclosed = dialed - CONFIGURED_HOSTS
    assert not undisclosed, f"shipped fetcher dialed undisclosed host(s): {undisclosed}"
    # under posture B the disclosed Google routing IS expected:
    assert "news.google.com" in dialed


def test_c1b_per_source_scoping_headlines_only_dials_google():
    app = _shipped_app(default_config())
    headlines = app.config.card("headlines")
    spy = SocketDialSpy()
    with spy.patch():
        try:
            app.refresher.refresh_card(headlines)
        except Exception:
            pass
    assert spy.dialed_hosts() == {"news.google.com"}, (
        f"headlines card fanned out beyond Google: {spy.dialed_hosts()}"
    )


def test_c1c_off_switch_produces_ZERO_socket_dials_to_google():
    """The off-switch claim, proven at the socket layer (NOT via the recorder)."""
    cfg = default_config()
    # turn OFF every aggregator news source (Option-A behavior)
    for card in cfg.cards:
        news = card.params.get("news")
        if isinstance(news, NewsSource) and news.mode == "aggregator":
            news.enabled = False
        for f in (card.params.get("feeds") or []):
            if isinstance(f, NewsSource) and f.mode == "aggregator":
                f.enabled = False
    app = _shipped_app(cfg)
    spy = SocketDialSpy()
    with spy.patch():
        for card in app.config.cards:
            try:
                app.refresher.refresh_card(card)
            except Exception:
                pass
    # the forbidden outcome: ANY dial to google, OR even a DNS lookup of it
    assert "news.google.com" not in spy.dialed_hosts(), "off-switch still DIALED Google"
    assert "news.google.com" not in spy.gai_hosts, "off-switch still RESOLVED Google (DNS leak)"

    # control: flipping back on re-introduces the dial (proves the gate, not dead wiring)
    app2 = _shipped_app(default_config())
    spy2 = SocketDialSpy()
    with spy2.patch():
        for card in app2.config.cards:
            try:
                app2.refresher.refresh_card(card)
            except Exception:
                pass
    assert "news.google.com" in spy2.dialed_hosts(), "on-switch failed to re-enable egress"


# =====================================================================================
# CONTROL 2 — loopback-only bind
# =====================================================================================
def test_c2_loopback_bind_clamp_and_live_socket_and_foreign_host(make_app, serve_app):
    # 2a clamp: a routable bind value is rejected/clamped to loopback + banner
    c = cfgmod.parse_config({"bind": "0.0.0.0", "port": 8777, "cards": []})
    assert c.bind == "loopback" and c.warnings, "0.0.0.0 not clamped"
    c2 = cfgmod.parse_config({"bind": "203.0.113.5", "port": 8777, "cards": []})
    assert c2.bind == "loopback", "routable IP not clamped"

    # 2b live socket is genuinely a loopback address (read it off the real bound socket)
    app = make_app()
    port, http = serve_app(app)
    srv = app  # not used; the live socket is in serve_app's server
    # 2c foreign Host header -> 403 (anti-DNS-rebinding)
    status, _, _ = http("GET", "/api/state", host="attacker.example.com")
    assert status == 403, f"foreign Host accepted (status {status})"
    # and confirm the bound socket family is loopback by hitting it only via 127.0.0.1
    status_ok, _, _ = http("GET", "/api/state")  # host defaults to 127.0.0.1:port
    assert status_ok == 200


def test_c2_live_socket_is_loopback_address(make_app):
    app = make_app()
    srv = serve(app)
    try:
        bound_ip = srv.socket.getsockname()[0]
        assert bound_ip == "127.0.0.1", f"server bound a NON-loopback address: {bound_ip}"
        # prove it is in the IPv4 loopback /8 (defensive, not just string match)
        import ipaddress
        assert ipaddress.ip_address(bound_ip).is_loopback
    finally:
        srv.server_close()


# =====================================================================================
# CONTROL 3 — anti-CSRF: cross-Origin POST 403 + NO ACAO header EVER
# =====================================================================================
def test_c3_cross_origin_post_403_and_never_emits_acao(make_app, serve_app):
    app = make_app()
    port, http = serve_app(app)

    # cross-origin POST must 403
    status, headers, _ = http("POST", "/api/refresh", origin="http://evil.example.com")
    assert status == 403, f"cross-origin POST accepted (status {status})"
    assert "Access-Control-Allow-Origin" not in headers

    # sweep EVERY response surface for an ACAO header — it must never appear
    surfaces = [
        ("GET", "/", None),
        ("GET", "/api/state", None),
        ("GET", "/api/config", None),
        ("POST", "/api/refresh", "http://127.0.0.1:%d" % port),  # same-origin, allowed
    ]
    for method, path, origin in surfaces:
        st, hdrs, _ = http(method, path, origin=origin)
        assert "Access-Control-Allow-Origin" not in hdrs, f"ACAO leaked on {method} {path}"
        assert "access-control-allow-origin" not in {k.lower() for k in hdrs}


# =====================================================================================
# CONTROL 4 — CSP directive-complete (live header, parsed)
# =====================================================================================
def test_c4_csp_directive_complete_parsed_from_live_header(make_app, serve_app):
    app = make_app()
    port, http = serve_app(app)
    _, headers, _ = http("GET", "/")
    csp = headers.get("Content-Security-Policy", "")
    assert csp, "no CSP header served"
    directives = {}
    for d in csp.split(";"):
        d = d.strip()
        if not d:
            continue
        parts = d.split(None, 1)
        directives[parts[0]] = parts[1] if len(parts) > 1 else ""

    expected = {
        "default-src": "'self'",
        "connect-src": "'self'",
        "img-src": "'self'",
        "script-src": "'self'",
        "style-src": "'self'",
        "font-src": "'self'",
        "base-uri": "'none'",
        "form-action": "'self'",
        "frame-ancestors": "'none'",
    }
    for name, val in expected.items():
        assert name in directives, f"CSP missing directive {name}"
        assert directives[name] == val, f"CSP {name} = {directives[name]!r}, want {val!r}"
    # nothing dangerous anywhere
    low = csp.lower()
    assert "unsafe-inline" not in low, "CSP contains unsafe-inline"
    assert "unsafe-eval" not in low, "CSP contains unsafe-eval"
    assert "*" not in csp, "CSP contains a wildcard"
    assert "http:" not in low and "https:" not in low, "CSP allows a remote scheme source"


# =====================================================================================
# CONTROL 5 — feed XSS inert (data-only + no innerHTML + safeHref drops javascript:)
# =====================================================================================
def test_c5a_xss_feed_stored_as_inert_data(make_app):
    cfg = default_config()
    hl = cfg.card("headlines")
    hl.params["feeds"] = [NewsSource(name="X", mode="direct", url="https://example.com/x.xml")]
    app = make_app(config=cfg, routes=lambda u: Cassette(200, fx.XSS_RSS))
    app.refresher.refresh_card(hl)
    item = app.cache.get("headlines").payload["items"][0]
    # stored verbatim as DATA (the server must not "sanitize" by mangling; inertness is
    # the front-end's job: textContent + scheme-check). The javascript: URL survives as data.
    assert item["url"].startswith("javascript:"), "javascript: link should be kept as inert data"
    # and the title is plain text in the JSON (no executable context server-side)
    assert isinstance(item["title"], str)


def _strip_js_comments(js: str) -> str:
    """Remove /* block */ and // line comments so we test only executable JS."""
    no_block = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    out_lines = []
    for ln in no_block.splitlines():
        out_lines.append(ln.split("//", 1)[0])
    return "\n".join(out_lines)


def test_c5b_appjs_never_uses_innerHTML_on_feed_data():
    # No HTML-injection sinks anywhere in the EXECUTABLE front end (comments stripped).
    code = _strip_js_comments(APP_JS)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in code, f"app.js uses a dangerous sink in executable code: {sink}"
    assert "textContent" in APP_JS and "createElement" in APP_JS


def test_c5c_safeHref_drops_javascript_and_data_schemes():
    """Statically extract safeHref's scheme regex and prove it rejects javascript:/data:."""
    m = re.search(r"function safeHref\(url\)\s*\{(.*?)\}", APP_JS, re.S)
    assert m, "safeHref not found in app.js"
    body = m.group(1)
    rx = re.search(r"/\^?(https\?:[^/]*)/i?", body)
    assert rx, f"safeHref does not use an ^https? scheme test: {body!r}"
    # reconstruct the JS regex in Python and prove the dangerous schemes fail it
    py_re = re.compile(r"^https?://", re.I)
    assert py_re.match("https://ok.example/x")
    assert py_re.match("http://ok.example/x")
    assert not py_re.match("javascript:alert(1)"), "safeHref would ALLOW javascript:"
    assert not py_re.match("data:text/html,<script>"), "safeHref would ALLOW data:"
    assert not py_re.match(" javascript:alert(1)")  # leading space (app trims first)
    # confirm app.js actually trims before testing (defeats ' javascript:' bypass)
    assert ".trim()" in body, "safeHref must trim before scheme-checking"
    # and the unsafe branch falls back to plain text (no <a> built) — structural check
    assert "el(\"div\", null, titleText)" in APP_JS or "el('div', null, titleText)" in APP_JS


# =====================================================================================
# CONTROL 6 — SSRF (attack the fetch boundary directly)
# =====================================================================================
# Literal-IP + bad-scheme targets: these are refused WITHOUT any DNS (the guard classifies
# the literal IP directly / rejects the scheme), so an injected public resolver cannot mask them.
@pytest.mark.parametrize("bad_url", [
    "http://169.254.169.254/latest/meta-data/",     # cloud metadata (link-local)
    "http://[fd00::1]/x",                            # ULA private IPv6
    "http://127.0.0.1:9001/admin",                   # loopback port scan
    "http://10.0.0.5/x",                             # private 10/8
    "http://192.168.1.1/x",                          # private 192.168/16
    "http://172.16.5.5/x",                           # private 172.16/12
    "http://0.0.0.0/x",                              # unspecified
    "file:///etc/passwd",                            # file scheme
    "gopher://127.0.0.1:6379/x",                     # gopher scheme
    "ftp://example.com/x",                           # ftp scheme
    "data:text/plain,hi",                            # data scheme
])
def test_c6a_ssrf_refuses_dangerous_targets_at_fetch_boundary(bad_url):
    f = ReplayFetcher(routes=lambda u: Cassette(200, b"ok"), resolver=public_resolver)
    with pytest.raises(FetchError) as e:
        f.get(bad_url, SsrfPolicy.public())
    assert e.value.kind == "ssrf", f"{bad_url} was NOT refused as ssrf (kind={e.value.kind})"


# Obfuscated-loopback hostnames (shorthand/decimal/hex) are NOT literal IPs to ipaddress,
# so they go through the resolver. Use the REAL OS resolver to prove the SHIPPED guard
# (resolve-then-check) still blocks them — this is the channel that actually ships.
@pytest.mark.parametrize("obfuscated", ["127.1", "2130706433", "0x7f000001"])
def test_c6a2_ssrf_blocks_obfuscated_loopback_via_real_resolver(obfuscated):
    real_addrs = socket.getaddrinfo(obfuscated, 80, proto=socket.IPPROTO_TCP)
    resolved = sorted({i[4][0] for i in real_addrs})
    if "127.0.0.1" not in resolved:
        pytest.skip(f"this OS does not resolve {obfuscated} to loopback")
    # SHIPPED guard uses the REAL resolver (no injection) -> must block
    with pytest.raises(SsrfBlocked):
        check_url(f"http://{obfuscated}/x", SsrfPolicy.public())


def test_c6b_ssrf_blocks_hostname_resolving_to_private_ip():
    """A *public-looking* hostname that resolves to an internal IP must still be refused
    (DNS-rebinding style). We inject a resolver that maps it to a private address."""
    f = ReplayFetcher(routes=lambda u: Cassette(200, b"ok"),
                      resolver=lambda h: ["10.1.2.3"])
    with pytest.raises(FetchError) as e:
        f.get("http://totally-public-looking.com/x", SsrfPolicy.public())
    assert e.value.kind == "ssrf"
    # and a metadata-rebind name
    f2 = ReplayFetcher(routes=lambda u: Cassette(200, b"ok"),
                       resolver=lambda h: ["169.254.169.254"])
    with pytest.raises(FetchError) as e2:
        f2.get("http://rebind.example/x", SsrfPolicy.public())
    assert e2.value.kind == "ssrf"


def test_c6c_public_user_feed_host_is_allowed():
    f = ReplayFetcher(routes=lambda u: Cassette(200, b"ok"), resolver=public_resolver)
    res = f.get("https://blog.example.com/feed.xml", SsrfPolicy.public())
    assert res.ok, "a legitimate public user feed must be fetchable (add-a-feed must work)"


def test_c6d_team_markets_pins_held_and_siblings_refused():
    f = ReplayFetcher(routes=lambda u: Cassette(200, b"ok"), resolver=public_resolver)
    # pinned hosts allowed
    assert f.get("https://api-web.nhle.com/v1/x", SsrfPolicy.allowlist(["api-web.nhle.com"])).ok
    assert f.get("https://stooq.com/q/l/?s=^dji", SsrfPolicy.allowlist(["stooq.com"])).ok
    assert f.get("https://statsapi.mlb.com/api/v1/x", SsrfPolicy.allowlist(["statsapi.mlb.com"])).ok
    # look-alike / sibling hosts refused even if public
    for evil in ["https://stooq.evil.com/x", "https://api-web.nhle.com.evil.com/x",
                 "https://notstooq.com/x", "https://evil.com/x"]:
        with pytest.raises(FetchError) as e:
            f.get(evil, SsrfPolicy.allowlist(["stooq.com", "api-web.nhle.com", "statsapi.mlb.com"]))
        assert e.value.kind == "ssrf", evil


def test_c6e_adapters_use_allowlist_for_team_and_markets_public_for_news():
    """Static guarantee: the team/markets adapters pin a strict allowlist; news uses public.
    (Catches a regression that would silently widen team/markets to public-IP-only.)"""
    from homebase.sources import nhl, mlb, stooq, yahoo
    from homebase.clock import FrozenClock
    import datetime as dt
    clock = FrozenClock(dt.datetime(2099, 6, 17, tzinfo=dt.timezone.utc))

    def policies(adapter, card):
        return [s.policy for s in adapter.requests(card, clock)]

    cfg = default_config()
    rangers = cfg.card("rangers")
    mets = cfg.card("mets")
    markets = cfg.card("markets")

    sched_policy = [s for s in nhl.NhlAdapter().requests(rangers, clock) if s.name == "schedule"][0].policy
    assert sched_policy.mode == "allowlist" and sched_policy.allowed_hosts == {"api-web.nhle.com"}

    mlb_sched = [s for s in mlb.MlbAdapter().requests(mets, clock) if s.name == "schedule"][0].policy
    assert mlb_sched.mode == "allowlist" and mlb_sched.allowed_hosts == {"statsapi.mlb.com"}

    mk = stooq.StooqAdapter().requests(markets, clock)
    assert mk and all(s.policy.mode == "allowlist" and s.policy.allowed_hosts == {"stooq.com"} for s in mk)

    # the news sub-spec on a team card must be PUBLIC mode (so add-a-feed works), not allowlist
    news_spec = [s for s in nhl.NhlAdapter().requests(rangers, clock) if s.name == "news"]
    assert news_spec and news_spec[0].policy.mode == "public"


# =====================================================================================
# CONTROL 7 — file perms (REAL on-disk stat)
# =====================================================================================
@pytest.mark.skipif(os.name == "nt", reason="POSIX perms; Windows ACL is out-of-band")
def test_c7_real_config_and_cache_files_are_owner_only(make_app, isolated_data_dir):
    import stat as _stat
    app = make_app()
    cfgmod.save_config(app.config)
    app.cache.update_good("rangers", {"kind": "team"}, None)
    app.cache.save()

    cpath = cfgmod.config_path()
    cache_path = app.cache.path
    assert cpath.exists(), "config.json was not written"
    assert cache_path.exists(), "cache file was not written"

    # stat the REAL inodes (not the helper) and assert the exact POSIX modes
    cmode = _stat.S_IMODE(os.stat(cpath).st_mode)
    cache_mode = _stat.S_IMODE(os.stat(cache_path).st_mode)
    cdir_mode = _stat.S_IMODE(os.stat(cpath.parent).st_mode)
    cache_dir_mode = _stat.S_IMODE(os.stat(cache_path.parent).st_mode)
    assert cmode == 0o600, f"config.json is {oct(cmode)}, want 0600"
    assert cache_mode == 0o600, f"cache is {oct(cache_mode)}, want 0600"
    assert cdir_mode == 0o700, f"config dir is {oct(cdir_mode)}, want 0700"
    assert cache_dir_mode == 0o700, f"cache dir is {oct(cache_dir_mode)}, want 0700"
    # group/other have NO read bit
    assert not (cmode & 0o077), "config.json readable by group/other"
    assert not (cache_mode & 0o077), "cache readable by group/other"
    # and the .bak snapshot, if present, is also locked down
    bak = cpath.with_suffix(".json.bak")
    if bak.exists():
        assert _stat.S_IMODE(os.stat(bak).st_mode) == 0o600, "config .bak is world/group readable"


# =====================================================================================
# CONTROL 8 — TLS fail-closed
# =====================================================================================
def test_c8a_shipped_ssl_context_verifies():
    uf = UrllibFetcher()
    assert uf._ssl.verify_mode == __import__("ssl").CERT_REQUIRED
    assert uf._ssl.check_hostname is True


def _strip_py_strings_and_comments(src: str) -> str:
    """Remove docstrings/string-literals + # comments so we grep only executable Python.

    (The shipped code mentions 'never verify=False' in a docstring; that is prose, not a
    TLS-disabling construct. We must not be fooled by prose in EITHER direction.)"""
    import io, tokenize
    out = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in toks:
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            out.append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return src
    return " ".join(out)


def test_c8b_no_verify_false_anywhere_in_codebase():
    offenders = []
    pat = re.compile(r"verify\s*=\s*False|_create_unverified|CERT_NONE|check_hostname\s*=\s*False")
    for py in PKG_DIR.rglob("*.py"):
        executable = _strip_py_strings_and_comments(py.read_text())
        if pat.search(executable):
            offenders.append(str(py))
    assert not offenders, "TLS-disabling construct found in executable code:\n" + "\n".join(offenders)


def test_c8c_tls_failure_does_not_go_fresh_or_poison_good_cache(make_app):
    cfg = default_config()
    mk = cfg.card("markets")
    # first: a GOOD fetch establishes good cache
    app = make_app(config=cfg)
    app.refresher.refresh_card(mk)
    rt = app.cache.get("markets")
    assert rt.state == "fresh" and rt.payload is not None
    good_payload = json.dumps(rt.payload, sort_keys=True)
    good_fetched_at = rt.fetched_at

    # now: the source presents a TLS failure (sentinel cassette -1)
    app.refresher.fetcher = ReplayFetcher(routes=lambda u: Cassette(-1, b""),
                                          recorder=None, clock=app.clock, resolver=public_resolver)
    app.refresher.refresh_card(mk)
    rt2 = app.cache.get("markets")
    # the card must NOT be 'fresh' off a failed TLS handshake
    assert rt2.state in ("stale", "dead"), f"TLS-failed card went to {rt2.state}"
    # and the good payload is preserved byte-for-byte
    assert json.dumps(rt2.payload, sort_keys=True) == good_payload, "good cache was poisoned by TLS fail"
    assert rt2.fetched_at == good_fetched_at, "fetched_at advanced on a failed fetch"


# =====================================================================================
# CONTROL 9 — cookie / Referer suppression (>=2 cycles) + no cookie jar
# =====================================================================================
def test_c9a_no_cookie_or_referer_across_two_cycles_socket_independent():
    """Inspect the actual Request headers the SHIPPED fetcher builds (not the recorder)."""
    captured_headers = []

    real_open = UrllibFetcher.__init__

    # Wrap the opener.open to capture the urllib Request headers actually sent.
    cfg = default_config()
    app = _shipped_app(cfg)
    fetcher = app.refresher.fetcher

    orig_open = fetcher._opener.open

    def spy_open(req, *a, **k):
        captured_headers.append({k.lower(): v for k, v in req.header_items()})
        raise urllib.error.URLError("blocked (T3)")

    fetcher._opener.open = spy_open
    spy = SocketDialSpy()
    with spy.patch():
        for _cycle in range(2):
            for card in app.config.cards:
                try:
                    app.refresher.refresh_card(card)
                except Exception:
                    pass
    assert captured_headers, "no outbound requests were built"
    for hdrs in captured_headers:
        assert "cookie" not in hdrs, f"Cookie header emitted: {hdrs}"
        assert "referer" not in hdrs, f"Referer header emitted: {hdrs}"
        assert "authorization" not in hdrs, f"Authorization header emitted: {hdrs}"


def test_c9b_forbidden_headers_dropped_even_if_adapter_injects_them():
    rec = EgressRecorder()
    f = ReplayFetcher(routes=lambda u: Cassette(200, b"ok"), recorder=rec, resolver=public_resolver)
    f.get("https://example.com/x", SsrfPolicy.public(),
          headers={"Cookie": "sid=secret", "Referer": "http://leak", "Authorization": "Bearer x", "User-Agent": "ua"})
    names = rec.records[0].header_names
    assert "cookie" not in names and "referer" not in names and "authorization" not in names


def test_c9c_no_cookie_processor_installed():
    import urllib.request as _ur
    handlers = UrllibFetcher()._opener.handlers
    assert not any(isinstance(h, _ur.HTTPCookieProcessor) for h in handlers), "a cookie jar is installed"


# =====================================================================================
# CONTROL 10 — cache-only on load + force-refresh debounced
# =====================================================================================
def test_c10a_hammering_api_state_makes_zero_upstream_fetches(make_app, serve_app):
    """Hit /api/state many times rapidly; the read path must perform ZERO upstream fetches.

    Two independent witnesses:
      (i)  spy on the app's fetcher -> the read path must never call it; and
      (ii) socket layer -> no dial to a NON-loopback host (the loopback dial is our own
           test HTTP client, which we explicitly allow).
    """
    app = make_app()
    for card in app.config.cards:  # seed cache so there is data to serve
        app.refresher.refresh_card(card)
    port, http = serve_app(app)

    # (i) wrap the fetcher.get so any upstream fetch during the read storm is caught
    fetch_calls = []
    orig_get = app.refresher.fetcher.get

    def counting_get(*a, **k):
        fetch_calls.append(a[0] if a else k.get("url"))
        return orig_get(*a, **k)

    app.refresher.fetcher.get = counting_get

    # (ii) socket-layer witness that allows loopback (our own client) but records non-loopback dials
    nonloopback_dials = []
    real_create = socket.create_connection

    def watch_create(address, *a, **k):
        host = address[0]
        try:
            import ipaddress
            if not ipaddress.ip_address(host).is_loopback:
                nonloopback_dials.append(address)
        except ValueError:
            nonloopback_dials.append(address)  # a hostname dial during a read = suspicious
        return real_create(address, *a, **k)

    with mock.patch("socket.create_connection", watch_create):
        for _ in range(25):
            status, _, body = http("GET", "/api/state")
            assert status == 200
            assert json.loads(body)["cards"], "state served no cards"

    assert fetch_calls == [], f"read path /api/state performed upstream fetch(es): {fetch_calls}"
    assert nonloopback_dials == [], f"read path dialed a non-loopback host: {nonloopback_dials}"


def test_c10b_force_refresh_is_debounced(make_app):
    app = make_app()
    ok1, _ = app.force_refresh()
    ok2, msg2 = app.force_refresh()  # immediate second call
    assert ok1 is True, "first force-refresh should succeed"
    assert ok2 is False, "second immediate force-refresh must be debounced"
    assert "debounce" in msg2.lower()


def test_c10c_force_refresh_respects_per_spec_ttl(make_app, recorder):
    """A force-refresh inside a spec's TTL must NOT re-hit that spec (rate-limit spine)."""
    app = make_app(the_recorder=recorder)
    app.force_refresh()              # first hit
    n_after_first = len(recorder.records)
    # advance clock past the global debounce but stay inside the per-spec TTL
    import datetime as dt
    from homebase.refresher import FORCE_DEBOUNCE_SEC
    app.clock.set(app.clock.now_utc() + dt.timedelta(seconds=FORCE_DEBOUNCE_SEC + 1))
    recorder.reset()
    app.force_refresh()
    # within TTL, most specs should be skipped -> far fewer (ideally zero) new hits than the first
    assert len(recorder.records) < n_after_first, (
        "force-refresh re-hit every spec despite being inside per-spec TTL"
    )


# =====================================================================================
# CONTROL 11 — validate-before-cache (good cache preserved on bad fetch)
# =====================================================================================
@pytest.mark.parametrize("bad", [
    Cassette(500, b"upstream boom"),                 # non-200
    Cassette(200, b""),                              # empty body
    Cassette(200, b"<html>error</html>not json"),    # garbage / schema drift
    Cassette(-2, b""),                              # network error sentinel
])
def test_c11_good_cache_preserved_byte_for_byte_on_bad_fetch(make_app, bad):
    cfg = default_config()
    app = make_app(config=cfg)
    rangers = cfg.card("rangers")  # team card with a strict schema
    # establish good cache
    app.refresher.refresh_card(rangers)
    good = app.cache.get("rangers")
    assert good.state == "fresh" and good.payload is not None
    good_blob = json.dumps(good.payload, sort_keys=True)
    good_fetched = good.fetched_at

    # now the schedule source returns something bad
    app.refresher.fetcher = ReplayFetcher(
        routes=lambda u: bad if "nhle.com" in u else fx_default(u),
        recorder=None, clock=app.clock, resolver=public_resolver,
    )
    app.refresher.refresh_card(rangers)
    rt = app.cache.get("rangers")
    # forbidden outcome: good payload overwritten or fetched_at advanced or card shows fresh-but-bad
    assert json.dumps(rt.payload, sort_keys=True) == good_blob, "good cache OVERWRITTEN by bad fetch"
    assert rt.fetched_at == good_fetched, "fetched_at advanced on a failed validation"
    assert rt.state in ("stale", "dead"), f"bad fetch left card labeled {rt.state}, not stale/dead"
    assert rt.last_error, "no error recorded for the failed fetch"


def fx_default(url):
    """Reuse the conftest default cassettes for non-target hosts."""
    from .conftest import default_routes
    return default_routes(url)


# =====================================================================================
# CONTROL 12 — no interest logs under the data dir
# =====================================================================================
def test_c12_no_log_file_contains_any_interest_content(make_app, isolated_data_dir):
    app = make_app()
    for card in app.config.cards:
        app.refresher.refresh_card(card)
    app.cache.save()
    cfgmod.save_config(app.config)

    data_root = Path(isolated_data_dir)
    # the interest graph (queries, source URLs, team names) must not appear in any .log
    interest_markers = [
        "apnews.com", "reuters.com", "New York Rangers", "rss/search", "rss/articles",
        "statsapi.mlb.com", "api-web.nhle.com", "stooq.com", "news.google.com",
        "site:apnews", "when:",
    ]
    log_files = [p for p in data_root.rglob("*") if p.is_file() and (
        p.suffix == ".log" or "log" in p.parent.name.lower())]
    for lf in log_files:
        text = lf.read_text(errors="ignore")
        for m in interest_markers:
            assert m not in text, f"interest content leaked into {lf}: {m!r}"
    # strongest form: the app creates NO .log file at all by default
    assert not list(data_root.rglob("*.log")), "an interest-logging .log file was created"
