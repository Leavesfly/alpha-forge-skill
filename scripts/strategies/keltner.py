"""Keltner 通道策略（ATR 通道趋势跟踪）。

以 EMA 为中轨，上下轨为中轨 ± atr_mult × ATR：
- 入场：收盘价突破上轨做多（开空需 allow_short，跌破下轨做空）；
- 离场：多头回落到中轨之下平仓（空头回升到中轨之上平仓）。
通道与 ATR 均用 shift(1) 的历史窗口，避免前视。

与布林带（标准差通道）相比，ATR 通道对跳空缺口更敏感、对成交密集区
的假突破更钝化，适合波动结构清晰的趋势品种。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


def _atr(df: pd.DataFrame, window: int) -> pd.Series:
    """平均真实波幅（历史窗口，shift(1) 防前视）。"""
    close = df["close"].astype(float)
    high = df["high"].astype(float) if "high" in df.columns else close
    low = df["low"].astype(float) if "low" in df.columns else close
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(window).mean().shift(1)


class KeltnerStrategy(Strategy):
    name = "keltner"
    display_name = "Keltner 通道"
    param_grid = {
        "window": [10, 20, 30],
        "atr_mult": [1.5, 2.0, 2.5],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"window": 20, "atr_mult": 2.0, "allow_short": False}

    def validate_params(self) -> None:
        if int(self.params["window"]) < 2:
            raise ValueError("Keltner 通道 window 应 >= 2。")
        if float(self.params["atr_mult"]) <= 0:
            raise ValueError("atr_mult 应为正数（如 2.0）。")

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        window = int(self.params["window"])
        atr_mult = float(self.params["atr_mult"])
        allow_short = bool(self.params.get("allow_short"))

        close = df["close"].astype(float)
        mid = close.ewm(span=window, adjust=False).mean().shift(1)
        atr = _atr(df, window)
        upper = (mid + atr_mult * atr).to_numpy()
        lower = (mid - atr_mult * atr).to_numpy()
        mid_np = mid.to_numpy()
        c = close.to_numpy()

        n = len(c)
        out = np.zeros(n)
        pos = 0
        for i in range(n):
            if np.isnan(upper[i]) or np.isnan(mid_np[i]):
                out[i] = 0
                continue
            if pos == 0:
                if c[i] > upper[i]:
                    pos = 1
                elif allow_short and c[i] < lower[i]:
                    pos = -1
            elif pos == 1:
                if c[i] < mid_np[i]:
                    pos = -1 if (allow_short and c[i] < lower[i]) else 0
            else:  # pos == -1
                if c[i] > mid_np[i]:
                    pos = 1 if c[i] > upper[i] else 0
            out[i] = pos

        return pd.Series(out, index=close.index).astype(int)
