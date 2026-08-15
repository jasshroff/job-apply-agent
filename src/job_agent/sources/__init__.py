"""Source registry.

To add a board, write a ``Source`` subclass, import it here, and add it to
``ALL_SOURCES``. Then switch it on under ``sources:`` in config.yaml.
"""

from __future__ import annotations

from ..config import Config
from .base import Source
from .boards import GreenhouseSource, LeverSource
from .keyed import AdzunaSource, JSearchSource
from .public_apis import ArbeitnowSource, RemoteOKSource, RemotiveSource
from .unofficial import LinkedInSource, NaukriSource

ALL_SOURCES: list[type[Source]] = [
    RemotiveSource,
    RemoteOKSource,
    ArbeitnowSource,
    GreenhouseSource,
    LeverSource,
    AdzunaSource,
    JSearchSource,
    LinkedInSource,
    NaukriSource,
]


def enabled_sources(config: Config) -> list[Source]:
    return [cls() for cls in ALL_SOURCES if config.source_enabled(cls.name)]


__all__ = ["Source", "ALL_SOURCES", "enabled_sources"]
