"""Deterministic report math. All numbers in reports come from here, never from an LLM.

Conventions
-----------
* Totals sum only days whose value is ``available``. A total is ``complete`` only when every
  day in the period is available; otherwise it is reported as partial with the day counts.
* Period-over-period change is computed only when both periods are complete.
* Engagement rate uses the per-platform formula in ``metrics.yaml``. If any numerator part or
  the denominator is missing or zero, the rate is unavailable — it is never estimated.
"""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel

from socialledger.config import EngagementRateRule
from socialledger.schema import (
    ENGAGEMENT_METRICS,
    Account,
    Availability,
    DailyMetrics,
    Metric,
    Post,
    Source,
)

PeriodName = Literal["weekly", "monthly", "custom"]


class Period(BaseModel):
    label: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> Period:
        length = timedelta(days=self.days)
        return Period(label="previous", start=self.start - length, end=self.end - length)


def resolve_period(
    name: PeriodName, today: date, start: date | None = None, end: date | None = None
) -> Period:
    if name == "weekly":
        last = today - timedelta(days=1)
        return Period(label="weekly", start=last - timedelta(days=6), end=last)
    if name == "monthly":
        first_this_month = today.replace(day=1)
        last = first_this_month - timedelta(days=1)
        return Period(label="monthly", start=last.replace(day=1), end=last)
    if start is None or end is None:
        raise ValueError("A custom period needs both --start and --end.")
    if end < start:
        raise ValueError("--end must not be before --start.")
    return Period(label="custom", start=start, end=end)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


class Aggregate(BaseModel):
    metric: Metric
    value: float | None
    days_available: int
    days_total: int
    days_not_in_store: int = 0
    sources: list[Source]
    availability: Availability

    @property
    def complete(self) -> bool:
        return self.days_total > 0 and self.days_available == self.days_total


def aggregate(daily: Sequence[DailyMetrics], metric: Metric, period: Period) -> Aggregate:
    """Sum a metric over a period, counting (never hiding) the days that are missing."""
    values: list[float] = []
    sources: set[Source] = set()
    reasons: Counter[Availability] = Counter()
    not_in_store = 0
    by_date = {d.date: d for d in daily}
    for offset in range(period.days):
        day = by_date.get(period.start + timedelta(days=offset))
        mv = day.metrics.get(metric) if day else None
        if mv is None:
            not_in_store += 1
            continue
        if mv.is_available and mv.value is not None:
            values.append(mv.value)
            sources.add(mv.source)
        else:
            reasons[mv.availability] += 1
    if values:
        availability = Availability.AVAILABLE
    elif reasons:
        availability = reasons.most_common(1)[0][0]
    else:
        availability = Availability.STALE  # nothing snapshotted for this period
    return Aggregate(
        metric=metric,
        value=sum(values) if values else None,
        days_available=len(values),
        days_total=period.days,
        days_not_in_store=not_in_store,
        sources=sorted(sources, key=lambda s: s.value),
        availability=availability,
    )


def pct_change(current: Aggregate | None, previous: Aggregate | None) -> float | None:
    if (
        current is None
        or previous is None
        or not current.complete
        or not previous.complete
        or current.value is None
        or previous.value is None
        or previous.value == 0
    ):
        return None
    return (current.value - previous.value) / previous.value


class FollowerGrowth(BaseModel):
    start: float | None
    end: float | None
    change: float | None
    method: str
    net_from_daily: float | None = None


def follower_growth(
    accounts: Sequence[Account], daily: Sequence[DailyMetrics], period: Period
) -> FollowerGrowth:
    """Growth from stored follower snapshots; also net gained-lost from daily metrics."""
    in_range = sorted(
        (a for a in accounts if period.start <= a.as_of <= period.end and a.followers.is_available),
        key=lambda a: a.as_of,
    )
    gained = aggregate(daily, Metric.FOLLOWERS_GAINED, period)
    lost = aggregate(daily, Metric.FOLLOWERS_LOST, period)
    net = None
    if gained.complete and lost.complete and gained.value is not None and lost.value is not None:
        net = gained.value - lost.value
    if len(in_range) >= 2:
        start_v, end_v = in_range[0].followers.value, in_range[-1].followers.value
        assert start_v is not None and end_v is not None
        return FollowerGrowth(
            start=start_v,
            end=end_v,
            change=end_v - start_v,
            method=f"snapshots {in_range[0].as_of} → {in_range[-1].as_of}",
            net_from_daily=net,
        )
    end_v = in_range[-1].followers.value if in_range else None
    if net is not None:
        return FollowerGrowth(
            start=None,
            end=end_v,
            change=net,
            method="sum of daily gained - lost",
            net_from_daily=net,
        )
    return FollowerGrowth(
        start=None,
        end=end_v,
        change=None,
        method="insufficient snapshots in period",
        net_from_daily=None,
    )


class EngagementRate(BaseModel):
    value: float | None
    formula: str
    reason: str | None = None


def engagement_rate(
    totals: dict[Metric, Aggregate], rule: EngagementRateRule | None
) -> EngagementRate:
    if rule is None:
        return EngagementRate(value=None, formula="not defined", reason="No formula configured.")
    parts = [totals.get(m) for m in rule.numerator]
    denom = totals.get(rule.denominator)
    missing = [
        m.value for m, p in zip(rule.numerator, parts, strict=True) if p is None or p.value is None
    ]
    if missing:
        return EngagementRate(
            value=None,
            formula=rule.label,
            reason=f"Missing numerator metrics: {', '.join(missing)}.",
        )
    if denom is None or denom.value is None or denom.value == 0:
        return EngagementRate(
            value=None,
            formula=rule.label,
            reason=f"Denominator '{rule.denominator.value}' unavailable or zero.",
        )
    if not denom.complete or not all(p is not None and p.complete for p in parts):
        return EngagementRate(
            value=None, formula=rule.label, reason="Partial data in period; rate not computed."
        )
    numerator = sum(p.value for p in parts if p is not None and p.value is not None)
    return EngagementRate(value=numerator / denom.value, formula=rule.label)


def post_engagements(post: Post) -> float | None:
    vals = [
        post.metrics[m].value
        for m in ENGAGEMENT_METRICS
        if m in post.metrics and post.metrics[m].is_available
    ]
    return sum(v for v in vals if v is not None) if vals else None


class RankedPost(BaseModel):
    post_id: str
    url: str | None
    title: str | None
    published_at: str
    format: str
    ranked_by: str
    score: float
    views: float | None
    engagements: float | None


def rank_posts(posts: Iterable[Post], n: int = 5) -> tuple[list[RankedPost], list[RankedPost], str]:
    """Top and bottom posts by views (if every post has views) else by engagements."""
    posts = list(posts)
    by_views = all(
        p.metrics.get(Metric.VIEWS) and p.metrics[Metric.VIEWS].is_available for p in posts
    )
    key = "views" if by_views else "engagements"
    scored: list[RankedPost] = []
    for p in posts:
        views_mv = p.metrics.get(Metric.VIEWS)
        views = views_mv.value if views_mv and views_mv.is_available else None
        eng = post_engagements(p)
        score = views if by_views else eng
        if score is None:
            continue
        scored.append(
            RankedPost(
                post_id=p.post_id,
                url=p.url,
                title=p.title,
                published_at=p.published_at.isoformat(),
                format=p.format.value,
                ranked_by=key,
                score=score,
                views=views,
                engagements=eng,
            )
        )
    scored.sort(key=lambda r: r.score, reverse=True)
    top = scored[:n]
    bottom = [r for r in reversed(scored[-n:]) if r not in top] if len(scored) > n else []
    return top, bottom, key


class FormatShare(BaseModel):
    format: str
    posts: int
    share_of_posts: float
    views: float | None
    share_of_views: float | None


def format_mix(posts: Iterable[Post]) -> list[FormatShare]:
    posts = list(posts)
    if not posts:
        return []
    counts: Counter[str] = Counter(p.format.value for p in posts)
    views: defaultdict[str, float] = defaultdict(float)
    views_complete = True
    for p in posts:
        mv = p.metrics.get(Metric.VIEWS)
        if mv and mv.is_available and mv.value is not None:
            views[p.format.value] += mv.value
        else:
            views_complete = False
    total_views = sum(views.values())
    return [
        FormatShare(
            format=fmt,
            posts=n,
            share_of_posts=n / len(posts),
            views=views[fmt] if views_complete else None,
            share_of_views=(views[fmt] / total_views) if views_complete and total_views else None,
        )
        for fmt, n in counts.most_common()
    ]
