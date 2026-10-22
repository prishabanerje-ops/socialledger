# Contributing

Thanks for helping! A few ground rules keep this project safe and trustworthy.

## Setup

```bash
uv sync                      # Python 3.11+
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## Rules

1. **Never commit real account data.** That includes real IDs, handles, tokens or API
   responses. Tests use hand-written synthetic fixtures (`tests/fixtures/`). If you base a
   fixture on a real response, scrub every identifier and number first.
2. **Never guess API details.** When adding or changing a connector, read the platform's
   current official documentation. Cite the URLs, with the date verified, in the connector's
   docstring and in `docs/platforms/<platform>.md`. Mark anything you couldn't confirm as
   *UNVERIFIED*.
3. **Metric names belong in `config/metrics.yaml`**, not in code. Pin API versions there too.
4. **Read-only.** Connectors may only call read endpoints and OAuth token endpoints.
5. **No zero-filling.** A missing metric is a `MetricValue` with an availability flag, never
   `0`.
6. **No new dependencies** without a justification in the pull request.
7. **Live API tests** go in `tests/live/`. They are opt-in (`SOCIALLEDGER_LIVE=1`) and never
   run in CI.

## Commits and changelog

Update `CHANGELOG.md` under "Unreleased" for user-visible changes.
