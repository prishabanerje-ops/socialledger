"""socialledger: local, read-only reporting, audits and research for your own social accounts."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("socialledger")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0"

__all__ = ["__version__"]
