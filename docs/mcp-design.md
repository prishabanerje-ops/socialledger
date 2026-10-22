# Design notes: wrapping socialledger as a remote MCP server

socialledger does **not** ship an MCP server yet. The code is structured so one can be added
without touching platform logic.

## Layering

```
platform APIs ← connectors/ ← api.py (service layer) ← cli.py         (today)
                                                     ← skills/ (call the CLI)
                                                     ← mcp server     (future)
```

- `socialledger.api` exposes every operation as a plain function: `status`, `snapshot`,
  `backfill`, `report`, `connect` and, later, `audit`, `research` and `dashboard`.
  - The functions take plain arguments and return pydantic models, which are JSON-serialisable
    with `.model_dump(mode="json")`. They never print.
  - Secrets (`SecretStore`) and HTTP (`Http`) are passed in as parameters, so a server can
    supply its own implementations.
- Report math is deterministic and lives in `metrics.py` and `report.py`. An MCP tool returns
  `report.json`, and the model writes the narrative on top of it without changing the numbers.

## Proposed tool surface

| Tool | Wraps | Notes |
|---|---|---|
| `social_status` | `api.status` | read-only |
| `social_report` | `api.report` | returns `report.json`; files optional |
| `social_audit` | `api.audit` (phase 6) | |
| `social_research` | `api.research` (phase 7) | public/third-party data, labelled |
| `social_snapshot` | `api.snapshot` | might be scheduled server-side instead |

`connect` stays interactive. A remote server needs a web OAuth flow instead of the loopback
listener.

## What changes for a remote server

1. **Credentials per user.** Bring-your-own still applies: each user supplies their own app
   credentials. Implement `SecretStore` over a server-side KMS-encrypted store, keyed by the
   authenticated MCP user. Never share tokens between users.
2. **OAuth redirect.** Replace `auth/loopback.py` with a hosted HTTPS callback. Every user's
   developer app must then register that callback URL, and some platforms (Meta, LinkedIn,
   TikTok Business) require HTTPS.
3. **Storage.** Replace the single SQLite file with one database per user, or add a `user_id`
   column with row-level isolation. Platform retention rules (for example LinkedIn's 1-year
   limit) still apply.
4. **Read-only stays absolute.** Expose no tools that write to platforms. Keep the read-only
   test (every connector call is a GET or an OAuth token call).
5. **Data handling.** Hosting user analytics makes the operator a data processor, which brings
   privacy policy, deletion and platform terms obligations that the local tool avoids.
