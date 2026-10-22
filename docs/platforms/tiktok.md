# TikTok

**Status:** research notes. The connector arrives in phase 4. **Last verified:** 2026-10-22.

## Planned route: Login Kit for desktop + Display API v2 (sandbox app)

- **Scopes:** `user.info.basic`, `user.info.profile`, `user.info.stats`, `video.list`.
  - `follower_count` and `likes_count` need `user.info.stats`; they moved off
    `user.info.basic` in 2024.
  - https://developers.tiktok.com/docs/en/tiktok-api-v2-get-user-info
  - https://developers.tiktok.com/bulletin/user-info-scope-migration
- **Videos:**
  - `POST /v2/video/list/` returns public posts only, at most 20 per page, with no date
    filter.
  - `POST /v2/video/query/` takes at most 20 IDs.
  - Fields: `view_count`, `like_count`, `comment_count`, `share_count`, `create_time`,
    `duration`.
  - https://developers.tiktok.com/docs/en/tiktok-api-v2-video-list
  - https://developers.tiktok.com/docs/en/tiktok-api-v2-video-object
- **Lifetime counters only:** there is no daily history, so socialledger builds daily deltas
  from its own snapshots.
- **Sandbox:** you can add your own TikTok account as a target user, up to 10 per sandbox.
  https://developers.tiktok.com/docs/en/add-a-sandbox
- **Production review** requires a public website and says "Apps must not be for private or
  personal use", so personal tools stay in sandbox.
  https://developers.tiktok.com/docs/en/app-review-guidelines
- **Desktop OAuth:**
  - Redirects to `localhost` / `127.0.0.1` over `http` are allowed (a port is required).
  - PKCE is required. The docs specify *hex*-encoded SHA-256, which differs from RFC 7636
    base64url, so both are tested live.
  - https://developers.tiktok.com/docs/en/login-kit-desktop
- **Tokens:** access tokens last 24 hours and refresh tokens 365 days.
  https://developers.tiktok.com/docs/en/oauth-user-access-token-management
- **Rate limit:** 600 requests per minute per endpoint.
  https://developers.tiktok.com/docs/en/tiktok-api-v2-rate-limit

## Not in v0.1: TikTok API for Business (Accounts API)

- Gives daily `video_views`, `profile_views`, follower changes and audience data.
- Many metrics need Advanced Access or a Verified Business Account, and since 2026-03-20 there
  is an extra access application form.
- The creator redirect must be static HTTPS, so there is no loopback.
- https://business-api.tiktok.com/portal/docs/get-profile-data-of-a-tiktok-account/v1.3
- https://business-api.tiktok.com/portal/docs/get-authorization-from-creators/v1.3

## CSV fallback

- TikTok Studio analytics can be downloaded "up to 365 days at a time".
  https://www.tiktok.com/creator-academy/en/article/tool-analytics-intro
- Column names aren't documented and will be verified against a real export.
