"""多标的组合回测引擎。

基于目标权重矩阵进行组合回测，内置：
- shift(1) 防前视偏差（当日权重次日生效）
- 换手成本（按权重变动比例扣除）
- 等权 Buy & Hold 基准对比
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from metrics import compute_metrics


@dataclass
class PortfolioResult:
    """组合回测结果容器。"""

    symbols: list[str]
    period: str
    prices: pd.DataFrame
    weights: pd.DataFrame
    returns: pd.Series
    equity: pd.Series
    benchmark_equity: pd.Series
    metrics: dict = field(default_factory=dict)
    benchmark_metrics: dict = field(default_factory=dict)
    rebalance_count: int = 0


def run_portfolio_backtest(
    prices: pd.DataFrame,
    target_weights: pd.DataFrame,
    period: str = "1d",
    commission: float = 0.0005,
    slippage: float = 0.0005,
    risk_free: float = 0.0,
) -> PortfolioResult:
    """执行组合回测。

    Args:
        prices: 收盘价矩阵（索引为日期，列为标的）。
        target_weights: 目标权重矩阵（与 prices 同形），每行权重之和 <= 1。
        period: K 线周期（用于年化指标）。
        commission: 单边手续费率。
        slippage: 单边滑点率。
        risk_free: 年化无风险利率。

    Returns:
        PortfolioResult。基准为等权每日再平衡组合。
    """
    prices = prices.sort_index()
    returns = prices.pct_change().fillna(0.0)

    # 目标权重次日生效，避免前视
    weights = target_weights.reindex(prices.index).fillna(0.0)
    held = weights.shift(1).fillna(0.0)

    # 组合收益 = Σ(持仓权重 × 各标的收益) - 换手成本
    port_ret_gross = (held * returns).sum(axis=1)
    turnover = held.diff().abs().sum(axis=1).fillna(held.abs().sum(axis=1))
    cost = turnover * (commission + slippage)
    port_ret = port_ret_gross - cost
    equity = (1.0 + port_ret).cumprod()

    # 基准：等权（每日再平衡）买入持有
    benchmark_ret = returns.mean(axis=1)
    benchmark_equity = (1.0 + benchmark_ret).cumprod()

    rebalance_count = int((turnover > 1e-9).sum())

    metrics = compute_metrics(port_ret, equity, period=period, risk_free=risk_free)
    benchmark_metrics = compute_metrics(
        benchmark_ret, benchmark_equity, period=period, risk_free=risk_free
    )

    return PortfolioResult(
        symbols=list(prices.columns),
        period=period,
        prices=prices,
        weights=held,
        returns=port_ret,
        equity=equity,
        benchmark_equity=benchmark_equity,
        metrics=metrics,
        benchmark_metrics=benchmark_metrics,
        rebalance_count=rebalance_count,
    )
