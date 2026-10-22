"""YouTube connector against hand-written synthetic API responses (never the live API)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx

from socialledger.auth.tokens import MemoryStore, OAuthToken, token_key
from socialledger.config import AccountConfig, BrandConfig
from socialledger.connectors.base import NotConfigured, QuotaExceeded, TokenExpired
from socialledger.connectors.youtube import (
    ANALYTICS_API,
    DATA_API,
    TOKEN_URL,
    YouTubeConnector,
)
from socialledger.http import Http
from socialledger.schema import Availability, HealthStatus, Metric, Platform, PostFormat
from tests.conftest import FAKE_ACCESS_TOKEN, load_fixture

BRAND = BrandConfig(name="test-brand", accounts=[AccountConfig(platform=Platform.YOUTUBE)])


def make(secrets: MemoryStore, http: Http) -> YouTubeConnector:
    return YouTubeConnector(BRAND, BRAND.accounts[0], secrets, http=http)


def mock_data_api(router: respx.MockRouter) -> None:
    router.get(f"{DATA_API}/channels").respond(json=load_fixture("youtube", "channels_mine.json"))
    router.get(f"{DATA_API}/playlistItems").respond(
        json=load_fixture("youtube", "playlist_items.json")
    )
    router.get(f"{DATA_API}/videos").respond(json=load_fixture("youtube", "videos.json"))


@respx.mock
def test_fetch_account(youtube_secrets: MemoryStore, no_sleep_http: Http) -> None:
    mock_data_api(respx.mock)
    acct = make(youtube_secrets, no_sleep_http).fetch_account(date(2026, 10, 4))
    assert acct.account_id == "UCsynthetic00000000000001"
    assert acct.followers.value == 12300
    assert "3 significant figures" in (acct.followers.note or "")
    sent = respx.calls.last.request
    assert sent.headers["Authorization"] == f"Bearer {FAKE_ACCESS_TOKEN}"


@respx.mock
def test_daily_metrics_mapping_and_gaps(youtube_secrets: MemoryStore, no_sleep_http: Http) -> None:
    mock_data_api(respx.mock)
    route = respx.get(ANALYTICS_API).respond(json=load_fixture("youtube", "analytics_daily.json"))
    rows = make(youtube_secrets, no_sleep_http).fetch_daily_metrics(
        date(2026, 10, 1), date(2026, 10, 4)
    )
    params = route.calls.last.request.url.params
    assert params["ids"] == "channel==MINE" and params["dimensions"] == "day"
    assert "estimatedMinutesWatched" in params["metrics"]
    first = rows[0].metrics
    assert first[Metric.VIEWS].value == 1000
    assert first[Metric.WATCH_TIME_MINUTES].value == 2000
    assert first[Metric.FOLLOWERS_GAINED].value == 20
    # Not exposed by the API -> explicit flag, never zero
    assert first[Metric.IMPRESSIONS].availability is Availability.UNAVAILABLE_FROM_API
    assert first[Metric.IMPRESSIONS].value is None
    # A day with no row is a gap, not a zero
    missing = rows[3].metrics[Metric.VIEWS]
    assert missing.value is None


@respx.mock
def test_posts_use_creator_content_type(youtube_secrets: MemoryStore, no_sleep_http: Http) -> None:
    mock_data_api(respx.mock)
    route = respx.get(ANALYTICS_API).respond(json=load_fixture("youtube", "analytics_videos.json"))
    posts = make(youtube_secrets, no_sleep_http).fetch_posts(
        date(2026, 10, 1), date(2026, 10, 4), date(2026, 10, 10)
    )
    by_id = {p.post_id: p for p in posts}
    assert set(by_id) == {"vidSynthA01", "vidSynthB02"}  # old + private videos excluded
    assert by_id["vidSynthA01"].format is PostFormat.SHORT
    assert by_id["vidSynthB02"].format is PostFormat.LONG_VIDEO
    # Lifetime counters come from the Data API; hidden likeCount is flagged, not zero
    assert by_id["vidSynthB02"].metrics[Metric.VIEWS].value == 8000
    assert by_id["vidSynthB02"].metrics[Metric.LIKES].value is None
    assert by_id["vidSynthA01"].metrics[Metric.SHARES].value == 30
    params = route.calls.last.request.url.params
    assert params["dimensions"] == "video,creatorContentType"
    assert params["filters"].startswith("video==")


@respx.mock
def test_posts_fall_back_when_content_type_rejected(
    youtube_secrets: MemoryStore, no_sleep_http: Http
) -> None:
    mock_data_api(respx.mock)
    video_only = load_fixture("youtube", "analytics_videos.json")
    video_only["columnHeaders"].pop(1)
    for row in video_only["rows"]:
        row.pop(1)
    respx.get(ANALYTICS_API).mock(
        side_effect=[
            httpx.Response(
                400, json={"error": {"message": "bad combo", "errors": [{"reason": "badRequest"}]}}
            ),
            httpx.Response(200, json=video_only),
        ]
    )
    posts = make(youtube_secrets, no_sleep_http).fetch_posts(
        date(2026, 10, 1), date(2026, 10, 4), date(2026, 10, 10)
    )
    assert all(p.format is PostFormat.UNKNOWN for p in posts)
    assert {p.post_id: p for p in posts}["vidSynthA01"].metrics[Metric.SHARES].value == 30


@respx.mock
def test_quota_exceeded_is_terminal(youtube_secrets: MemoryStore, no_sleep_http: Http) -> None:
    respx.get(f"{DATA_API}/channels").respond(403, json=load_fixture("youtube", "error_quota.json"))
    with pytest.raises(QuotaExceeded, match="midnight Pacific"):
        make(youtube_secrets, no_sleep_http).fetch_account(date(2026, 10, 4))


@respx.mock
def test_expired_token_is_refreshed(youtube_secrets: MemoryStore, no_sleep_http: Http) -> None:
    youtube_secrets.set_model(
        token_key("youtube", "test-brand"),
        OAuthToken(
            access_token="old",
            refresh_token="1//r",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        ),
    )
    token_route = respx.post(TOKEN_URL).respond(
        json={"access_token": "new-token-abc", "expires_in": 3599}
    )
    mock_data_api(respx.mock)
    make(youtube_secrets, no_sleep_http).fetch_account(date(2026, 10, 4))
    form = token_route.calls.last.request.content.decode()
    assert "grant_type=refresh_token" in form
    stored = youtube_secrets.get_model(token_key("youtube", "test-brand"), OAuthToken)
    assert stored is not None and stored.access_token == "new-token-abc"
    assert stored.refresh_token == "1//r"  # Google omits it on refresh; we keep the old one


@respx.mock
def test_revoked_refresh_token_explains_testing_mode(
    youtube_secrets: MemoryStore, no_sleep_http: Http
) -> None:
    youtube_secrets.set_model(
        token_key("youtube", "test-brand"),
        OAuthToken(
            access_token="old",
            refresh_token="1//r",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        ),
    )
    respx.post(TOKEN_URL).respond(400, json={"error": "invalid_grant"})
    with pytest.raises(TokenExpired, match="7 days"):
        make(youtube_secrets, no_sleep_http).fetch_account(date(2026, 10, 4))


def test_health_not_configured(no_sleep_http: Http) -> None:
    health = make(MemoryStore(), no_sleep_http).health_check()
    assert health.status is HealthStatus.NOT_CONFIGURED
    with pytest.raises(NotConfigured):
        make(MemoryStore(), no_sleep_http).fetch_account(date(2026, 10, 4))


@respx.mock
def test_health_connected(youtube_secrets: MemoryStore, no_sleep_http: Http) -> None:
    mock_data_api(respx.mock)
    health = make(youtube_secrets, no_sleep_http).health_check()
    assert health.status is HealthStatus.CONNECTED
    assert health.account_id == "UCsynthetic00000000000001"
    assert any("Reporting API" in gap for gap in health.access_gaps)
