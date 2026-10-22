"""Public service layer.

Every user-facing operation lives here as a plain function: arguments in, pydantic models
out, nothing printed. The CLI and the Claude Code skills are thin wrappers around these
functions, and a future MCP server would expose the same functions as tools
(see docs/mcp-design.md).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from socialledger import __version__, paths
from socialledger.auth.tokens import (
    AppCredentials,
    SecretStore,
    default_store,
    token_key,
)
from socialledger.config import (
    AccountConfig,
    BrandConfig,
    BrandsFile,
    ConfigError,
    init_brands_from_example,
    load_brands,
    load_metrics,
    save_brands,
)
from socialledger.connectors import IMPLEMENTED_API_PLATFORMS, connector_class
from socialledger.connectors.base import Connector, ConnectorError
from socialledger.http import Http, redact
from socialledger.metrics import PeriodName, resolve_period
from socialledger.render import render_html, render_markdown
from socialledger.report import Report, build_report
from socialledger.schema import REPORT_SCHEMA_VERSION, Health, HealthStatus, Platform
from socialledger.store import Store

Announce = Callable[[str], None]

#: How many recent days `snapshot` re-fetches so late-arriving platform data is filled in.
SNAPSHOT_DAILY_LOOKBACK = 7
#: Posts published within this many days are re-observed on each snapshot.
SNAPSHOT_POST_LOOKBACK = 30


def open_store() -> Store:
    paths.ensure_home()
    return Store(paths.db_path())


# ---- setup ------------------------------------------------------------------------------


class InitResult(BaseModel):
    home: str
    brands_file: str
    created: bool


def init_workspace() -> InitResult:
    existed = paths.brands_path().exists()
    target = init_brands_from_example()
    open_store().close()
    return InitResult(home=str(paths.home()), brands_file=str(target), created=not existed)


def ensure_account(
    brand: str, platform: Platform, mode: Literal["api", "csv"] | None = None
) -> BrandsFile:
    """Add a platform account to a brand (creating the brand) if missing. Idempotent.

    ``mode`` (if given) is applied to the account whether it is new or existing.
    """
    brands = load_brands() if paths.brands_path().exists() else BrandsFile()
    cfg = next((b for b in brands.brands if b.name == brand), None)
    if cfg is None:
        cfg = BrandConfig(name=brand)
        brands.brands.append(cfg)
    acct = cfg.account(platform)
    if acct is None:
        cfg.accounts.append(AccountConfig(platform=platform, mode=mode or "api"))
    elif mode:
        acct.mode = mode
    save_brands(brands)
    return brands


def connect(
    platform: Platform,
    brand: str,
    app: AppCredentials,
    *,
    secrets: SecretStore | None = None,
    http: Http | None = None,
    announce: Announce = print,
    **flow_kwargs: Any,
) -> Health:
    """Run the OAuth flow for one brand/platform, store the token, record the account ID."""
    if platform not in IMPLEMENTED_API_PLATFORMS:
        raise ConfigError(
            f"The {platform.value} API connector is not available yet. "
            "Set `mode: csv` for this account and use CSV import."
        )
    secrets = secrets or default_store()
    cls = connector_class(platform)
    authorize = getattr(cls, "authorize")  # noqa: B009 - each connector defines its own flow
    token = authorize(secrets, app, http=http, announce=announce, **flow_kwargs)
    secrets.set_model(token_key(cls.credential_namespace, brand), token)

    brands = ensure_account(brand, platform)
    brand_cfg = brands.get(brand)
    account = brand_cfg.account(platform)
    assert account is not None
    account.mode = "api"
    health = cls(brand_cfg, account, secrets, http=http).health_check()
    if health.account_id:
        account.account_id = health.account_id
        account.handle = health.handle or account.handle
        save_brands(brands)
    return health


def seed_demo(today: date | None = None) -> int:
    """Create the synthetic `demo-brand` and fill the store with fictional data."""
    from socialledger import demo

    brands = ensure_account(demo.DEMO_BRAND, Platform.YOUTUBE)
    cfg = brands.get(demo.DEMO_BRAND)
    cfg.demo = True
    account = cfg.account(Platform.YOUTUBE)
    assert account is not None
    account.account_id, account.handle = demo.DEMO_ACCOUNT_ID, "@demo-channel"
    save_brands(brands)
    with open_store() as store:
        return demo.seed(store, today or date.today())


# ---- status -----------------------------------------------------------------------------


class StatusRow(BaseModel):
    brand: str
    platform: Platform
    mode: str
    health: Health
    last_snapshot: str | None
    last_error: str | None


def status(
    *, check_live: bool = True, secrets: SecretStore | None = None, http: Http | None = None
) -> list[StatusRow]:
    brands = load_brands()
    rows: list[StatusRow] = []
    with open_store() as store:
        for brand in brands.brands:
            for account in brand.accounts:
                if brand.demo:
                    health = Health(
                        platform=account.platform,
                        status=HealthStatus.CONNECTED,
                        detail="Synthetic demo data (social demo); never fetched.",
                    )
                elif account.mode == "csv":
                    health = Health(
                        platform=account.platform,
                        status=HealthStatus.CSV_ONLY,
                        detail="Configured for CSV import (no API access).",
                    )
                elif account.platform not in IMPLEMENTED_API_PLATFORMS:
                    health = Health(
                        platform=account.platform,
                        status=HealthStatus.CSV_ONLY,
                        detail="API connector not available yet; use CSV import.",
                    )
                elif not check_live:
                    health = Health(
                        platform=account.platform,
                        status=HealthStatus.NOT_CONFIGURED
                        if not account.account_id
                        else HealthStatus.CONNECTED,
                        detail="Live check skipped (--offline).",
                        account_id=account.account_id,
                        handle=account.handle,
                    )
                else:
                    connector = _connector(brand, account, secrets, http)
                    health = connector.health_check()
                rows.append(
                    StatusRow(
                        brand=brand.name,
                        platform=account.platform,
                        mode=account.mode,
                        health=health,
                        last_snapshot=store.last_snapshot(brand.name, account.platform),
                        last_error=store.last_snapshot(
                            brand.name, account.platform, status="error"
                        ),
                    )
                )
    return rows


# ---- snapshot / backfill ------------------------------------------------------------------


class SnapshotOutcome(BaseModel):
    brand: str
    platform: Platform
    ok: bool
    detail: str
    daily_rows: int = 0
    posts: int = 0
    purged_rows: int = 0


def snapshot(
    brand: str | None = None,
    *,
    today: date | None = None,
    daily_lookback: int = SNAPSHOT_DAILY_LOOKBACK,
    post_lookback: int = SNAPSHOT_POST_LOOKBACK,
    secrets: SecretStore | None = None,
    http: Http | None = None,
) -> list[SnapshotOutcome]:
    """Fetch the latest data for every API-mode account and upsert it. Idempotent."""
    today = today or date.today()
    return _fetch_all(
        brand,
        today,
        today - timedelta(days=daily_lookback),
        today - timedelta(days=post_lookback),
        secrets,
        http,
    )


def backfill(
    brand: str | None = None,
    *,
    days: int | None = None,
    today: date | None = None,
    secrets: SecretStore | None = None,
    http: Http | None = None,
) -> list[SnapshotOutcome]:
    """Pull as much history as each API allows (capped by ``days`` if given)."""
    today = today or date.today()
    return _fetch_all(brand, today, None, None, secrets, http, backfill_days=days)


def _fetch_all(
    brand_name: str | None,
    today: date,
    daily_start: date | None,
    post_start: date | None,
    secrets: SecretStore | None,
    http: Http | None,
    backfill_days: int | None = None,
) -> list[SnapshotOutcome]:
    brands = load_brands()
    selected = [brands.get(brand_name)] if brand_name else brands.brands
    metrics_cfg = load_metrics()
    outcomes: list[SnapshotOutcome] = []
    with open_store() as store:
        for brand in selected:
            if brand.demo:
                continue
            for account in brand.accounts:
                if account.mode != "api" or account.platform not in IMPLEMENTED_API_PLATFORMS:
                    continue
                connector = _connector(brand, account, secrets, http)
                limit = connector.max_backfill_days
                window = min(limit, backfill_days) if backfill_days else limit
                d_start = daily_start or today - timedelta(days=window)
                p_start = post_start or today - timedelta(days=window)
                yesterday = today - timedelta(days=1)
                try:
                    acct = connector.fetch_account(today)
                    store.upsert_account(acct)
                    daily = connector.fetch_daily_metrics(d_start, yesterday)
                    n_daily = store.upsert_daily(daily, api_version=connector.api_version)
                    posts = connector.fetch_posts(p_start, today, today)
                    n_posts = store.upsert_posts(posts)
                except ConnectorError as exc:
                    detail = redact(str(exc))
                    store.log_snapshot(brand.name, account.platform, "error", detail)
                    outcomes.append(
                        SnapshotOutcome(
                            brand=brand.name, platform=account.platform, ok=False, detail=detail
                        )
                    )
                    continue
                purged = 0
                retention = metrics_cfg.platforms.get(account.platform)
                if retention and retention.retention_days:
                    purged = store.purge_older_than(
                        account.platform, retention.retention_days, today
                    )
                detail = f"{n_daily} daily metric values, {n_posts} posts ({d_start} → {yesterday})"
                store.log_snapshot(brand.name, account.platform, "ok", detail)
                outcomes.append(
                    SnapshotOutcome(
                        brand=brand.name,
                        platform=account.platform,
                        ok=True,
                        detail=detail,
                        daily_rows=n_daily,
                        posts=n_posts,
                        purged_rows=purged,
                    )
                )
    return outcomes


def _connector(
    brand: BrandConfig, account: AccountConfig, secrets: SecretStore | None, http: Http | None
) -> Connector:
    cls = connector_class(account.platform)
    return cls(brand, account, secrets or default_store(), http=http)


# ---- reporting ----------------------------------------------------------------------------


class ReportRun(BaseModel):
    run_id: str
    run_dir: str
    files: list[str]
    report: Report


class Manifest(BaseModel):
    schema_version: str = REPORT_SCHEMA_VERSION
    tool_version: str = __version__
    run_id: str
    kind: str
    created_at: datetime
    parameters: dict[str, Any]
    store_schema_version: int
    last_snapshots: dict[str, str | None]
    files: dict[str, str] = Field(default_factory=dict, description="file name -> sha256")


def report(
    brand: str,
    period: PeriodName = "weekly",
    *,
    start: date | None = None,
    end: date | None = None,
    platforms: list[Platform] | None = None,
    formats: tuple[str, ...] = ("md", "html"),
    today: date | None = None,
    runs_dir: Path | None = None,
) -> ReportRun:
    brand_cfg = load_brands().get(brand)
    resolved = resolve_period(period, today or date.today(), start, end)
    chosen = platforms or [a.platform for a in brand_cfg.accounts]
    created = datetime.now(UTC)
    run_id = f"{created:%Y%m%dT%H%M%SZ}-{brand}-{resolved.label}"
    run_dir = (runs_dir or paths.runs_dir()) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    with open_store() as store:
        rep = build_report(
            store, brand, chosen, resolved, load_metrics(), run_id, demo=brand_cfg.demo
        )
        manifest = Manifest(
            run_id=run_id,
            kind="report",
            created_at=created,
            parameters={
                "brand": brand,
                "period": resolved.label,
                "start": resolved.start.isoformat(),
                "end": resolved.end.isoformat(),
                "platforms": [p.value for p in chosen],
                "formats": list(formats),
            },
            store_schema_version=store.schema_version,
            last_snapshots={p.value: store.last_snapshot(brand, p) for p in chosen},
        )
    _flag_stale_sources(rep, manifest.last_snapshots, brand_cfg, created)

    outputs: dict[str, str] = {"report.json": rep.model_dump_json(indent=2)}
    if "md" in formats:
        outputs["report.md"] = render_markdown(rep)
    if "html" in formats:
        outputs["report.html"] = render_html(rep)
    for name, content in outputs.items():
        (run_dir / name).write_text(content, encoding="utf-8")
        manifest.files[name] = hashlib.sha256(content.encode()).hexdigest()
    if "pdf" in formats:
        from socialledger.render.pdf import write_pdf

        pdf = write_pdf(outputs.get("report.html") or render_html(rep), run_dir / "report.pdf")
        manifest.files["report.pdf"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2), encoding="utf-8"
    )
    return ReportRun(
        run_id=run_id,
        run_dir=str(run_dir),
        files=sorted([*manifest.files, "manifest.json"]),
        report=rep,
    )


#: An API source whose last successful snapshot is older than this is flagged as stale.
STALE_AFTER = timedelta(days=2)


def _flag_stale_sources(
    rep: Report, last: dict[str, str | None], brand: BrandConfig, now: datetime
) -> None:
    from socialledger.report import DataQualityItem

    for account in brand.accounts:
        if account.mode != "api" or account.platform.value not in last:
            continue
        ts = last[account.platform.value]
        if ts is None:
            detail = "No successful API snapshot recorded yet."
        elif now - datetime.fromisoformat(ts) > STALE_AFTER:
            detail = f"Last successful API snapshot was {ts}; data may be stale."
        else:
            continue
        rep.data_quality.items.append(
            DataQualityItem(platform=account.platform, kind="stale_source", detail=detail)
        )
