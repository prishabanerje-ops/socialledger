"""Synthetic demo data so new users can try reports without connecting any account.

The data is generated deterministically (fixed seed), is entirely fictional, and the brand is
flagged ``demo: true`` so every report carries a "synthetic data" banner.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, time, timedelta

from socialledger.config import load_metrics
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
from socialledger.store import Store

DEMO_BRAND = "demo-brand"
DEMO_ACCOUNT_ID = "UC_DEMO_SYNTHETIC_0000"


def seed(store: Store, today: date, days: int = 120, brand: str = DEMO_BRAND) -> int:
    rng = random.Random(42)  # noqa: S311 - synthetic data, not security relevant
    followers = 12_000.0
    start = today - timedelta(days=days)
    daily_rows: list[DailyMetrics] = []
    for i in range(days):
        day = start + timedelta(days=i)
        weekday_boost = 1.25 if day.weekday() in (5, 6) else 1.0
        views = rng.randint(1_800, 3_200) * weekday_boost * (1 + i / days * 0.3)
        gained = rng.randint(15, 60)
        lost = rng.randint(3, 15)
        followers += gained - lost
        daily_rows.append(
            DailyMetrics(
                brand=brand,
                platform=Platform.YOUTUBE,
                account_id=DEMO_ACCOUNT_ID,
                date=day,
                metrics={
                    Metric.VIEWS: MetricValue.of(round(views)),
                    Metric.WATCH_TIME_MINUTES: MetricValue.of(round(views * rng.uniform(1.2, 2.4))),
                    Metric.AVG_VIEW_DURATION_SECONDS: MetricValue.of(rng.randint(70, 160)),
                    Metric.FOLLOWERS_GAINED: MetricValue.of(gained),
                    Metric.FOLLOWERS_LOST: MetricValue.of(lost),
                    Metric.LIKES: MetricValue.of(round(views * rng.uniform(0.03, 0.06))),
                    Metric.COMMENTS: MetricValue.of(round(views * rng.uniform(0.002, 0.006))),
                    Metric.SHARES: MetricValue.of(round(views * rng.uniform(0.002, 0.005))),
                },
            )
        )
        store.upsert_account(
            Account(
                brand=brand,
                platform=Platform.YOUTUBE,
                account_id=DEMO_ACCOUNT_ID,
                handle="@demo-channel",
                followers=MetricValue.of(followers),
                as_of=day,
            )
        )
    # The real connector marks unexposed metrics explicitly; mirror that here.
    cfg = load_metrics().for_platform(Platform.YOUTUBE)
    for row in daily_rows:
        for metric in cfg.unavailable("daily"):
            row.metrics[metric] = MetricValue.missing(
                Availability.UNAVAILABLE_FROM_API, "youtube API does not expose this metric"
            )
    store.upsert_daily(daily_rows, api_version="demo")

    posts: list[Post] = []
    for n in range(days // 4):
        published = start + timedelta(days=n * 4 + rng.randint(0, 3))
        fmt = rng.choice([PostFormat.SHORT, PostFormat.SHORT, PostFormat.LONG_VIDEO])
        views = rng.randint(800, 25_000) * (0.6 if fmt is PostFormat.SHORT else 1.4)
        posts.append(
            Post(
                brand=brand,
                platform=Platform.YOUTUBE,
                account_id=DEMO_ACCOUNT_ID,
                post_id=f"demo{n:04d}",
                url=None,
                published_at=datetime.combine(published, time(14, 0), tzinfo=UTC),
                format=fmt,
                title=f"Demo video {n + 1}",
                as_of=today,
                metrics={
                    Metric.VIEWS: MetricValue.of(round(views)),
                    Metric.LIKES: MetricValue.of(round(views * rng.uniform(0.02, 0.08))),
                    Metric.COMMENTS: MetricValue.of(round(views * rng.uniform(0.001, 0.01))),
                    Metric.SHARES: MetricValue.of(round(views * rng.uniform(0.001, 0.006))),
                    Metric.WATCH_TIME_MINUTES: MetricValue.of(round(views * rng.uniform(0.5, 3))),
                },
            )
        )
    store.upsert_posts(posts)
    store.log_snapshot(brand, Platform.YOUTUBE, "ok", "synthetic demo data")
    return len(daily_rows)
