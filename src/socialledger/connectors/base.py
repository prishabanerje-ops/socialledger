"""The interface every platform connector implements.

Connectors are read-only: they only call documented read endpoints (plus OAuth token
endpoints). They return canonical models and never write to the store themselves.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from typing import ClassVar, Literal

from socialledger.auth.tokens import AppCredentials, OAuthToken, SecretStore, app_key, token_key
from socialledger.config import AccountConfig, BrandConfig, PlatformMetrics, load_metrics
from socialledger.http import Http
from socialledger.schema import (
    Account,
    Availability,
    DailyMetrics,
    Health,
    Metric,
    MetricMap,
    MetricValue,
    Platform,
    Post,
)


class ConnectorError(Exception):
    """A failure the user can act on. The message is redacted and human readable."""

    availability: Availability = Availability.ERROR


class NotConfigured(ConnectorError):
    availability = Availability.NOT_AUTHORIZED


class NotAuthorized(ConnectorError):
    """Missing approval, scope or role on the platform side."""

    availability = Availability.NOT_AUTHORIZED


class TokenExpired(ConnectorError):
    availability = Availability.NOT_AUTHORIZED


class QuotaExceeded(ConnectorError):
    availability = Availability.ERROR


Announce = Callable[[str], None]


class Connector(ABC):
    platform: ClassVar[Platform]
    #: Key used for stored app credentials/tokens (Instagram and Facebook share "meta").
    credential_namespace: ClassVar[str]

    def __init__(
        self,
        brand: BrandConfig,
        account: AccountConfig,
        secrets: SecretStore,
        http: Http | None = None,
        metrics: PlatformMetrics | None = None,
    ) -> None:
        self.brand = brand
        self.account = account
        self.secrets = secrets
        self.http = http or Http()
        self.metrics = metrics or load_metrics().for_platform(self.platform)

    # ---- the interface ------------------------------------------------------------------

    @abstractmethod
    def health_check(self) -> Health: ...

    @abstractmethod
    def fetch_account(self, as_of: date) -> Account: ...

    @abstractmethod
    def fetch_daily_metrics(self, start: date, end: date) -> list[DailyMetrics]: ...

    @abstractmethod
    def fetch_posts(self, start: date, end: date, as_of: date) -> list[Post]: ...

    @property
    def api_version(self) -> str:
        return ""

    @property
    def max_backfill_days(self) -> int:
        return self.metrics.max_backfill_days

    # ---- shared helpers -----------------------------------------------------------------

    def app_credentials(self) -> AppCredentials:
        from socialledger.auth.tokens import app_credentials_from_env

        creds = app_credentials_from_env(self.credential_namespace) or self.secrets.get_model(
            app_key(self.credential_namespace), AppCredentials
        )
        if creds is None:
            raise NotConfigured(
                f"No {self.credential_namespace} app credentials found. "
                f"Run `social connect {self.platform.value}`."
            )
        return creds

    def stored_token(self) -> OAuthToken:
        token = self.secrets.get_model(
            token_key(self.credential_namespace, self.brand.name), OAuthToken
        )
        if token is None:
            name = self.platform.value
            raise NotConfigured(f"Not connected to {name}. Run `social connect {name}`.")
        return token

    def save_token(self, token: OAuthToken) -> None:
        self.secrets.set_model(token_key(self.credential_namespace, self.brand.name), token)

    def complete(self, kind: Literal["daily", "post"], metrics: MetricMap) -> MetricMap:
        """Add explicit ``unavailable_from_api`` entries for metrics the API does not expose."""
        for metric in self.metrics.unavailable(kind):
            metrics.setdefault(
                metric,
                MetricValue.missing(
                    Availability.UNAVAILABLE_FROM_API,
                    f"{self.platform.value} API does not expose this metric",
                ),
            )
        return metrics


def date_range(start: date, end: date) -> Iterable[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def missing_for(metrics: Iterable[Metric], availability: Availability, note: str) -> MetricMap:
    return {m: MetricValue.missing(availability, note) for m in metrics}
