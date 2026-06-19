"""Refresher — the only thing (besides a gated force-refresh) that fetches upstream.

Source etiquette (the keyless spine depends on not getting his IP banned, §9):
  - Page load + auto-poll NEVER come here; they read cache. Only the background timer
    and an explicit, gated force-refresh fetch upstream.
  - force-refresh requires: past the spec's TTL AND globally debounced AND rate-limited.
  - per-card refresh interval with jitter so cards don't burst in sync.
  - exponential backoff on consecutive failures (circuit-breaker) so a dead source isn't
    hammered.
  - a consent-disabled spec is skipped entirely -> zero egress (AC-PRIV-3).
"""
from __future__ import annotations

import datetime as _dt
import random
import threading
from typing import Dict, Optional, Tuple

from .cache import CardCache
from .clock import Clock, default_clock
from .config import Config
from .fetcher import FetchError, Fetcher
from .model import ValidationError
from .sources import get_adapter

FORCE_DEBOUNCE_SEC = 8
BACKOFF_CAP_MIN = 120


def payload_as_of(payload: dict) -> Optional[str]:
    kind = payload.get("kind")
    if kind == "markets":
        times = [r.get("as_of") for r in payload.get("rows", []) if r.get("as_of")]
        return max(times) if times else None
    if kind == "headlines":
        times = [i.get("published_at") for i in payload.get("items", []) if i.get("published_at")]
        return max(times) if times else None
    return None


class Refresher:
    def __init__(
        self,
        config: Config,
        fetcher: Fetcher,
        cache: CardCache,
        clock: Optional[Clock] = None,
    ):
        self.config = config
        self.fetcher = fetcher
        self.cache = cache
        self.clock = clock or default_clock()
        self._last_spec_fetch: Dict[Tuple[str, str], _dt.datetime] = {}
        self._next_due: Dict[str, _dt.datetime] = {}
        self._last_force: Optional[_dt.datetime] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ---- one card --------------------------------------------------------------------
    def refresh_card(self, card, *, force: bool = False) -> None:
        adapter = get_adapter(card.source)
        rt = self.cache.ensure(card.id)
        if adapter is None:
            self.cache.mark_failure(card.id, f"unknown source '{card.source}'")
            return
        if not card.enabled:
            return
        now = self.clock.now_utc()
        responses: Dict[str, object] = {}
        for spec in adapter.requests(card, self.clock):
            if not spec.enabled:
                continue  # consent gate -> no egress
            key = (card.id, spec.name)
            if force:
                last = self._last_spec_fetch.get(key)
                if last is not None and (now - last).total_seconds() < spec.ttl_minutes * 60:
                    # past-TTL gate failed; serve from existing cache for this spec
                    continue
            try:
                res = self.fetcher.get(spec.url, spec.policy, headers=spec.headers)
                responses[spec.name] = res
                self._last_spec_fetch[key] = now
            except FetchError as exc:
                responses[spec.name] = None
                if not spec.optional:
                    rt.last_error = f"{spec.name}: {exc}"
        try:
            payload = adapter.parse(card, responses, self.clock)
        except ValidationError as exc:
            self.cache.mark_failure(card.id, str(exc))
            return
        except Exception as exc:  # defensive: an adapter bug must not crash the server
            self.cache.mark_failure(card.id, f"parse error: {exc}")
            return
        pd = payload.to_dict()
        self.cache.update_good(card.id, pd, payload_as_of(pd))

    # ---- gated force-refresh (the /api/refresh entry) --------------------------------
    def force_refresh(self) -> Tuple[bool, str]:
        now = self.clock.now_utc()
        with self._lock:
            if self._last_force is not None and (now - self._last_force).total_seconds() < FORCE_DEBOUNCE_SEC:
                return False, "debounced — try again in a moment"
            self._last_force = now
        for card in self.config.cards:
            self.refresh_card(card, force=True)
        self.cache.save()
        return True, "refreshed"

    # ---- background timer ------------------------------------------------------------
    def _interval_min(self, card) -> int:
        base = card.refresh_minutes or self.config.refresh_default_minutes
        return max(5, int(base))

    def _schedule_next(self, card, now: _dt.datetime, *, failed: bool) -> None:
        base = self._interval_min(card)
        rt = self.cache.get(card.id)
        if failed and rt is not None and rt.consecutive_failures:
            base = min(base * (2 ** min(rt.consecutive_failures, 6)), BACKOFF_CAP_MIN)
        jitter = 1.0 + random.uniform(-self.config.refresh_jitter_pct, self.config.refresh_jitter_pct)
        self._next_due[card.id] = now + _dt.timedelta(minutes=base * jitter)

    def _loop(self) -> None:
        # initial staggered due times so cards don't burst together
        now = self.clock.now_utc()
        for i, card in enumerate(self.config.cards):
            self._next_due[card.id] = now + _dt.timedelta(seconds=2 + i * 3)
        while not self._stop.is_set():
            now = self.clock.now_utc()
            for card in self.config.cards:
                due = self._next_due.get(card.id)
                if due is None or now >= due:
                    self.refresh_card(card)
                    rt = self.cache.get(card.id)
                    failed = bool(rt and rt.consecutive_failures)
                    self._schedule_next(card, self.clock.now_utc(), failed=failed)
            self.cache.save()
            self._stop.wait(15)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="homebase-refresher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
