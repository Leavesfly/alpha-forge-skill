"""SuperTrend 策略（ATR 追踪止损趋势线）。

基于 ATR 上下轨迭代生成 SuperTrend 线：
- 基础上轨 = (high+low)/2 + mult × ATR，基础下轨 = (high+low)/2 - mult × ATR；
- 上/下轨随价格单向收紧（追踪止损），收盘价穿越 SuperTrend 线时趋势翻转；
- 上升趋势（价格在线上方）持有多头，下降趋势空仓（allow_short 时持有空头）。

信号取值 {-1, 0, 1}，指标未形成前输出 0；ATR 用 shift(1) 历史窗口防前视。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class SuperTrendStrategy(Strategy):
    name = "supertrend"
    display_name = "SuperTrend"
    param_grid = {
        "period": [7, 10, 14],
        "mult": [2.0, 3.0, 4.0],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"period": 10, "mult": 3.0, "allow_short": False}

    def validate_params(self) -> None:
        if int(self.params["period"]) < 2:
            raise ValueError("SuperTrend period 应 >= 2。")
        if float(self.params["mult"]) <= 0:
            raise ValueError("mult 应为正数（如 3.0）。")

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        period = int(self.params["period"])
        mult = float(self.params["mult"])
        allow_short = bool(self.params.get("allow_short"))

        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close

        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)
        atr = tr.rolling(period).mean().shift(1)

        hl2 = (high + low) / 2.0
        basic_upper = (hl2 + mult * atr).to_numpy()
        basic_lower = (hl2 - mult * atr).to_numpy()
        c = close.to_numpy()

        n = len(c)
        out = np.zeros(n)
        final_upper = np.nan
        final_lower = np.nan
        trend = 0  # 1=上升趋势，-1=下降趋势，0=未形成
        for i in range(n):
            if np.isnan(basic_upper[i]):
                out[i] = 0
                continue
            # 追踪收紧：上轨只降不升（下降趋势中），下轨只升不降（上升趋势中）
            if np.isnan(final_upper) or basic_upper[i] < final_upper or c[i - 1] > final_upper:
                final_upper = basic_upper[i]
            if np.isnan(final_lower) or basic_lower[i] > final_lower or c[i - 1] < final_lower:
                final_lower = basic_lower[i]

            if trend <= 0 and c[i] > final_upper:
                trend = 1
            elif trend >= 0 and c[i] < final_lower:
                trend = -1
            elif trend == 0:
                out[i] = 0
                continue

            out[i] = 1 if trend == 1 else (-1 if allow_short else 0)

        return pd.Series(out, index=close.index).astype(int)
