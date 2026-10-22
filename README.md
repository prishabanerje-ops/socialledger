# socialledger

Local, **read-only** reporting, audits and research for **your own** social media accounts:
YouTube, Instagram, Facebook, LinkedIn and TikTok.

`socialledger` installs a `social` command. It fetches organic performance data from official
platform APIs (or from CSV files the platforms let you export), stores daily snapshots in a
local SQLite database, and produces reports with every number computed in code and every data
gap shown explicitly.

> **Status: early development (0.1.0.dev).** Implemented so far: the core engine, the snapshot
> store, the YouTube connector, `social init` for YouTube, and Markdown/HTML reports. The other
> platforms, CSV import, the dashboard, audits, research and the Claude Code plugin follow, in
> that order (see [CHANGELOG.md](CHANGELOG.md)).

## Read this first: you bring your own credentials

Platforms only let *approved developer apps* read account analytics, and that approval belongs
to whoever registers the app. So socialledger does **not** come with a shared app, and
**nothing is hosted** by this project.

- You register your own (usually free) developer app on each platform you want to connect, and
  sign in to your own accounts through it. `social init` walks you through it step by step.
- Some platforms require an approval that many individuals won't have (LinkedIn's Community
  Management API, for example). For those, and for any platform you don't want to set up,
  socialledger supports **CSV import** of the analytics files the platforms let you export.
  *(CSV import arrives in a later release.)*
- Expect some setup per platform. It will not connect with zero setup.

## Your data stays on your machine

- Data, configuration and reports live in `~/.socialledger/` (change it with `SOCIALLEDGER_HOME`).
- Tokens and app secrets live in your **OS keychain**. If no keychain is available,
  socialledger uses an encrypted file and warns you clearly.
- Tokens are never written to config files, logs, reports, run manifests or the dashboard.
- **No telemetry.** The only outbound connections go to the platform APIs you configure (and,
  later, the optional third-party research provider if you enable it).
- **Read-only.** socialledger never posts, deletes or changes anything on any account.

## Platform access

Verified against official documentation on **2026-10-22**. Platforms change these rules often;
see `docs/platforms/` for sources.

| Platform | Official route | What you need | Approval likelihood for your own account | Fallback | socialledger status |
|---|---|---|---|---|---|
| YouTube | Data API v3 + Analytics API v2 | Google Cloud project, OAuth client of type "Desktop app" | High. No review needed for personal use; you'll see an "unverified app" screen | CSV export | **Implemented** |
| Instagram | Graph API v26.0 (Facebook Login for Business) | Meta Business-type app; IG professional account linked to a Facebook Page | High for accounts with a role on your app (Standard Access) | CSV export | Planned (next) |
| Facebook Pages | Graph API v26.0 Page Insights | Same Meta app; Page admin with insights access | High (Standard Access) | CSV export | Planned |
| LinkedIn Pages | Community Management API (`Linkedin-Version: 202610`) | LinkedIn app approved for Community Management; registered legal organisation; business email | **Low for individuals** (approval needed) | CSV/XLS export | Planned |
| TikTok | Display API v2 (Login Kit for desktop, sandbox app) | TikTok developer app in sandbox with your account as a target user | Medium. Sandbox works for your own account; production review rejects personal-use apps | CSV export | Planned |

## Quick start

```bash
pipx install socialledger            # or: uvx --from socialledger social --help
social demo                          # optional: load clearly-labelled synthetic data
social report --brand demo-brand --period monthly

social init                          # guided setup for your real accounts
social backfill                      # once: pull whatever history each API allows
social snapshot                      # daily: fetch the latest data (safe to re-run)
social report --brand my-brand --period weekly
social status                        # connections, token expiry, last snapshot, gaps
```

*(Until the first release is published to PyPI, install from a clone:
`uv tool install .` or `pipx install .`)*

### What `social init` does

1. Creates `~/.socialledger/brands.yaml` from the example and asks for a brand name.
2. For each platform you choose, prints the exact developer-portal steps and links, then asks
   whether to use the API or CSV import.
3. For the API, it asks for your app's client ID and secret (hidden input), opens your browser
   for consent, and receives the authorization on a **local loopback address** (`127.0.0.1`).
   No hosted server is involved.
4. Stores tokens in your OS keychain and runs a health check showing, per platform:
   connected, pending approval, or CSV-only, and why.

It is safe to run twice. Reconnect one platform any time with `social connect <platform>`.

## Reports

`social report --brand <name> --period weekly|monthly|custom [--start --end] [--platforms ...]`

- **Periods.** *weekly* is the 7 full days ending yesterday; *monthly* is the last full
  calendar month; *custom* takes `--start` and `--end`. Each is compared with the period of
  the same length immediately before it.
- **Output.** Each run writes `runs/<run-id>/` containing `manifest.json` (parameters and
  SHA-256 of each file), `report.json` (versioned schema), `report.md` and `report.html`.
  PDF is **optional**: `--format md,html,pdf` needs `pipx install 'socialledger[pdf]'`.
  Otherwise, print the HTML to PDF from your browser.
- **Computed in code.** All numbers come from code, never from an LLM: follower growth,
  views/reach, engagement rate, top and bottom posts, period-over-period change and format mix.
- **Gaps are never hidden.** A metric the API doesn't expose, isn't authorized for, or hasn't
  processed yet is shown as "—" with its reason in the `data_quality` section. It is never
  shown as zero. Period-over-period change is only computed when both periods are complete.

### Engagement-rate formulas

Defined per platform in `config/metrics.yaml` and recorded in every `report.json`:

| Platform | Formula |
|---|---|
| YouTube | (likes + comments + shares) ÷ views |
| Others | Documented as each connector lands |

## Why snapshots matter

Platforms limit how far back some metrics go. YouTube subscriber counts are only "now", for
example, and other platforms keep 90 days to 2 years. **The local store is the source of
truth for trends**, so run `social snapshot` daily (see [docs/scheduling.md](docs/scheduling.md)).
Snapshots are idempotent: re-running on the same day updates rather than duplicates, and a
failed fetch never overwrites good data. Where a platform's terms limit how long data may be
kept, socialledger enforces that retention automatically.

## Platform notes

- **YouTube:** see [docs/platforms/youtube.md](docs/platforms/youtube.md). Key points:
  - Set your Google OAuth consent screen to **In production**. In *Testing*, refresh tokens
    expire after 7 days.
  - Impressions and thumbnail CTR are not available through the APIs used here.
  - The subscriber count is rounded by YouTube.
  - YouTube changed how views are counted in 2025 and 2026, and reports flag comparisons
    that cross those dates.
- Research notes for Meta, LinkedIn and TikTok (verified, with sources) are in `docs/platforms/`.

## Designed to be wrapped as an MCP server (later)

All logic lives in the `socialledger` Python package. `socialledger.api` exposes every
operation as a plain function: arguments in, pydantic models out, nothing printed. The CLI
and the Claude Code skills are thin layers over it. A future remote MCP server can expose the
same functions as tools. See [docs/mcp-design.md](docs/mcp-design.md).

## Disclaimer

You are responsible for complying with each platform's developer terms and policies when you
register apps and use their APIs, including their data-storage and retention rules. This
project is not affiliated with or endorsed by Google, YouTube, Meta, LinkedIn or TikTok.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Licensed under the
[MIT License](LICENSE).
