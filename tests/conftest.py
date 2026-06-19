"""Shared AC-suite machinery.

Key soundness tool: the in-process egress recorder is a faithful pcap substitute ONLY if
the fetcher is the sole thing touching the network. `block_sockets` enforces that — it
makes socket.socket raise, so any egress path OTHER than the (socket-free) ReplayFetcher
would blow up. A refresh that completes under block_sockets proves no hidden egress exists.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from homebase.app import App
from homebase.clock import FrozenClock
from homebase.config import default_config
from homebase.fetcher import Cassette, EgressRecorder, ReplayFetcher
from homebase.server import serve

from . import fixtures as fx

# A Wednesday, 2:00pm ET (market open) — the default "now".
DEFAULT_NOW = dt.datetime(2099, 6, 17, 18, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Every test gets a private app dir — no real config/cache is ever touched."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture
def clock():
    return FrozenClock(DEFAULT_NOW)


def public_resolver(_host):
    """Classify every fixture hostname as public (no real DNS in tests)."""
    return ["93.184.216.34"]


def default_routes(url):
    if url.startswith("https://api-web.nhle.com"):
        return Cassette(200, fx.NHL_OFFSEASON)
    if url.startswith("https://statsapi.mlb.com"):
        return Cassette(200, fx.MLB_UPCOMING)
    if url.startswith("https://www.mlb.com/mets"):
        return Cassette(200, fx.METS_RSS)
    if url.startswith("https://news.google.com"):
        return Cassette(200, fx.GOOGLE_RSS)
    if url.startswith("https://stooq.com"):
        return Cassette(200, fx.STOOQ_DJI)
    if url.startswith("https://query1.finance.yahoo.com"):
        return Cassette(200, fx.YAHOO_AAPL)
    return None


@pytest.fixture
def recorder():
    return EgressRecorder()


@pytest.fixture
def make_app(clock, recorder):
    """Factory: App wired to a ReplayFetcher (no sockets), a frozen clock, a recorder."""
    created = []

    def _make(config=None, routes=default_routes, the_clock=None, the_recorder=None):
        c = the_clock or clock
        rec = the_recorder if the_recorder is not None else recorder
        fetcher = ReplayFetcher(routes=routes, recorder=rec, clock=c, resolver=public_resolver)
        app = App(config=config or default_config(), fetcher=fetcher, clock=c, recorder=rec)
        created.append(app)
        return app

    yield _make
    for app in created:
        with contextlib.suppress(Exception):
            app.stop()


@pytest.fixture
def refreshed_app(make_app):
    """An app whose cards have all been refreshed once from default cassettes."""
    app = make_app()
    for card in app.config.cards:
        app.refresher.refresh_card(card)
    return app


@contextlib.contextmanager
def block_sockets():
    real = socket.socket

    def _boom(*a, **k):
        raise AssertionError("rogue egress: a socket was opened outside the fetcher")

    socket.socket = _boom
    try:
        yield
    finally:
        socket.socket = real


@pytest.fixture
def serve_app():
    """Start a real loopback server in a thread; yield (app, port, http helper)."""
    servers = []

    def _start(app):
        srv = serve(app)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        servers.append(srv)
        time.sleep(0.2)
        port = app.config.port

        def http(method, path, host=None, origin=None, body=None, raw_body=None):
            host = host or f"127.0.0.1:{port}"
            url = f"http://127.0.0.1:{port}{path}"
            headers = {"Host": host}
            data = None
            if body is not None:
                import json as _json
                data = _json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            elif raw_body is not None:
                data = raw_body
                headers["Content-Type"] = "application/json"
            if origin is not None:
                headers["Origin"] = origin
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                resp = urllib.request.urlopen(req, timeout=5)
                return resp.status, dict(resp.headers), resp.read()
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers), e.read()

        return port, http

    yield _start
    for srv in servers:
        with contextlib.suppress(Exception):
            srv.shutdown()
            srv.server_close()
