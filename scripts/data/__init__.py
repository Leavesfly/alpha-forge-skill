"""数据层：透明缓存、复权口径管理、多数据源抽象与交叉验证。"""

from __future__ import annotations

from .cache import (
    CacheConfig,
    default_config,
    load_klines,
    normalize_adjust,
)
from .sources import (
    AkshareSource,
    BaostockSource,
    DataSource,
    TickFlowSource,
    get_sources,
    source_label,
)
from .verify import (
    ColumnDiff,
    VerifyResult,
    verify_symbol,
)

__all__ = [
    "CacheConfig",
    "default_config",
    "load_klines",
    "normalize_adjust",
    "DataSource",
    "TickFlowSource",
    "BaostockSource",
    "AkshareSource",
    "get_sources",
    "source_label",
    "ColumnDiff",
    "VerifyResult",
    "verify_symbol",
]
