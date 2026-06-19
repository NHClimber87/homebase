"""AC-FRESH — honesty under failure: validate-before-cache, lifecycle states."""
from __future__ import annotations

import datetime as dt

from homebase.clock import FrozenClock
from homebase.config import default_config
from homebase.fetcher import Cassette

from . import fixtures as fx

OPEN_WED = FrozenClock(dt.datetime(2026, 6, 17, 18, 0, tzinfo=dt.timezone.utc))


class Route:
    """A swappable single-cassette route so a card can be made to fail mid-run."""

    def __init__(self, cas):
        self.cas = cas

    def __call__(self, _url):
        return self.cas


def _markets_app(make_app, route):
    cfg = default_config()
    cfg.card("markets").source = "stooq"  # these fixtures are Stooq CSV
    cfg.card("markets").params["symbols"] = ["DJI"]
    return make_app(config=cfg, the_clock=OPEN_WED, routes=route)


def test_ac_fresh_keeps_last_good_and_labels(make_app):
    route = Route(Cassette(200, fx.STOOQ_DJI))
    app = _markets_app(make_app, route)
    mk = app.config.card("markets")

    app.refresher.refresh_card(mk)
    rt = app.cache.get("markets")
    assert rt.state == "fresh" and rt.payload["rows"][0]["price"] == 38150.0
    good = rt.payload

    # each failure mode must flip to stale AND preserve the good cache (not overwrite)
    for bad in [Cassette(500, b"err"), Cassette(200, b""), Cassette(200, b"<html>error</html>"),
                Cassette(-1, b"")]:  # non-200 / empty / HTML-error / TLS
        route.cas = bad
        app.refresher.refresh_card(mk)
        rt = app.cache.get("markets")
        assert rt.state in ("stale", "dead"), bad
        assert rt.payload == good, f"good cache overwritten by {bad}"
        assert rt.payload["rows"][0]["price"] == 38150.0


def test_ac_fresh_long_dead(make_app):
    route = Route(Cassette(200, fx.STOOQ_DJI))
    app = _markets_app(make_app, route)
    mk = app.config.card("markets")
    app.refresher.refresh_card(mk)
    assert app.cache.get("markets").state == "fresh"
    route.cas = Cassette(500, b"err")
    for _ in range(app.cache.dead_after_fails + 1):
        app.refresher.refresh_card(mk)
    assert app.cache.get("markets").state == "dead"


def test_ac_fresh_first_boot_no_data(make_app):
    route = Route(Cassette(500, b"err"))
    app = _markets_app(make_app, route)
    mk = app.config.card("markets")
    app.refresher.refresh_card(mk)  # first-ever fetch fails, never had good data
    rt = app.cache.get("markets")
    assert rt.state == "no-data-yet" and rt.payload is None
