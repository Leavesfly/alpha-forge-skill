"""数据层：透明缓存、复权口径管理与多数据源抽象。"""

from __future__ import annotations

from .cache import (
    CacheConfig,
    default_config,
    load_klines,
    normalize_adjust,
)
from .sources import (
    AkshareSource,
    DataSource,
    TickFlowSource,
    get_sources,
    source_label,
)

__all__ = [
    "CacheConfig",
    "default_config",
    "load_klines",
    "normalize_adjust",
    "DataSource",
    "TickFlowSource",
    "AkshareSource",
    "get_sources",
    "source_label",
]
