"""Dual Thrust 策略（区间突破）。

经典开盘区间突破法在日线上的近似实现：
- Range = max(HH - LC, HC - LL)，由过去 n 日的最高/最低/收盘计算（shift 1）；
- 上轨 = 当日开盘 + k1 × Range，下轨 = 当日开盘 - k2 × Range；
- 收盘价突破上轨做多，跌破下轨平多（allow_short 时反手做空）。

注：原版为日内策略（当日开盘算轨道、盘中触发）；本实现遵循引擎
shift(1) 约定，信号次日生效，属于日线级别的区间突破近似。
缺少 open 列时退化为用前一日收盘价代替开盘价。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class DualThrustStrategy(Strategy):
    name = "dual_thrust"
    display_name = "Dual Thrust"
    param_grid = {
        "n": [3, 4, 7],
        "k1": [0.4, 0.5, 0.7],
        "k2": [0.4, 0.5, 0.7],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"n": 4, "k1": 0.5, "k2": 0.5, "allow_short": False}

    def validate_params(self) -> None:
        if int(self.params["n"]) < 1:
            raise ValueError("Dual Thrust n 应 >= 1。")
        if float(self.params["k1"]) <= 0 or float(self.params["k2"]) <= 0:
            raise ValueError("k1/k2 应为正数（如 0.5）。")

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        n_days = int(self.params["n"])
        k1 = float(self.params["k1"])
        k2 = float(self.params["k2"])
        allow_short = bool(self.params.get("allow_short"))

        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        open_ = df["open"].astype(float) if "open" in df.columns else close.shift(1)

        # Range 由过去 n 日区间构成（不含当日，防前视）
        hh = high.rolling(n_days).max().shift(1)
        ll = low.rolling(n_days).min().shift(1)
        hc = close.rolling(n_days).max().shift(1)
        lc = close.rolling(n_days).min().shift(1)
        rng = pd.concat([hh - lc, hc - ll], axis=1).max(axis=1)

        upper = (open_ + k1 * rng).to_numpy()
        lower = (open_ - k2 * rng).to_numpy()
        c = close.to_numpy()

        n = len(c)
        out = np.zeros(n)
        pos = 0
        for i in range(n):
            if np.isnan(upper[i]) or np.isnan(lower[i]):
                out[i] = 0
                continue
            if c[i] > upper[i]:
                pos = 1
            elif c[i] < lower[i]:
                pos = -1 if allow_short else 0
            out[i] = pos

        return pd.Series(out, index=close.index).astype(int)
