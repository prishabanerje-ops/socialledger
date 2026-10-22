"""LIVE smoke test against your own YouTube channel. Opt-in only; never runs in CI.

    SOCIALLEDGER_LIVE=1 SOCIALLEDGER_LIVE_BRAND=<your brand> uv run pytest tests/live -k youtube -s

Uses your real ~/.socialledger config and keychain (it does not use the isolated test home).
It only reads, and it prints shapes and availability flags, never tokens or raw responses.
"""

from __future__ import annotations

import os
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest

from socialledger.auth.tokens import default_store
from socialledger.config import load_brands
from socialledger.connectors.youtube import YouTubeConnector
from socialledger.schema import HealthStatus, Platform

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("SOCIALLEDGER_LIVE") != "1", reason="live tests are opt-in"),
]


@pytest.fixture
def connector(monkeypatch: pytest.MonkeyPatch) -> YouTubeConnector:
    real_home = os.environ.get("SOCIALLEDGER_REAL_HOME", str(Path.home() / ".socialledger"))
    monkeypatch.setenv("SOCIALLEDGER_HOME", real_home)
    brand = load_brands().get(os.environ["SOCIALLEDGER_LIVE_BRAND"])
    account = brand.account(Platform.YOUTUBE)
    assert account is not None, "brand has no youtube account configured"
    return YouTubeConnector(brand, account, default_store())


def test_live_youtube(connector: YouTubeConnector) -> None:
    health = connector.health_check()
    print(f"\nhealth: {health.status.value}: {health.detail}")
    assert health.status is HealthStatus.CONNECTED

    today = date.today()
    account = connector.fetch_account(today)
    print(f"followers availability: {account.followers.availability.value}")

    daily = connector.fetch_daily_metrics(today - timedelta(days=10), today - timedelta(days=1))
    flags = Counter((m.value, mv.availability.value) for d in daily for m, mv in d.metrics.items())
    print("daily metric availability:", dict(sorted(flags.items())))
    assert len(daily) == 10

    posts = connector.fetch_posts(today - timedelta(days=90), today, today)
    print(
        f"posts in last 90 days: {len(posts)}; formats:",
        dict(Counter(p.format.value for p in posts)),
    )
