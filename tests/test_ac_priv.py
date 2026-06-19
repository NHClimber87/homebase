"""AC-PRIV-1..13 — the privacy/security forbidden-outcome suite.

Egress ACs use the in-process recorder under block_sockets (the pcap substitute proven
sound by the socket guard). Browser-side ACs (XSS, media) are graded by a combination of
payload-is-inert-data + static guarantees about app.js + the CSP that makes injection
non-executable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from homebase import config as cfgmod
from homebase.config import NewsSource, default_config
from homebase.fetcher import Cassette, FetchError, ReplayFetcher, UrllibFetcher
from homebase.paths import perms_are_owner_only
from homebase.server import CSP, serve
from homebase.ssrf import SsrfPolicy

from .conftest import block_sockets, public_resolver
from . import fixtures as fx

STATIC = Path(__file__).resolve().parent.parent / "homebase" / "static"
APP_JS = (STATIC / "app.js").read_text()
INDEX = (STATIC / "index.html").read_text()
STYLE = (STATIC / "style.css").read_text()

CONFIGURED_HOSTS = {
    "api-web.nhle.com", "statsapi.mlb.com", "www.mlb.com", "news.google.com",
    "query1.finance.yahoo.com",  # default markets source (Stooq blocks residential IPs)
}
TRACKER_HOSTS = {
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "facebook.com", "scorecardresearch.com", "amazon-adsystem.com",
}


def test_ac_priv_1_no_undisclosed_egress(make_app, recorder):
    """Only the explicitly-configured source hosts are ever contacted; zero trackers."""
    app = make_app()
    with block_sockets():  # proves the fetcher is the SOLE egress path
        for card in app.config.cards:
            app.refresher.refresh_card(card)
    hosts = set(recorder.hosts())
    assert hosts, "expected some egress"
    assert hosts <= CONFIGURED_HOSTS, f"undisclosed egress to {hosts - CONFIGURED_HOSTS}"
    assert not (hosts & TRACKER_HOSTS)
    # Under posture B the default config DOES (disclosed) route news via Google:
    assert "news.google.com" in hosts


def test_ac_priv_2_per_source_egress_scoping(make_app, recorder):
    """A news card's interest token leaves only to that card's configured source host."""
    app = make_app()
    headlines = app.config.card("headlines")
    recorder.reset()
    with block_sockets():
        app.refresher.refresh_card(headlines)
    assert set(recorder.hosts()) == {"news.google.com"}
    # and the query (the interest) is in the path to google, nowhere else
    assert all(r.host == "news.google.com" for r in recorder.records)


def test_ac_priv_3_off_switch_wired(make_app, recorder):
    """A news source set enabled=False produces zero egress to it; on=True it returns."""
    cfg = default_config()
    for card in cfg.cards:
        news = card.params.get("news")
        if isinstance(news, NewsSource) and news.mode == "aggregator":
            news.enabled = False
        for f in (card.params.get("feeds") or []):
            if f.mode == "aggregator":
                f.enabled = False
    app = make_app(config=cfg)
    with block_sockets():
        for card in app.config.cards:
            app.refresher.refresh_card(card)
    assert "news.google.com" not in set(recorder.hosts()), "off-switch leaked to Google"

    # flip back on -> egress returns (proves the gate, not a broken integration)
    recorder.reset()
    app2 = make_app(config=default_config())
    with block_sockets():
        for card in app2.config.cards:
            app2.refresher.refresh_card(card)
    assert "news.google.com" in set(recorder.hosts())


def test_ac_priv_4_loopback_only(make_app):
    # config clamp
    c = cfgmod.parse_config({"bind": "0.0.0.0", "port": 8777, "cards": []})
    assert c.bind == "loopback" and c.warnings
    c2 = cfgmod.parse_config({"bind": "203.0.113.5", "port": 8777, "cards": []})
    assert c2.bind == "loopback"
    # actual bind is a loopback address
    app = make_app()
    srv = serve(app)
    try:
        bound = srv.socket.getsockname()[0]
        assert bound == "127.0.0.1", f"server bound non-loopback {bound}"
    finally:
        srv.server_close()


def test_ac_priv_5_anti_rebind_and_csrf(make_app, serve_app):
    app = make_app()
    port, http = serve_app(app)
    # anti-DNS-rebinding: foreign Host rejected
    status, _, _ = http("GET", "/api/state", host="evil.example.com")
    assert status == 403
    # anti-CSRF: cross-origin POST rejected
    status, _, _ = http("POST", "/api/refresh", origin="http://evil.example.com")
    assert status == 403
    # no permissive CORS anywhere
    status, headers, _ = http("GET", "/api/state")
    assert "Access-Control-Allow-Origin" not in headers
    assert status == 200


def test_ac_priv_6_csp_directive_complete(make_app, serve_app):
    app = make_app()
    port, http = serve_app(app)
    _, headers, _ = http("GET", "/")
    csp = headers.get("Content-Security-Policy", "")
    directives = {d.strip().split(" ", 1)[0]: d.strip() for d in csp.split(";") if d.strip()}
    required = {
        "default-src": "default-src 'self'",
        "connect-src": "connect-src 'self'",
        "img-src": "img-src 'self'",
        "script-src": "script-src 'self'",
        "style-src": "style-src 'self'",
        "font-src": "font-src 'self'",
        "base-uri": "base-uri 'none'",
        "form-action": "form-action 'self'",
        "frame-ancestors": "frame-ancestors 'none'",
    }
    for name, exact in required.items():
        assert directives.get(name) == exact, f"CSP {name} wrong/missing: {directives.get(name)}"
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "*" not in csp


def test_ac_priv_7_feed_xss_inert(make_app):
    """A malicious feed item is stored as DATA; it cannot execute (no innerHTML + CSP)."""
    cfg = default_config()
    hl = cfg.card("headlines")
    hl.params["feeds"] = [NewsSource(name="X", mode="direct", url="https://example.com/x.xml")]
    app = make_app(config=cfg, routes=lambda u: Cassette(200, fx.XSS_RSS))
    app.refresher.refresh_card(hl)
    payload = app.cache.get("headlines").payload
    title = payload["items"][0]["title"]
    assert "<script>" in title  # stored verbatim as data, not executed
    assert payload["items"][0]["url"].startswith("javascript:")  # kept as data; client drops it
    # structural inertness guarantees:
    assert ".innerHTML" not in APP_JS, "app.js must never use innerHTML with feed data"
    assert "textContent" in APP_JS and "createElement" in APP_JS
    assert "https?:" in APP_JS, "safeHref must scheme-check links (http/https only)"
    assert "script-src 'self'" in CSP and "unsafe-inline" not in CSP


def test_ac_priv_8_no_third_party_browser_fetch(make_app):
    """Feed media is stripped: no payload media field, no <img> built from feed, no off-host asset."""
    app = make_app()
    for card in app.config.cards:
        app.refresher.refresh_card(card)
    # no payload carries an image/media URL field
    for rt in app.cache.snapshot().values():
        blob = str(rt.get("payload"))
        assert "image" not in blob.lower() and "og:image" not in blob.lower()
    # app.js never constructs an image element from feed data
    assert "createElement(\"img\"" not in APP_JS and "createElement('img'" not in APP_JS
    assert "new Image" not in APP_JS
    # the page references only same-origin assets
    assert "http://" not in INDEX and "https://" not in INDEX
    assert "url(http" not in STYLE.replace(" ", "")


def test_ac_priv_9_ssrf_guard():
    f = ReplayFetcher(routes=lambda u: Cassette(200, b"ok"), resolver=public_resolver)
    # team/markets (allowlist): off-allowlist host refused
    with pytest.raises(FetchError) as e:
        f.get("https://evil.com/x", SsrfPolicy.allowlist(["api-web.nhle.com"]))
    assert e.value.kind == "ssrf"
    # public mode: private/link-local IPs + dangerous schemes refused
    for bad in [
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1:9001/admin",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/x",
    ]:
        with pytest.raises(FetchError) as e:
            f.get(bad, SsrfPolicy.public())
        assert e.value.kind == "ssrf", bad
    # public user-feed host (public IP) is allowed
    res = f.get("https://example.com/feed.xml", SsrfPolicy.public())
    assert res.ok
    # markets pinned host allowed, sibling host refused
    assert f.get("https://stooq.com/q/l/?s=^dji", SsrfPolicy.allowlist(["stooq.com"])).ok
    with pytest.raises(FetchError):
        f.get("https://stooq.evil.com/x", SsrfPolicy.allowlist(["stooq.com"]))


@pytest.mark.skipif(os.name == "nt", reason="POSIX perms; Windows ACL checked out-of-band")
def test_ac_priv_10_file_perms(make_app, isolated_data_dir):
    app = make_app()
    cfgmod.save_config(app.config)
    app.cache.update_good("rangers", {"kind": "team"}, None)
    app.cache.save()
    cpath = cfgmod.config_path()
    cache_path = app.cache.path
    assert cpath.exists() and perms_are_owner_only(cpath)
    assert cache_path.exists() and perms_are_owner_only(cache_path)
    assert perms_are_owner_only(cpath.parent)  # dir 0700


def test_ac_priv_11_tls_fail_closed(make_app):
    # the fetcher fails closed on a TLS error (sentinel cassette), card never goes fresh
    cfg = default_config()
    mk = cfg.card("markets")
    app = make_app(config=cfg, routes=lambda u: Cassette(-1, b""))  # -1 == TLS failure sentinel
    app.refresher.refresh_card(mk)
    rt = app.cache.get("markets")
    assert rt.payload is None or rt.state in ("no-data-yet", "stale", "dead")
    # the shipped fetcher verifies TLS (never verify=False)
    uf = UrllibFetcher()
    assert uf._ssl.verify_mode.name == "CERT_REQUIRED"
    assert uf._ssl.check_hostname is True


def test_ac_priv_12_cookie_referer_suppression(make_app, recorder):
    app = make_app()
    for _cycle in range(2):
        for card in app.config.cards:
            app.refresher.refresh_card(card)
    for r in recorder.records:
        assert "cookie" not in r.header_names
        assert "referer" not in r.header_names
        assert "authorization" not in r.header_names
    # even if an adapter slips a forbidden header in, the fetcher drops it
    f = ReplayFetcher(routes=lambda u: Cassette(200, b"ok"), recorder=recorder, resolver=public_resolver)
    recorder.reset()
    f.get("https://example.com/x", SsrfPolicy.public(),
          headers={"Cookie": "a=b", "Referer": "http://x", "User-Agent": "ua"})
    assert "cookie" not in recorder.records[0].header_names
    assert "referer" not in recorder.records[0].header_names
    # the shipped fetcher installs no cookie jar -> Set-Cookie is never persisted/echoed
    import urllib.request as _ur
    assert not any(isinstance(h, _ur.HTTPCookieProcessor) for h in UrllibFetcher()._opener.handlers)


def test_ac_priv_13_no_interest_logs(refreshed_app, isolated_data_dir):
    refreshed_app.cache.save()
    # scan for log files; none may carry query/URL/interest content
    interest_markers = ["apnews.com", "New York Rangers", "rss/search", "statsapi"]
    log_files = list(Path(isolated_data_dir).rglob("*.log")) + list(Path(isolated_data_dir).rglob("*/logs/*"))
    for lf in log_files:
        if lf.is_file():
            text = lf.read_text(errors="ignore")
            for m in interest_markers:
                assert m not in text, f"interest leaked into log {lf}: {m}"
    # and we simply don't create request logs at all
    assert not any(p.name.endswith(".log") for p in Path(isolated_data_dir).rglob("*"))
