"""标签工程：三重障碍标注与 meta-labeling（López de Prado, AFML）。

固定持有期标签（build_target）把「什么时候退出」硬编码为 horizon 期后，
与真实交易的止盈/止损行为脱节。三重障碍（triple barrier）为每笔潜在交易
设置三道退出：止盈线（上障碍）、止损线（下障碍）、最长持有期（垂直障碍），
按先触发者定标签，更贴近实际交易结果。

meta-labeling 则不预测方向，而是给「一级策略已给出的信号」打分：
该信号按三重障碍规则执行是否赚钱。模型学到的是「什么时候该相信策略」，
用于过滤假信号——对任何现有策略都可叠加，无需改动策略本身。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def rolling_vol(close: pd.Series, window: int = 20) -> pd.Series:
    """滚动收益波动率（障碍宽度的尺度基准）。"""
    return close.pct_change().rolling(window).std()


def triple_barrier_labels(
    close: pd.Series,
    horizon: int = 5,
    pt_mult: float = 2.0,
    sl_mult: float = 1.0,
    vol_window: int = 20,
    side: np.ndarray | None = None,
) -> pd.Series:
    """三重障碍标签：{1=先触止盈, 0=先触止损/到期不赚}，未实现为 NaN。

    Args:
        close: 收盘价序列。
        horizon: 垂直障碍（最长持有期）。
        pt_mult: 止盈障碍宽度 = pt_mult × 滚动波动率。
        sl_mult: 止损障碍宽度 = sl_mult × 滚动波动率。
        vol_window: 波动率滚动窗口。
        side: 可选的方向数组（1 多 / -1 空）；空头时止盈止损镜像。
            缺省全部按多头。

    Returns:
        与 close 等长的标签 Series；末尾 horizon 根与波动率未形成段为 NaN。
    """
    px = close.to_numpy(dtype=float)
    vol = rolling_vol(close, vol_window).to_numpy(dtype=float)
    n = len(px)
    labels = np.full(n, np.nan)
    sides = np.ones(n) if side is None else np.asarray(side, dtype=float)

    for i in range(n - 1):
        v = vol[i]
        s = sides[i]
        if not np.isfinite(v) or v <= 0 or s == 0:
            continue
        end = min(i + horizon, n - 1)
        if end <= i:
            continue
        # 多头：路径收益 = p/p0-1；空头取相反数，使「赚钱」方向统一为正
        path = (px[i + 1 : end + 1] / px[i] - 1.0) * s
        upper, lower = pt_mult * v, -sl_mult * v
        hit_up = np.argmax(path >= upper) if (path >= upper).any() else -1
        hit_dn = np.argmax(path <= lower) if (path <= lower).any() else -1
        if hit_up >= 0 and (hit_dn < 0 or hit_up <= hit_dn):
            labels[i] = 1.0
        elif hit_dn >= 0:
            labels[i] = 0.0
        elif i + horizon <= n - 1:  # 垂直障碍到期：按到期收益符号
            labels[i] = 1.0 if path[-1] > 0 else 0.0
        # else：垂直障碍尚未到期（近端），保持 NaN 不进训练集
    return pd.Series(labels, index=close.index)


def meta_labels(
    close: pd.Series,
    primary_signals: pd.Series | np.ndarray,
    horizon: int = 5,
    pt_mult: float = 2.0,
    sl_mult: float = 1.0,
    vol_window: int = 20,
) -> pd.Series:
    """meta-labeling 标签：一级信号按三重障碍执行是否赚钱。

    仅在一级信号非零的 bar 上有标签（1=该信号赚钱 / 0=不赚钱），
    信号为零或结果未实现的 bar 为 NaN。
    """
    sig = np.sign(np.asarray(primary_signals, dtype=float))
    tb = triple_barrier_labels(
        close, horizon=horizon, pt_mult=pt_mult, sl_mult=sl_mult,
        vol_window=vol_window, side=sig,
    )
    out = tb.to_numpy(copy=True)
    out[sig == 0] = np.nan
    return pd.Series(out, index=tb.index)
