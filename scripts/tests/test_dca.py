"""定投模块回归测试：XIRR 求解、现金流账本与各模式信号边界。

全部使用合成数据，不走网络。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dca.engine import MODES, run_dca_backtest
from dca.metrics import xirr
from tests.helpers import make_ohlcv


# ---------------------------------------------------------------- XIRR


def test_xirr_known_cashflow():
    """-1000 于 t=0 投入、一年后回收 1100，IRR 应为 10%。"""
    rate = xirr(np.array([-1000.0, 1100.0]), np.array([0.0, 1.0]))
    assert rate == pytest.approx(0.10, abs=1e-6)


def test_xirr_solution_zeroes_npv():
    """多笔现金流：解出的利率代回 NPV 应约等于 0。"""
    amounts = np.array([-100.0, -100.0, 215.0])
    fracs = np.array([0.0, 0.5, 1.0])
    rate = xirr(amounts, fracs)
    npv = float(np.sum(amounts / (1.0 + rate) ** fracs))
    assert abs(npv) < 1e-6


def test_xirr_same_sign_returns_nan():
    """现金流全部同号无解，应返回 nan。"""
    assert np.isnan(xirr(np.array([-1.0, -2.0]), np.array([0.0, 1.0])))
    assert np.isnan(xirr(np.array([1.0, 2.0]), np.array([0.0, 1.0])))


# ---------------------------------------------------------- 现金流账本


def test_fixed_mode_ledger_consistency(random_walk_df):
    """纯定投：每月首个交易日投入一次，账本口径自洽。"""
    amount = 1000.0
    result = run_dca_backtest(random_walk_df, freq="monthly", amount=amount, mode="fixed")

    dates = pd.to_datetime(random_walk_df["trade_date"])
    n_months = len({(d.year, d.month) for d in dates})
    assert result.metrics["num_contributions"] == n_months
    # 纯买入：累计投入 = 期数 × 每期金额；现金流恒为 -contribution
    assert result.metrics["total_invested"] == pytest.approx(n_months * amount)
    pd.testing.assert_series_equal(
        result.cashflow, -result.contribution, check_names=False
    )
    # 累计投入曲线单调不减，份额恒非负
    assert result.invested.is_monotonic_increasing
    assert (result.shares >= 0).all()
    # 一次性投入基准与定投同本金
    assert result.lumpsum_metrics["total_invested"] == pytest.approx(
        result.metrics["total_invested"]
    )


def test_fixed_mode_no_dca_baseline(random_walk_df):
    """fixed 模式自身即基准，不应再附纯定投基准。"""
    result = run_dca_backtest(random_walk_df, mode="fixed")
    assert result.dca_metrics is None


@pytest.mark.parametrize("mode", [m for m in MODES if m != "fixed"])
def test_enhanced_modes_run_with_baseline(mode, random_walk_df):
    """增强模式均可跑通，且附带同参数纯定投基准。"""
    result = run_dca_backtest(random_walk_df, mode=mode, freq="weekly")
    assert result.dca_metrics is not None
    assert result.metrics["num_contributions"] > 0
    assert np.isfinite(result.metrics["final_value"])


def test_ma_mode_boost_increases_investment(trending_down_df):
    """ma 模式：下跌市低于均线加码，总投入应高于纯定投。"""
    boosted = run_dca_backtest(
        trending_down_df, mode="ma", freq="weekly", ma_window=20, boost=2.0
    )
    assert (
        boosted.metrics["total_invested"]
        > boosted.dca_metrics["total_invested"]
    )


def test_value_avg_sells_in_rally(trending_up_df):
    """价值平均：持续上涨时市值超过目标线，应出现卖出（contribution<0）。"""
    result = run_dca_backtest(trending_up_df, mode="value_avg", freq="weekly")
    assert (result.contribution < 0).any()
    # 卖出日实际现金流为正（资金流回投资者）
    sell_days = result.contribution < 0
    assert (result.cashflow[sell_days] > 0).all()


def test_transactions_records_direction(trending_up_df):
    """交易明细：BUY/SELL 方向与金额符号一致。"""
    result = run_dca_backtest(trending_up_df, mode="value_avg", freq="weekly")
    tx = result.transactions
    assert set(tx["action"]).issubset({"BUY", "SELL"})
    assert ((tx["amount"] > 0) == (tx["action"] == "BUY")).all()


def test_unknown_mode_raises(random_walk_df):
    with pytest.raises(ValueError):
        run_dca_backtest(random_walk_df, mode="no_such_mode")


def test_unknown_freq_raises(random_walk_df):
    with pytest.raises(ValueError):
        run_dca_backtest(random_walk_df, freq="hourly")


def test_non_datetime_index_degrades_gracefully():
    """无时间列时按固定间隔投入，XIRR 退化为 nan 而非报错。"""
    close = 100.0 + np.zeros(100)
    df = make_ohlcv(close).drop(columns=["trade_date"])
    result = run_dca_backtest(df, freq="monthly")
    assert result.metrics["num_contributions"] > 0
    assert np.isnan(result.metrics["annual_return"])


# ---------------------------------------------------------------- 分红建模


def _flat_df(n: int = 250, price: float = 100.0) -> pd.DataFrame:
    """恒定价格行情：隔离价格波动，只看分红效应。"""
    return make_ohlcv(np.full(n, price))


def _one_div(df: pd.DataFrame, dps: float, at: int) -> pd.Series:
    """在第 at 根 K 线日派发每股 dps 的分红。"""
    date = pd.to_datetime(df["trade_date"].iloc[at])
    return pd.Series([dps], index=pd.DatetimeIndex([date]))


def test_dividend_reinvest_increases_value():
    """再投入：分红转份额，期末市值高于无分红基准；盈亏≈分红额（扣成本）。"""
    df = _flat_df()
    div = _one_div(df, dps=2.0, at=150)
    base = run_dca_backtest(df, freq="monthly", commission=0.0, slippage=0.0)
    with_div = run_dca_backtest(
        df, freq="monthly", commission=0.0, slippage=0.0,
        dividends=div, div_policy="reinvest",
    )
    assert with_div.metrics["total_dividends"] > 0
    extra = with_div.metrics["final_value"] - base.metrics["final_value"]
    assert extra == pytest.approx(with_div.metrics["total_dividends"], rel=1e-6)
    # 再投入不改变投入本金
    assert with_div.metrics["total_invested"] == base.metrics["total_invested"]


def test_dividend_cash_flows_to_investor():
    """现金落袋：份额不变，分红计入盈亏与正向现金流（XIRR 口径）。"""
    df = _flat_df()
    div = _one_div(df, dps=2.0, at=150)
    base = run_dca_backtest(df, freq="monthly", commission=0.0, slippage=0.0)
    with_div = run_dca_backtest(
        df, freq="monthly", commission=0.0, slippage=0.0,
        dividends=div, div_policy="cash",
    )
    # 份额与市值不变（价格恒定），但盈亏多出落袋分红
    assert with_div.metrics["final_value"] == pytest.approx(base.metrics["final_value"])
    profit_gap = with_div.metrics["total_profit"] - base.metrics["total_profit"]
    assert profit_gap == pytest.approx(with_div.metrics["total_dividends"], rel=1e-9)
    # 平盘+分红下 XIRR 应为正（无分红时为 0）
    assert with_div.metrics["annual_return"] > 0


def test_dividend_before_first_bar_ignored():
    """回测区间开始前的分红应被丢弃（当时尚无持仓）。"""
    df = _flat_df()
    early = pd.Series(
        [5.0], index=pd.DatetimeIndex([pd.to_datetime(df["trade_date"].iloc[0]) - pd.Timedelta(days=30)])
    )
    result = run_dca_backtest(df, freq="monthly", dividends=early)
    assert result.metrics["total_dividends"] == 0.0


def test_dividend_on_non_trading_day_rolls_forward():
    """除权日落在非交易日时顺延到其后首个交易日入账。"""
    df = _flat_df()
    # make_ohlcv 用 B 频率：周六必非交易日
    trade_dates = pd.DatetimeIndex(pd.to_datetime(df["trade_date"]))
    saturday = trade_dates[100] + pd.offsets.Week(weekday=5)
    assert saturday not in trade_dates
    div = pd.Series([1.0], index=pd.DatetimeIndex([saturday]))
    result = run_dca_backtest(df, freq="monthly", dividends=div, div_policy="cash")
    assert result.metrics["total_dividends"] > 0


def test_lumpsum_baseline_includes_dividends():
    """一次性基准在显式分红下同样计入分红现金（否则对比不公平）。"""
    df = _flat_df()
    div = _one_div(df, dps=2.0, at=150)
    result = run_dca_backtest(df, freq="monthly", dividends=div)
    assert result.lumpsum_metrics["total_dividends"] > 0


def test_invalid_div_policy_raises(random_walk_df):
    with pytest.raises(ValueError):
        run_dca_backtest(random_walk_df, div_policy="spend")
