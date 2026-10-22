# LinkedIn Pages (Community Management API)

**Status:** research notes. The connector arrives in phase 4. **Last verified:** 2026-10-22.

## Access: most individuals won't get it

- Page analytics require the **Community Management API**. Every app starts on the
  **Development** tier: 500 calls per app and 100 per member per 24 hours, no batch calls, and
  you must finish building within 12 months.
  https://learn.microsoft.com/en-us/linkedin/marketing/increasing-access
- The **Standard** tier needs a screencast of every use case, test credentials and a privacy
  policy. https://learn.microsoft.com/en-us/linkedin/marketing/community-management-app-review
- **Eligibility:** "registered legal organizations for commercial use cases only". You also
  need a verified business email and a Page super-admin to verify the app.
- Development tier can only be requested on a new app that has no other products.
  https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview
- **What socialledger does without approval:** the API connector reports `not_authorized`,
  and LinkedIn data comes from the Page admin's **analytics export** (Visitors, Content,
  Followers and Competitors, as XLS). https://www.linkedin.com/help/linkedin/answer/a551206/
  The export's column names aren't documented and will be verified against a real export.

## API facts (for approved apps)

- **Base URL and version:** `https://api.linkedin.com/rest/` with header
  `Linkedin-Version: 202610`, which is supported until 2027-10-15.
  https://learn.microsoft.com/en-us/linkedin/marketing/versioning
  https://learn.microsoft.com/en-us/linkedin/marketing/integrations/migrations
- **Follower statistics:** `organizationalEntityFollowerStatistics`, daily `followerGains`
  (organic/paid). 12 months of history, about 2 days' lag.
  https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations/follower-statistics
- **Page statistics:** `organizationPageStatistics` (page views).
  https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations/page-statistics
- **Share statistics:** `organizationalEntityShareStatistics`. Fields: `impressionCount`,
  `uniqueImpressionsCount`, `clickCount`, `likeCount`, `commentCount`, `shareCount`,
  `engagement`. Organic only; rolling 12 months.
  https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations/share-statistics
- **Follower count:** `networkSizes/urn:li:organization:{id}?edgeType=COMPANY_FOLLOWED_BY_MEMBER`.
  https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations/organization-lookup-api
- **Posts:** `posts?q=author` (`r_organization_social`).
  https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
- **Scopes:** the statistics endpoints require `rw_organization_admin`. socialledger only ever
  issues GET requests with it.
- **Tokens:** access tokens last 60 days. Refresh tokens are only for approved partners, so
  otherwise you sign in again.
  https://learn.microsoft.com/en-us/linkedin/shared/authentication/programmatic-refresh-tokens
- **Redirect URIs:** "HTTPS" is documented, and `http://localhost` is *UNVERIFIED*.
  https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow
- **Errors:** 401 means an invalid or expired token, 403 means a missing scope or role, 426
  means a sunset version, and 429 means rate limited (limits reset at midnight UTC).
  https://learn.microsoft.com/en-us/linkedin/shared/api-guide/concepts/error-handling

## Data retention (enforced)

Page admin and reporting data may be stored for **at most one year**, so socialledger purges
LinkedIn rows older than 365 days on every snapshot.
https://learn.microsoft.com/en-us/linkedin/marketing/data-storage-requirements
