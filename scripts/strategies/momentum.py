"""动量策略（Momentum / ROC）。

过去 N 期收益率为正则做多；开启做空时，动量为负则做空。
"""

from __future__ import annotations

import pandas as pd

from .base import Strategy


class MomentumStrategy(Strategy):
    name = "momentum"
    display_name = "动量"
    param_grid = {
        "period": [10, 20, 30, 60],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"period": 20, "allow_short": False}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        period = int(self.params["period"])
        close = df["close"]

        # 过去 period 期的收益率（动量）
        roc = close.pct_change(period)

        # 动量为正做多
        long_signal = roc > 0
        signal = long_signal.astype(int)
        # 开启做空：动量为负持有空头 (-1)
        if self.params.get("allow_short"):
            signal = signal.where(long_signal, -1)
        # 动量未形成前不入场
        signal[roc.isna()] = 0
        return signal
