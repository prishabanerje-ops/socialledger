"""Snapshot → store → report → render, entirely offline with synthetic API responses."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import respx

from socialledger import api
from socialledger.auth.tokens import MemoryStore
from socialledger.config import AccountConfig, BrandConfig, BrandsFile, save_brands
from socialledger.connectors.youtube import ANALYTICS_API, DATA_API, TOKEN_URL
from socialledger.http import Http
from socialledger.schema import Platform
from tests.conftest import (
    FAKE_ACCESS_TOKEN,
    FAKE_CLIENT_SECRET,
    FAKE_REFRESH_TOKEN,
    load_fixture,
)

TODAY = date(2026, 10, 8)


def _configure() -> None:
    save_brands(
        BrandsFile(
            brands=[
                BrandConfig(
                    name="test-brand",
                    accounts=[
                        AccountConfig(platform=Platform.YOUTUBE, mode="api"),
                        AccountConfig(platform=Platform.TIKTOK, mode="csv"),
                    ],
                )
            ]
        )
    )


def _mock_youtube(router: respx.MockRouter) -> None:
    router.get(f"{DATA_API}/channels").respond(json=load_fixture("youtube", "channels_mine.json"))
    router.get(f"{DATA_API}/playlistItems").respond(
        json=load_fixture("youtube", "playlist_items.json")
    )
    router.get(f"{DATA_API}/videos").respond(json=load_fixture("youtube", "videos.json"))
    router.get(ANALYTICS_API, params={"dimensions": "day"}).respond(
        json=load_fixture("youtube", "analytics_daily.json")
    )
    router.get(ANALYTICS_API).respond(json=load_fixture("youtube", "analytics_videos.json"))


@respx.mock
def test_snapshot_then_report(youtube_secrets: MemoryStore, isolated_home: Path) -> None:
    _configure()
    _mock_youtube(respx.mock)
    http = Http(sleep=lambda s: None)

    first = api.snapshot(today=TODAY, secrets=youtube_secrets, http=http)
    second = api.snapshot(today=TODAY, secrets=youtube_secrets, http=http)  # idempotent
    assert [o.ok for o in first] == [True] and [o.ok for o in second] == [True]
    assert first[0].daily_rows == second[0].daily_rows

    # Read-only: every API call is a GET; the only POST allowed is the OAuth token endpoint.
    for call in respx.calls:
        assert call.request.method == "GET" or str(call.request.url).startswith(TOKEN_URL)

    run = api.report(
        "test-brand", "custom", start=date(2026, 10, 1), end=date(2026, 10, 7), today=TODAY
    )
    run_dir = Path(run.run_dir)
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

    assert report["schema_version"] == "1.0" and manifest["kind"] == "report"
    yt = next(p for p in report["platforms"] if p["platform"] == "youtube")
    assert yt["metrics"]["views"]["current"]["value"] == 3000  # 3 days with rows
    assert yt["metrics"]["views"]["current"]["days_available"] == 3
    assert yt["metrics"]["views"]["change_pct"] is None  # partial periods: no % change
    assert yt["posts_published"] == 2
    kinds = {(i["platform"], i["kind"]) for i in report["data_quality"]["items"]}
    assert ("youtube", "partial") in kinds
    assert ("youtube", "unavailable_from_api") in kinds
    assert ("tiktok", "gap") in kinds  # CSV-mode platform with no data is reported, not hidden

    for name in ("report.md", "report.html", "report.json", "manifest.json"):
        content = (run_dir / name).read_text(encoding="utf-8")
        for secret in (FAKE_ACCESS_TOKEN, FAKE_REFRESH_TOKEN, FAKE_CLIENT_SECRET):
            assert secret not in content, f"secret leaked into {name}"
        assert manifest["files"].get(name) or name == "manifest.json"


@respx.mock
def test_snapshot_failure_is_logged_not_fatal(youtube_secrets: MemoryStore) -> None:
    _configure()
    respx.get(f"{DATA_API}/channels").respond(403, json=load_fixture("youtube", "error_quota.json"))
    outcomes = api.snapshot(today=TODAY, secrets=youtube_secrets, http=Http(sleep=lambda s: None))
    assert outcomes[0].ok is False and "quota" in outcomes[0].detail.lower()
    rows = api.status(check_live=False)
    yt = next(r for r in rows if r.platform is Platform.YOUTUBE)
    assert yt.last_snapshot is None and yt.last_error is not None


def test_html_report_has_no_external_resources() -> None:
    api.seed_demo(today=TODAY)
    run = api.report("demo-brand", "monthly", today=TODAY)
    html = (Path(run.run_dir) / "report.html").read_text(encoding="utf-8")
    assert not re.search(r"<(script|link)[^>]+(src|href)=[\"']https?://", html)
    assert "Synthetic demo data" in html
