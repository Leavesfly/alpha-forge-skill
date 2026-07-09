"""KDJ 随机指标策略。

基于 RSV 计算 K、D、J 三线：K 上穿 D（金叉）做多，下穿（死叉）平多。
开启做空时，死叉持有空头。
"""

from __future__ import annotations

import pandas as pd

from .base import Strategy


def compute_kdj(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    n: int,
    k_period: int,
    d_period: int,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """计算 KDJ 三线。K/D 采用 SMA 平滑，J = 3K - 2D。"""
    low_n = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0.0, pd.NA) * 100
    rsv = rsv.astype(float).fillna(50.0)

    k = rsv.ewm(alpha=1 / k_period, adjust=False).mean()
    d = k.ewm(alpha=1 / d_period, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


class KDJStrategy(Strategy):
    name = "kdj"
    display_name = "KDJ"
    param_grid = {
        "n": [9, 14, 21],
        "k_period": [3, 5],
        "d_period": [3, 5],
    }

    @classmethod
    def default_params(cls) -> dict:
        return {"n": 9, "k_period": 3, "d_period": 3, "allow_short": False}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        n = int(self.params["n"])
        k_period = int(self.params["k_period"])
        d_period = int(self.params["d_period"])
        close = df["close"]
        # 无 high/low 时退化为收盘价
        high = df["high"] if "high" in df.columns else close
        low = df["low"] if "low" in df.columns else close

        k, d, _ = compute_kdj(high, low, close, n, k_period, d_period)

        # K 在 D 之上（金叉后）持有多头
        long_signal = k > d
        signal = long_signal.astype(int)
        # 开启做空：死叉时持有空头 (-1)
        if self.params.get("allow_short"):
            signal = signal.where(long_signal, -1)
        # 指标未形成前不入场
        signal.iloc[:n] = 0
        return signal
