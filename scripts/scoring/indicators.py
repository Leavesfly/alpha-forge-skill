"""评分所需技术指标助手。

公式与 ``strategies/`` 各策略保持一致（RSI/KDJ 直接复用策略模块实现，
MACD/ATR 与 macd.py / keltner.py 同公式）。所有指标只使用截至当日（含）
的数据；评分在最近一根已完成 K 线上进行，故 ATR 不做 shift(1)。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.kdj import compute_kdj
from strategies.rsi import compute_rsi

__all__ = [
    "compute_rsi",
    "compute_kdj",
    "atr",
    "macd",
    "efficiency_ratio",
    "annualized_vol",
]


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """平均真实波幅（含当日，用于交易计划价位）。

    TR 公式与 keltner.py 保持一致；无 high/low 列时退化为收盘价。
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float) if "high" in df.columns else close
    low = df["low"].astype(float) if "low" in df.columns else close
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(window).mean()


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series]:
    """MACD 快慢线（DIF/DEA），与 strategies/macd.py 同公式。"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif, dea


def efficiency_ratio(close: pd.Series, window: int = 20) -> pd.Series:
    """Kaufman 效率比 ER = |N 日净变动| / N 日逐日变动绝对值之和（0~1）。

    趋势越顺畅 ER 越接近 1，来回震荡则趋近 0。
    """
    change = (close - close.shift(window)).abs()
    volatility = close.diff().abs().rolling(window).sum()
    return (change / volatility.replace(0.0, np.nan)).clip(0.0, 1.0)


def annualized_vol(close: pd.Series, window: int = 60, ann: float = 252.0) -> pd.Series:
    """滚动年化波动率（日收益标准差 × √年化系数）。"""
    return close.pct_change().rolling(window).std(ddof=0) * np.sqrt(ann)
