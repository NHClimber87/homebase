"""Headlines adapter — multiple feeds (AP + Reuters via Google by default under posture B).

Each feed is consent-gated; a disabled feed is never fetched (no egress) and surfaces a
note. Items across feeds are deduped + sorted (AC-NEWS). A dead feed -> "source unavailable".
"""
from __future__ import annotations

from typing import Dict, List

from ..clock import Clock
from ..config import NewsSource
from ..fetcher import FetchResult
from ..model import HeadlinesPayload, ValidationError
from ..rss import FeedParseError, dedup_and_sort, parse_feed, to_news_items
from .base import FetchSpec, SourceAdapter, register
from .news import news_fetchspec


def _feeds(card) -> List[NewsSource]:
    feeds = card.params.get("feeds") or []
    return [f for f in feeds if isinstance(f, NewsSource)]


class HeadlinesAdapter(SourceAdapter):
    id = "google-news"

    def requests(self, card, clock: Clock) -> List[FetchSpec]:
        specs: List[FetchSpec] = []
        for i, feed in enumerate(_feeds(card)):
            spec = news_fetchspec(feed, name=f"feed:{i}", window="24h")
            if spec is not None:
                specs.append(spec)
        return specs

    def parse(self, card, responses: Dict[str, FetchResult], clock: Clock) -> HeadlinesPayload:
        feeds = _feeds(card)
        if not feeds:
            raise ValidationError("headlines card has no feeds configured")
        payload = HeadlinesPayload()
        all_items = []
        any_ok = False
        for i, feed in enumerate(feeds):
            if not feed.enabled:
                payload.notes.append(f"{feed.name}: off — enable to fetch (routes {feed.badge or 'direct'})")
                continue
            resp = responses.get(f"feed:{i}")
            if resp is None or not getattr(resp, "ok", False):
                payload.notes.append(f"{feed.name}: source unavailable")
                continue
            try:
                raws = parse_feed(resp.body)
            except FeedParseError:
                payload.notes.append(f"{feed.name}: source unavailable")
                continue
            any_ok = True
            all_items.extend(
                to_news_items(
                    raws,
                    default_source=feed.name,
                    via_aggregator=(feed.mode == "aggregator"),
                    badge=feed.badge,
                    aggregator_strip_suffix=(feed.mode == "aggregator"),
                )
            )
        payload.items = dedup_and_sort(all_items)[:20]
        # If every enabled feed failed AND there were enabled feeds, that's a hard failure
        # so the cache keeps last-good (AC-FRESH). All-disabled is a valid (empty) state.
        enabled_count = sum(1 for f in feeds if f.enabled)
        if enabled_count and not any_ok:
            raise ValidationError("all enabled headline feeds failed")
        return payload


register(HeadlinesAdapter())
