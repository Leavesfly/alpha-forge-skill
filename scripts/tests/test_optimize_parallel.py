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
