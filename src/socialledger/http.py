"""Shared HTTP client: retries with backoff, rate-limit handling and secret redaction.

All outbound calls go through here. Error messages are always passed through ``redact`` so
tokens, client secrets and auth codes never reach logs, reports or the terminal.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from socialledger import __version__

USER_AGENT = f"socialledger/{__version__} (+https://github.com/prishabanerje-ops/socialledger)"

_SECRET_PARAM = re.compile(
    r"(?i)((?:access_token|refresh_token|client_secret|code|code_verifier|key|api_key|"
    r"fb_exchange_token|password)=)[^&\s\"']+"
)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_JSON_SECRET = re.compile(
    r"(?i)(\"(?:access_token|refresh_token|client_secret|id_token|api_key)\"\s*:\s*\")[^\"]+"
)
_registered_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a secret so ``redact`` scrubs it verbatim wherever it appears."""
    if value and len(value) >= 6:
        _registered_secrets.add(value)


def redact(text: str) -> str:
    for secret in _registered_secrets:
        text = text.replace(secret, "[REDACTED]")
    text = _SECRET_PARAM.sub(r"\1[REDACTED]", text)
    text = _BEARER.sub(r"\1[REDACTED]", text)
    return _JSON_SECRET.sub(r"\1[REDACTED]", text)


class HttpError(Exception):
    """A failed API call. ``str()`` is always redacted and safe to display."""

    def __init__(self, message: str, status: int | None = None, body: Any = None) -> None:
        super().__init__(redact(message))
        self.status = status
        self.body = body


class RateLimited(HttpError):
    """Retries exhausted, or the platform said wait longer than we are willing to."""


#: Decide whether a response should be retried; returns seconds to wait, or None.
RetryPolicy = Callable[[httpx.Response], float | None]


def default_retry_policy(response: httpx.Response) -> float | None:
    if response.status_code == 429 or response.status_code >= 500:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        return 0.0  # use exponential backoff
    return None


class Http:
    def __init__(
        self,
        *,
        base_url: str = "",
        timeout: float = 30.0,
        max_retries: int = 4,
        max_wait: float = 120.0,
        retry_policy: RetryPolicy = default_retry_policy,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            transport=transport,
            follow_redirects=False,
        )
        self.max_retries = max_retries
        self.max_wait = max_wait
        self.retry_policy = retry_policy
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        data: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = self._client.request(
                    method, url, params=params, headers=headers, data=data, json=json
                )
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise HttpError(f"Network error calling {_safe_url(url)}: {exc}") from exc
                self._backoff(attempt, 0.0)
                attempt += 1
                continue
            wait = self.retry_policy(response)
            if wait is None:
                return response
            if attempt >= self.max_retries or wait > self.max_wait:
                raise RateLimited(
                    f"{_safe_url(url)} is rate limited or unavailable "
                    f"(HTTP {response.status_code}) after {attempt + 1} attempts.",
                    status=response.status_code,
                )
            self._backoff(attempt, wait)
            attempt += 1

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self._json(self.request("GET", url, **kwargs))

    def post_json(self, url: str, **kwargs: Any) -> Any:
        return self._json(self.request("POST", url, **kwargs))

    def _backoff(self, attempt: int, suggested: float) -> None:
        delay = suggested or min(self.max_wait, (2**attempt) + random.uniform(0, 1))  # noqa: S311
        self._sleep(delay)

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.is_error:
            raise HttpError(
                f"HTTP {response.status_code} from {_safe_url(str(response.request.url))}",
                status=response.status_code,
                body=body,
            )
        return body


def _safe_url(url: str) -> str:
    return redact(url.split("?", 1)[0])
