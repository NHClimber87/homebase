"""Yahoo markets adapter (fallback; near-real-time; keyless, unofficial). Datacenter-IP-
blocked + needs a browser UA. Swappable from Stooq via the settings UI vetted-alternate list.

Per-symbol JSON: https://query1.finance.yahoo.com/v8/finance/chart/<native>
meta.regularMarketPrice / previousClose / regularMarketTime drive the row.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Dict, List

from ..clock import Clock
from ..fetcher import FetchResult
from ..markets.marketstate import is_closed, market_state
from ..markets.symbols import display_name, resolve
from ..model import (
    Q_LIVE,
    Q_PRIOR_CLOSE,
    MarketRow,
    MarketsPayload,
    ValidationError,
)
from ..ssrf import SsrfPolicy
from .base import FetchSpec, SourceAdapter, register

YAHOO_HOST = "query1.finance.yahoo.com"
_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


class YahooAdapter(SourceAdapter):
    id = "yahoo"

    def requests(self, card, clock: Clock) -> List[FetchSpec]:
        specs = []
        for sym in card.params.get("symbols", []):
            native = resolve(sym, "yahoo")
            specs.append(
                FetchSpec(
                    name=f"sym:{sym}",
                    # range/interval params match the request verified working from a
                    # residential IP 2026-06-18; meta carries regularMarketPrice + previousClose.
                    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{native}?range=1d&interval=1d",
                    policy=SsrfPolicy.allowlist([YAHOO_HOST]),
                    headers={"User-Agent": _BROWSER_UA},
                    optional=True,
                    ttl_minutes=card.refresh_minutes or 5,
                )
            )
        return specs

    def parse(self, card, responses: Dict[str, FetchResult], clock: Clock) -> MarketsPayload:
        symbols = list(card.params.get("symbols", []))
        state = market_state(clock)
        closed = is_closed(state)
        payload = MarketsPayload()
        for sym in symbols:
            resp = responses.get(f"sym:{sym}")
            row = MarketRow(symbol=sym, display=display_name(sym), market_state=state,
                            quote_type=Q_PRIOR_CLOSE if closed else Q_LIVE)
            if resp is None or not getattr(resp, "ok", False):
                row.error = "unavailable"
                payload.rows.append(row)
                continue
            meta = self._meta(resp.body)
            if meta is None:
                row.error = "unknown symbol"
                payload.rows.append(row)
                continue
            price = meta.get("regularMarketPrice")
            prev = meta.get("previousClose", meta.get("chartPreviousClose"))
            if isinstance(price, (int, float)):
                row.price = float(price)
                if isinstance(prev, (int, float)) and prev:
                    row.change = round(price - prev, 4)
                    row.pct = round((price - prev) / prev * 100, 3)
            t = meta.get("regularMarketTime")
            if isinstance(t, (int, float)):
                row.as_of = _dt.datetime.fromtimestamp(t, tz=_dt.timezone.utc).isoformat()
            if row.price is None:
                row.error = "no data"
            payload.rows.append(row)

        # validate-before-cache: keep last-good unless at least one price came back (AC-FRESH).
        if symbols and not any(r.price is not None for r in payload.rows):
            raise ValidationError("no Yahoo symbol returned a usable price")
        return payload

    @staticmethod
    def _meta(body: bytes):
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return None
        chart = (data or {}).get("chart") or {}
        if chart.get("error"):
            return None
        results = chart.get("result") or []
        if not results:
            return None
        return (results[0] or {}).get("meta") or {}


register(YahooAdapter())
