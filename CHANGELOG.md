# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Canonical schema with a per-metric availability flag (`available`, `unavailable_from_api`,
  `not_authorized`, `stale`, `error`) and source (`api`, `csv_import`). There is no
  zero-filling.
- SQLite snapshot store with idempotent upserts, a "failures never overwrite good data" rule,
  per-platform retention purge and a snapshot log.
- Shared HTTP client with backoff, `Retry-After` support and secret redaction.
- Secret storage in the OS keychain, with an encrypted-file fallback (Fernet + scrypt) and a
  warning.
- OAuth loopback flow with PKCE and state checking; request lines are never logged.
- YouTube connector (Data API v3 + Analytics API v2): channel, daily metrics, per-video
  metrics, and Shorts vs long-form via `creatorContentType`.
- `social` CLI with `init`, `connect`, `status`, `snapshot`, `backfill`, `report` and `demo`.
- Reports: `report.json` (schema 1.0) with a `data_quality` section, Markdown and
  self-contained HTML, a manifest with SHA-256 hashes, and optional PDF.
- CI for tests on Linux, macOS and Windows, lint, type check, gitleaks secret scan, and a
  build with a wheel smoke test.
