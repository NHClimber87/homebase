"""AC-NEWS — dedup / reverse-chron order / sourcing honesty / consent labels."""
from __future__ import annotations

from homebase.config import NewsSource, default_config
from homebase.fetcher import Cassette

from . import fixtures as fx


def _news_route(url):
    # route the two google feeds by their site: query so they return different content
    if "apnews" in url:
        return Cassette(200, fx.GOOGLE_RSS)       # "Big Story Happens - The Associated Press" @12:00
    if "reuters" in url:
        return Cassette(200, fx.GOOGLE_RSS_DUP)   # dup story @11:00 + an untimed item
    return None


def test_ac_news_dedup_order_and_badges(make_app):
    cfg = default_config()
    app = make_app(config=cfg, routes=_news_route)
    app.refresher.refresh_card(cfg.card("headlines"))
    items = app.cache.get("headlines").payload["items"]

    # exactly ONE "Big Story Happens" (the AP + Reuters duplicate collapsed)
    big = [i for i in items if i["title"] == "Big Story Happens"]
    assert len(big) == 1, [i["title"] for i in items]
    # suffix stripped; source + via-aggregator badge present
    assert big[0]["source"] == "The Associated Press"
    assert big[0]["via_aggregator"] is True and big[0]["badge"] == "via Google"
    # reverse-chron: timed item first, untimed last + badged "time unknown" (None publishedAt)
    assert items[0]["title"] == "Big Story Happens"
    assert items[-1]["published_at"] is None and items[-1]["title"] == "Later Untimed Item"


def test_ac_news_dead_feed_is_labeled(make_app):
    cfg = default_config()

    def route(url):
        if "apnews" in url:
            return Cassette(200, fx.GOOGLE_RSS)
        if "reuters" in url:
            return Cassette(500, b"err")  # dead feed
        return None

    app = make_app(config=cfg, routes=route)
    app.refresher.refresh_card(cfg.card("headlines"))
    payload = app.cache.get("headlines").payload
    assert any("unavailable" in n.lower() for n in payload["notes"]), payload["notes"]
    # the live feed still rendered (partial failure doesn't blank the card)
    assert payload["items"]


def test_ac_news_team_news_off_pending_consent(make_app):
    cfg = default_config()
    cfg.card("rangers").params["news"].enabled = False
    app = make_app(config=cfg)
    app.refresher.refresh_card(cfg.card("rangers"))
    p = app.cache.get("rangers").payload
    assert p["news_state"] == "off-pending-consent"
    assert p["news"] == []  # nothing fetched, nothing shown — but a labeled state, not a gap
