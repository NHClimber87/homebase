"""App context — wires config + cache + fetcher + refresher and exposes the read/refresh
operations the HTTP layer calls. The HTTP layer never reaches upstream; it only reads the
cache and (on a gated POST) asks the refresher to fetch.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import APP_NAME, __version__
from .cache import CardCache
from .clock import Clock, default_clock
from .config import Config, load_config, parse_config, save_config
from .fetcher import EgressRecorder, Fetcher, UrllibFetcher
from .model import DEAD, FRESH, NO_DATA, STALE
from .sources import VETTED_ALTERNATES


class App:
    def __init__(
        self,
        config: Optional[Config] = None,
        *,
        fetcher: Optional[Fetcher] = None,
        cache: Optional[CardCache] = None,
        clock: Optional[Clock] = None,
        recorder: Optional[EgressRecorder] = None,
    ):
        self.clock = clock or default_clock()
        self.recorder = recorder
        self.config = config or load_config()
        self.fetcher = fetcher or UrllibFetcher(recorder=recorder, clock=self.clock)
        self.cache = cache or CardCache(clock=self.clock)
        self.cache.load()
        for card in self.config.cards:
            self.cache.ensure(card.id)
        self.cache.prune({c.id for c in self.config.cards})
        # late import to avoid a cycle (refresher imports sources which import config)
        from .refresher import Refresher

        self.refresher = Refresher(self.config, self.fetcher, self.cache, clock=self.clock)

    # ---- read paths (cache-only) -----------------------------------------------------
    def state_dict(self) -> Dict[str, Any]:
        runtime = self.cache.snapshot()
        return {
            "app": APP_NAME,
            "version": __version__,
            "config": self.config.to_dict(),
            "cards": runtime,
            "warnings": list(self.config.warnings),
            "health": self._health(runtime),
            "vetted_alternates": VETTED_ALTERNATES,
        }

    def _health(self, runtime: Dict[str, Any]) -> Dict[str, Any]:
        total = len(self.config.cards)
        healthy = sum(1 for cid in (c.id for c in self.config.cards)
                      if runtime.get(cid, {}).get("state") in (FRESH, STALE))
        last = [runtime.get(c.id, {}).get("fetched_at") for c in self.config.cards]
        last = [x for x in last if x]
        return {
            "server_ok": True,
            "last_refresh": max(last) if last else None,
            "healthy_sources": healthy,
            "total_sources": total,
        }

    # ---- gated write paths -----------------------------------------------------------
    def force_refresh(self):
        return self.refresher.force_refresh()

    def update_config(self, new_raw: Dict[str, Any]) -> Dict[str, Any]:
        """Validate + atomically persist a new config, then re-point the running app at it."""
        new_cfg = parse_config(new_raw)
        save_config(new_cfg)
        self.config = new_cfg
        self.refresher.config = new_cfg
        for card in self.config.cards:
            self.cache.ensure(card.id)
        self.cache.prune({c.id for c in self.config.cards})
        return {"ok": True, "warnings": new_cfg.warnings, "config": new_cfg.to_dict()}

    def reset_config(self) -> Dict[str, Any]:
        from .config import default_config

        return self.update_config(default_config().to_dict())

    # ---- lifecycle -------------------------------------------------------------------
    def start_background(self) -> None:
        self.refresher.start()

    def stop(self) -> None:
        self.refresher.stop()
        self.cache.save()
