"""策略与寻优回归测试。

验证所有内置策略输出信号的形状、取值域与防前视约定，
以及网格寻优的排序、裁剪与非法组合过滤。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.optimize import grid_search
from strategies import STRATEGIES, get_strategy
from strategies.ma_cross import MACrossStrategy
from tests.helpers import make_ohlcv

#: 输出连续仓位（而非 {-1,0,1} 离散信号）的策略
CONTINUOUS = {"grid"}


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_signal_shape_and_domain(name, random_walk_df):
    """每个策略：信号与数据等长、取值在 [-1,1]、无 NaN。"""
    strategy = get_strategy(name)
    sig = strategy.generate_signals(random_walk_df)
    assert len(sig) == len(random_walk_df)
    assert not sig.isna().any()
    if name in CONTINUOUS:
        assert ((sig >= -1.0) & (sig <= 1.0)).all()
    else:
        assert set(np.unique(sig.to_numpy())).issubset({-1.0, 0.0, 1.0})


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_long_only_by_default(name, random_walk_df):
    """默认不做空：不应出现 -1。"""
    strategy = get_strategy(name)
    sig = strategy.generate_signals(random_walk_df)
    assert (sig >= 0).all()


def test_allow_short_can_produce_negative(random_walk_df):
    strategy = get_strategy("ma_cross", allow_short=True)
    sig = strategy.generate_signals(random_walk_df)
    assert (sig == -1.0).any()


def test_unknown_strategy_raises():
    with pytest.raises(ValueError):
        get_strategy("no_such_strategy")


def test_grid_search_sorted_and_capped(random_walk_df):
    table = grid_search(
        random_walk_df, MACrossStrategy, metric="sharpe", top_n=3
    )
    assert len(table) <= 3
    # 结果按 sharpe 降序
    assert table["sharpe"].is_monotonic_decreasing


def test_grid_search_skips_invalid_fast_slow(random_walk_df):
    """fast >= slow 的非法组合应被跳过。"""
    table = grid_search(random_walk_df, MACrossStrategy, metric="sharpe")
    assert (table["fast"] < table["slow"]).all()


def test_grid_search_unknown_metric_raises(random_walk_df):
    with pytest.raises(KeyError):
        grid_search(random_walk_df, MACrossStrategy, metric="not_a_metric")


def test_grid_adds_position_when_price_falls():
    """网格：价格跌破基准一档后仓位应高于基准附近的半仓。"""
    n = 80
    close = np.full(n, 100.0)
    close[60:] = 88.0  # 跌 12%，超过 step=5% 的两档
    df = make_ohlcv(close)
    sig = get_strategy("grid", step=0.05, levels=5, window=30).generate_signals(df)
    assert sig.iloc[55] == pytest.approx(0.5)  # 基准附近持半仓
    assert sig.iloc[62] > 0.5  # 下跌后加仓
    assert sig.iloc[:30].eq(0.0).all()  # 基准未形成前空仓


def test_turtle_atr_stop_exits_earlier_than_donchian():
    """海龟：深度回撤时 ATR 止损应不晚于唐奇安通道离场。"""
    n = 120
    close = np.concatenate(
        [np.full(40, 100.0), np.linspace(100, 140, 40), np.linspace(140, 96, 40)]
    )
    df = make_ohlcv(close)
    turtle = get_strategy("turtle", entry=20, exit=10, atr_mult=2.0).generate_signals(df)
    donchian = get_strategy("donchian", entry=20, exit=10).generate_signals(df)
    # 两者都应在上涨段建仓
    assert (turtle.iloc[45:75] == 1).any()
    # 海龟首次离场不晚于唐奇安（ATR 止损只会提前）
    def first_exit(sig):
        held = np.where(sig.to_numpy() == 1)[0]
        after = np.where((sig.to_numpy() == 0) & (np.arange(len(sig)) > held[0]))[0]
        return after[0] if len(after) else len(sig)

    assert first_exit(turtle) <= first_exit(donchian)


@pytest.mark.parametrize(
    "name",
    ["grid", "turtle", "keltner", "supertrend", "dual_thrust", "cci", "williams_r"],
)
def test_new_strategies_no_lookahead(name, random_walk_df):
    """新策略无前视：截断尾部数据不改变历史信号。"""
    strategy = get_strategy(name)
    full = strategy.generate_signals(random_walk_df)
    trunc = get_strategy(name).generate_signals(random_walk_df.iloc[:-30])
    pd.testing.assert_series_equal(
        full.iloc[: len(trunc)], trunc, check_names=False
    )


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("ma_cross", {"fast": 30, "slow": 10}),
        ("ma_cross", {"fast": 20, "slow": 20}),
        ("donchian", {"entry": 10, "exit": 20}),
        ("turtle", {"entry": 10, "exit": 20}),
        ("turtle", {"atr_mult": -1.0}),
        ("keltner", {"atr_mult": 0.0}),
        ("keltner", {"window": 1}),
        ("supertrend", {"mult": -3.0}),
        ("dual_thrust", {"k1": 0.0}),
        ("dual_thrust", {"n": 0}),
        ("cci", {"entry": 100, "exit": -100}),
        ("williams_r", {"lower": -20, "upper": -80}),
        ("williams_r", {"lower": -120, "upper": -20}),
    ],
)
def test_invalid_params_raise_value_error(name, params):
    """跨参数校验：非法组合在构造期抛 ValueError（含修改提示）。"""
    with pytest.raises(ValueError):
        get_strategy(name, **params)


def test_valid_boundary_params_accepted():
    """边界合法组合不应误杀（donchian/turtle 网格含 entry == exit）。"""
    get_strategy("donchian", entry=20, exit=20)
    get_strategy("turtle", entry=20, exit=20, atr_mult=2.0)


def test_grid_search_skips_invalid_generic(random_walk_df):
    """通用非法组合过滤：cci 网格混入 entry >= exit 组合应被跳过。"""
    from strategies.cci import CCIStrategy

    table = grid_search(
        random_walk_df,
        CCIStrategy,
        param_grid={"period": [14, 20], "entry": [-100, 100], "exit": [100]},
        metric="sharpe",
    )
    assert len(table) > 0
    assert (table["entry"] < table["exit"]).all()
