"""Stooq markets adapter (primary; delayed; keyless). Datacenter-IP-blocked -> needs the
residential smoke-test before it ships live (AC-MKT-1, deferred to the friend's machine).

Per-symbol CSV: https://stooq.com/q/l/?s=<native>&f=sd2t2ohlcv&h&e=csv
A symbol Stooq doesn't know returns N/D fields -> a labeled error row (AC-CONFIG), not a crash.
"""
from __future__ import annotations

import csv
import io
from typing import Dict, List

from ..clock import Clock
from ..fetcher import FetchResult
from ..markets.marketstate import is_closed, market_state
from ..markets.symbols import display_name, resolve
from ..model import (
    Q_DELAYED,
    Q_PRIOR_CLOSE,
    MarketRow,
    MarketsPayload,
    ValidationError,
)
from ..ssrf import SsrfPolicy
from .base import FetchSpec, SourceAdapter, register

STOOQ_HOST = "stooq.com"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class StooqAdapter(SourceAdapter):
    id = "stooq"

    def requests(self, card, clock: Clock) -> List[FetchSpec]:
        specs = []
        for sym in card.params.get("symbols", []):
            native = resolve(sym, "stooq")
            specs.append(
                FetchSpec(
                    name=f"sym:{sym}",
                    url=f"https://stooq.com/q/l/?s={native}&f=sd2t2ohlcv&h&e=csv",
                    policy=SsrfPolicy.allowlist([STOOQ_HOST]),
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
                            quote_type=Q_PRIOR_CLOSE if closed else Q_DELAYED)
            if resp is None or not getattr(resp, "ok", False):
                row.error = "unavailable"
                payload.rows.append(row)
                continue
            parsed = self._parse_csv(resp.text())
            if parsed is None:
                row.error = "no data"
                payload.rows.append(row)
                continue
            close, open_, date, time = parsed
            if close is None:
                row.error = "unknown symbol"
                payload.rows.append(row)
                continue
            row.price = close
            if open_ is not None and open_ != 0:
                row.change = round(close - open_, 4)
                row.pct = round((close - open_) / open_ * 100, 3)
            row.as_of = f"{date} {time}".strip()
            payload.rows.append(row)

        # validate-before-cache: if NOT ONE symbol produced a price (transport failed, or
        # every response was empty/garbage/unknown), keep the last-good cache (AC-FRESH).
        # A partial success (>=1 price) still updates, with the bad symbols as labeled rows.
        if symbols and not any(r.price is not None for r in payload.rows):
            raise ValidationError("no Stooq symbol returned a usable price")
        return payload

    @staticmethod
    def _parse_csv(text: str):
        try:
            reader = csv.DictReader(io.StringIO(text))
            row = next(reader, None)
        except (csv.Error, StopIteration):
            return None
        if not row:
            return None
        close = _f(row.get("Close"))
        open_ = _f(row.get("Open"))
        date = (row.get("Date") or "").strip()
        time = (row.get("Time") or "").strip()
        if date.upper() == "N/D":
            return (None, None, "", "")
        return (close, open_, date, time)


register(StooqAdapter())
