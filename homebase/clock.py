"""Injectable clock.

Every time-dependent decision (market open/closed, offseason, staleness, TTL,
relative timestamps) goes through a Clock so tests can freeze time deterministically
([clock] ACs). The real runtime uses RealClock; tests use FrozenClock.

The shipped runtime NEVER constructs a FrozenClock — it is test-only, the same way
the replay fetcher is test-only.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

UTC = _dt.timezone.utc


class Clock:
    """Abstract clock."""

    def now(self) -> _dt.datetime:
        raise NotImplementedError

    def now_utc(self) -> _dt.datetime:
        n = self.now()
        if n.tzinfo is None:
            return n.replace(tzinfo=UTC)
        return n.astimezone(UTC)


class RealClock(Clock):
    def now(self) -> _dt.datetime:
        return _dt.datetime.now(tz=UTC)


class FrozenClock(Clock):
    """Test-only clock frozen to a fixed instant."""

    def __init__(self, when: _dt.datetime):
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        self._when = when

    def set(self, when: _dt.datetime) -> None:
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        self._when = when

    def now(self) -> _dt.datetime:
        return self._when


_DEFAULT = RealClock()


def default_clock() -> Clock:
    return _DEFAULT
