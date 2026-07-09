"""因子预处理与合成。

横截面（每个交易日对所有标的）做 MAD 去极值 + z-score 标准化，再按权重
合成综合得分。对缺失值鲁棒：综合得分为「可用因子的加权平均 z 分」。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: MAD 到标准差的一致性系数
_MAD_SCALE = 1.4826


def winsorize_cross(frame: pd.DataFrame, n_mad: float = 3.0) -> pd.DataFrame:
    """按行（横截面）做 MAD 去极值：中位数 ± n×1.4826×MAD 截断。"""
    med = frame.median(axis=1)
    mad = frame.sub(med, axis=0).abs().median(axis=1)
    span = n_mad * _MAD_SCALE * mad
    lower = med - span
    upper = med + span
    return frame.clip(lower=lower, upper=upper, axis=0)


def zscore_cross(frame: pd.DataFrame) -> pd.DataFrame:
    """按行（横截面）做 z-score 标准化。"""
    mean = frame.mean(axis=1)
    std = frame.std(axis=1).replace(0.0, np.nan)
    return frame.sub(mean, axis=0).div(std, axis=0)


def standardize(frame: pd.DataFrame, n_mad: float = 3.0) -> pd.DataFrame:
    """去极值 + 标准化。"""
    return zscore_cross(winsorize_cross(frame, n_mad))


def composite_score(
    factor_frames: dict[str, pd.DataFrame],
    weights: dict[str, float] | None = None,
    n_mad: float = 3.0,
) -> pd.DataFrame:
    """将多个因子矩阵合成综合得分（日期 × 标的）。

    各因子先标准化，再按权重求「有效因子加权平均 z 分」，对缺失鲁棒。

    Args:
        factor_frames: {因子名: 因子矩阵}，各矩阵需同 index/columns 对齐。
        weights: {因子名: 权重}，默认等权。
        n_mad: 去极值的 MAD 倍数。
    """
    if not factor_frames:
        raise ValueError("没有可用于合成的因子。")
    weights = weights or {name: 1.0 for name in factor_frames}

    weighted_sum: pd.DataFrame | None = None
    weight_total: pd.DataFrame | None = None
    for name, frame in factor_frames.items():
        w = float(weights.get(name, 1.0))
        z = standardize(frame, n_mad)
        contrib = (z * w).fillna(0.0)
        active = z.notna().astype(float) * w
        weighted_sum = contrib if weighted_sum is None else weighted_sum.add(contrib, fill_value=0.0)
        weight_total = active if weight_total is None else weight_total.add(active, fill_value=0.0)

    return weighted_sum.div(weight_total.replace(0.0, np.nan))
