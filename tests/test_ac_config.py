"""AC-CONFIG — configurable (not coded) + fault-tolerant config handling."""
from __future__ import annotations

from homebase import config as cfgmod
from homebase.config import Card, NewsSource, default_config, load_config
from homebase.fetcher import Cassette

from . import fixtures as fx


def _route(url):
    if "zzzz" in url.lower():
        return Cassette(200, fx.STOOQ_ND)        # unknown symbol -> N/D
    if url.startswith("https://stooq.com"):
        return Cassette(200, fx.STOOQ_DJI)
    if url.startswith("https://api-web.nhle.com"):
        return Cassette(200, fx.NHL_OFFSEASON)
    if url.startswith("https://news.google.com"):
        return Cassette(200, fx.GOOGLE_RSS)
    return None


def test_ac_config_add_symbol_and_team_persist(make_app, isolated_data_dir):
    cfg = default_config()
    app = make_app(config=cfg, routes=_route)

    # add a symbol and a brand-new team card never referenced in source
    new = default_config()
    new.card("markets").source = "stooq"  # these routes serve Stooq CSV
    new.card("markets").params["symbols"] = ["DJI", "AAPL"]
    new.cards.append(Card(id="islanders", type="team", title="NY Islanders", source="nhl",
                          params={"league": "NHL", "team": "NYI",
                                  "news": NewsSource(name="Isles news", mode="aggregator",
                                                     query="New York Islanders", enabled=False)}))
    app.update_config(new.to_dict())

    # persisted to config.json on disk (byte-present) — configurable, not coded
    disk = cfgmod.config_path().read_text()
    assert "AAPL" in disk and "islanders" in disk

    # survives a "restart": a fresh load sees both, and both fetch + render
    reloaded = load_config()
    assert reloaded.card("islanders") is not None
    app2 = make_app(config=reloaded, routes=_route)
    for card in app2.config.cards:
        app2.refresher.refresh_card(card)
    assert app2.cache.get("islanders").payload is not None
    syms = [r["symbol"] for r in app2.cache.get("markets").payload["rows"]]
    assert "AAPL" in syms


def test_ac_config_malformed_symbol_is_error_row(make_app):
    cfg = default_config()
    cfg.card("markets").source = "stooq"
    cfg.card("markets").params["symbols"] = ["DJI", "ZZZZ"]
    app = make_app(config=cfg, routes=_route)
    app.refresher.refresh_card(cfg.card("markets"))
    rows = {r["symbol"]: r for r in app.cache.get("markets").payload["rows"]}
    assert rows["DJI"]["price"] == 38150.0          # good row survives
    assert rows["ZZZZ"]["error"]                      # bad symbol -> labeled error row
    assert app.cache.get("markets").state == "fresh"  # page not broken by one bad symbol


def test_ac_config_malformed_file_falls_back_to_defaults(isolated_data_dir):
    path = cfgmod.config_path()
    from homebase import paths
    paths.atomic_write_owner_only(path, "{ this is not valid json ]")
    cfg = load_config()
    assert cfg.cards, "must still serve cards on a broken config"
    assert cfg.warnings, "must surface a banner about the config error"
    # safe fallback: aggregator news defaults OFF on a defaults-fallback
    rangers_news = cfg.card("rangers").params.get("news") if cfg.card("rangers") else None
    if rangers_news is not None and rangers_news.mode == "aggregator":
        assert rangers_news.enabled is False


def test_ac_config_roundtrip(isolated_data_dir):
    cfg = default_config()
    cfg.refresh_default_minutes = 25
    cfgmod.save_config(cfg)
    reloaded = load_config()
    assert reloaded.refresh_default_minutes == 25
