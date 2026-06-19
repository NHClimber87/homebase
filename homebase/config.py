"""Config model: the only place the friend's interests live (owner-only on disk).

Hardening obligations encoded here:
  - `bind` is an ENUM {127.0.0.1, ::1, loopback}; anything else is CLAMPED to loopback
    + a banner warning (AC-PRIV-4). Binding a routable interface is impossible by config.
  - refresh floor >= 5 min (Yahoo budget); default 15.
  - atomic, validated, owner-only saves with a prior-snapshot backup (AC-CONFIG).
  - a parse/validate failure falls back to last-good, else shipped defaults with
    aggregator news OFF, and keeps serving with a banner (never crash).
  - aggregator (Google-routed) sources carry an `enabled` flag (default True under
    posture B) + a "via Google" badge — the §2c consent machine. Set enabled False
    per source for Option-A behavior; an off source produces zero egress (AC-PRIV-3).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import paths

BIND_ENUM = ("127.0.0.1", "::1", "loopback")
DEFAULT_BIND = "loopback"
REFRESH_FLOOR_MIN = 5
DEFAULT_REFRESH_MIN = 15
DEFAULT_PORT = 8777
DEFAULT_JITTER_PCT = 0.2
VALID_TYPES = ("team", "markets", "headlines")


@dataclass
class NewsSource:
    """A news/headlines sub-source. `mode` direct|aggregator; aggregator carries consent."""

    name: str
    mode: str  # "direct" | "aggregator"
    query: str = ""          # for google-news search feeds
    url: str = ""            # for direct RSS feeds
    enabled: bool = True     # default True under posture B; False == opted-out (no egress)
    badge: str = ""          # e.g. "via Google" for aggregator feeds
    consented_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "mode": self.mode,
            "query": self.query,
            "url": self.url,
            "enabled": self.enabled,
            "badge": self.badge,
            "consented_at": self.consented_at,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "NewsSource":
        mode = d.get("mode", "direct")
        return NewsSource(
            name=str(d.get("name", "")),
            mode="aggregator" if mode == "aggregator" else "direct",
            query=str(d.get("query", "")),
            url=str(d.get("url", "")),
            enabled=bool(d.get("enabled", True)),
            badge=str(d.get("badge", "via Google" if mode == "aggregator" else "")),
            consented_at=d.get("consented_at"),
        )


@dataclass
class Card:
    id: str
    type: str
    title: str
    source: str                       # primary data-source adapter id
    params: Dict[str, Any] = field(default_factory=dict)
    refresh_minutes: Optional[int] = None
    enabled: bool = True              # card-level on/off

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "source": self.source,
            "params": _params_to_dict(self.params),
            "enabled": self.enabled,
        }
        if self.refresh_minutes is not None:
            d["refresh_minutes"] = self.refresh_minutes
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Card":
        params = dict(d.get("params", {}))
        # rehydrate NewsSource objects in known params
        if "news" in params and isinstance(params["news"], dict):
            params["news"] = NewsSource.from_dict(params["news"])
        if "feeds" in params and isinstance(params["feeds"], list):
            params["feeds"] = [NewsSource.from_dict(f) for f in params["feeds"]]
        rm = d.get("refresh_minutes")
        return Card(
            id=str(d["id"]),
            type=str(d["type"]),
            title=str(d.get("title", d["id"])),
            source=str(d.get("source", "")),
            params=params,
            refresh_minutes=int(rm) if rm is not None else None,
            enabled=bool(d.get("enabled", True)),
        )


def _params_to_dict(params: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in params.items():
        if isinstance(v, NewsSource):
            out[k] = v.to_dict()
        elif isinstance(v, list) and v and isinstance(v[0], NewsSource):
            out[k] = [x.to_dict() for x in v]
        else:
            out[k] = v
    return out


@dataclass
class Config:
    bind: str = DEFAULT_BIND
    port: int = DEFAULT_PORT
    refresh_default_minutes: int = DEFAULT_REFRESH_MIN
    refresh_jitter_pct: float = DEFAULT_JITTER_PCT
    cards: List[Card] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)  # banners surfaced to the UI

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bind": self.bind,
            "port": self.port,
            "refresh_default_minutes": self.refresh_default_minutes,
            "refresh_jitter_pct": self.refresh_jitter_pct,
            "cards": [c.to_dict() for c in self.cards],
        }

    def card(self, card_id: str) -> Optional[Card]:
        for c in self.cards:
            if c.id == card_id:
                return c
        return None


class ConfigError(ValueError):
    pass


def _clamp_bind(raw: Any, warnings: List[str]) -> str:
    if raw in BIND_ENUM:
        return raw
    warnings.append(
        f"bind '{raw}' is not an allowed loopback value — clamped to '{DEFAULT_BIND}' "
        f"(HomeBase only ever binds the loopback interface)."
    )
    return DEFAULT_BIND


def _validate_card(d: Any, warnings: List[str]) -> Optional[Card]:
    if not isinstance(d, dict) or "id" not in d or "type" not in d:
        warnings.append("dropped a card with no id/type")
        return None
    if d["type"] not in VALID_TYPES:
        warnings.append(f"dropped card '{d.get('id')}' — unknown type '{d.get('type')}'")
        return None
    try:
        return Card.from_dict(d)
    except (KeyError, ValueError, TypeError) as exc:
        warnings.append(f"dropped malformed card '{d.get('id')}': {exc}")
        return None


def parse_config(data: Dict[str, Any]) -> Config:
    """Validate a raw dict into a Config. Raises ConfigError only on irrecoverable shape."""
    if not isinstance(data, dict):
        raise ConfigError("config root is not an object")
    warnings: List[str] = []
    bind = _clamp_bind(data.get("bind", DEFAULT_BIND), warnings)
    try:
        port = int(data.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_PORT
        warnings.append("invalid port — using default")
    if not (1 <= port <= 65535):
        warnings.append(f"port {port} out of range — using {DEFAULT_PORT}")
        port = DEFAULT_PORT

    try:
        refresh = int(data.get("refresh_default_minutes", DEFAULT_REFRESH_MIN))
    except (TypeError, ValueError):
        refresh = DEFAULT_REFRESH_MIN
    if refresh < REFRESH_FLOOR_MIN:
        warnings.append(f"refresh_default_minutes raised to floor {REFRESH_FLOOR_MIN}")
        refresh = REFRESH_FLOOR_MIN

    try:
        jitter = float(data.get("refresh_jitter_pct", DEFAULT_JITTER_PCT))
    except (TypeError, ValueError):
        jitter = DEFAULT_JITTER_PCT
    jitter = min(max(jitter, 0.0), 0.9)

    cards: List[Card] = []
    seen_ids = set()
    raw_cards = data.get("cards", [])
    if not isinstance(raw_cards, list):
        warnings.append("cards is not a list — ignoring")
        raw_cards = []
    for rc in raw_cards:
        card = _validate_card(rc, warnings)
        if card is None:
            continue
        if card.id in seen_ids:
            warnings.append(f"dropped duplicate card id '{card.id}'")
            continue
        seen_ids.add(card.id)
        cards.append(card)

    return Config(
        bind=bind,
        port=port,
        refresh_default_minutes=refresh,
        refresh_jitter_pct=jitter,
        cards=cards,
        warnings=warnings,
    )


def config_path() -> Path:
    return paths.app_dir() / "config.json"


def load_config(path: Optional[Path] = None) -> Config:
    """Load config with full fault tolerance: bad file -> last-good backup -> defaults+banner."""
    path = path or config_path()
    if not path.exists():
        cfg = default_config()
        save_config(cfg, path)
        return cfg
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return parse_config(raw)
    except (json.JSONDecodeError, OSError, ConfigError) as exc:
        backup = path.with_suffix(".json.bak")
        if backup.exists():
            try:
                cfg = parse_config(json.loads(backup.read_text(encoding="utf-8")))
                cfg.warnings.insert(0, f"config.json unreadable ({exc}) — restored last-good backup")
                return cfg
            except (json.JSONDecodeError, OSError, ConfigError):
                pass
        cfg = default_config()
        # Posture-A-safe fallback: disable all aggregator sources on a defaults-fallback.
        _disable_all_aggregators(cfg)
        cfg.warnings.insert(0, f"config error — running on safe defaults (aggregator news off): {exc}")
        return cfg


def save_config(cfg: Config, path: Optional[Path] = None) -> None:
    """Atomic, validated, owner-only save with a prior-snapshot backup."""
    path = path or config_path()
    # validate by round-tripping through parse_config (raises on irrecoverable shape)
    parse_config(cfg.to_dict())
    if path.exists():
        try:
            backup = path.with_suffix(".json.bak")
            paths.atomic_write_owner_only(backup, path.read_text(encoding="utf-8"))
        except OSError:
            pass
    paths.atomic_write_owner_only(path, json.dumps(cfg.to_dict(), indent=2))


def _disable_all_aggregators(cfg: Config) -> None:
    for card in cfg.cards:
        news = card.params.get("news")
        if isinstance(news, NewsSource) and news.mode == "aggregator":
            news.enabled = False
        feeds = card.params.get("feeds")
        if isinstance(feeds, list):
            for f in feeds:
                if isinstance(f, NewsSource) and f.mode == "aggregator":
                    f.enabled = False


# --------------------------------------------------------------------------------------
# The shipped defaults (the friend's set). Posture B: aggregator news ON + badged.
# --------------------------------------------------------------------------------------

def default_config() -> Config:
    rangers_news = NewsSource(
        name="Rangers news",
        mode="aggregator",
        query="New York Rangers NHL",
        enabled=True,
        badge="via Google",
    )
    mets_news = NewsSource(
        name="Mets news",
        mode="direct",
        url="https://www.mlb.com/mets/feeds/news/rss.xml",
        enabled=True,
    )
    ap = NewsSource(name="AP", mode="aggregator", query="site:apnews.com", enabled=True, badge="via Google")
    reuters = NewsSource(
        name="Reuters", mode="aggregator", query="site:reuters.com", enabled=True, badge="via Google"
    )
    cards = [
        Card(
            id="rangers",
            type="team",
            title="NY Rangers",
            source="nhl",
            params={"league": "NHL", "team": "NYR", "news": rangers_news},
        ),
        Card(
            id="mets",
            type="team",
            title="NY Mets",
            source="mlb",
            params={"league": "MLB", "team_id": 121, "news": mets_news},
        ),
        Card(
            id="markets",
            type="markets",
            title="Markets",
            # Yahoo is the default: verified working from a residential connection on
            # 2026-06-18, whereas Stooq anti-bot-blocks home/datacenter IPs (serves a
            # noindex stub + 404s the quote endpoint). Stooq stays a one-click source-swap
            # in Settings for networks where it works.
            source="yahoo",
            params={"symbols": ["DJI", "SPX", "NDQ"]},
        ),
        Card(
            id="headlines",
            type="headlines",
            title="Latest Headlines",
            source="google-news",
            params={"feeds": [ap, reuters]},
        ),
    ]
    return Config(cards=cards)
