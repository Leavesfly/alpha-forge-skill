"""参数寻优。

对策略参数做网格搜索，逐组回测，按指定指标排序，返回结果表。
"""

from __future__ import annotations

import itertools

import pandas as pd

from strategies.base import Strategy

from .engine import run_backtest


def grid_search(
    df: pd.DataFrame,
    strategy_cls: type[Strategy],
    param_grid: dict[str, list] | None = None,
    symbol: str = "",
    period: str = "1d",
    metric: str = "sharpe",
    commission: float = 0.0005,
    slippage: float = 0.0005,
    top_n: int | None = None,
    fixed_params: dict | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    vol_target: float | None = None,
    vol_window: int = 20,
    max_leverage: float = 1.0,
) -> pd.DataFrame:
    """网格参数寻优。

    Args:
        df: OHLCV 数据。
        strategy_cls: 策略类。
        param_grid: 参数网格；缺省用策略类自带的 ``param_grid``。
        metric: 排序依据的指标名（如 sharpe/total_return/calmar）。
        top_n: 仅返回前 N 行；None 返回全部。
        fixed_params: 对所有组合生效的固定参数（如 {"allow_short": True}）。
        stop_loss: 止损比例，传递给回测引擎。
        take_profit: 止盈比例，传递给回测引擎。

    Returns:
        按 metric 降序排列的 DataFrame，含各参数列与全部绩效指标。
    """
    grid = param_grid or strategy_cls.param_grid
    if not grid:
        raise ValueError(f"策略 {strategy_cls.name} 未定义 param_grid，无法寻优")

    fixed = fixed_params or {}
    keys = list(grid.keys())
    combos = list(itertools.product(*(grid[k] for k in keys)))

    rows = []
    for combo in combos:
        params = dict(zip(keys, combo))
        # 跳过无意义组合（如快线周期 >= 慢线周期）
        if "fast" in params and "slow" in params and params["fast"] >= params["slow"]:
            continue
        strategy = strategy_cls(**{**fixed, **params})
        result = run_backtest(
            df,
            strategy,
            symbol=symbol,
            period=period,
            commission=commission,
            slippage=slippage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            vol_target=vol_target,
            vol_window=vol_window,
            max_leverage=max_leverage,
        )
        row = {**params, **result.metrics}
        rows.append(row)

    if not rows:
        raise ValueError("无有效参数组合可回测")

    table = pd.DataFrame(rows)
    if metric not in table.columns:
        raise KeyError(f"未知指标 '{metric}'，可选：{list(result.metrics.keys())}")

    table = table.sort_values(metric, ascending=False).reset_index(drop=True)
    if top_n is not None:
        table = table.head(top_n)
    return table
