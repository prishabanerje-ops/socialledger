from datetime import UTC, date, datetime, timedelta

import pytest

from socialledger.config import EngagementRateRule
from socialledger.metrics import (
    Period,
    aggregate,
    engagement_rate,
    follower_growth,
    format_mix,
    pct_change,
    rank_posts,
    resolve_period,
)
from socialledger.schema import (
    Account,
    Availability,
    DailyMetrics,
    Metric,
    MetricValue,
    Platform,
    Post,
    PostFormat,
)

P = Period(label="custom", start=date(2026, 10, 1), end=date(2026, 10, 3))
RULE = EngagementRateRule(
    numerator=[Metric.LIKES, Metric.COMMENTS],
    denominator=Metric.VIEWS,
    label="(likes + comments) / views",
)


def _days(values: dict[Metric, list[float | None]], start: date = P.start) -> list[DailyMetrics]:
    n = len(next(iter(values.values())))
    out = []
    for i in range(n):
        metrics = {}
        for m, vals in values.items():
            v = vals[i]
            metrics[m] = (
                MetricValue.of(v) if v is not None else MetricValue.missing(Availability.STALE)
            )
        out.append(
            DailyMetrics(
                brand="b",
                platform=Platform.YOUTUBE,
                account_id="a",
                date=start + timedelta(days=i),
                metrics=metrics,
            )
        )
    return out


def test_resolve_periods() -> None:
    today = date(2026, 10, 22)
    weekly = resolve_period("weekly", today)
    assert (weekly.start, weekly.end, weekly.days) == (date(2026, 10, 15), date(2026, 10, 21), 7)
    monthly = resolve_period("monthly", today)
    assert (monthly.start, monthly.end) == (date(2026, 9, 1), date(2026, 9, 30))
    prev = monthly.previous()
    assert prev.end == date(2026, 8, 31) and prev.days == 30
    with pytest.raises(ValueError):
        resolve_period("custom", today)


def test_aggregate_complete_and_partial() -> None:
    full = aggregate(_days({Metric.VIEWS: [1, 2, 3]}), Metric.VIEWS, P)
    assert full.value == 6 and full.complete
    partial = aggregate(_days({Metric.VIEWS: [1, None, 3]}), Metric.VIEWS, P)
    assert partial.value == 4 and not partial.complete and partial.days_available == 2


def test_aggregate_counts_days_never_snapshotted() -> None:
    agg = aggregate(_days({Metric.VIEWS: [5]}), Metric.VIEWS, P)
    assert agg.days_not_in_store == 2 and not agg.complete


def test_aggregate_all_missing_is_none_not_zero() -> None:
    agg = aggregate(_days({Metric.VIEWS: [None, None, None]}), Metric.VIEWS, P)
    assert agg.value is None and agg.availability is Availability.STALE


def test_pct_change_requires_complete_periods() -> None:
    cur = aggregate(_days({Metric.VIEWS: [2, 2, 2]}), Metric.VIEWS, P)
    prev_period = P.previous()
    prev = aggregate(_days({Metric.VIEWS: [1, 1, 1]}, prev_period.start), Metric.VIEWS, prev_period)
    assert pct_change(cur, prev) == pytest.approx(1.0)
    partial_prev = aggregate(
        _days({Metric.VIEWS: [1, None, 1]}, prev_period.start), Metric.VIEWS, prev_period
    )
    assert pct_change(cur, partial_prev) is None


def test_engagement_rate_formula_and_gaps() -> None:
    days = _days(
        {Metric.VIEWS: [100, 100, 100], Metric.LIKES: [5, 5, 5], Metric.COMMENTS: [1, 1, 1]}
    )
    totals = {m: aggregate(days, m, P) for m in (Metric.VIEWS, Metric.LIKES, Metric.COMMENTS)}
    rate = engagement_rate(totals, RULE)
    assert rate.value == pytest.approx(18 / 300)
    del totals[Metric.COMMENTS]
    missing = engagement_rate(totals, RULE)
    assert missing.value is None and "comments" in (missing.reason or "")


def test_engagement_rate_zero_denominator() -> None:
    days = _days({Metric.VIEWS: [0, 0, 0], Metric.LIKES: [0, 0, 0], Metric.COMMENTS: [0, 0, 0]})
    totals = {m: aggregate(days, m, P) for m in (Metric.VIEWS, Metric.LIKES, Metric.COMMENTS)}
    assert engagement_rate(totals, RULE).value is None


def test_follower_growth_from_snapshots() -> None:
    accounts = [
        Account(
            brand="b",
            platform=Platform.YOUTUBE,
            account_id="a",
            followers=MetricValue.of(v),
            as_of=d,
        )
        for d, v in ((date(2026, 10, 1), 100), (date(2026, 10, 3), 130))
    ]
    growth = follower_growth(accounts, [], P)
    assert growth.change == 30 and growth.end == 130


def test_follower_growth_from_daily_net() -> None:
    days = _days({Metric.FOLLOWERS_GAINED: [5, 5, 5], Metric.FOLLOWERS_LOST: [1, 1, 1]})
    growth = follower_growth([], days, P)
    assert growth.change == 12 and "gained" in growth.method


def _post(pid: str, views: float | None, fmt: PostFormat, likes: float = 1) -> Post:
    metrics = {Metric.LIKES: MetricValue.of(likes)}
    metrics[Metric.VIEWS] = (
        MetricValue.of(views)
        if views is not None
        else MetricValue.missing(Availability.UNAVAILABLE_FROM_API)
    )
    return Post(
        brand="b",
        platform=Platform.YOUTUBE,
        account_id="a",
        post_id=pid,
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
        format=fmt,
        as_of=date(2026, 10, 3),
        metrics=metrics,
    )


def test_rank_posts_by_views() -> None:
    posts = [_post(str(i), i * 10, PostFormat.SHORT) for i in range(1, 8)]
    top, bottom, key = rank_posts(posts, n=3)
    assert key == "views"
    assert [p.post_id for p in top] == ["7", "6", "5"]
    assert [p.post_id for p in bottom] == ["1", "2", "3"]


def test_rank_falls_back_to_engagements_when_views_missing() -> None:
    posts = [_post("a", None, PostFormat.IMAGE, likes=3), _post("b", 10, PostFormat.IMAGE)]
    _, _, key = rank_posts(posts)
    assert key == "engagements"


def test_format_mix() -> None:
    mix = format_mix(
        [
            _post("a", 30, PostFormat.SHORT),
            _post("b", 10, PostFormat.SHORT),
            _post("c", 60, PostFormat.LONG_VIDEO),
        ]
    )
    by = {m.format: m for m in mix}
    assert by["short"].posts == 2 and by["short"].share_of_views == pytest.approx(0.4)
    assert by["long_video"].share_of_posts == pytest.approx(1 / 3)
