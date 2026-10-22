"""Where socialledger keeps user data. Nothing user-specific lives in the installed package."""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path

HOME_ENV = "SOCIALLEDGER_HOME"


def home() -> Path:
    """User data directory (config, SQLite store, report runs). Default ``~/.socialledger``."""
    override = os.environ.get(HOME_ENV)
    return Path(override).expanduser() if override else Path.home() / ".socialledger"


def ensure_home() -> Path:
    path = home()
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)
    return path


def db_path() -> Path:
    return home() / "socialledger.sqlite"


def runs_dir() -> Path:
    return home() / "runs"


def brands_path() -> Path:
    return home() / "brands.yaml"


def defaults_dir() -> Path:
    """Shipped default config (metrics.yaml, thresholds.yaml, csv_mappings/, examples).

    In a wheel it is packaged as ``socialledger/_defaults``; in a source checkout it is the
    repo's ``config/`` folder.
    """
    packaged = Path(str(resources.files("socialledger"))) / "_defaults"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "config"


def config_file(name: str) -> Path:
    """A user override in the data dir wins over the shipped default."""
    user = home() / name
    return user if user.exists() else defaults_dir() / name
