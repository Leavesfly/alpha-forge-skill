"""威廉指标策略（Williams %R 超买超卖）。

%R = (HH - close) / (HH - LL) × -100，取值 [-100, 0]：
- %R 低于 lower（默认 -80，超卖）买入并维持多头；
- %R 高于 upper（默认 -20，超买）卖出空仓（状态延续）。
开启做空时在超买端持有空头，直到回落到超卖端平空。
HH/LL 用 shift(1) 的历史窗口计算，避免前视。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy
from .indicators import extract_ohlcv


class WilliamsRStrategy(Strategy):
    name = "williams_r"
    display_name = "威廉指标 %R"
    param_grid = {
        "period": [10, 14, 21],
        "lower": [-90, -80],
        "upper": [-20, -10],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"period": 14, "lower": -80, "upper": -20, "allow_short": False}

    def validate_params(self) -> None:
        if int(self.params["period"]) < 2:
            raise ValueError("威廉指标 period 应 >= 2。")
        lower, upper = float(self.params["lower"]), float(self.params["upper"])
        if not (-100 <= lower < upper <= 0):
            raise ValueError(
                f"威廉指标要求 -100 <= lower < upper <= 0，"
                f"当前 lower={lower}, upper={upper}。"
            )

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        period = int(self.params["period"])
        lower = float(self.params["lower"])
        upper = float(self.params["upper"])
        allow_short = bool(self.params.get("allow_short"))

        _, high, low, close = extract_ohlcv(df)

        # %R = (HH - C) / (HH - LL) × -100，取值 [-100, 0]
        # HH/LL 用 shift(1) 历史窗口，不含当前 bar
        hh = high.rolling(period).max().shift(1)
        ll = low.rolling(period).min().shift(1)
        span = (hh - ll).replace(0.0, np.nan)  # 防除零
        wr = ((hh - close) / span * -100.0).to_numpy()

        n = len(close)
        out = np.zeros(n)
        pos = 0
        for i in range(n):
            if np.isnan(wr[i]):
                out[i] = 0
                continue
            if pos == 0:
                if wr[i] < lower:
                    pos = 1
                elif allow_short and wr[i] > upper:
                    pos = -1
            elif pos == 1:
                if wr[i] > upper:
                    pos = -1 if allow_short else 0
            else:  # pos == -1
                if wr[i] < lower:
                    pos = 1
            out[i] = pos

        return pd.Series(out, index=close.index).astype(int)
