"""MLB adapter — Mets scores/schedule (direct) + direct Mets RSS news.

Source: statsapi.mlb.com/api/v1/schedule?sportId=1&teamId=121 (verified 2026-06-17).
teamId 121 = Mets. The date window is computed from the injectable clock, so a
frozen-January clock returns no games -> offseason (AC-CORR-2).
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Dict, List

from ..clock import Clock
from ..config import NewsSource
from ..fetcher import FetchResult
from ..model import (
    G_FINAL,
    G_LIVE,
    G_OFFSEASON,
    G_POSTPONED,
    G_PRE,
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

MLB_HOST = "statsapi.mlb.com"

_ABSTRACT = {"Preview": G_SCHEDULED, "Live": G_LIVE, "Final": G_FINAL}


def _map_status(status: dict) -> str:
    detailed = str(status.get("detailedState", ""))
    if "Postponed" in detailed or "Suspended" in detailed or "Cancelled" in detailed:
        return G_POSTPONED
    if detailed in ("Pre-Game", "Warmup"):
        return G_PRE
    return _ABSTRACT.get(str(status.get("abstractGameState", "")), G_SCHEDULED)


class MlbAdapter(SourceAdapter):
    id = "mlb"

    def requests(self, card, clock: Clock) -> List[FetchSpec]:
        team_id = int(card.params.get("team_id", 121))
        now = clock.now_utc()
        start = (now - _dt.timedelta(days=4)).date().isoformat()
        end = (now + _dt.timedelta(days=10)).date().isoformat()
        url = (
            "https://statsapi.mlb.com/api/v1/schedule?sportId=1"
            f"&teamId={team_id}&startDate={start}&endDate={end}"
            "&hydrate=linescore,team"
        )
        specs = [
            FetchSpec(
                name="schedule",
                url=url,
                policy=SsrfPolicy.allowlist([MLB_HOST]),
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
            raise ValidationError(f"MLB schedule fetch not ok: {getattr(sched, 'status', 'missing')}")
        try:
            data = json.loads(sched.body)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValidationError(f"MLB schedule not JSON: {exc}") from exc
        if not isinstance(data, dict) or "dates" not in data:
            raise ValidationError("MLB schedule missing 'dates' key (schema drift)")

        games = self._games(data.get("dates") or [])
        now = clock.now_utc()
        future = [g for (g, t) in games if t and t > now and g.status not in (G_FINAL, G_POSTPONED)]
        past = [g for (g, t) in games if t and t <= now]

        payload = TeamPayload(league="MLB", team=str(card.title or "Mets"))
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
            payload.note = "Offseason — no games in window"

        self._attach_news(card, responses, payload)
        return payload

    def _games(self, dates: list):
        out = []
        for d in dates:
            for g in (d.get("games") or []) if isinstance(d, dict) else []:
                if not isinstance(g, dict):
                    continue
                utc = parse_iso_utc(g.get("gameDate", "") or "")
                status = _map_status(g.get("status") or {})
                teams = g.get("teams") or {}
                away = (teams.get("away") or {})
                home = (teams.get("home") or {})
                a_name = ((away.get("team") or {}).get("name") or "")
                h_name = ((home.get("team") or {}).get("name") or "")
                score = None
                if status in (G_FINAL, G_LIVE) and ("score" in away or "score" in home):
                    score = f"{a_name} {away.get('score', '-')} – {home.get('score', '-')} {h_name}"
                out.append(
                    (
                        Game(
                            status=status,
                            opponent=f"{a_name} @ {h_name}".strip(" @"),
                            start_utc=utc.isoformat() if utc else None,
                            start_local=fmt_ny(utc) if utc else None,
                            score=score,
                            detail=str((g.get("status") or {}).get("detailedState", "")),
                        ),
                        utc,
                    )
                )
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


register(MlbAdapter())
