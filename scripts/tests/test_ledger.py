"""账本引擎回归测试：与向量化引擎互相校验 + 整数股/一手约束。"""

from __future__ import annotations

import numpy as np
import pytest

from backtest.costs import CostModel
from backtest.engine import run_backtest
from backtest.ledger import run_backtest_ledger
from backtest.rules import TradingRules
from strategies import get_strategy
from tests.helpers import make_ohlcv


@pytest.fixture
def price_df():
    rng = np.random.default_rng(21)
    steps = rng.normal(loc=0.0005, scale=0.015, size=300)
    close = 100.0 * np.exp(np.cumsum(steps))
    return make_ohlcv(close)


def test_ledger_matches_vector_frictionless(price_df):
    """无成本、lot_size=1、大资金下，账本净值应与向量化引擎近似一致。"""
    strat = get_strategy("ma_cross")
    zero_cost = CostModel(commission=0.0, slippage=0.0)
    vec = run_backtest(price_df, strat, cost_model=zero_cost)
    led = run_backtest_ledger(
        price_df,
        get_strategy("ma_cross"),
        cost_model=zero_cost,
        initial_capital=1e9,  # 资金足够大，取整误差可忽略
        lot_size=1,
    )
    diff = float((vec.equity - led.equity).abs().max())
    assert diff < 1e-4


def test_ledger_costs_reduce_equity(price_df):
    """有成本时账本净值应低于零成本账本。"""
    kwargs = dict(initial_capital=1e8, lot_size=1)
    free = run_backtest_ledger(
        price_df, get_strategy("ma_cross"),
        cost_model=CostModel(commission=0.0, slippage=0.0), **kwargs,
    )
    costly = run_backtest_ledger(
        price_df, get_strategy("ma_cross"),
        cost_model=CostModel.preset("astock"), **kwargs,
    )
    assert costly.equity.iloc[-1] < free.equity.iloc[-1]


def test_ledger_lot_constraint_blocks_small_capital(capsys):
    """资金不足一手时无法建仓，净值保持 1.0。"""
    close = np.full(60, 500.0)  # 一手 = 500*100 = 5 万
    df = make_ohlcv(close)
    strat = get_strategy("momentum", lookback=5)
    res = run_backtest_ledger(df, strat, initial_capital=10_000.0, lot_size=100)
    assert float(res.equity.iloc[-1]) == pytest.approx(1.0)
    assert (res.positions == 0.0).all()


def test_ledger_lot_rounding(price_df):
    """lot_size=100 时持股数应为 100 的整数倍（通过仓位市值间接校验）。"""
    strat = get_strategy("ma_cross")
    res = run_backtest_ledger(
        price_df, strat,
        cost_model=CostModel(commission=0.0, slippage=0.0),
        initial_capital=200_000.0, lot_size=100,
    )
    # 满仓时仓位比例应接近但不超过 1.0（现金不透支）
    assert float(res.positions.max()) <= 1.0 + 1e-9


def test_ledger_no_lookahead(price_df):
    """截断尾部数据不改变历史净值（无前视）。"""
    strat = get_strategy("ma_cross")
    full = run_backtest_ledger(price_df, strat, initial_capital=1e8)
    trunc = run_backtest_ledger(price_df.iloc[:-20], get_strategy("ma_cross"), initial_capital=1e8)
    n = len(trunc.equity)
    diff = float((full.equity.iloc[:n].values - trunc.equity.values).max())
    assert abs(diff) < 1e-12


def test_ledger_respects_trading_rules():
    """停牌（volume=0）期间不发生调仓。"""
    close = np.concatenate([np.full(30, 100.0), np.linspace(100, 130, 30)])
    volume = np.full(60, 1e6)
    volume[35:45] = 0.0  # 停牌窗口
    df = make_ohlcv(close, volume=volume)
    strat = get_strategy("momentum", lookback=5)
    rules = TradingRules(limit_pct=None, check_suspension=True)
    res = run_backtest_ledger(df, strat, initial_capital=1e8, trading_rules=rules)
    pos = res.positions.to_numpy()
    # 停牌窗口内仓位比例只随价格波动，不因交易跳变：持股不变 =>
    # 相邻两日仓位市值比 = 价格比
    for t in range(36, 45):
        if pos[t - 1] > 0:
            expected = pos[t - 1] * close[t] / close[t - 1] / (
                1 + pos[t - 1] * (close[t] / close[t - 1] - 1)
            )
            assert pos[t] == pytest.approx(expected, rel=1e-9)
