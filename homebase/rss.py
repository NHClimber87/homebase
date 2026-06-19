"""Minimal stdlib RSS 2.0 / Atom parser + news normalization (dedup, ordering, honesty).

Handles:
  - Mets direct feed (mlb.com RSS 2.0).
  - Google News search feed (RSS 2.0; titles carry a " - Publisher" suffix; links are
    news.google.com/rss/articles/... redirects; <source> tag gives the publisher).

Normalization obligations (AC-NEWS):
  - strip the " - Publisher" title suffix; surface the publisher as the source.
  - dedup by canonicalized URL OR normalized title (so the same story via a direct feed
    and via-Google collapses to one).
  - sort publishedAt desc; missing timestamps badged "time unknown" and sorted last.
"""
from __future__ import annotations

import datetime as _dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import List, Optional
from urllib.parse import urlsplit, urlunsplit

from .model import NewsItem

_ATOM = "{http://www.w3.org/2005/Atom}"
_WS = re.compile(r"\s+")
_TITLE_SUFFIX = re.compile(r"\s+-\s+[^-]+$")  # " - Publisher" (publisher has no dash)


@dataclass
class RawItem:
    title: str
    link: str
    published: Optional[_dt.datetime]
    source_hint: str = ""


class FeedParseError(ValueError):
    pass


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _parse_date(s: str) -> Optional[_dt.datetime]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)  # RFC822 (RSS pubDate)
        if d is not None:
            return d.astimezone(_dt.timezone.utc) if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))  # ISO (Atom)
        return d.astimezone(_dt.timezone.utc) if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def parse_feed(xml_bytes: bytes) -> List[RawItem]:
    """Parse RSS 2.0 or Atom into RawItems. Raises FeedParseError on non-XML/HTML-error."""
    if not xml_bytes or not xml_bytes.strip():
        raise FeedParseError("empty feed")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise FeedParseError(f"not valid XML: {exc}") from exc

    items: List[RawItem] = []
    # RSS 2.0
    for item in root.iter("item"):
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        pub = _text(item.find("pubDate")) or _text(item.find("{http://purl.org/dc/elements/1.1/}date"))
        src = item.find("source")
        source_hint = _text(src) if src is not None else ""
        if title or link:
            items.append(RawItem(title, link, _parse_date(pub), source_hint))
    if items:
        return items
    # Atom fallback
    for entry in root.iter(f"{_ATOM}entry"):
        title = _text(entry.find(f"{_ATOM}title"))
        link_el = entry.find(f"{_ATOM}link")
        link = link_el.get("href", "") if link_el is not None else ""
        pub = _text(entry.find(f"{_ATOM}published")) or _text(entry.find(f"{_ATOM}updated"))
        if title or link:
            items.append(RawItem(title, link, _parse_date(pub), ""))
    if not items:
        raise FeedParseError("no items found in feed")
    return items


def _strip_suffix(title: str) -> str:
    return _TITLE_SUFFIX.sub("", title).strip()


def _publisher_from_title(title: str) -> str:
    m = _TITLE_SUFFIX.search(title)
    return m.group(0).lstrip(" -").strip() if m else ""


def _canon_url(url: str) -> str:
    try:
        p = urlsplit(url)
    except ValueError:
        return url.lower().strip()
    # drop query + fragment; lowercase scheme+host; strip trailing slash
    path = p.path.rstrip("/")
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, "", "")) or url.lower()


def _norm_title(title: str) -> str:
    return _WS.sub(" ", _strip_suffix(title).lower()).strip(" .-—:")


def to_news_items(
    raws: List[RawItem],
    *,
    default_source: str,
    via_aggregator: bool,
    badge: str = "",
    aggregator_strip_suffix: bool = False,
) -> List[NewsItem]:
    out: List[NewsItem] = []
    for r in raws:
        title = r.title
        source = r.source_hint or default_source
        if aggregator_strip_suffix:
            pub = _publisher_from_title(r.title)
            if pub and not r.source_hint:
                source = pub
            title = _strip_suffix(r.title)
        out.append(
            NewsItem(
                title=title,
                source=source,
                url=r.link,
                published_at=r.published.isoformat() if r.published else None,
                via_aggregator=via_aggregator,
                badge=badge,
            )
        )
    return out


def dedup_and_sort(items: List[NewsItem]) -> List[NewsItem]:
    """Dedup by canonical URL OR normalized title; sort publishedAt desc; unknowns last."""
    seen_urls = set()
    seen_titles = set()
    deduped: List[NewsItem] = []
    for it in items:
        cu = _canon_url(it.url) if it.url else ""
        nt = _norm_title(it.title) if it.title else ""
        if (cu and cu in seen_urls) or (nt and nt in seen_titles):
            continue
        if cu:
            seen_urls.add(cu)
        if nt:
            seen_titles.add(nt)
        deduped.append(it)

    def sort_key(it: NewsItem):
        if it.published_at:
            return (1, it.published_at)
        return (0, "")  # missing timestamp sorts last

    deduped.sort(key=sort_key, reverse=True)
    return deduped
