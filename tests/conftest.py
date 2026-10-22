from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from socialledger import config
from socialledger.auth.tokens import AppCredentials, MemoryStore, OAuthToken, app_key, token_key
from socialledger.http import Http

FIXTURES = Path(__file__).parent / "fixtures"

#: Synthetic credentials used only in tests; asserted to never appear in any output.
FAKE_CLIENT_SECRET = "test-client-secret-SHOULD-NEVER-LEAK"
FAKE_ACCESS_TOKEN = "ya29.test-access-token-SHOULD-NEVER-LEAK"
FAKE_REFRESH_TOKEN = "1//test-refresh-token-SHOULD-NEVER-LEAK"


def load_fixture(*parts: str) -> Any:
    return json.loads(FIXTURES.joinpath(*parts).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Every test gets its own empty SOCIALLEDGER_HOME; never touches the real one."""
    home = tmp_path / "home"
    monkeypatch.setenv("SOCIALLEDGER_HOME", str(home))
    for var in ("SOCIALLEDGER_YOUTUBE_CLIENT_ID", "SOCIALLEDGER_YOUTUBE_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    config._metrics_cached.cache_clear()
    yield home


@pytest.fixture
def no_sleep_http() -> Http:
    return Http(sleep=lambda _s: None)


@pytest.fixture
def youtube_secrets() -> MemoryStore:
    from datetime import UTC, datetime, timedelta

    store = MemoryStore()
    store.set_model(
        app_key("youtube"),
        AppCredentials(client_id="test-client-id", client_secret=FAKE_CLIENT_SECRET),
    )
    store.set_model(
        token_key("youtube", "test-brand"),
        OAuthToken(
            access_token=FAKE_ACCESS_TOKEN,
            refresh_token=FAKE_REFRESH_TOKEN,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    return store
