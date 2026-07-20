"""测试辅助函数：构造确定性合成行情数据。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_ohlcv(
    close: np.ndarray,
    start: str = "2020-01-01",
    freq: str = "B",
    open_prices: np.ndarray | None = None,
    volume: np.ndarray | None = None,
) -> pd.DataFrame:
    """由收盘价数组构造包含 trade_date/OHLCV 的 DataFrame。

    open/high/low 由 close 派生（确定性），volume 为常数，
    足以驱动引擎、策略与指标的回归测试。

    Args:
        open_prices: 自定义开盘价（用于测试跳空/次日开盘成交）；默认取上一日收盘。
        volume: 自定义成交量（用于测试停牌）；默认常数。
    """
    close = np.asarray(close, dtype=float)
    n = len(close)
    dates = pd.date_range(start=start, periods=n, freq=freq)
    prev = np.concatenate([[close[0]], close[:-1]])
    open_arr = prev if open_prices is None else np.asarray(open_prices, dtype=float)
    vol = np.full(n, 1_000_000.0) if volume is None else np.asarray(volume, dtype=float)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": open_arr,
            "high": np.maximum(close, open_arr) * 1.01,
            "low": np.minimum(close, open_arr) * 0.99,
            "close": close,
            "volume": vol,
        }
    )
