"""Build the versioned ``report.json`` model from the snapshot store."""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel, Field

from socialledger import __version__
from socialledger.config import MetricsConfig
from socialledger.metrics import (
    Aggregate,
    EngagementRate,
    FollowerGrowth,
    FormatShare,
    Period,
    RankedPost,
    aggregate,
    engagement_rate,
    follower_growth,
    format_mix,
    pct_change,
    rank_posts,
)
from socialledger.schema import REPORT_SCHEMA_VERSION, Availability, Metric, Platform, Source
from socialledger.store import Store

REPORTED_METRICS: tuple[Metric, ...] = (
    Metric.VIEWS,
    Metric.REACH,
    Metric.IMPRESSIONS,
    Metric.WATCH_TIME_MINUTES,
    Metric.FOLLOWERS_GAINED,
    Metric.FOLLOWERS_LOST,
    Metric.LIKES,
    Metric.COMMENTS,
    Metric.SHARES,
    Metric.SAVES,
    Metric.PROFILE_VISITS,
    Metric.LINK_CLICKS,
)


class MetricSummary(BaseModel):
    current: Aggregate
    previous: Aggregate
    change_pct: float | None


class PlatformSection(BaseModel):
    platform: Platform
    has_data: bool
    sources: list[Source]
    followers: FollowerGrowth
    metrics: dict[Metric, MetricSummary]
    engagement_rate: EngagementRate
    engagement_rate_previous: EngagementRate
    posts_published: int
    top_posts: list[RankedPost]
    bottom_posts: list[RankedPost]
    posts_ranked_by: str
    format_mix: list[FormatShare]


class DataQualityItem(BaseModel):
    platform: Platform
    kind: str  # gap | partial | stale | not_authorized | unavailable_from_api | csv_import | ...
    metric: str | None = None
    detail: str


class DataQuality(BaseModel):
    items: list[DataQualityItem] = Field(default_factory=list)
    csv_sourced_metrics: list[str] = Field(default_factory=list)
    platforms_without_data: list[Platform] = Field(default_factory=list)


class Report(BaseModel):
    schema_version: str = REPORT_SCHEMA_VERSION
    tool_version: str = __version__
    run_id: str
    brand: str
    demo: bool = False
    generated_at: datetime
    period: Period
    comparison_period: Period
    platforms: list[PlatformSection]
    data_quality: DataQuality


def build_report(
    store: Store,
    brand: str,
    platforms: list[Platform],
    period: Period,
    metrics_cfg: MetricsConfig,
    run_id: str,
    demo: bool = False,
) -> Report:
    previous = period.previous()
    sections: list[PlatformSection] = []
    dq = DataQuality()
    for platform in platforms:
        daily = store.daily(brand, platform, previous.start, period.end)
        accounts = store.account_history(brand, platform, previous.start, period.end)
        posts = store.posts(brand, platform, period.start, period.end)
        has_data = bool(daily or accounts or posts)
        if not has_data:
            dq.platforms_without_data.append(platform)
            dq.items.append(
                DataQualityItem(
                    platform=platform,
                    kind="gap",
                    detail="No snapshots stored for this period. Run `social snapshot` "
                    "(or `social backfill`) or import a CSV.",
                )
            )

        summaries: dict[Metric, MetricSummary] = {}
        current_totals: dict[Metric, Aggregate] = {}
        previous_totals: dict[Metric, Aggregate] = {}
        sources: set[Source] = set()
        for metric in REPORTED_METRICS:
            cur = aggregate(daily, metric, period)
            prev = aggregate(daily, metric, previous)
            current_totals[metric], previous_totals[metric] = cur, prev
            summaries[metric] = MetricSummary(
                current=cur, previous=prev, change_pct=pct_change(cur, prev)
            )
            sources.update(cur.sources)
            if has_data:
                _note_quality(dq, platform, cur)

        rule = metrics_cfg.engagement_rate.get(platform)
        top, bottom, ranked_by = rank_posts(posts)
        for post in posts:
            sources.update(mv.source for mv in post.metrics.values() if mv.is_available)
        section = PlatformSection(
            platform=platform,
            has_data=has_data,
            sources=sorted(sources, key=lambda s: s.value),
            followers=follower_growth(accounts, daily, period),
            metrics=summaries,
            engagement_rate=engagement_rate(current_totals, rule),
            engagement_rate_previous=engagement_rate(previous_totals, rule),
            posts_published=len(posts),
            top_posts=top,
            bottom_posts=bottom,
            posts_ranked_by=ranked_by,
            format_mix=format_mix(posts),
        )
        sections.append(section)
        if Source.CSV_IMPORT in sources:
            dq.csv_sourced_metrics.extend(
                f"{platform.value}.{m.value}"
                for m, a in current_totals.items()
                if Source.CSV_IMPORT in a.sources
            )
        if platform in metrics_cfg.platforms:
            for change in metrics_cfg.platforms[platform].definition_changes:
                changed = date.fromisoformat(change.date)
                if previous.start <= changed <= period.end:
                    dq.items.append(
                        DataQualityItem(
                            platform=platform,
                            kind="definition_change",
                            metric=change.metric.value,
                            detail=f"{change.date}: {change.note} Comparisons across this date "
                            "are not like-for-like.",
                        )
                    )
    return Report(
        run_id=run_id,
        brand=brand,
        demo=demo,
        generated_at=datetime.now(UTC),
        period=period,
        comparison_period=previous,
        platforms=sections,
        data_quality=dq,
    )


def _note_quality(dq: DataQuality, platform: Platform, agg: Aggregate) -> None:
    if agg.complete:
        return
    if agg.value is None:
        kind = agg.availability.value
        detail = {
            Availability.UNAVAILABLE_FROM_API: "Not exposed by the platform API.",
            Availability.NOT_AUTHORIZED: "Not authorized: missing approval, scope or role.",
            Availability.STALE: "Not yet available (platform processing delay or no snapshot).",
            Availability.ERROR: "Fetching failed; see `social status`.",
        }.get(agg.availability, "Unavailable.")
    else:
        kind = "partial"
        detail = (
            f"Only {agg.days_available} of {agg.days_total} days available"
            + (
                f" ({agg.days_not_in_store} days never snapshotted)"
                if agg.days_not_in_store
                else ""
            )
            + "; total is partial."
        )
    dq.items.append(
        DataQualityItem(platform=platform, kind=kind, metric=agg.metric.value, detail=detail)
    )
