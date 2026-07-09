"""多因子选股模型：打分 -> 选股 -> 权重矩阵 -> 分层回测。

复用 portfolio.run_portfolio_backtest 做组合回测，用分层（按综合得分分组）
验证因子有效性（单调性）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from portfolio.engine import PortfolioResult, run_portfolio_backtest

from .library import FACTORS, compute_factor
from .preprocess import composite_score


@dataclass
class FactorResult:
    """多因子选股结果容器。"""

    factors_used: list[str]
    scores: pd.DataFrame
    top_portfolio: PortfolioResult
    layers: list[PortfolioResult] = field(default_factory=list)
    latest_date: object = None
    latest_picks: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    top_quantile: float = 0.2
    rebalance: int = 20
    skipped: list[str] = field(default_factory=list)


def _rebalance_dates(n: int, warmup: int, rebalance: int) -> list[int]:
    return list(range(warmup, n, rebalance))


def _weights_from_members(
    scores: pd.DataFrame,
    rebal_idx: list[int],
    member_func,
) -> pd.DataFrame:
    """按调仓日选出的成分构造等权目标权重矩阵（非调仓日前向填充）。"""
    target = pd.DataFrame(np.nan, index=scores.index, columns=scores.columns)
    for i in rebal_idx:
        row = scores.iloc[i].dropna()
        if row.empty:
            continue
        members = member_func(row)
        if len(members) == 0:
            continue
        w = pd.Series(0.0, index=scores.columns)
        w[members] = 1.0 / len(members)
        target.iloc[i] = w
    return target.ffill().fillna(0.0)


def _top_members(row: pd.Series, top_quantile: float) -> pd.Index:
    k = max(1, int(round(len(row) * top_quantile)))
    return row.nlargest(k).index


def _layer_members(row: pd.Series, layers: int, layer_idx: int) -> pd.Index:
    ranked = row.sort_values(ascending=False).index  # 高分在前
    parts = np.array_split(np.array(ranked), layers)
    return pd.Index(parts[layer_idx])


def run_factor_model(
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame | None,
    factors: list[str] | None = None,
    weights: dict[str, float] | None = None,
    top_quantile: float = 0.2,
    layers: int = 5,
    lookback: int = 60,
    lag_days: int = 60,
    rebalance: int = 20,
    period: str = "1d",
    commission: float = 0.0005,
    slippage: float = 0.0005,
) -> FactorResult:
    """运行多因子选股与分层回测。

    Args:
        prices: 收盘价矩阵（日期 × 标的）。
        fundamentals: 财务指标（无权限时为 None，基本面因子自动跳过）。
        factors: 启用的因子名列表（默认 FACTORS 全部）。
        top_quantile: 选股分位（0.2 = 综合得分前 20%）。
        layers: 分层数（验证单调性）。
        lookback: 价格因子回看周期，也用作 warmup。
        lag_days: 财务因子报告期滞后天数。
        rebalance: 调仓周期。
    """
    prices = prices.sort_index()
    names = factors or list(FACTORS)

    # 计算各因子，跳过不可用（如无财务权限）
    factor_frames: dict[str, pd.DataFrame] = {}
    skipped: list[str] = []
    for name in names:
        frame = compute_factor(name, prices, fundamentals, lookback, lag_days)
        if frame is None or frame.dropna(how="all").empty:
            skipped.append(name)
            continue
        factor_frames[name] = frame.reindex(index=prices.index, columns=prices.columns)

    if not factor_frames:
        raise RuntimeError(
            "没有可用因子。价格因子需足够历史，基本面因子需 TICKFLOW_API_KEY 及财务权限。"
        )

    scores = composite_score(factor_frames, weights)

    n = len(prices)
    rebal_idx = _rebalance_dates(n, lookback, rebalance)
    if not rebal_idx:
        raise RuntimeError(f"历史长度不足以在 warmup={lookback} 后调仓，请增大 --count。")

    # Top 分位组合
    top_weights = _weights_from_members(
        scores, rebal_idx, lambda row: _top_members(row, top_quantile)
    )
    top_portfolio = run_portfolio_backtest(
        prices, top_weights, period=period, commission=commission, slippage=slippage
    )

    # 分层回测（L1=最高分层）
    layer_results: list[PortfolioResult] = []
    for layer_idx in range(layers):
        lw = _weights_from_members(
            scores, rebal_idx, lambda row, li=layer_idx: _layer_members(row, layers, li)
        )
        layer_results.append(
            run_portfolio_backtest(
                prices, lw, period=period, commission=commission, slippage=slippage
            )
        )

    # 最新一期选股清单
    last_i = rebal_idx[-1]
    last_row = scores.iloc[last_i].dropna()
    latest_picks = last_row.loc[_top_members(last_row, top_quantile)].sort_values(
        ascending=False
    )

    result = FactorResult(
        factors_used=list(factor_frames),
        scores=scores,
        top_portfolio=top_portfolio,
        layers=layer_results,
        latest_date=prices.index[last_i],
        latest_picks=latest_picks,
        top_quantile=top_quantile,
        rebalance=rebalance,
        skipped=skipped,
    )
    return result
