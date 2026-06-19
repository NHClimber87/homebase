"""Compute NYSE market state locally from the injectable clock + the bundled calendar.

No network call — the holiday list ships with the app. marketState drives whether a
price is labeled open / pre / post / closed_weekend / closed_holiday, and (with the
active source) the quoteType (live / delayed / prior-close). A closed or delayed price
is NEVER rendered without its label (AC-MKT-2).
"""
from __future__ import annotations

import datetime as _dt
import functools
import json
from pathlib import Path
from typing import Set

from ..clock import Clock
from ..model import (
    M_CLOSED_HOLIDAY,
    M_CLOSED_WEEKEND,
    M_OPEN,
    M_POST,
    M_PRE,
    NY,
)

_OPEN = _dt.time(9, 30)
_CLOSE = _dt.time(16, 0)


@functools.lru_cache(maxsize=1)
def _holidays() -> Set[str]:
    path = Path(__file__).with_name("nyse_holidays.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("holidays", []))


def market_state(clock: Clock) -> str:
    now_ny = clock.now_utc().astimezone(NY)
    day = now_ny.date()
    if day.weekday() >= 5:  # Sat/Sun
        return M_CLOSED_WEEKEND
    if day.isoformat() in _holidays():
        return M_CLOSED_HOLIDAY
    t = now_ny.time()
    if t < _OPEN:
        return M_PRE
    if t >= _CLOSE:
        return M_POST
    return M_OPEN


def is_closed(state: str) -> bool:
    return state in (M_CLOSED_WEEKEND, M_CLOSED_HOLIDAY)


def state_label(state: str) -> str:
    return {
        M_OPEN: "Open",
        M_PRE: "Pre-market",
        M_POST: "After hours",
        M_CLOSED_WEEKEND: "Closed (weekend)",
        M_CLOSED_HOLIDAY: "Closed (holiday)",
    }.get(state, state)
