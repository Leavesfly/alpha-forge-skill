"""CCI 商品通道指数策略（超买超卖均值回归）。

CCI = (TP - SMA(TP)) / (0.015 × MAD)，其中：
- TP = (high + low + close) / 3（典型价格）；
- MAD = 窗口内 |TP - SMA(TP)| 的均值（平均绝对偏差）；
- 0.015 为 Lambert 原始设计中的缩放常数，使 CCI 在 ±100 附近具有统计意义。

CCI 跌破 entry（默认 -100，超卖）买入并维持多头，直到回升越过
exit（默认 +100，超买）卖出空仓（状态延续，避免频繁进出）。
开启做空时在超买端反向持有空头，直到回落到超卖端平空。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy
from .indicators import extract_ohlcv


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

        _, high, low, close = extract_ohlcv(df)

        # 典型价格 TP = (H + L + C) / 3
        tp = (high + low + close) / 3.0
        sma = tp.rolling(period).mean()
        # 平均绝对偏差 MAD：向量化实现（避免 rolling.apply 的逐窗口 Python 回调）
        mad = (tp - sma).abs().rolling(period).mean()
        # CCI = (TP - SMA) / (0.015 × MAD)，0.015 为 Lambert 缩放常数
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
