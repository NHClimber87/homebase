"""AC-CORR-1..3 — correctness/honesty: timezone/DST, offseason, status enum."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from homebase.clock import FrozenClock
from homebase.config import NewsSource, default_config
from homebase.fetcher import Cassette
from homebase.model import G_OFFSEASON, G_POSTPONED

from . import fixtures as fx

NY = ZoneInfo("America/New_York")


def _rangers_only_schedule(cfg):
    """Disable rangers news so only the schedule cassette is needed."""
    cfg.card("rangers").params["news"].enabled = False


def test_ac_corr_1_timezone_dst(make_app):
    # EDT (June): 23:00Z -> 19:00 ET ; EST (January): 23:00Z -> 18:00 ET. Fixed-offset fails one.
    cfg = default_config()
    _rangers_only_schedule(cfg)
    app = make_app(config=cfg, routes=lambda u: Cassette(200, fx.NHL_EDT))
    app.refresher.refresh_card(app.config.card("rangers"))
    g = app.cache.get("rangers").payload["next_game"]
    expect = dt.datetime(2099, 6, 15, 23, 0, tzinfo=dt.timezone.utc).astimezone(NY)
    assert expect.strftime("%I").lstrip("0") + ":00 PM ET" in g["start_local"]
    assert "7:00 PM ET" in g["start_local"]  # DST-correct EDT

    cfg2 = default_config()
    _rangers_only_schedule(cfg2)
    app2 = make_app(config=cfg2, routes=lambda u: Cassette(200, fx.NHL_EST))
    app2.refresher.refresh_card(app2.config.card("rangers"))
    g2 = app2.cache.get("rangers").payload["next_game"]
    assert "6:00 PM ET" in g2["start_local"]  # EST, one hour different from naive offset


def test_ac_corr_2_offseason_per_league(make_app):
    # NHL: all-past schedule (real offseason) -> offseason
    cfg = default_config()
    _rangers_only_schedule(cfg)
    app = make_app(config=cfg, routes=lambda u: Cassette(200, fx.NHL_OFFSEASON))
    app.refresher.refresh_card(app.config.card("rangers"))
    p = app.cache.get("rangers").payload
    assert p["status"] == G_OFFSEASON and p["note"] and p["next_game"] is None

    # MLB: frozen-January clock -> empty window -> offseason (not blank/error)
    jan = FrozenClock(dt.datetime(2099, 1, 15, 18, 0, tzinfo=dt.timezone.utc))
    cfg2 = default_config()
    cfg2.card("mets").params["news"].enabled = False
    app2 = make_app(config=cfg2, the_clock=jan, routes=lambda u: Cassette(200, fx.MLB_EMPTY))
    app2.refresher.refresh_card(app2.config.card("mets"))
    p2 = app2.cache.get("mets").payload
    assert p2["status"] == G_OFFSEASON and p2["note"]
    assert app2.cache.get("mets").state == "fresh"  # offseason is a VALID state, not a failure


def test_ac_corr_3_status_enum_postponed_and_doubleheader(make_app):
    cfg = default_config()
    cfg.card("mets").params["news"].enabled = False
    app = make_app(config=cfg, routes=lambda u: Cassette(200, fx.MLB_POSTPONED_DH))
    app.refresher.refresh_card(app.config.card("mets"))
    p = app.cache.get("mets").payload
    # a postponed game is present and labeled
    statuses = [g["status"] for g in p["schedule"]]
    assert G_POSTPONED in statuses
    # "next" skips the postponed game and picks the earliest doubleheader game (Miami, 1:10 PM ET)
    nxt = p["next_game"]
    assert "Miami" in nxt["opponent"]
    assert "1:10 PM ET" in nxt["start_local"]
