"""Loading and validating user configuration (brands) and shipped config (metrics)."""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from socialledger import paths
from socialledger.schema import Metric, Platform, PostFormat


class ConfigError(Exception):
    """Configuration is missing or invalid. The message is safe to show to the user."""


class AccountConfig(BaseModel):
    platform: Platform
    mode: Literal["api", "csv"] = "api"
    account_id: str | None = None
    handle: str | None = None


class BrandConfig(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    timezone: str = "UTC"
    #: Synthetic demo data (`social demo`); never fetched, always bannered in reports.
    demo: bool = False
    accounts: list[AccountConfig] = Field(default_factory=list)

    def account(self, platform: Platform) -> AccountConfig | None:
        return next((a for a in self.accounts if a.platform is platform), None)


class BrandsFile(BaseModel):
    brands: list[BrandConfig] = Field(default_factory=list)

    def get(self, name: str) -> BrandConfig:
        for brand in self.brands:
            if brand.name == name:
                return brand
        known = ", ".join(b.name for b in self.brands) or "none"
        raise ConfigError(f"Unknown brand '{name}'. Configured brands: {known}.")


class DefinitionChange(BaseModel):
    metric: Metric
    date: str
    note: str


class PlatformMetrics(BaseModel):
    daily: dict[Metric, str | None] = Field(default_factory=dict)
    post: dict[Metric, str | None] = Field(default_factory=dict)
    formats: dict[str, PostFormat] = Field(default_factory=dict)
    followers_note: str | None = None
    latency_days: int = 0
    max_backfill_days: int = 30
    retention_days: int | None = None
    definition_changes: list[DefinitionChange] = Field(default_factory=list)

    def api_names(self, kind: Literal["daily", "post"]) -> dict[Metric, str]:
        """Canonical -> API name, only for metrics the API exposes."""
        mapping = self.daily if kind == "daily" else self.post
        return {m: name for m, name in mapping.items() if name}

    def unavailable(self, kind: Literal["daily", "post"]) -> list[Metric]:
        mapping = self.daily if kind == "daily" else self.post
        return [m for m, name in mapping.items() if not name]


class EngagementRateRule(BaseModel):
    numerator: list[Metric]
    denominator: Metric
    label: str


class MetricsConfig(BaseModel):
    verified_on: str
    api_versions: dict[str, Any]
    platforms: dict[Platform, PlatformMetrics]
    engagement_rate: dict[Platform, EngagementRateRule] = Field(default_factory=dict)

    def for_platform(self, platform: Platform) -> PlatformMetrics:
        try:
            return self.platforms[platform]
        except KeyError as exc:
            raise ConfigError(f"No metric mapping for {platform} in metrics.yaml.") from exc


def _read_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc


@lru_cache(maxsize=4)
def _metrics_cached(path: str) -> MetricsConfig:
    try:
        return MetricsConfig.model_validate(_read_yaml(Path(path)))
    except ValidationError as exc:
        raise ConfigError(f"Invalid metrics config {path}:\n{exc}") from exc


def load_metrics() -> MetricsConfig:
    return _metrics_cached(str(paths.config_file("metrics.yaml")))


def load_brands(path: Path | None = None) -> BrandsFile:
    path = path or paths.brands_path()
    if not path.exists():
        raise ConfigError(f"No brand config at {path}. Run `social init` first.")
    try:
        return BrandsFile.model_validate(_read_yaml(path))
    except ValidationError as exc:
        raise ConfigError(f"Invalid brand config {path}:\n{exc}") from exc


def save_brands(brands: BrandsFile, path: Path | None = None) -> Path:
    path = path or paths.brands_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = brands.model_dump(mode="json")
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    tmp.replace(path)
    return path


def init_brands_from_example() -> Path:
    """Create ~/.socialledger/brands.yaml from the example if it does not exist. Idempotent."""
    target = paths.brands_path()
    if not target.exists():
        paths.ensure_home()
        shutil.copyfile(paths.defaults_dir() / "brands.example.yaml", target)
    return target
