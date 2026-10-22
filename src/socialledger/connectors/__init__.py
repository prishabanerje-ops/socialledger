"""Platform connectors. Use ``connector_class`` to look one up by platform."""

from __future__ import annotations

from socialledger.connectors.base import Connector
from socialledger.schema import Platform


def connector_class(platform: Platform) -> type[Connector]:
    if platform is Platform.YOUTUBE:
        from socialledger.connectors.youtube import YouTubeConnector

        return YouTubeConnector
    raise NotImplementedError(
        f"The {platform.value} API connector is not available yet; use CSV import (mode: csv)."
    )


IMPLEMENTED_API_PLATFORMS: tuple[Platform, ...] = (Platform.YOUTUBE,)
