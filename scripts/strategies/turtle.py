"""海龟交易策略（Turtle Trading）。

在唐奇安通道突破基础上加入 ATR 风控，是经典海龟法则的核心逻辑：
- 入场：收盘价突破过去 entry 日最高价做多（开空需 allow_short）；
- 离场：跌破过去 exit 日最低价，或自建仓价回撤超过 atr_mult × ATR（N 值止损）；
- ATR 采用 shift(1) 的历史窗口，通道同理，均不含当前 bar，避免前视。

与 donchian 的区别在于 ATR 止损：波动越大止损越宽，波动收敛后止损收紧，
限制单笔亏损约 atr_mult 个 N。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy


def _atr(df: pd.DataFrame, window: int) -> pd.Series:
    """平均真实波幅（用历史窗口，shift(1) 防前视）。"""
    close = df["close"].astype(float)
    high = df["high"].astype(float) if "high" in df.columns else close
    low = df["low"].astype(float) if "low" in df.columns else close
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(window).mean().shift(1)


class TurtleStrategy(Strategy):
    name = "turtle"
    display_name = "海龟交易"
    param_grid = {
        "entry": [20, 55],
        "exit": [10, 20],
        "atr_mult": [2.0, 3.0],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {
            "entry": 20,
            "exit": 10,
            "atr_window": 20,
            "atr_mult": 2.0,
            "allow_short": False,
        }

    def validate_params(self) -> None:
        entry, exit_ = int(self.params["entry"]), int(self.params["exit"])
        if exit_ > entry:
            raise ValueError(
                f"海龟策略要求 exit <= entry，当前 entry={entry}, exit={exit_}。"
            )
        if float(self.params["atr_mult"]) <= 0:
            raise ValueError("atr_mult 应为正数（如 2.0）。")

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        entry = int(self.params["entry"])
        exit_ = int(self.params["exit"])
        atr_window = int(self.params.get("atr_window", 20))
        atr_mult = float(self.params["atr_mult"])
        allow_short = bool(self.params.get("allow_short"))

        close = df["close"].astype(float)
        high = df["high"] if "high" in df.columns else close
        low = df["low"] if "low" in df.columns else close

        upper = high.rolling(entry).max().shift(1).to_numpy()
        lower = low.rolling(entry).min().shift(1).to_numpy()
        exit_low = low.rolling(exit_).min().shift(1).to_numpy()
        exit_high = high.rolling(exit_).max().shift(1).to_numpy()
        atr = _atr(df, atr_window).to_numpy()
        c = close.to_numpy()

        n = len(c)
        out = np.zeros(n)
        pos = 0
        entry_price = np.nan
        for i in range(n):
            if np.isnan(upper[i]) or np.isnan(exit_low[i]) or np.isnan(atr[i]):
                out[i] = 0
                continue
            if pos == 0:
                if c[i] > upper[i]:
                    pos, entry_price = 1, c[i]
                elif allow_short and c[i] < lower[i]:
                    pos, entry_price = -1, c[i]
            elif pos == 1:
                stop = entry_price - atr_mult * atr[i]
                if c[i] < exit_low[i] or c[i] <= stop:
                    pos, entry_price = 0, np.nan
            else:  # pos == -1
                stop = entry_price + atr_mult * atr[i]
                if c[i] > exit_high[i] or c[i] >= stop:
                    pos, entry_price = 0, np.nan
            out[i] = pos

        return pd.Series(out, index=close.index).astype(int)
