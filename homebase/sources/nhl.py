"""NHL adapter — Rangers scores/schedule (direct) + optional news (aggregator).

Source: api-web.nhle.com/v1/club-schedule-season/<TEAM>/now (verified 2026-06-17).
An empty/all-past schedule in June is a VALID offseason state, not a fetch failure.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Dict, List, Optional

from ..clock import Clock
from ..config import NewsSource
from ..fetcher import FetchResult
from ..model import (
    G_FINAL,
    G_LIVE,
    G_OFFSEASON,
    G_POSTPONED,
    G_PRE,
    G_PRESEASON,
    G_SCHEDULED,
    Game,
    TeamPayload,
    ValidationError,
    fmt_ny,
    parse_iso_utc,
)
from ..rss import FeedParseError, dedup_and_sort, parse_feed, to_news_items
from ..ssrf import SsrfPolicy
from .base import FetchSpec, SourceAdapter, register
from .news import news_fetchspec

NHL_HOST = "api-web.nhle.com"

_STATE_MAP = {
    "FUT": G_SCHEDULED,
    "PRE": G_PRE,
    "LIVE": G_LIVE,
    "CRIT": G_LIVE,
    "OFF": G_FINAL,
    "FINAL": G_FINAL,
}
_PPD = {"PPD", "SUSP", "CNCL"}


def _fmt_local(utc: _dt.datetime) -> str:
    return fmt_ny(utc)


class NhlAdapter(SourceAdapter):
    id = "nhl"

    def requests(self, card, clock: Clock) -> List[FetchSpec]:
        team = str(card.params.get("team", "NYR")).upper()
        specs = [
            FetchSpec(
                name="schedule",
                url=f"https://api-web.nhle.com/v1/club-schedule-season/{team}/now",
                policy=SsrfPolicy.allowlist([NHL_HOST]),
                ttl_minutes=card.refresh_minutes or 10,
            )
        ]
        ns = card.params.get("news")
        spec = news_fetchspec(ns if isinstance(ns, NewsSource) else None, name="news")
        if spec is not None:
            specs.append(spec)
        return specs

    def parse(self, card, responses: Dict[str, FetchResult], clock: Clock) -> TeamPayload:
        sched = responses.get("schedule")
        if sched is None or not sched.ok:
            raise ValidationError(f"NHL schedule fetch not ok: {getattr(sched, 'status', 'missing')}")
        try:
            data = json.loads(sched.body)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValidationError(f"NHL schedule not JSON: {exc}") from exc
        if not isinstance(data, dict) or "games" not in data:
            raise ValidationError("NHL schedule missing 'games' key (schema drift)")

        games = self._games(data.get("games") or [])
        now = clock.now_utc()
        future = [g for (g, t) in games if t and t > now and g.status not in (G_FINAL, G_POSTPONED)]
        past = [g for (g, t) in games if t and t <= now]

        payload = TeamPayload(league="NHL", team=str(card.params.get("team", "NYR")).upper())
        payload.schedule = [g for (g, _t) in games]
        if future:
            payload.status = future[0].status
            payload.next_game = future[0]
        else:
            payload.status = G_OFFSEASON
            payload.note = "Offseason — schedule resumes next season"
        if past:
            payload.last_game = past[-1]
        if not games:
            payload.status = G_OFFSEASON
            payload.note = "Offseason — no games scheduled"

        self._attach_news(card, responses, payload)
        return payload

    def _games(self, raw_games: list):
        out = []
        for g in raw_games:
            if not isinstance(g, dict):
                continue
            utc = parse_iso_utc(g.get("startTimeUTC", "") or "")
            state = _STATE_MAP.get(str(g.get("gameState", "")).upper(), G_SCHEDULED)
            if str(g.get("gameScheduleState", "OK")).upper() in _PPD:
                state = G_POSTPONED
            away = g.get("awayTeam") or {}
            home = g.get("homeTeam") or {}
            a_ab, h_ab = str(away.get("abbrev", "")), str(home.get("abbrev", ""))
            score = None
            if state in (G_FINAL, G_LIVE) and ("score" in away or "score" in home):
                score = f"{a_ab} {away.get('score', '-')} – {home.get('score', '-')} {h_ab}"
            game = Game(
                status=state,
                opponent=f"{a_ab} @ {h_ab}" if a_ab and h_ab else (a_ab or h_ab),
                home=None,
                start_utc=utc.isoformat() if utc else None,
                start_local=_fmt_local(utc) if utc else None,
                score=score,
                detail=str(g.get("gameScheduleState", "")),
            )
            out.append((game, utc))
        out.sort(key=lambda gt: gt[1] or _dt.datetime.max.replace(tzinfo=_dt.timezone.utc))
        return out

    def _attach_news(self, card, responses, payload: TeamPayload):
        ns = card.params.get("news")
        if not isinstance(ns, NewsSource):
            payload.news_state = "absent"
            return
        if not ns.enabled:
            payload.news_state = "off-pending-consent"
            return
        resp = responses.get("news")
        if resp is None or not getattr(resp, "ok", False):
            payload.news_state = "unavailable"
            return
        try:
            raws = parse_feed(resp.body)
        except FeedParseError:
            payload.news_state = "unavailable"
            return
        items = to_news_items(
            raws,
            default_source=ns.name,
            via_aggregator=(ns.mode == "aggregator"),
            badge=ns.badge,
            aggregator_strip_suffix=(ns.mode == "aggregator"),
        )
        payload.news = dedup_and_sort(items)[:8]
        payload.news_state = "ok" if payload.news else "unavailable"


register(NhlAdapter())
