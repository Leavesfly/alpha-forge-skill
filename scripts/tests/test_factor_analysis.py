"""因子研究平台测试：IC/IR、衰减、相关性、正交化。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.analysis import (
    compute_ic,
    factor_correlation,
    factor_decay,
    ic_summary,
    neutralize,
)


def _make_panel(n_periods=200, n_assets=20, seed=0):
    """构造价格面板与一个「与未来收益正相关」的因子。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n_periods, freq="B")
    cols = [f"S{i}" for i in range(n_assets)]

    # 每期给每只股票一个因子值；t->t+1 的收益由 factor[t] 驱动（+噪声）
    factor = pd.DataFrame(
        rng.normal(0, 1, (n_periods, n_assets)), index=dates, columns=cols
    )
    step_ret = (0.3 * factor + rng.normal(0, 1, (n_periods, n_assets))) / 100.0
    # 构造价格：prices[t+1]/prices[t]-1 == step_ret[t]（即由 factor[t] 驱动）
    price_factor = (1.0 + step_ret).cumprod()
    prices = 100.0 * price_factor.shift(1)
    prices.iloc[0] = 100.0
    return factor, prices, cols


def test_compute_ic_positive_for_predictive_factor():
    factor, prices, _ = _make_panel()
    ic = compute_ic(factor, prices, horizon=1, method="spearman")
    assert len(ic) > 0
    assert ic.mean() > 0  # 因子对未来收益有正预测力


def test_ic_summary_fields_and_ir():
    factor, prices, _ = _make_panel()
    ic = compute_ic(factor, prices, horizon=1)
    summ = ic_summary(ic)
    for key in ("ic_mean", "ic_std", "ic_ir", "t_stat", "hit_rate", "n"):
        assert key in summ
    assert summ["hit_rate"] > 0.5  # 多数期 IC 为正


def test_ic_summary_empty():
    summ = ic_summary(pd.Series([], dtype=float))
    assert summ["n"] == 0 and summ["ic_ir"] == 0.0


def test_factor_decay_table():
    factor, prices, _ = _make_panel()
    decay = factor_decay(factor, prices, horizons=(1, 5, 10))
    assert list(decay.index) == [1, 5, 10]
    assert "ic_mean" in decay.columns and "ic_ir" in decay.columns


def test_factor_correlation_matrix():
    factor, prices, cols = _make_panel()
    frames = {"f1": factor, "f2": factor * -1.0, "f3": factor.rank(axis=1)}
    corr = factor_correlation(frames, method="spearman")
    assert corr.shape == (3, 3)
    # 对角线为 1
    assert all(corr.loc[n, n] == pytest.approx(1.0) for n in frames)
    # f1 与 f2（取负）应强负相关
    assert corr.loc["f1", "f2"] < -0.9


def test_neutralize_removes_common_factor():
    """目标因子对自身的强相关因子中性化后，与其相关性应显著下降。"""
    rng = np.random.default_rng(1)
    dates = pd.date_range("2020-01-01", periods=120, freq="B")
    cols = [f"S{i}" for i in range(15)]
    base = pd.DataFrame(rng.normal(0, 1, (120, 15)), index=dates, columns=cols)
    target = base * 0.8 + pd.DataFrame(
        rng.normal(0, 0.3, (120, 15)), index=dates, columns=cols
    )

    resid = neutralize(target, [base])
    # 残差与 base 的平均横截面相关应接近 0
    corr = factor_correlation({"resid": resid, "base": base})
    assert abs(corr.loc["resid", "base"]) < 0.2
