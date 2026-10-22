# YouTube

**Status:** implemented. **Last verified against official docs:** 2026-10-22.

## What you need

A Google Cloud project with an OAuth client of type **Desktop app**. It's free, and no Google
review is needed for personal use.

1. **Create or pick a project:** https://console.cloud.google.com/projectcreate
2. **Enable both APIs:**
   - YouTube Data API v3: https://console.cloud.google.com/apis/library/youtube.googleapis.com
   - YouTube Analytics API: https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com
3. **Configure the OAuth consent screen** (Google Auth Platform → Branding / Audience):
   - Choose user type **External** and add your Google account as a test user.
   - Then set publishing status to **In production**. While the app is in *Testing*, Google
     expires refresh tokens after **7 days** and you would have to reconnect weekly.
   - An app used only by you (or a few people you know) doesn't need verification. You will
     see an "unverified app" warning during sign-in, which you can accept. Unverified apps are
     capped at 100 users.
4. **Create the OAuth client** at https://console.cloud.google.com/auth/clients. Choose type
   **Desktop app**, then copy the client ID and client secret.
5. **Connect.** Run `social connect youtube --brand <name>` (or `social init`) and sign in with
   the Google account that owns the channel. For a Brand Account channel, pick the channel when
   prompted.

Scopes requested (read-only):
- `https://www.googleapis.com/auth/youtube.readonly`
- `https://www.googleapis.com/auth/yt-analytics.readonly`

`reports.query` now requires `youtube.readonly` in addition to the analytics scope.

## What socialledger collects

| Canonical metric | Source | Notes |
|---|---|---|
| followers | Data API `channels.list` `statistics.subscriberCount` | **Rounded down to 3 significant figures** by YouTube; "now" only, so trends come from daily snapshots |
| followers_gained / followers_lost | Analytics `subscribersGained` / `subscribersLost` | Daily |
| views | Analytics `views` (daily); Data API `viewCount` (per video, lifetime) | View definition changed (see below) |
| watch_time_minutes | Analytics `estimatedMinutesWatched` | |
| avg_view_duration_seconds | Analytics `averageViewDuration` | |
| likes, comments, shares | Analytics (daily and per video); Data API counters per video | `likeCount` can be hidden, in which case it's flagged, not zero |
| post format | Analytics `creatorContentType` (`SHORTS`, `VIDEO_ON_DEMAND`, `LIVE_STREAM`, `STORY`) | Official Shorts vs long-form split |
| impressions, thumbnail CTR | **Not available** | Only in the bulk YouTube Reporting API, which socialledger doesn't use |
| reach, saves, profile visits, link clicks | **Not available** | Not exposed by YouTube APIs |

Metric names live in `config/metrics.yaml`.

## Limits and behaviour

- **Quota.** 10,000 units/day by default, resetting at midnight Pacific Time. `channels.list`,
  `playlistItems.list` and `videos.list` cost 1 unit each, and a daily snapshot uses a handful.
  `quotaExceeded` (HTTP 403) is treated as final until the reset; it isn't retried.
- **Latency.** Analytics data usually lags 48–72 hours, and the most recent days are omitted.
  socialledger marks them `stale` and re-fetches the last 7 days on every snapshot.
- **Uploads.** The uploads playlist (`playlistItems.list`) is used, as YouTube recommends, not
  `search.list`.
- **Refresh tokens:**
  - expire after 6 months unused;
  - each client is limited to 100 per Google account;
  - expire after 7 days if the consent screen is in Testing.
- **View-definition changes:**
  - From 2025-03-31, Shorts views count every start or replay (`engagedViews` keeps the old
    definition).
  - From 2026-08-27 (Data API from 2026-08-24), view counting was aligned across long-form,
    Live and Shorts.
  - Reports spanning these dates show a `definition_change` data-quality note.
- **Unverified combination.** Combining `dimensions=video,creatorContentType` with a
  `video==` filter is checked in the live smoke test. If YouTube rejects it, socialledger
  falls back to per-video analytics without format and marks the format `unknown`.

## CSV fallback

YouTube Studio → Analytics → Advanced mode → Export. CSV import arrives in a later release.

## Sources (verified 2026-10-22)

- https://developers.google.com/youtube/v3/docs/channels
- https://developers.google.com/youtube/v3/docs/playlistItems/list
- https://developers.google.com/youtube/v3/docs/search/list
- https://developers.google.com/youtube/v3/docs/videos
- https://developers.google.com/youtube/v3/determine_quota_cost
- https://developers.google.com/youtube/v3/docs/errors
- https://developers.google.com/youtube/v3/guides/auth/installed-apps
- https://developers.google.com/youtube/analytics/reference/reports/query
- https://developers.google.com/youtube/analytics/metrics
- https://developers.google.com/youtube/analytics/dimensions
- https://developers.google.com/youtube/analytics/channel_reports
- https://developers.google.com/youtube/analytics/data_model
- https://developers.google.com/youtube/analytics/revision_history
- https://developers.google.com/youtube/reporting/v1/reports/channel_reports
- https://developers.google.com/identity/protocols/oauth2
- https://developers.google.com/identity/protocols/oauth2/native-app
- https://support.google.com/cloud/answer/7454865
