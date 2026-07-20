"""CCI 商品通道指数策略（超买超卖均值回归）。

CCI = (TP - SMA(TP)) / (0.015 × 平均绝对偏差)，TP = (high+low+close)/3。
CCI 跌破 entry（默认 -100，超卖）买入并维持多头，直到回升越过
exit（默认 +100，超买）卖出空仓（状态延续，避免频繁进出）。
开启做空时在超买端反向持有空头，直到回落到超卖端平空。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class CCIStrategy(Strategy):
    name = "cci"
    display_name = "CCI 顺势指标"
    param_grid = {
        "period": [14, 20, 28],
        "entry": [-150, -100],
        "exit": [100, 150],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"period": 20, "entry": -100, "exit": 100, "allow_short": False}

    def validate_params(self) -> None:
        if int(self.params["period"]) < 2:
            raise ValueError("CCI period 应 >= 2。")
        if float(self.params["entry"]) >= float(self.params["exit"]):
            raise ValueError(
                f"CCI 要求 entry < exit（超卖买入阈值应低于超买卖出阈值），"
                f"当前 entry={self.params['entry']}, exit={self.params['exit']}。"
            )

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        period = int(self.params["period"])
        entry = float(self.params["entry"])
        exit_ = float(self.params["exit"])
        allow_short = bool(self.params.get("allow_short"))

        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close

        tp = (high + low + close) / 3.0
        sma = tp.rolling(period).mean()
        mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
        cci = ((tp - sma) / (0.015 * mad)).to_numpy()

        n = len(close)
        out = np.zeros(n)
        pos = 0
        for i in range(n):
            if np.isnan(cci[i]):
                out[i] = 0
                continue
            if pos == 0:
                if cci[i] < entry:
                    pos = 1
                elif allow_short and cci[i] > exit_:
                    pos = -1
            elif pos == 1:
                if cci[i] > exit_:
                    pos = -1 if allow_short else 0
            else:  # pos == -1
                if cci[i] < entry:
                    pos = 1
            out[i] = pos

        return pd.Series(out, index=close.index).astype(int)
