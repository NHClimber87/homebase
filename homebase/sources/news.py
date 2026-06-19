"""News helpers shared by team cards and the headlines card.

Google News search feed builder + a function that turns a configured NewsSource into a
consent-aware FetchSpec (an aggregator source that is disabled yields an *unenabled*
spec -> never fetched -> zero egress).
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote_plus

from ..config import NewsSource
from ..ssrf import SsrfPolicy
from .base import FetchSpec

GOOGLE_NEWS_HOST = "news.google.com"


def google_news_url(query: str, *, window: str = "7d") -> str:
    q = query.strip()
    if "when:" not in q:
        q = f"{q} when:{window}"
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(q)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def news_fetchspec(news: Optional[NewsSource], *, name: str = "news", window: str = "7d") -> Optional[FetchSpec]:
    """Build the FetchSpec for a NewsSource, honoring its consent (`enabled`) state.

    Returns None if there is no news source configured. Returns a spec with enabled=False
    (so the refresher skips it -> no egress) when an aggregator source is opted out.
    """
    if news is None:
        return None
    if news.mode == "aggregator":
        url = google_news_url(news.query, window=window)
        return FetchSpec(
            name=name,
            url=url,
            policy=SsrfPolicy.public(),  # headlines/user-feeds: scheme + public-IP only
            optional=True,
            enabled=bool(news.enabled),
        )
    # direct RSS feed
    if not news.url:
        return None
    return FetchSpec(
        name=name,
        url=news.url,
        policy=SsrfPolicy.public(),
        optional=True,
        enabled=bool(news.enabled),
    )
