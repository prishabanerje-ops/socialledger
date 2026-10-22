"""The `social` command. Parses arguments, calls ``socialledger.api``, prints results."""

from __future__ import annotations

import json
import sys
from datetime import date
from typing import Annotated, Any

import typer

from socialledger import __version__, api
from socialledger.auth.loopback import OAuthFlowError
from socialledger.auth.tokens import SecretStoreError
from socialledger.config import ConfigError
from socialledger.connectors.base import ConnectorError
from socialledger.http import HttpError, redact
from socialledger.schema import HealthStatus, Platform

app = typer.Typer(
    name="social",
    help="Read-only reporting, audits and research for your own social accounts. "
    "Data stays on this machine.",
    no_args_is_help=True,
    add_completion=False,
    # Never print local variables in tracebacks: they can hold tokens.
    pretty_exceptions_show_locals=False,
)

BrandOpt = Annotated[str | None, typer.Option("--brand", "-b", help="Brand name from brands.yaml.")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]


def _print_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


def _version(value: bool) -> None:
    if value:
        typer.echo(f"socialledger {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show version and exit."),
    ] = False,
) -> None:
    pass


@app.command()
def init() -> None:
    """Guided setup: create your brand config and connect platforms. Safe to re-run."""
    from socialledger import wizard

    wizard.run()


@app.command()
def connect(
    platform: Annotated[Platform, typer.Argument(help="Platform to connect.")],
    brand: Annotated[str, typer.Option("--brand", "-b", prompt="Brand name")],
) -> None:
    """(Re)connect one platform for a brand. Safe to run again to refresh access."""
    from socialledger import wizard

    wizard.connect_interactive(platform, brand)


@app.command()
def status(
    offline: Annotated[bool, typer.Option("--offline", help="Skip live API checks.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Connection health, token expiry, last snapshot and known access gaps."""
    rows = api.status(check_live=not offline)
    if as_json:
        _print_json([r.model_dump(mode="json") for r in rows])
        return
    if not rows:
        typer.echo("No accounts configured. Run `social init`.")
    for r in rows:
        h = r.health
        color = (
            typer.colors.GREEN
            if h.status is HealthStatus.CONNECTED
            else (typer.colors.CYAN if h.status is HealthStatus.CSV_ONLY else typer.colors.YELLOW)
        )
        typer.secho(
            f"{r.brand} / {r.platform.value} [{r.mode}]: {h.status.value}", fg=color, bold=True
        )
        typer.echo(f"   {h.detail}")
        if h.token_expires_at:
            typer.echo(f"   refresh token expires: {h.token_expires_at:%Y-%m-%d}")
        typer.echo(f"   last successful snapshot: {r.last_snapshot or 'never'}")
        if r.last_error and (not r.last_snapshot or r.last_error > r.last_snapshot):
            typer.echo(f"   last error: {r.last_error}")
        for gap in h.access_gaps:
            typer.echo(f"   gap: {gap}")


@app.command()
def snapshot(brand: BrandOpt = None, as_json: JsonOpt = False) -> None:
    """Fetch the latest data for all API-connected accounts (idempotent; cron-safe)."""
    outcomes = api.snapshot(brand)
    _report_outcomes(outcomes, as_json)


@app.command()
def backfill(
    brand: BrandOpt = None,
    days: Annotated[int | None, typer.Option(help="Cap history to this many days.")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Pull as much history as each platform API allows (run once after connecting)."""
    outcomes = api.backfill(brand, days=days)
    _report_outcomes(outcomes, as_json)


def _report_outcomes(outcomes: list[api.SnapshotOutcome], as_json: bool) -> None:
    if as_json:
        _print_json([o.model_dump(mode="json") for o in outcomes])
    elif not outcomes:
        typer.echo("Nothing to fetch: no API-connected accounts. Run `social init`.")
    for o in outcomes if not as_json else []:
        mark = "ok " if o.ok else "ERR"
        typer.secho(
            f"[{mark}] {o.brand} / {o.platform.value}: {o.detail}",
            fg=typer.colors.GREEN if o.ok else typer.colors.RED,
        )
    if any(not o.ok for o in outcomes):
        raise typer.Exit(code=2)


@app.command()
def report(
    brand: Annotated[str, typer.Option("--brand", "-b", help="Brand name.")],
    period: Annotated[str, typer.Option(help="weekly | monthly | custom")] = "weekly",
    start: Annotated[str | None, typer.Option(help="Custom period start (YYYY-MM-DD).")] = None,
    end: Annotated[str | None, typer.Option(help="Custom period end (YYYY-MM-DD).")] = None,
    platforms: Annotated[
        str | None, typer.Option(help="Comma-separated, e.g. youtube,tiktok")
    ] = None,
    formats: Annotated[
        str, typer.Option("--format", help="md,html[,pdf] (pdf is optional)")
    ] = "md,html",
    as_json: JsonOpt = False,
) -> None:
    """Build a report from stored snapshots: report.json + Markdown + HTML."""
    if period not in ("weekly", "monthly", "custom"):
        raise typer.BadParameter("period must be weekly, monthly or custom")
    run = api.report(
        brand,
        period,  # type: ignore[arg-type]
        start=date.fromisoformat(start) if start else None,
        end=date.fromisoformat(end) if end else None,
        platforms=[Platform(p.strip()) for p in platforms.split(",")] if platforms else None,
        formats=tuple(f.strip() for f in formats.split(",")),
    )
    if as_json:
        _print_json({"run_id": run.run_id, "run_dir": run.run_dir, "files": run.files})
        return
    typer.echo(f"Report written to {run.run_dir}")
    for f in run.files:
        typer.echo(f"  {f}")
    gaps = len(run.report.data_quality.items)
    if gaps:
        typer.secho(
            f"{gaps} data-quality notes; see the Data quality section.", fg=typer.colors.YELLOW
        )


@app.command()
def demo() -> None:
    """Load clearly-labelled synthetic data into a `demo-brand` to try reports offline."""
    n = api.seed_demo()
    typer.echo(f"Seeded {n} days of synthetic YouTube data for `demo-brand`.")
    typer.echo("Try: social report --brand demo-brand --period monthly")


def main() -> None:
    try:
        app()
    except (
        ConfigError,
        ConnectorError,
        HttpError,
        OAuthFlowError,
        SecretStoreError,
        ValueError,
    ) as exc:
        typer.secho(f"Error: {redact(str(exc))}", fg=typer.colors.RED, err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
