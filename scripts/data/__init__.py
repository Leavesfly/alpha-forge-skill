"""数据层：透明缓存、复权口径管理、多数据源抽象、质量校验、交叉验证、分红、财务、估值分位与宏观数据。"""

from __future__ import annotations

from .cache import (
    CacheConfig,
    cache_usage,
    default_config,
    find_date_column,
    load_json_obj,
    load_klines,
    load_table,
    normalize_adjust,
    prune_cache,
    read_meta,
    resolve_cache_dir,
)
from .calendar import SessionInfo, last_closed_session, market_of
from .dividends import fetch_dividends
from .fundamentals import fetch_fundamentals
from .macro import (
    MacroRegime,
    MacroSnapshot,
    detect_macro_regime,
    fetch_macro_snapshot,
    format_macro_regime,
)
from .panel import load_panel
from .quality import QualityIssue, QualityReport, validate_ohlcv
from .sources import (
    AkshareSource,
    BaostockSource,
    DataSource,
    TickFlowSource,
    get_sources,
    source_label,
)
from .sync import SyncReport, sync_symbols
from .valuation import (
    ValuationPercentile,
    fetch_valuation_percentile,
    format_valuation,
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
    "load_json_obj",
    "load_klines",
    "load_table",
    "normalize_adjust",
    "resolve_cache_dir",
    # 缓存审计与治理
    "read_meta",
    "cache_usage",
    "prune_cache",
    "fetch_dividends",
    "fetch_fundamentals",
    # 交易日历（缓存新鲜度基准）
    "SessionInfo",
    "last_closed_session",
    "market_of",
    # 数据质量校验
    "QualityIssue",
    "QualityReport",
    "validate_ohlcv",
    "DataSource",
    "TickFlowSource",
    "BaostockSource",
    "AkshareSource",
    "get_sources",
    "source_label",
    "ColumnDiff",
    "VerifyResult",
    "verify_symbol",
    # 批量预同步与本地面板
    "SyncReport",
    "sync_symbols",
    "load_panel",
    # 估值分位
    "ValuationPercentile",
    "fetch_valuation_percentile",
    "format_valuation",
    # 宏观数据
    "MacroRegime",
    "MacroSnapshot",
    "detect_macro_regime",
    "fetch_macro_snapshot",
    "format_macro_regime",
]
