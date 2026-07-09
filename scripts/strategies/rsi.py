"""RSI 超买超卖（均值回归）策略。

RSI 低于下阈值（超卖）时买入并持有，直到 RSI 高于上阈值（超买）时卖出空仓。
采用状态延续逻辑：在超卖买入后维持多头，直到出现超买信号。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


def compute_rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder 平滑法计算 RSI。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    # 当平均亏损为 0 时 RSI 视为 100
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi


class RSIStrategy(Strategy):
    name = "rsi"
    display_name = "RSI 超买超卖"
    param_grid = {
        "period": [6, 14, 21],
        "lower": [20, 30, 40],
        "upper": [60, 70, 80],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"period": 14, "lower": 30, "upper": 70, "allow_short": False}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        period = int(self.params["period"])
        lower = float(self.params["lower"])
        upper = float(self.params["upper"])
        close = df["close"]

        rsi = compute_rsi(close, period)

        # 状态机：超卖买入(1)，超买卖出(0)；开启做空时超买转空头(-1)
        short_val = -1.0 if self.params.get("allow_short") else 0.0
        raw = pd.Series(np.nan, index=close.index)
        raw[rsi < lower] = 1.0
        raw[rsi > upper] = short_val
        signal = raw.ffill().fillna(0.0).astype(int)
        signal[rsi.isna()] = 0
        return signal
