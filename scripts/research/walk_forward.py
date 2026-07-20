"""Walk-forward（走步）样本外验证。

对经典策略做「滚动重寻优 + 只在样本外计价」：每个测试块开始前，用其之前的
训练窗做参数寻优选出最优参数，再把该参数应用到紧随其后的测试块，拼接各测试块
的样本外收益。这样得到的净值天然规避样本内过拟合，是评估策略稳健性的金标准。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from backtest.costs import CostModel
from backtest.engine import run_backtest
from backtest.metrics import compute_metrics
from backtest.optimize import grid_search
from backtest.rules import TradingRules
from strategies.base import Strategy


@dataclass
class WalkForwardResult:
    """走步验证结果容器。"""

    oos_returns: pd.Series
    oos_equity: pd.Series
    oos_metrics: dict
    benchmark_equity: pd.Series
    benchmark_metrics: dict
    folds: pd.DataFrame = field(default_factory=pd.DataFrame)


def _best_params(table: pd.DataFrame, keys: list[str]) -> dict:
    row = table.iloc[0]
    out = {}
    for k in keys:
        if k in table.columns:
            v = getattr(row[k], "item", lambda: row[k])()
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            out[k] = v
    return out


def walk_forward(
    df: pd.DataFrame,
    strategy_cls: type[Strategy],
    param_grid: dict[str, list] | None = None,
    metric: str = "sharpe",
    train_window: int = 250,
    test_window: int = 60,
    anchored: bool = False,
    period: str = "1d",
    fixed_params: dict | None = None,
    cost_model: CostModel | None = None,
    exec_price: str = "close",
    trading_rules: TradingRules | None = None,
) -> WalkForwardResult:
    """执行走步样本外验证。

    Args:
        df: OHLCV DataFrame（时间升序）。
        strategy_cls: 策略类（需定义 param_grid 或显式传入）。
        metric: 训练窗内选参依据的指标。
        train_window: 训练窗长度（周期数）。
        test_window: 每个测试块长度（周期数），也是步长。
        anchored: True 为锚定式（训练窗起点固定为 0），False 为滚动式。
        fixed_params: 对所有组合固定的参数（如 allow_short）。

    Returns:
        WalkForwardResult：拼接后的样本外净值/指标/分折明细。
    """
    grid = param_grid or strategy_cls.param_grid
    if not grid:
        raise ValueError(f"策略 {strategy_cls.name} 未定义 param_grid，无法走步寻优")
    keys = list(grid.keys())
    fixed = fixed_params or {}

    df = df.reset_index(drop=True)
    n = len(df)
    if train_window + test_window > n:
        raise RuntimeError(
            f"历史长度不足：需 > {train_window + test_window} 根 K 线，当前 {n} 根。"
        )

    fold_rows: list[dict] = []
    oos_parts: list[pd.Series] = []

    for test_start in range(train_window, n, test_window):
        test_end = min(test_start + test_window, n)
        if test_end - test_start < 2:
            break
        train_lo = 0 if anchored else max(0, test_start - train_window)

        train_df = df.iloc[train_lo:test_start].reset_index(drop=True)
        table = grid_search(
            train_df, strategy_cls, param_grid=grid, metric=metric,
            period=period, fixed_params=fixed,
            cost_model=cost_model, exec_price=exec_price, trading_rules=trading_rules,
        )
        best = _best_params(table, keys)

        # 在「训练窗 + 测试块」上运行，取测试块段的样本外收益
        eval_df = df.iloc[train_lo:test_end].reset_index(drop=True)
        strat = strategy_cls(**{**fixed, **best})
        res = run_backtest(
            eval_df, strat, period=period,
            cost_model=cost_model, exec_price=exec_price, trading_rules=trading_rules,
        )
        test_len = test_end - test_start
        fold_oos = res.returns.iloc[-test_len:]
        oos_parts.append(fold_oos)

        fold_rows.append({
            "train_lo": train_lo,
            "test_start": test_start,
            "test_end": test_end,
            **best,
            "is_metric": float(table.iloc[0][metric]),
            "oos_return": float((1.0 + fold_oos).prod() - 1.0),
        })

    if not oos_parts:
        raise RuntimeError("未产生任何样本外测试块，请减小 train/test 窗口或增大数据量。")

    oos_returns = pd.concat(oos_parts)
    oos_returns = oos_returns[~oos_returns.index.duplicated(keep="first")]
    oos_equity = (1.0 + oos_returns).cumprod()

    # 基准：同一样本外区间的买入持有
    close = df["close"].astype(float)
    close.index = run_backtest(df, strategy_cls(**fixed), period=period).close.index
    bench_ret = close.pct_change().reindex(oos_returns.index).fillna(0.0)
    bench_equity = (1.0 + bench_ret).cumprod()

    oos_metrics = compute_metrics(oos_returns, oos_equity, period=period)
    bench_metrics = compute_metrics(bench_ret, bench_equity, period=period)

    return WalkForwardResult(
        oos_returns=oos_returns,
        oos_equity=oos_equity,
        oos_metrics=oos_metrics,
        benchmark_equity=bench_equity,
        benchmark_metrics=bench_metrics,
        folds=pd.DataFrame(fold_rows),
    )
