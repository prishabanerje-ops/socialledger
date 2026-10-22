# Instagram and Facebook (Meta Graph API)

**Status:** research notes. The connector arrives in phase 2. **Last verified:** 2026-10-22.
Items marked *UNVERIFIED* will be checked against a live app when the connector is built.

## Versions

- Latest is **v26.0**, released 2026-07-29. Live versions are v21.0 through v26.0; v20.0
  expired 2026-09-24. socialledger pins v26.0 in `config/metrics.yaml`.
  https://developers.facebook.com/docs/graph-api/changelog
- **From 2026-10-27, on all versions:**
  - `date_format` and `GET /?ids=` return errors.
  - `pretty` and `debug` are ignored.
  - ETag support is removed.
  - https://developers.facebook.com/docs/graph-api/changelog/version26.0

## Login path (planned)

The primary path is **Facebook Login for Business**, on `graph.facebook.com`.
- The Instagram professional account must be linked to a Facebook Page.
- One login covers both Instagram and the Page.
- It is the only path that exposes `media_product_type` and the `total_*` metrics.
- Scopes: `instagram_basic`, `instagram_manage_insights`, `pages_read_engagement`,
  `read_insights`, `pages_show_list`.
- https://developers.facebook.com/docs/instagram-platform/overview
- https://developers.facebook.com/docs/instagram-platform/insights

The alternate path is **Instagram API with Instagram Login**, on `graph.instagram.com`.
- It is for Business or Creator accounts with no Page.
- Scopes: `instagram_business_basic`, `instagram_business_manage_insights`.
- Tokens are long-lived (60 days) and refreshable with `ig_refresh_token` once at least 24
  hours old.
- https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token

## Access

- **Standard Access** (the default) covers accounts you own or manage and people with a role
  on the app. App Review isn't needed for that.
  https://developers.facebook.com/docs/instagram-platform/overview
- **Conflicting doc:** the Instagram Login setup page says App Review is required "before your
  app can access live data". This is checked live.
  https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/create-a-meta-app-with-instagram
- In Development mode, data is limited to people with a role on the app.
  https://developers.facebook.com/docs/development/build-and-test/app-modes
- The app must be **Business**-type. A business portfolio is only needed for system-user tokens.
  https://developers.facebook.com/docs/facebook-login/facebook-login-for-business

## Redirect URI risk

- Strict Mode and "Enforce HTTPS" are on by default.
  https://developers.facebook.com/docs/facebook-login/security
- Whether `http://localhost` is accepted is *UNVERIFIED*.
- **Fallback if it isn't accepted:**
  1. Paste a token generated in the App Dashboard or Graph API Explorer.
  2. socialledger exchanges it for a long-lived token with `fb_exchange_token` (about 60 days).
  3. socialledger derives Page tokens from it, which don't expire.
  - https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived

## Instagram metrics (current)

**User insights:**
- `reach`, `views`, `accounts_engaged`, `total_interactions`, `likes`, `comments`, `shares`,
  `saves`, `replies`, `reposts`, `profile_links_taps`, `follows_and_unfollows`, and the
  demographics metrics.
- Only `reach` supports `metric_type=time_series`.
- Data may lag up to 48 hours.
- Some metrics need 100 or more followers.
- https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/insights

**Deprecated user metrics:**
- `profile_views`, `website_clicks` and the contact-click metrics (2025-01-08).
  https://developers.facebook.com/docs/graph-api/changelog/version21.0
- `impressions` (2025-04-21). https://developers.facebook.com/docs/graph-api/changelog/version22.0

**Media insights:**
- **Feed:** `views`, `reach`, `likes`, `comments`, `shares`, `saved`, `total_interactions`,
  `profile_visits`.
- **Reels:** feed metrics plus `ig_reels_avg_watch_time`, `ig_reels_video_view_total_time`,
  `reels_skip_rate`.
- **Stories:** available for 24 hours only.
- Media insights are kept for 2 years.
- https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights

**Removed media metrics:** `plays`, `clips_replays_count`,
`ig_reels_aggregated_all_plays_count` (2025-04-21) and `video_views` (2025-01-08).

## Facebook Page metrics (current)

**Metrics to use:**
- Page level: `page_follows`, `page_daily_follows_unique`, `page_media_view`,
  `page_total_media_view_unique`, `page_post_engagements`, `page_views_total`.
- Post level: `post_media_view`, `post_total_media_view_unique`,
  `post_reactions_by_type_total`, `post_clicks`.

**Limits:** 2 years of history, at most 90 days per request, and the Page needs 100 or more
likes. https://developers.facebook.com/docs/graph-api/reference/insights

**Deprecations:**
- `page_fans` → `page_follows`
- `page_impressions` → `page_media_view`
- `*_impressions_unique` → `*_total_media_view_unique`
- https://developers.facebook.com/docs/platforminsights/page/deprecated-metrics
- https://developers.facebook.com/docs/graph-api/changelog/version25.0

## Rate limits and errors

- **Rate limits:**
  - App: 200 calls × users per rolling hour.
  - Business Use Case: 4800 × impressions (Instagram) or × engaged users (Pages) per 24 hours.
  - Headers: `X-App-Usage` and `X-Business-Use-Case-Usage`, which includes
    `estimated_time_to_regain_access`.
  - https://developers.facebook.com/docs/graph-api/overview/rate-limiting
- **Error codes:**
  - 4, 17, 32, 613, 80001 and 80002 are rate limits.
  - 190 means the token is invalid or expired (subcodes 458, 460, 463, 467, 492).
  - 10 and 200–299 are permission errors.
  - https://developers.facebook.com/docs/graph-api/guides/error-handling
