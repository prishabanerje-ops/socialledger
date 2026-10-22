"""Canonical data model.

Every connector (API or CSV) maps its data into these models, so reports, audits and the
dashboard never depend on which platform or source the data came from.

Rule: a missing number is never zero. Every metric carries an ``Availability`` flag and a
``Source``; ``value`` is ``None`` whenever the metric is not ``available``.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Version of the ``report.json`` / ``manifest.json`` layout. Bump on breaking changes.
REPORT_SCHEMA_VERSION = "1.0"


class Platform(StrEnum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    LINKEDIN = "linkedin"
    TIKTOK = "tiktok"


PLATFORM_NAMES: dict[Platform, str] = {
    Platform.YOUTUBE: "YouTube",
    Platform.INSTAGRAM: "Instagram",
    Platform.FACEBOOK: "Facebook",
    Platform.LINKEDIN: "LinkedIn",
    Platform.TIKTOK: "TikTok",
}


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE_FROM_API = "unavailable_from_api"
    NOT_AUTHORIZED = "not_authorized"
    STALE = "stale"
    ERROR = "error"


class Source(StrEnum):
    API = "api"
    CSV_IMPORT = "csv_import"


class PostFormat(StrEnum):
    REEL = "reel"
    SHORT = "short"
    LONG_VIDEO = "long_video"
    LIVE = "live"
    VIDEO = "video"
    IMAGE = "image"
    CAROUSEL = "carousel"
    TEXT = "text"
    STORY = "story"
    UNKNOWN = "unknown"


class Metric(StrEnum):
    """Canonical metric names. Platform-specific names live in ``config/metrics.yaml``."""

    FOLLOWERS = "followers"
    FOLLOWERS_GAINED = "followers_gained"
    FOLLOWERS_LOST = "followers_lost"
    REACH = "reach"
    IMPRESSIONS = "impressions"
    VIEWS = "views"
    LIKES = "likes"
    COMMENTS = "comments"
    SHARES = "shares"
    SAVES = "saves"
    PROFILE_VISITS = "profile_visits"
    LINK_CLICKS = "link_clicks"
    WATCH_TIME_MINUTES = "watch_time_minutes"
    AVG_VIEW_DURATION_SECONDS = "avg_view_duration_seconds"


ENGAGEMENT_METRICS: tuple[Metric, ...] = (
    Metric.LIKES,
    Metric.COMMENTS,
    Metric.SHARES,
    Metric.SAVES,
)


class MetricValue(BaseModel):
    """One number plus where it came from and whether it can be trusted."""

    model_config = ConfigDict(frozen=True)

    value: float | None = None
    availability: Availability
    source: Source = Source.API
    note: str | None = None

    @model_validator(mode="after")
    def _value_matches_availability(self) -> Self:
        if self.availability is Availability.AVAILABLE and self.value is None:
            raise ValueError("an 'available' metric must have a value")
        if self.availability is not Availability.AVAILABLE and self.value is not None:
            raise ValueError(
                f"a metric flagged '{self.availability}' must not carry a value (no zero-filling)"
            )
        return self

    @classmethod
    def of(cls, value: float, source: Source = Source.API) -> MetricValue:
        return cls(value=float(value), availability=Availability.AVAILABLE, source=source)

    @classmethod
    def missing(
        cls, availability: Availability, note: str | None = None, source: Source = Source.API
    ) -> MetricValue:
        return cls(value=None, availability=availability, source=source, note=note)

    @property
    def is_available(self) -> bool:
        return self.availability is Availability.AVAILABLE


MetricMap = dict[Metric, MetricValue]


class Account(BaseModel):
    brand: str
    platform: Platform
    account_id: str
    handle: str | None = None
    followers: MetricValue
    as_of: date


class DailyMetrics(BaseModel):
    """Account-level metrics for one calendar day (platform's reporting timezone)."""

    brand: str
    platform: Platform
    account_id: str
    date: date
    metrics: MetricMap = Field(default_factory=dict)


class Post(BaseModel):
    """One published item with its metrics as observed on ``as_of``.

    Most platforms report lifetime-to-date post metrics, so the same post is re-observed on
    later snapshots; the store keeps each observation.
    """

    brand: str
    platform: Platform
    account_id: str
    post_id: str
    url: str | None = None
    published_at: datetime
    format: PostFormat = PostFormat.UNKNOWN
    title: str | None = None
    as_of: date
    metrics: MetricMap = Field(default_factory=dict)


class HealthStatus(StrEnum):
    CONNECTED = "connected"
    NOT_CONFIGURED = "not_configured"
    PENDING_APPROVAL = "pending_approval"
    CSV_ONLY = "csv_only"
    TOKEN_EXPIRED = "token_expired"  # noqa: S105
    ERROR = "error"


class Health(BaseModel):
    platform: Platform
    status: HealthStatus
    detail: str
    account_id: str | None = None
    handle: str | None = None
    token_expires_at: datetime | None = None
    access_gaps: list[str] = Field(default_factory=list)
