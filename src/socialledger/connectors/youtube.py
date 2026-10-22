"""YouTube connector: YouTube Data API v3 + YouTube Analytics API v2, via Google OAuth.

Documentation verified 2026-10-22:

* Data API channels (subscriberCount rounded to 3 significant figures; uploads playlist):
  https://developers.google.com/youtube/v3/docs/channels
* Uploads listing (use playlistItems, not search.list):
  https://developers.google.com/youtube/v3/docs/playlistItems/list
  https://developers.google.com/youtube/v3/docs/search/list
* Video resource and statistics: https://developers.google.com/youtube/v3/docs/videos
* Quota costs (1 unit for channels/playlistItems/videos.list; 10,000/day default):
  https://developers.google.com/youtube/v3/determine_quota_cost
* Errors (quotaExceeded = HTTP 403): https://developers.google.com/youtube/v3/docs/errors
* Analytics reports.query (requires youtube.readonly in addition to yt-analytics.readonly;
  ids=channel==MINE; YYYY-MM-DD dates; rows omitted when empty):
  https://developers.google.com/youtube/analytics/reference/reports/query
* Metrics: https://developers.google.com/youtube/analytics/metrics
* Dimensions (creatorContentType: SHORTS, VIDEO_ON_DEMAND, LIVE_STREAM, STORY, UNSPECIFIED):
  https://developers.google.com/youtube/analytics/dimensions
* Channel reports (time-based day report; top-videos sort + maxResults<=200):
  https://developers.google.com/youtube/analytics/channel_reports
* Data latency (48-72h; most recent days omitted): https://developers.google.com/youtube/analytics/data_model
* View-definition changes (2025-03-31, 2026-08-27):
  https://developers.google.com/youtube/analytics/revision_history
* OAuth for desktop apps (loopback redirect, PKCE S256, refresh tokens):
  https://developers.google.com/identity/protocols/oauth2/native-app
* Refresh-token expiry (7 days in "Testing" publishing status; 6 months unused; 100/client):
  https://developers.google.com/identity/protocols/oauth2

Not used: impressions / thumbnail CTR, which exist only in the bulk YouTube Reporting API
(https://developers.google.com/youtube/reporting/v1/reports/channel_reports).

Unverified (checked during the live smoke test): combining ``dimensions=video,creatorContentType``
with a ``video==`` filter. If YouTube rejects it, the connector falls back to video-only
analytics and marks the post format ``unknown``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar

from socialledger.auth.loopback import run_loopback_flow
from socialledger.auth.tokens import AppCredentials, OAuthToken, SecretStore, app_key
from socialledger.connectors.base import (
    Announce,
    Connector,
    ConnectorError,
    NotAuthorized,
    NotConfigured,
    QuotaExceeded,
    TokenExpired,
    date_range,
)
from socialledger.http import Http, register_secret
from socialledger.schema import (
    Account,
    Availability,
    DailyMetrics,
    Health,
    HealthStatus,
    Metric,
    MetricMap,
    MetricValue,
    Platform,
    Post,
    PostFormat,
)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint, not a secret
DATA_API = "https://www.googleapis.com/youtube/v3"
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"
SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)
_VIDEO_FILTER_BATCH = 200
_TESTING_MODE_HINT = (
    "If your Google OAuth consent screen is in 'Testing' status, refresh tokens expire after "
    "7 days. Set the app to 'In production' (personal use does not need verification)."
)


class BadRequest(ConnectorError):
    pass


class YouTubeConnector(Connector):
    platform: ClassVar[Platform] = Platform.YOUTUBE
    credential_namespace: ClassVar[str] = "youtube"
    _channel_cache: dict[str, Any] | None = None

    @property
    def api_version(self) -> str:
        return "data-v3/analytics-v2"

    # ---- auth ---------------------------------------------------------------------------

    @staticmethod
    def authorize(
        secrets: SecretStore,
        app: AppCredentials,
        http: Http | None = None,
        announce: Announce = print,
        **flow_kwargs: Any,
    ) -> OAuthToken:
        """Browser consent via loopback redirect + PKCE; stores app credentials and token."""
        http = http or Http()
        register_secret(app.client_secret)
        result = run_loopback_flow(
            AUTH_URL,
            {
                "client_id": app.client_id,
                "scope": " ".join(SCOPES),
                "access_type": "offline",
                "prompt": "consent",
            },
            announce=announce,
            **flow_kwargs,
        )
        form = {
            "grant_type": "authorization_code",
            "code": result.code,
            "client_id": app.client_id,
            "redirect_uri": result.redirect_uri,
        }
        if app.client_secret:
            form["client_secret"] = app.client_secret
        if result.pkce:
            form["code_verifier"] = result.pkce.verifier
        token = OAuthToken.from_response(_token_call(http, form))
        register_secret(token.access_token)
        register_secret(token.refresh_token)
        secrets.set_model(app_key("youtube"), app)
        return token

    def _access_token(self) -> str:
        token = self.stored_token()
        if token.expires_within(timedelta(minutes=2)):
            token = self._refresh(token)
        register_secret(token.access_token)
        return token.access_token

    def _refresh(self, token: OAuthToken) -> OAuthToken:
        if not token.refresh_token:
            raise TokenExpired("YouTube access expired. Run `social connect youtube`.")
        app = self.app_credentials()
        form = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": app.client_id,
        }
        if app.client_secret:
            form["client_secret"] = app.client_secret
        try:
            data = _token_call(self.http, form)
        except TokenExpired as exc:
            raise TokenExpired(
                f"YouTube refresh token is expired or revoked. Run `social connect youtube`. "
                f"{_TESTING_MODE_HINT}"
            ) from exc
        new = OAuthToken.from_response(data)
        new.refresh_token = new.refresh_token or token.refresh_token
        self.save_token(new)
        return new

    def _get(self, url: str, params: dict[str, Any]) -> Any:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        response = self.http.request("GET", url, params=params, headers=headers)
        if response.status_code == 401:
            token = self._refresh(self.stored_token())
            headers = {"Authorization": f"Bearer {token.access_token}"}
            response = self.http.request("GET", url, params=params, headers=headers)
        if response.is_error:
            raise _translate_error(response.status_code, _safe_json(response))
        return response.json()

    # ---- interface ----------------------------------------------------------------------

    def health_check(self) -> Health:
        gaps = [
            "Impressions and thumbnail CTR are only in the bulk YouTube Reporting API "
            "(not used); reported as unavailable_from_api.",
            "Reach, saves, profile visits and link clicks are not exposed by YouTube APIs.",
        ]
        try:
            channel = self._channel()
        except NotConfigured as exc:
            return Health(
                platform=self.platform,
                status=HealthStatus.NOT_CONFIGURED,
                detail=str(exc),
                access_gaps=gaps,
            )
        except TokenExpired as exc:
            return Health(
                platform=self.platform,
                status=HealthStatus.TOKEN_EXPIRED,
                detail=str(exc),
                access_gaps=gaps,
            )
        except ConnectorError as exc:
            return Health(
                platform=self.platform, status=HealthStatus.ERROR, detail=str(exc), access_gaps=gaps
            )
        token = self.stored_token()
        snippet = channel.get("snippet", {})
        return Health(
            platform=self.platform,
            status=HealthStatus.CONNECTED,
            detail=f"Connected to channel '{snippet.get('title', channel['id'])}'.",
            account_id=channel["id"],
            handle=snippet.get("customUrl"),
            token_expires_at=token.refresh_expires_at,
            access_gaps=[*gaps, _TESTING_MODE_HINT],
        )

    def fetch_account(self, as_of: date) -> Account:
        channel = self._channel()
        stats = channel.get("statistics", {})
        if stats.get("hiddenSubscriberCount") or "subscriberCount" not in stats:
            followers = MetricValue.missing(
                Availability.UNAVAILABLE_FROM_API, "Subscriber count is hidden on this channel."
            )
        else:
            followers = MetricValue(
                value=float(stats["subscriberCount"]),
                availability=Availability.AVAILABLE,
                note=self.metrics.followers_note,
            )
        return Account(
            brand=self.brand.name,
            platform=self.platform,
            account_id=channel["id"],
            handle=channel.get("snippet", {}).get("customUrl"),
            followers=followers,
            as_of=as_of,
        )

    def fetch_daily_metrics(self, start: date, end: date) -> list[DailyMetrics]:
        names = self.metrics.api_names("daily")
        report = self._get(
            ANALYTICS_API,
            {
                "ids": "channel==MINE",
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "metrics": ",".join(names.values()),
                "dimensions": "day",
                "sort": "day",
            },
        )
        rows = {r["day"]: r for r in _rows(report)}
        account_id = self._account_id()
        processed_until = date.today() - timedelta(days=self.metrics.latency_days)
        out: list[DailyMetrics] = []
        for day in date_range(start, end):
            row = rows.get(day.isoformat())
            metrics: MetricMap = {}
            for metric, api_name in names.items():
                if row is not None and row.get(api_name) is not None:
                    metrics[metric] = MetricValue.of(row[api_name])
                elif day > processed_until:
                    metrics[metric] = MetricValue.missing(
                        Availability.STALE, "YouTube Analytics has not processed this day yet."
                    )
                else:
                    metrics[metric] = MetricValue.missing(
                        Availability.UNAVAILABLE_FROM_API, "YouTube Analytics returned no row."
                    )
            out.append(
                DailyMetrics(
                    brand=self.brand.name,
                    platform=self.platform,
                    account_id=account_id,
                    date=day,
                    metrics=self.complete("daily", metrics),
                )
            )
        return out

    def fetch_posts(self, start: date, end: date, as_of: date) -> list[Post]:
        channel = self._channel()
        uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        video_ids = [
            vid
            for vid, published in self._uploads(uploads, start)
            if start <= published.date() <= end
        ]
        if not video_ids:
            return []
        videos = {v["id"]: v for v in self._videos(video_ids)}
        earliest = min(_published(v).date() for v in videos.values())
        analytics, format_known = self._video_analytics(list(videos), earliest, as_of)

        posts: list[Post] = []
        for vid, video in videos.items():
            stats = video.get("statistics", {})
            row = analytics.get(vid, {})
            metrics: MetricMap = {}
            # Real-time lifetime counters from the Data API.
            for metric, key in (
                (Metric.VIEWS, "viewCount"),
                (Metric.LIKES, "likeCount"),
                (Metric.COMMENTS, "commentCount"),
            ):
                metrics[metric] = (
                    MetricValue.of(float(stats[key]))
                    if key in stats
                    else MetricValue.missing(
                        Availability.UNAVAILABLE_FROM_API, f"{key} hidden or not returned"
                    )
                )
            # Analytics-only metrics (lag 48-72h).
            for metric, api_name in self.metrics.api_names("post").items():
                if metric in metrics:
                    continue
                if row.get(api_name) is not None:
                    metrics[metric] = MetricValue.of(row[api_name])
                else:
                    metrics[metric] = MetricValue.missing(
                        Availability.STALE
                        if _published(video).date()
                        > as_of - timedelta(days=self.metrics.latency_days)
                        else Availability.UNAVAILABLE_FROM_API,
                        "No YouTube Analytics data for this video yet.",
                    )
            fmt = PostFormat.UNKNOWN
            if format_known and row.get("creatorContentType"):
                fmt = self.metrics.formats.get(row["creatorContentType"], PostFormat.UNKNOWN)
            posts.append(
                Post(
                    brand=self.brand.name,
                    platform=self.platform,
                    account_id=channel["id"],
                    post_id=vid,
                    url=f"https://www.youtube.com/watch?v={vid}",
                    published_at=_published(video),
                    format=fmt,
                    title=video.get("snippet", {}).get("title"),
                    as_of=as_of,
                    metrics=self.complete("post", metrics),
                )
            )
        return posts

    # ---- API helpers --------------------------------------------------------------------

    def _channel(self) -> dict[str, Any]:
        if self._channel_cache is None:
            data = self._get(
                f"{DATA_API}/channels",
                {"part": "snippet,statistics,contentDetails", "mine": "true"},
            )
            items = data.get("items") or []
            if not items:
                raise NotAuthorized(
                    "The signed-in Google account has no YouTube channel. Sign in with the "
                    "account (or Brand Account) that owns the channel."
                )
            self._channel_cache = items[0]
        return self._channel_cache

    def _account_id(self) -> str:
        return self.account.account_id or str(self._channel()["id"])

    def _uploads(self, playlist_id: str, since: date) -> Iterator[tuple[str, datetime]]:
        """Yield (video_id, published_at), newest first, until past ``since``."""
        page_token: str | None = None
        while True:
            params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50}
            if page_token:
                params["pageToken"] = page_token
            data = self._get(f"{DATA_API}/playlistItems", params)
            items = data.get("items") or []
            older = 0
            for item in items:
                details = item.get("contentDetails", {})
                published = details.get("videoPublishedAt")
                if not published:
                    continue  # private/deleted videos have no publish time
                ts = _parse_ts(published)
                if ts.date() < since:
                    older += 1
                yield details["videoId"], ts
            page_token = data.get("nextPageToken")
            # Stop once a whole page predates the range (uploads are newest first).
            if not page_token or (items and older == len(items)):
                return

    def _videos(self, ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(0, len(ids), 50):
            data = self._get(
                f"{DATA_API}/videos",
                {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i : i + 50])},
            )
            out.extend(data.get("items") or [])
        return out

    def _video_analytics(
        self, ids: list[str], start: date, end: date
    ) -> tuple[dict[str, dict[str, Any]], bool]:
        names = self.metrics.api_names("post")
        metrics = ",".join(names.values())
        by_video: dict[str, dict[str, Any]] = {}
        format_known = True
        for i in range(0, len(ids), _VIDEO_FILTER_BATCH):
            batch = ids[i : i + _VIDEO_FILTER_BATCH]
            params = {
                "ids": "channel==MINE",
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "metrics": metrics,
                "dimensions": "video,creatorContentType",
                "filters": "video==" + ",".join(batch),
                "sort": "-views",
                "maxResults": _VIDEO_FILTER_BATCH,
            }
            try:
                report = self._get(ANALYTICS_API, params)
            except BadRequest:
                format_known = False
                params["dimensions"] = "video"
                report = self._get(ANALYTICS_API, params)
            for row in _rows(report):
                by_video[row["video"]] = row
        return by_video, format_known


# ---- module helpers ---------------------------------------------------------------------


def _token_call(http: Http, form: dict[str, str]) -> dict[str, Any]:
    response = http.request("POST", TOKEN_URL, data=form)
    body = _safe_json(response)
    if response.is_error:
        error = body.get("error") if isinstance(body, dict) else None
        if error == "invalid_grant":
            raise TokenExpired("Google rejected the grant (expired or revoked).")
        raise ConnectorError(f"Google token endpoint returned HTTP {response.status_code}: {error}")
    if not isinstance(body, dict) or "access_token" not in body:
        raise ConnectorError("Google token endpoint returned an unexpected response.")
    return body


def _safe_json(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _translate_error(status: int, body: Any) -> ConnectorError:
    err = body.get("error", {}) if isinstance(body, dict) else {}
    errors = err.get("errors") or [{}]
    reason = errors[0].get("reason", "") if isinstance(errors[0], dict) else ""
    message = err.get("message", "") if isinstance(err, dict) else ""
    if reason in {"quotaExceeded", "dailyLimitExceeded"}:
        return QuotaExceeded(
            "YouTube API daily quota exceeded. It resets at midnight Pacific Time; "
            "re-run `social snapshot` after that."
        )
    if status == 401:
        return TokenExpired("YouTube rejected the access token. Run `social connect youtube`.")
    if status == 403:
        return NotAuthorized(
            f"YouTube denied access ({reason or 'forbidden'}): {message}. Check that the YouTube "
            "Data API v3 and YouTube Analytics API are enabled in your Google Cloud project."
        )
    if status == 400:
        return BadRequest(f"YouTube API rejected the query ({reason or 'badRequest'}): {message}")
    return ConnectorError(f"YouTube API error HTTP {status} {reason or ''}: {message}".strip())


def _rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    headers = [h["name"] for h in report.get("columnHeaders", [])]
    return [dict(zip(headers, row, strict=False)) for row in report.get("rows") or []]


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _published(video: dict[str, Any]) -> datetime:
    return _parse_ts(video["snippet"]["publishedAt"])
