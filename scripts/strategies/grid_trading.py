"""网格交易策略。

以基准价（rolling 均线）为中心划分等比价格网格：价格每跌一档加仓
1/(2*levels)，每涨一档减仓 1/(2*levels)；在基准价附近持半仓。
输出 [0, 1] 的连续目标仓位（与波动率目标仓位同为连续仓位模式），
适合震荡市，单边下跌市会持续加仓（风险自担，可叠加 --stop-loss）。

基准价使用 shift(1) 的历史均线，不含当前 bar，避免前视。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


class GridStrategy(Strategy):
    name = "grid"
    display_name = "网格交易"
    param_grid = {
        "step": [0.03, 0.05, 0.08],
        "levels": [3, 5],
        "window": [60, 120],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"step": 0.05, "levels": 5, "window": 60}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        step = float(self.params["step"])
        levels = int(self.params["levels"])
        window = int(self.params["window"])
        close = df["close"].astype(float)

        # 基准价：历史均线（不含当前 bar）
        base = close.rolling(window).mean().shift(1)
        deviation = (close - base) / base

        # 低于基准 k 档 -> 加 k 份仓；高于基准 -> 减仓（k 为负）
        k = np.floor(-deviation / step)
        k = k.clip(lower=-levels, upper=levels)
        position = 0.5 + k / (2.0 * levels)
        position = position.clip(lower=0.0, upper=1.0)

        # 基准未形成前空仓
        return position.where(base.notna(), 0.0)
