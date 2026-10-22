"""`social init`: the guided one-step setup. Safe to run repeatedly."""

from __future__ import annotations

import os

import typer

from socialledger import api
from socialledger.auth.tokens import FILE_STORE_WARNING, AppCredentials, KeyringStore
from socialledger.config import load_brands, save_brands
from socialledger.connectors import IMPLEMENTED_API_PLATFORMS
from socialledger.schema import HealthStatus, Platform

SETUP_GUIDES: dict[Platform, str] = {
    Platform.YOUTUBE: """\
YouTube uses your own Google Cloud OAuth client (free). One-time steps:
  1. Create (or pick) a project: https://console.cloud.google.com/projectcreate
  2. Enable both APIs in that project:
       https://console.cloud.google.com/apis/library/youtube.googleapis.com
       https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com
  3. Configure the OAuth consent screen (Google Auth Platform → Branding / Audience):
       user type "External", add your Google account as a test user.
       Then set publishing status to "In production". In "Testing", Google expires refresh
       tokens after 7 days and you would have to reconnect weekly. Personal use does not
       require verification; you will see an "unverified app" warning you can accept.
  4. Create an OAuth client: https://console.cloud.google.com/auth/clients
       Application type: "Desktop app". Copy the client ID and client secret.
Docs: https://developers.google.com/youtube/v3/guides/auth/installed-apps
      https://developers.google.com/identity/protocols/oauth2/native-app
Full guide: docs/platforms/youtube.md""",
}

NOT_YET = (
    "The {name} API connector arrives in a later release. It will be set to CSV-only mode for "
    "now (`social import`, coming soon); you can switch it with `social connect {name}` later."
)


def run() -> None:
    result = api.init_workspace()
    typer.echo(f"socialledger data folder: {result.home}")
    typer.echo(f"Brand config: {result.brands_file}" + (" (created)" if result.created else ""))
    typer.echo(
        "\nsocialledger is bring-your-own-credentials: it connects with developer apps that YOU"
        "\nregister on each platform. Nothing is hosted; data stays on this machine.\n"
    )
    if not KeyringStore.available():
        typer.secho(FILE_STORE_WARNING, fg=typer.colors.YELLOW)

    brands = load_brands()
    placeholder = (
        len(brands.brands) == 1
        and brands.brands[0].name == "example-brand"
        and not any(a.account_id for a in brands.brands[0].accounts)
    )
    default = "my-brand" if placeholder or not brands.brands else brands.brands[0].name
    brand = typer.prompt("Brand name (letters, digits, - _ .)", default=default)
    if placeholder:
        brands.brands = []
        save_brands(brands)

    for platform in Platform:
        if not typer.confirm(f"\nSet up {platform.value}?", default=platform is Platform.YOUTUBE):
            continue
        if platform not in IMPLEMENTED_API_PLATFORMS:
            typer.echo(NOT_YET.format(name=platform.value))
            api.ensure_account(brand, platform, mode="csv")
            continue
        connect_interactive(platform, brand)

    typer.echo("\nHealth check:")
    for row in api.status():
        if row.brand == brand:
            _print_health(row.platform.value, row.health.status, row.health.detail)
    typer.echo(
        "\nNext: `social snapshot` to fetch data, then "
        f"`social report --brand {brand} --period weekly`."
    )


def connect_interactive(platform: Platform, brand: str) -> None:
    typer.echo("\n" + SETUP_GUIDES.get(platform, ""))
    mode = typer.prompt("Use the API or CSV import? [api/csv]", default="api")
    if mode.strip().lower() == "csv":
        api.ensure_account(brand, platform, mode="csv")
        typer.echo(f"{platform.value}: configured for CSV import.")
        return
    prefix = f"SOCIALLEDGER_{platform.value.upper()}_"
    client_id = os.environ.get(prefix + "CLIENT_ID") or typer.prompt("OAuth client ID")
    client_secret = os.environ.get(prefix + "CLIENT_SECRET") or typer.prompt(
        "OAuth client secret (input hidden)", hide_input=True
    )
    health = api.connect(
        platform,
        brand,
        AppCredentials(client_id=client_id.strip(), client_secret=client_secret.strip() or None),
        announce=typer.echo,
    )
    _print_health(platform.value, health.status, health.detail)
    for gap in health.access_gaps:
        typer.echo(f"   note: {gap}")


def _print_health(name: str, status: HealthStatus, detail: str) -> None:
    color = {
        HealthStatus.CONNECTED: typer.colors.GREEN,
        HealthStatus.CSV_ONLY: typer.colors.CYAN,
        HealthStatus.PENDING_APPROVAL: typer.colors.YELLOW,
    }.get(status, typer.colors.RED)
    typer.secho(f" • {name}: {status.value}: {detail}", fg=color)
