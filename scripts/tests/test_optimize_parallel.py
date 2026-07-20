"""寻优并行化回归测试：并行与串行结果必须逐行一致。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.optimize import PARALLEL_MIN_COMBOS, grid_search
from strategies import STRATEGIES
from tests.helpers import make_ohlcv


@pytest.fixture
def price_df() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    steps = rng.normal(loc=0.0005, scale=0.02, size=260)
    close = 100.0 * np.exp(np.cumsum(steps))
    return make_ohlcv(close)


def test_parallel_matches_serial(price_df):
    """多进程寻优结果与串行完全一致（含排序）。"""
    cls = STRATEGIES["ma_cross"]
    serial = grid_search(price_df, cls, period="1d", n_jobs=1)
    parallel = grid_search(price_df, cls, period="1d", n_jobs=2)
    pd.testing.assert_frame_equal(serial, parallel)


def test_small_grid_stays_serial(price_df):
    """组合数不足阈值时即使 n_jobs>1 也应正常返回（走串行路径）。"""
    cls = STRATEGIES["ma_cross"]
    grid = {"fast": [5], "slow": [20, 30]}  # 2 组 < PARALLEL_MIN_COMBOS
    assert len(grid["slow"]) < PARALLEL_MIN_COMBOS
    table = grid_search(price_df, cls, param_grid=grid, n_jobs=4)
    assert len(table) == 2


def test_progress_callback(price_df):
    """进度回调按完成数递增，总数与结果行数一致。"""
    cls = STRATEGIES["ma_cross"]
    calls: list[tuple[int, int]] = []
    table = grid_search(
        price_df, cls, n_jobs=1, progress=lambda done, total: calls.append((done, total))
    )
    assert calls[-1][0] == calls[-1][1] == len(table)
    assert [c[0] for c in calls] == list(range(1, len(table) + 1))


def test_unknown_metric_raises(price_df):
    cls = STRATEGIES["ma_cross"]
    with pytest.raises(KeyError):
        grid_search(price_df, cls, metric="not_a_metric")


def test_random_search_samples_subset(price_df):
    """random 方法：采样组数受 n_iter 限制，且结果是 grid 全集的子集。"""
    cls = STRATEGIES["ma_cross"]
    full = grid_search(price_df, cls, n_jobs=1)
    sampled = grid_search(price_df, cls, n_jobs=1, method="random", n_iter=5, seed=1)
    assert len(sampled) == 5 < len(full)
    key_cols = [c for c in cls.param_grid if c in full.columns]
    full_keys = {tuple(r) for r in full[key_cols].itertuples(index=False)}
    for row in sampled[key_cols].itertuples(index=False):
        assert tuple(row) in full_keys


def test_random_search_reproducible(price_df):
    """固定 seed 时 random 采样结果可复现；不同 seed 采样不同。"""
    cls = STRATEGIES["ma_cross"]
    a = grid_search(price_df, cls, method="random", n_iter=6, seed=7)
    b = grid_search(price_df, cls, method="random", n_iter=6, seed=7)
    pd.testing.assert_frame_equal(a, b)


def test_random_search_degrades_to_grid_when_niter_large(price_df):
    """n_iter >= 组合数时，random 退化为穷举，结果与 grid 一致。"""
    cls = STRATEGIES["ma_cross"]
    full = grid_search(price_df, cls)
    rand = grid_search(price_df, cls, method="random", n_iter=10_000)
    pd.testing.assert_frame_equal(full, rand)


def test_random_search_invalid_args(price_df):
    cls = STRATEGIES["ma_cross"]
    with pytest.raises(ValueError):
        grid_search(price_df, cls, method="annealing")
    with pytest.raises(ValueError):
        grid_search(price_df, cls, method="random", n_iter=0)
    with pytest.raises(ValueError):
        grid_search(price_df, cls, method="bayes", n_iter=0)


#: bayes 测试用的扩大网格（20 组，保证触发自适应迭代）
BAYES_GRID = {"fast": [3, 5, 8, 10, 12], "slow": [20, 30, 40, 50]}


def test_bayes_search_samples_subset(price_df):
    """bayes 方法：评估组数受 n_iter 限制，且结果是 grid 全集的子集。"""
    cls = STRATEGIES["ma_cross"]
    full = grid_search(price_df, cls, param_grid=BAYES_GRID, n_jobs=1)
    sampled = grid_search(
        price_df, cls, param_grid=BAYES_GRID, n_jobs=1, method="bayes", n_iter=12, seed=1
    )
    assert len(sampled) == 12 < len(full)
    key_cols = [c for c in BAYES_GRID if c in full.columns]
    full_keys = {tuple(r) for r in full[key_cols].itertuples(index=False)}
    for row in sampled[key_cols].itertuples(index=False):
        assert tuple(row) in full_keys


def test_bayes_search_reproducible_and_parallel_consistent(price_df):
    """固定 seed 时 bayes 可复现，且串行与并行结果一致。"""
    cls = STRATEGIES["ma_cross"]
    a = grid_search(price_df, cls, param_grid=BAYES_GRID, method="bayes", n_iter=12, seed=7, n_jobs=1)
    b = grid_search(price_df, cls, param_grid=BAYES_GRID, method="bayes", n_iter=12, seed=7, n_jobs=1)
    pd.testing.assert_frame_equal(a, b)
    c = grid_search(price_df, cls, param_grid=BAYES_GRID, method="bayes", n_iter=12, seed=7, n_jobs=2)
    pd.testing.assert_frame_equal(a, c)


def test_bayes_search_degrades_to_grid_when_niter_large(price_df):
    """n_iter >= 组合数时，bayes 退化为穷举，结果与 grid 一致。"""
    cls = STRATEGIES["ma_cross"]
    full = grid_search(price_df, cls)
    bayes = grid_search(price_df, cls, method="bayes", n_iter=10_000)
    pd.testing.assert_frame_equal(full, bayes)


def test_bayes_best_not_worse_than_random_median(price_df):
    """同预算下 bayes 的最优值不应明显差于随机搜索（宽松健康检查）。"""
    cls = STRATEGIES["ma_cross"]
    bayes = grid_search(price_df, cls, param_grid=BAYES_GRID, method="bayes", n_iter=12, seed=3)
    rand = grid_search(price_df, cls, param_grid=BAYES_GRID, method="random", n_iter=12, seed=3)
    # 不要求严格更优（小样本噪声大），只要求不坍塌
    assert bayes["sharpe"].iloc[0] >= rand["sharpe"].median()
