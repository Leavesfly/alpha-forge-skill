"""唐奇安通道突破策略（Donchian Channel / 海龟核心）。

收盘价突破过去 entry 日最高价做多；跌破过去 exit 日最低价平多。
开启做空时，跌破过去 entry 日最低价做空；突破过去 exit 日最高价平空。
通道使用历史窗口（shift 1）计算以避免前视偏差。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy
from .indicators import extract_ohlcv


class DonchianStrategy(Strategy):
    name = "donchian"
    display_name = "唐奇安通道突破"
    param_grid = {
        "entry": [20, 40, 55],
        "exit": [10, 20],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"entry": 20, "exit": 10, "allow_short": False}

    def validate_params(self) -> None:
        entry, exit_ = int(self.params["entry"]), int(self.params["exit"])
        if exit_ > entry:
            raise ValueError(
                f"唐奇安通道要求 exit <= entry，当前 entry={entry}, exit={exit_}；"
                "离场通道应不宽于入场通道。"
            )

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        entry = int(self.params["entry"])
        exit_ = int(self.params["exit"])
        allow_short = bool(self.params.get("allow_short"))

        _, high, low, close = extract_ohlcv(df)

        # 通道基于历史窗口（不含当前 bar），避免前视
        upper = high.rolling(entry).max().shift(1).to_numpy()
        lower = low.rolling(entry).min().shift(1).to_numpy()
        exit_low = low.rolling(exit_).min().shift(1).to_numpy()
        exit_high = high.rolling(exit_).max().shift(1).to_numpy()
        c = close.to_numpy()

        n = len(c)
        out = np.zeros(n)
        pos = 0
        for i in range(n):
            if np.isnan(upper[i]) or np.isnan(exit_low[i]):
                out[i] = 0
                continue
            if pos == 0:
                if c[i] > upper[i]:
                    pos = 1
                elif allow_short and c[i] < lower[i]:
                    pos = -1
            elif pos == 1:
                if c[i] < exit_low[i]:
                    pos = -1 if (allow_short and c[i] < lower[i]) else 0
            else:  # pos == -1
                if c[i] > exit_high[i]:
                    pos = 1 if c[i] > upper[i] else 0
            out[i] = pos

        return pd.Series(out, index=close.index).astype(int)
