"""双均线交叉策略。

短期均线上穿长期均线时做多，下穿时空仓（金叉/死叉）。
支持简单均线 (SMA) 与指数均线 (EMA)。
"""

from __future__ import annotations

import pandas as pd

from .base import Strategy


class MACrossStrategy(Strategy):
    name = "ma_cross"
    display_name = "双均线交叉"
    param_grid = {
        "fast": [5, 10, 20],
        "slow": [20, 30, 60],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"fast": 5, "slow": 20, "ma_type": "sma", "allow_short": False}

    def validate_params(self) -> None:
        fast, slow = int(self.params["fast"]), int(self.params["slow"])
        if fast >= slow:
            raise ValueError(
                f"双均线参数要求 fast < slow，当前 fast={fast}, slow={slow}；"
                "请减小 fast 或增大 slow。"
            )

    def _ma(self, series: pd.Series, window: int) -> pd.Series:
        if self.params.get("ma_type", "sma").lower() == "ema":
            return series.ewm(span=window, adjust=False).mean()
        return series.rolling(window).mean()

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        fast = int(self.params["fast"])
        slow = int(self.params["slow"])
        close = df["close"]

        fast_ma = self._ma(close, fast)
        slow_ma = self._ma(close, slow)

        # 短均线在长均线之上则持有多头
        long_signal = fast_ma > slow_ma
        signal = long_signal.astype(int)
        # 开启做空：死叉时持有空头 (-1)
        if self.params.get("allow_short"):
            signal = signal.where(long_signal, -1)
        # 均线未形成前不入场
        signal[slow_ma.isna()] = 0
        return signal
