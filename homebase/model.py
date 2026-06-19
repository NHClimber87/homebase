"""Normalized card payloads + lifecycle states (the JSON contract the front-end renders).

Card lifecycle state (§6): loading, no-data-yet, fresh, stale, dead.
Times: every payload carries `as_of` (source/exchange time) AND `fetched_at` (local).
The front-end renders the DATA time, never the fetch time, and re-derives relatives on a ticker.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from typing import List, Optional
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# lifecycle states
LOADING = "loading"
NO_DATA = "no-data-yet"
FRESH = "fresh"
STALE = "stale"
DEAD = "dead"

# team game statuses
G_SCHEDULED = "scheduled"
G_PRE = "pre"
G_LIVE = "live"
G_FINAL = "final"
G_POSTPONED = "postponed"
G_OFFSEASON = "offseason"
G_PRESEASON = "preseason"

# market states
M_OPEN = "open"
M_PRE = "pre"
M_POST = "post"
M_CLOSED_WEEKEND = "closed_weekend"
M_CLOSED_HOLIDAY = "closed_holiday"

# quote types
Q_LIVE = "live"
Q_DELAYED = "delayed"
Q_PRIOR_CLOSE = "prior-close"


def to_ny(utc: _dt.datetime) -> _dt.datetime:
    if utc.tzinfo is None:
        utc = utc.replace(tzinfo=_dt.timezone.utc)
    return utc.astimezone(NY)


def fmt_ny(utc: _dt.datetime) -> str:
    """Portable 'Tue Jun 17, 7:00 PM ET' (no glibc-only %-d/%-I, so it works on Windows)."""
    ny = to_ny(utc)
    hour12 = ny.hour % 12 or 12
    ampm = "AM" if ny.hour < 12 else "PM"
    return f"{ny:%a %b} {ny.day}, {hour12}:{ny.minute:02d} {ampm} ET"


def parse_iso_utc(s: str) -> Optional[_dt.datetime]:
    """Parse an ISO-8601 string (incl. trailing Z) to an aware UTC datetime."""
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(s2)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.astimezone(_dt.timezone.utc)
    except ValueError:
        return None


@dataclass
class NewsItem:
    title: str
    source: str
    url: str
    published_at: Optional[str] = None  # ISO UTC; None -> "time unknown"
    via_aggregator: bool = False
    badge: str = ""


@dataclass
class Game:
    status: str
    opponent: str = ""
    home: Optional[bool] = None
    start_utc: Optional[str] = None
    start_local: Optional[str] = None  # human "Tue Jun 17, 7:00 PM ET"
    score: Optional[str] = None
    detail: str = ""


@dataclass
class TeamPayload:
    league: str
    team: str
    status: str = G_SCHEDULED
    next_game: Optional[Game] = None
    last_game: Optional[Game] = None
    schedule: List[Game] = field(default_factory=list)
    news: List[NewsItem] = field(default_factory=list)
    news_state: str = "ok"   # ok | unavailable | off-pending-consent | absent
    note: str = ""           # e.g. offseason note

    kind: str = "team"

    def to_dict(self):
        return asdict(self)


@dataclass
class MarketRow:
    symbol: str            # canonical id (DJI, SPX, NDQ, AAPL)
    display: str           # human label
    price: Optional[float] = None
    change: Optional[float] = None
    pct: Optional[float] = None
    as_of: Optional[str] = None
    market_state: str = M_OPEN
    quote_type: str = Q_DELAYED
    error: str = ""


@dataclass
class MarketsPayload:
    rows: List[MarketRow] = field(default_factory=list)
    kind: str = "markets"

    def to_dict(self):
        return asdict(self)


@dataclass
class HeadlinesPayload:
    items: List[NewsItem] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)  # e.g. "Reuters: source unavailable"
    kind: str = "headlines"

    def to_dict(self):
        return asdict(self)


class ValidationError(Exception):
    """Raised by an adapter when a fetched payload fails post-normalize validation.

    A ValidationError means: DO NOT overwrite the good cache (AC-FRESH). The card
    keeps its last-good payload and flips to a labeled stale state.
    """
