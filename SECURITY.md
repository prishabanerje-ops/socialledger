# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report privately through
GitHub's [private vulnerability reporting](https://github.com/prishabanerje-ops/socialledger/security/advisories/new).

Include steps to reproduce, the affected version (`social --version`) and the impact. We aim
to acknowledge reports within 7 days.

## Scope and design commitments

- socialledger is **read-only**. Any code path that could write to a platform account is a
  security bug.
- Tokens and app secrets must only be stored in the OS keychain or the encrypted-file
  fallback. A token appearing in a config file, log, report, run manifest, dashboard,
  exception message or test fixture is a security bug.
- There is no telemetry. Any outbound connection other than the configured platform APIs and
  the optional research provider is a security bug.

## Supported versions

Only the latest release receives security fixes during the 0.x series.
