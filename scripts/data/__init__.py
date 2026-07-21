"""数据层：透明缓存、复权口径管理、多数据源抽象、交叉验证、分红与财务数据。"""

from __future__ import annotations

from .cache import (
    CacheConfig,
    default_config,
    find_date_column,
    load_klines,
    normalize_adjust,
)
from .dividends import fetch_dividends
from .fundamentals import fetch_fundamentals
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
    "find_date_column",
    "load_klines",
    "normalize_adjust",
    "fetch_dividends",
    "fetch_fundamentals",
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
