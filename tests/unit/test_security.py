import threading
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from socialledger.auth.loopback import OAuthFlowError, Pkce, run_loopback_flow
from socialledger.auth.tokens import (
    EncryptedFileStore,
    MemoryStore,
    OAuthToken,
    SecretStoreError,
)
from socialledger.http import Http, HttpError, RateLimited, redact, register_secret


@pytest.mark.parametrize(
    "text",
    [
        "https://x.test/a?access_token=abc123secret&x=1",
        "refresh_token=zzz-yyy&client_secret=qqq",
        "Authorization: Bearer ya29.abcdef",
        '{"access_token": "abc123secret", "ok": true}',
        "code=4/0Aauthcode&state=1",
    ],
)
def test_redact_patterns(text: str) -> None:
    out = redact(text)
    for secret in ("abc123secret", "zzz-yyy", "qqq", "ya29.abcdef", "4/0Aauthcode"):
        assert secret not in out


def test_registered_secret_is_scrubbed_anywhere() -> None:
    register_secret("verysecretvalue42")
    assert "verysecretvalue42" not in redact("weird place: verysecretvalue42!")


def test_http_error_message_is_redacted() -> None:
    err = HttpError("failed GET https://x.test?access_token=leaky1234")
    assert "leaky1234" not in str(err)


def test_retry_then_rate_limited() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "1"})

    http = Http(transport=httpx.MockTransport(handler), max_retries=2, sleep=lambda s: None)
    with pytest.raises(RateLimited):
        http.request("GET", "https://api.test/x")
    assert len(calls) == 3


def test_retry_recovers_after_5xx() -> None:
    responses = iter([httpx.Response(503), httpx.Response(200, json={"ok": True})])
    http = Http(transport=httpx.MockTransport(lambda r: next(responses)), sleep=lambda s: None)
    assert http.get_json("https://api.test/x") == {"ok": True}


def test_encrypted_file_store_roundtrip(tmp_path: Path) -> None:
    store = EncryptedFileStore(tmp_path / "tokens.enc", "correct horse")
    store.set_model("youtube:token:b", OAuthToken(access_token="plaintext-token-xyz"))
    raw = (tmp_path / "tokens.enc").read_text(encoding="utf-8")
    assert "plaintext-token-xyz" not in raw
    loaded = store.get_model("youtube:token:b", OAuthToken)
    assert loaded is not None and loaded.access_token == "plaintext-token-xyz"
    with pytest.raises(SecretStoreError):
        EncryptedFileStore(tmp_path / "tokens.enc", "wrong").get("youtube:token:b")
    with pytest.raises(SecretStoreError):
        EncryptedFileStore(tmp_path / "x.enc", "")


def test_memory_store_delete() -> None:
    store = MemoryStore()
    store.set("k", "v")
    store.delete("k")
    assert store.get("k") is None


def test_pkce_s256() -> None:
    import base64
    import hashlib

    p = Pkce.generate()
    assert 43 <= len(p.verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(p.verifier.encode()).digest()).rstrip(b"=")
    assert p.challenge == expected.decode()
    assert Pkce.generate("hex").challenge.isalnum()


def _browser(simulate: dict[str, str] | None = None, wrong_state: bool = False):  # type: ignore[no-untyped-def]
    def open_browser(url: str) -> bool:
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        params = simulate or {"code": "auth-code-123"}
        state = "tampered" if wrong_state else q["state"]
        target = f"{q['redirect_uri']}?state={state}&" + "&".join(
            f"{k}={v}" for k, v in params.items()
        )

        def hit() -> None:
            urllib.request.urlopen(target, timeout=5).read()  # noqa: S310

        threading.Timer(0.2, hit).start()
        return True

    return open_browser


def test_loopback_flow_returns_code() -> None:
    result = run_loopback_flow(
        "https://auth.test/authorize",
        {"client_id": "cid"},
        open_browser=_browser(),
        announce=lambda s: None,
        timeout=10,
    )
    assert result.code == "auth-code-123"
    assert result.redirect_uri.startswith("http://127.0.0.1:")
    assert result.pkce is not None


def test_loopback_flow_rejects_state_mismatch() -> None:
    with pytest.raises(OAuthFlowError, match="state"):
        run_loopback_flow(
            "https://auth.test/authorize",
            {"client_id": "cid"},
            open_browser=_browser(wrong_state=True),
            announce=lambda s: None,
            timeout=10,
        )


def test_loopback_flow_surfaces_platform_error() -> None:
    with pytest.raises(OAuthFlowError, match="access_denied"):
        run_loopback_flow(
            "https://auth.test/authorize",
            {"client_id": "cid"},
            open_browser=_browser({"error": "access_denied"}),
            announce=lambda s: None,
            timeout=10,
        )
