"""Source adapter framework.

An adapter turns a Card into (1) a list of FetchSpecs (what to fetch, with which SSRF
policy + consent gate) and (2) a parse() that normalizes the fetched responses into a
typed payload — raising ValidationError if the payload fails sanity checks (so the
cache keeps its last-good value instead of being overwritten: AC-FRESH).

A consent-disabled (enabled=False) spec is never fetched -> zero egress (AC-PRIV-3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..clock import Clock
from ..fetcher import FetchResult
from ..ssrf import SsrfPolicy


@dataclass
class FetchSpec:
    name: str                 # logical key, e.g. "schedule", "news", "AP"
    url: str
    policy: SsrfPolicy
    headers: Optional[Dict[str, str]] = None
    optional: bool = False    # failure degrades a subsection, doesn't fail the card
    enabled: bool = True       # consent gate; disabled -> not fetched (no egress)
    ttl_minutes: int = 10      # min interval before a force-refresh may re-hit this spec


class SourceAdapter:
    id: str = "base"

    def requests(self, card, clock: Clock) -> List[FetchSpec]:
        raise NotImplementedError

    def parse(self, card, responses: Dict[str, FetchResult], clock: Clock):
        raise NotImplementedError


_REGISTRY: Dict[str, SourceAdapter] = {}


def register(adapter: SourceAdapter) -> SourceAdapter:
    _REGISTRY[adapter.id] = adapter
    return adapter


def get_adapter(source_id: str) -> Optional[SourceAdapter]:
    return _REGISTRY.get(source_id)


def all_sources() -> List[str]:
    return sorted(_REGISTRY)
