"""AC-CACHE — page load + auto-poll serve cache ONLY; only timer/gated-force hit upstream."""
from __future__ import annotations

import json


def test_ac_cache_load_is_cache_only(make_app, serve_app, recorder):
    app = make_app()
    for card in app.config.cards:
        app.refresher.refresh_card(card)
    recorder.reset()  # forget the priming fetches

    port, http = serve_app(app)
    for _ in range(5):  # rapid page/auto-poll reads
        status, _, body = http("GET", "/api/state")
        assert status == 200
        assert json.loads(body)["cards"]
    # the forbidden outcome: a page load triggered an upstream fetch
    assert recorder.records == [], f"page load hit upstream: {recorder.hosts()}"


def test_ac_cache_force_refresh_is_debounced(make_app, serve_app):
    app = make_app()
    port, http = serve_app(app)
    s1, _, _ = http("POST", "/api/refresh", origin=f"http://127.0.0.1:{port}")
    s2, _, b2 = http("POST", "/api/refresh", origin=f"http://127.0.0.1:{port}")
    assert s1 == 200
    assert s2 == 429, "a second rapid force-refresh must be debounced (ban-avoidance)"


def test_ac_cache_only_refresh_fetches(make_app, recorder):
    """Reading state never fetches; calling the refresher does."""
    app = make_app()
    app.state_dict()
    app.state_dict()
    assert recorder.records == []
    app.refresher.refresh_card(app.config.cards[0])
    assert recorder.records, "the refresher is the thing that fetches upstream"
