"""MACD 策略。

计算 MACD (DIF) 与信号线 (DEA)，DIF 上穿 DEA 做多，下穿空仓。
"""

from __future__ import annotations

import pandas as pd

from .base import Strategy


class MACDStrategy(Strategy):
    name = "macd"
    display_name = "MACD"
    param_grid = {
        "fast": [8, 12, 16],
        "slow": [20, 26, 34],
        "signal": [7, 9, 12],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"fast": 12, "slow": 26, "signal": 9, "allow_short": False}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        fast = int(self.params["fast"])
        slow = int(self.params["slow"])
        signal_period = int(self.params["signal"])
        close = df["close"]

        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal_period, adjust=False).mean()

        # DIF 在 DEA 之上则持有多头
        long_signal = dif > dea
        signal = long_signal.astype(int)
        # 开启做空：DIF 下穿 DEA 时持有空头 (-1)
        if self.params.get("allow_short"):
            signal = signal.where(long_signal, -1)
        # 慢线尚未稳定的初始阶段不入场
        signal.iloc[:slow] = 0
        return signal
