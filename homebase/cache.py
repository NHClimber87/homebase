"""Per-card cache with validate-before-cache + lifecycle states (AC-FRESH).

The cache is the ONLY thing the page-load + auto-poll path reads (cache-only on load,
AC-CACHE). A good payload replaces cache only on a successful validated parse; any
failure keeps last-good and flips the card to a labeled state — the good data is never
overwritten with bad. Cache hydrates on boot so the page never renders cold-blank.

Lifecycle (§6): loading | no-data-yet | fresh | stale | dead.
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from . import paths
from .clock import Clock, default_clock
from .model import DEAD, FRESH, LOADING, NO_DATA, STALE

# thresholds (minutes)
DEFAULT_MAX_STALE_MIN = 60
DEFAULT_DEAD_AFTER_MIN = 360
DEFAULT_DEAD_AFTER_FAILS = 6


@dataclass
class CardRuntime:
    card_id: str
    state: str = LOADING
    payload: Optional[Dict[str, Any]] = None
    as_of: Optional[str] = None
    fetched_at: Optional[str] = None       # last SUCCESSFUL fetch (local)
    last_attempt: Optional[str] = None
    last_error: str = ""
    consecutive_failures: int = 0
    first_fail_at: Optional[str] = None

    def to_public(self) -> Dict[str, Any]:
        return asdict(self)


def _iso(d: _dt.datetime) -> str:
    return d.astimezone(_dt.timezone.utc).isoformat()


def _age_min(iso_ts: Optional[str], now: _dt.datetime) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        t = _dt.datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    return (now - t).total_seconds() / 60.0


class CardCache:
    def __init__(
        self,
        path: Optional[Path] = None,
        clock: Optional[Clock] = None,
        *,
        max_stale_min: int = DEFAULT_MAX_STALE_MIN,
        dead_after_min: int = DEFAULT_DEAD_AFTER_MIN,
        dead_after_fails: int = DEFAULT_DEAD_AFTER_FAILS,
    ):
        self.path = path or (paths.app_dir() / "cache" / "cards.json")
        self.clock = clock or default_clock()
        self.max_stale_min = max_stale_min
        self.dead_after_min = dead_after_min
        self.dead_after_fails = dead_after_fails
        self._cards: Dict[str, CardRuntime] = {}
        self._lock = threading.RLock()

    # ---- persistence -----------------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return
            for cid, d in (raw.get("cards") or {}).items():
                try:
                    rt = CardRuntime(card_id=cid, **{k: d.get(k) for k in (
                        "state", "payload", "as_of", "fetched_at", "last_attempt",
                        "last_error", "consecutive_failures", "first_fail_at") if k in d})
                except TypeError:
                    continue
                # recompute state on hydrate so a long-idle boot shows stale/dead honestly
                self._cards[cid] = rt
                self._recompute(cid)

    def save(self) -> None:
        with self._lock:
            data = {"cards": {cid: rt.to_public() for cid, rt in self._cards.items()}}
        try:
            paths.atomic_write_owner_only(self.path, json.dumps(data, indent=2))
        except OSError:
            pass

    # ---- accessors -------------------------------------------------------------------
    def ensure(self, card_id: str) -> CardRuntime:
        with self._lock:
            rt = self._cards.get(card_id)
            if rt is None:
                rt = CardRuntime(card_id=card_id, state=LOADING)
                self._cards[card_id] = rt
            return rt

    def get(self, card_id: str) -> Optional[CardRuntime]:
        with self._lock:
            return self._cards.get(card_id)

    def prune(self, valid_ids) -> None:
        with self._lock:
            for cid in list(self._cards):
                if cid not in valid_ids:
                    del self._cards[cid]

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            for cid in self._cards:
                self._recompute(cid)
            return {cid: rt.to_public() for cid, rt in self._cards.items()}

    # ---- mutation --------------------------------------------------------------------
    def update_good(self, card_id: str, payload: Dict[str, Any], as_of: Optional[str]) -> None:
        now = self.clock.now_utc()
        with self._lock:
            rt = self.ensure(card_id)
            rt.payload = payload
            rt.as_of = as_of or _iso(now)
            rt.fetched_at = _iso(now)
            rt.last_attempt = _iso(now)
            rt.last_error = ""
            rt.consecutive_failures = 0
            rt.first_fail_at = None
            rt.state = FRESH

    def mark_failure(self, card_id: str, error: str) -> None:
        now = self.clock.now_utc()
        with self._lock:
            rt = self.ensure(card_id)
            rt.last_attempt = _iso(now)
            rt.last_error = error
            rt.consecutive_failures += 1
            if rt.first_fail_at is None:
                rt.first_fail_at = _iso(now)
            self._recompute(card_id)

    def _recompute(self, card_id: str) -> None:
        now = self.clock.now_utc()
        rt = self._cards[card_id]
        has_good = rt.payload is not None and rt.fetched_at is not None
        failing = rt.consecutive_failures > 0
        if not has_good:
            rt.state = NO_DATA if failing else LOADING
            return
        age = _age_min(rt.fetched_at, now)
        too_dead = (
            rt.consecutive_failures >= self.dead_after_fails
            or (age is not None and age > self.dead_after_min)
        )
        if failing and too_dead:
            rt.state = DEAD
        elif failing or (age is not None and age > self.max_stale_min):
            rt.state = STALE
        else:
            rt.state = FRESH
