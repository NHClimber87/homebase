"""AC-MKT-1 (residential live = deferred) + AC-MKT-2 (closed/delayed honesty, [clock])."""
from __future__ import annotations

import datetime as dt

import pytest

from homebase.clock import FrozenClock
from homebase.config import default_config
from homebase.fetcher import Cassette
from homebase.markets.symbols import resolve, display_name

from . import fixtures as fx

# explicit clocks against the bundled 2026 NYSE calendar
OPEN_WED = FrozenClock(dt.datetime(2026, 6, 17, 18, 0, tzinfo=dt.timezone.utc))   # Wed 2pm ET
SATURDAY = FrozenClock(dt.datetime(2026, 6, 20, 18, 0, tzinfo=dt.timezone.utc))   # weekend
JUNETEENTH = FrozenClock(dt.datetime(2026, 6, 19, 18, 0, tzinfo=dt.timezone.utc))  # NYSE holiday


def test_ac_mkt_1_symbol_map_and_parse_deterministic(make_app):
    # the canonical -> native mapping (the §6 symbol map, "Nasdaq" = Composite not NDX)
    assert resolve("DJI", "stooq") == "^dji" and resolve("DJI", "yahoo") == "^DJI"
    assert resolve("SPX", "stooq") == "^spx" and resolve("SPX", "yahoo") == "^GSPC"
    assert resolve("NDQ", "stooq") == "^ndq" and resolve("NDQ", "yahoo") == "^IXIC"
    assert resolve("AAPL", "stooq") == "aapl.us" and resolve("AAPL", "yahoo") == "AAPL"
    assert display_name("NDQ") == "Nasdaq"
    # parse a Stooq CSV fixture into the right price (logic correctness, no live IP needed)
    cfg = default_config()
    mk = cfg.card("markets")
    mk.source = "stooq"
    mk.params["symbols"] = ["DJI"]
    app = make_app(config=cfg, the_clock=OPEN_WED, routes=lambda u: Cassette(200, fx.STOOQ_DJI))
    app.refresher.refresh_card(mk)
    row = app.cache.get("markets").payload["rows"][0]
    assert row["price"] == 38150.0 and row["display"] == "Dow"


@pytest.mark.skip(reason="AC-MKT-1 live: Stooq/Yahoo block datacenter IPs — must be verified on "
                         "the friend's RESIDENTIAL Windows machine (gated in §12).")
def test_ac_mkt_1_live_residential():  # pragma: no cover
    raise AssertionError("run manually on the friend's residential network")


def _stooq_markets(cfg):
    mk = cfg.card("markets")
    mk.source = "stooq"
    mk.params["symbols"] = ["DJI"]
    return mk


def test_ac_mkt_2_closed_and_delayed_honest(make_app):
    cfg = default_config()
    mk = _stooq_markets(cfg)

    # Saturday -> closed_weekend, prior-close label
    app = make_app(config=cfg, the_clock=SATURDAY, routes=lambda u: Cassette(200, fx.STOOQ_DJI))
    app.refresher.refresh_card(mk)
    row = app.cache.get("markets").payload["rows"][0]
    assert row["market_state"] == "closed_weekend" and row["quote_type"] == "prior-close"

    # NYSE holiday -> closed_holiday, prior-close
    cfg2 = default_config(); _stooq_markets(cfg2)
    app2 = make_app(config=cfg2, the_clock=JUNETEENTH, routes=lambda u: Cassette(200, fx.STOOQ_DJI))
    app2.refresher.refresh_card(app2.config.card("markets"))
    row2 = app2.cache.get("markets").payload["rows"][0]
    assert row2["market_state"] == "closed_holiday" and row2["quote_type"] == "prior-close"

    # Open weekday on Stooq -> delayed badge (never shown as live)
    cfg3 = default_config(); _stooq_markets(cfg3)
    app3 = make_app(config=cfg3, the_clock=OPEN_WED, routes=lambda u: Cassette(200, fx.STOOQ_DJI))
    app3.refresher.refresh_card(app3.config.card("markets"))
    row3 = app3.cache.get("markets").payload["rows"][0]
    assert row3["market_state"] == "open" and row3["quote_type"] == "delayed"

    # forbidden outcome: a closed/delayed price is never typed as "live"
    for r in (row, row2, row3):
        assert r["quote_type"] != "live"


def test_ac_mkt_3_yahoo_default_live_and_prior_close(make_app):
    """The shipped DEFAULT markets source is Yahoo (Stooq blocks residential IPs).
    Open weekday -> live quote w/ change vs previousClose; closed -> prior-close, never 'live'."""
    cfg = default_config()
    assert cfg.card("markets").source == "yahoo"  # the shipped default
    cfg.card("markets").params["symbols"] = ["AAPL"]
    app = make_app(config=cfg, the_clock=OPEN_WED, routes=lambda u: Cassette(200, fx.YAHOO_AAPL))
    app.refresher.refresh_card(app.config.card("markets"))
    row = app.cache.get("markets").payload["rows"][0]
    assert row["price"] == 201.5 and row["quote_type"] == "live"
    assert row["change"] == 1.5  # 201.5 - previousClose 200.0

    cfg2 = default_config(); cfg2.card("markets").params["symbols"] = ["AAPL"]
    app2 = make_app(config=cfg2, the_clock=SATURDAY, routes=lambda u: Cassette(200, fx.YAHOO_AAPL))
    app2.refresher.refresh_card(app2.config.card("markets"))
    row2 = app2.cache.get("markets").payload["rows"][0]
    assert row2["market_state"] == "closed_weekend" and row2["quote_type"] == "prior-close"
