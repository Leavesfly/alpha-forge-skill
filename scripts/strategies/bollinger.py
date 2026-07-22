"""布林带策略（均值回归模式）。

价格跌破下轨视为超卖买入，回升至中轨（均线）上方或触及上轨时卖出空仓。
采用状态延续逻辑，避免频繁进出。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class BollingerStrategy(Strategy):
    name = "bollinger"
    display_name = "布林带"
    param_grid = {
        "window": [10, 20, 30],
        "num_std": [1.5, 2.0, 2.5],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"window": 20, "num_std": 2.0, "allow_short": False}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        window = int(self.params["window"])
        num_std = float(self.params["num_std"])
        close = df["close"]

        # 布林带三轨：中轨 = SMA，上下轨 = 中轨 ± N × 标准差
        mid = close.rolling(window).mean()
        std = close.rolling(window).std()
        upper = mid + num_std * std  # 上轨：统计意义上的价格天花板
        lower = mid - num_std * std  # 下轨：统计意义上的价格地板

        # 状态机：跌破下轨买入(1)，回到中轨上方或突破上轨卖出(0)
        # 开启做空时，突破上轨转为空头(-1)
        short_val = -1.0 if self.params.get("allow_short") else 0.0
        raw = pd.Series(np.nan, index=close.index)
        raw[close < lower] = 1.0
        raw[close >= upper] = short_val
        raw[(close >= mid) & (close.shift(1) < mid.shift(1))] = 0.0
        signal = raw.ffill().fillna(0.0).astype(int)
        signal[mid.isna()] = 0
        return signal
