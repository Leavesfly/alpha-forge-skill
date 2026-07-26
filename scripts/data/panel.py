"""批量面板读取：多标的直接读本地缓存，组装宽表（不查 TTL、不走网络）。

适用场景：run_sync.py 预同步后，全市场/多标的研究直接从本地缓存批量装载
数据（如横截面因子、组合优化），避免逐只走「网络拉取 + 单标的缓存判断」。

与 ``datafeed.fetch_prices`` 的区别：本模块纯本地读取（离线可用、毫秒级），
缺失标的返回清单由调用方决策；fetch_prices 走完整拉取链路（可能联网）。
"""

from __future__ import annotations

import pandas as pd

from .cache import (
    CacheConfig,
    _key,
    _read_df,
    default_config,
    find_date_column,
    normalize_adjust,
)
from .sources import source_label


def load_panel(
    symbols: list[str],
    period: str = "1d",
    count: int = 1250,
    adjust: str = "forward",
    field: str = "close",
    strict: bool = False,
    config: CacheConfig | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """从本地缓存批量装载多标的数据为宽表（索引=日期，列=标的）。

    Args:
        symbols: 标的代码列表。
        period: K 线周期。
        count: 每只标的取尾部 K 线数量。
        adjust: 复权口径（自动归一化）。
        field: 取哪个字段列（close/open/high/low/volume）。
        strict: True 时任一标的缺缓存即抛错；False 时缺失记入返回清单。
        config: 缓存配置；None 时用 ``default_config(period)``。

    Returns:
        ``(宽表 DataFrame, 缺失标的列表)``；宽表保留 NaN（不做内连接），
        对齐口径由调用方按需 ``dropna``。

    Raises:
        RuntimeError: ``strict=True`` 且存在缺失标的时。
    """
    config = config or default_config(period)
    adj = normalize_adjust(adjust)
    source = source_label()

    series: dict[str, pd.Series] = {}
    missing: list[str] = []
    for sym in symbols:
        df = _read_cached(config, sym, period, adj, source)
        if df is None or field not in df.columns:
            missing.append(sym)
            continue
        df = df.tail(count)
        date_col = find_date_column(df)
        idx = pd.to_datetime(df[date_col]) if date_col else pd.RangeIndex(len(df))
        series[sym] = pd.Series(df[field].astype(float).values, index=idx)

    if strict and missing:
        raise RuntimeError(
            f"本地缓存缺失 {len(missing)} 只标的（如 {missing[:5]}），"
            "请先运行 run_sync.py 同步后重试。"
        )

    panel = pd.DataFrame(series).sort_index() if series else pd.DataFrame()
    return panel, missing


def _read_cached(
    config: CacheConfig,
    symbol: str,
    period: str,
    adjust: str,
    source: str,
) -> pd.DataFrame | None:
    """直接按缓存键读数据文件（两种格式都探），不存在返回 None。"""
    base = config.cache_dir / _key(symbol, period, adjust, source)
    for fmt, suffix in (("parquet", ".parquet"), ("pickle", ".pkl")):
        if base.with_suffix(suffix).exists():
            try:
                return _read_df(base, fmt)
            except Exception:
                return None
    return None
