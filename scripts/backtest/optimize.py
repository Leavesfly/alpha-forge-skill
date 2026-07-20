"""参数寻优。

对策略参数做网格搜索，逐组回测，按指定指标排序，返回结果表。
组合数较多时可用多进程并行评估（n_jobs），结果与串行完全一致。
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from strategies.base import Strategy

from .costs import CostModel
from .engine import run_backtest
from .rules import TradingRules

#: 组合数低于此值时不启用多进程（进程启动开销得不偿失）
PARALLEL_MIN_COMBOS = 8

#: 子进程内的共享上下文（由 initializer 填充，避免逐任务重复 pickle 数据）
_WORKER_CTX: dict = {}


def _init_worker(df: pd.DataFrame, strategy_cls: type, fixed: dict, engine_kwargs: dict) -> None:
    """进程池 initializer：每个 worker 只接收一次数据与回测配置。"""
    _WORKER_CTX["df"] = df
    _WORKER_CTX["strategy_cls"] = strategy_cls
    _WORKER_CTX["fixed"] = fixed
    _WORKER_CTX["engine_kwargs"] = engine_kwargs


def _eval_combo(params: dict) -> dict:
    """评估单组参数（串行与并行共用，保证结果一致）。"""
    ctx = _WORKER_CTX
    strategy = ctx["strategy_cls"](**{**ctx["fixed"], **params})
    result = run_backtest(ctx["df"], strategy, **ctx["engine_kwargs"])
    return {**params, **result.metrics}


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
    cost_model: CostModel | None = None,
    exec_price: str = "close",
    trading_rules: TradingRules | None = None,
    n_jobs: int | None = 1,
    progress: Callable[[int, int], None] | None = None,
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
        n_jobs: 并行进程数；1 串行（默认），None 取 CPU 核数。仅当
            有效组合数 >= ``PARALLEL_MIN_COMBOS`` 时才真正启用多进程。
        progress: 可选进度回调 ``progress(done, total)``，每完成一组调用一次。

    Returns:
        按 metric 降序排列的 DataFrame，含各参数列与全部绩效指标。
    """
    grid = param_grid or strategy_cls.param_grid
    if not grid:
        raise ValueError(f"策略 {strategy_cls.name} 未定义 param_grid，无法寻优")

    fixed = fixed_params or {}
    keys = list(grid.keys())
    combos = [dict(zip(keys, c)) for c in itertools.product(*(grid[k] for k in keys))]
    # 跳过无意义组合（如快线周期 >= 慢线周期）
    combos = [
        p for p in combos
        if not ("fast" in p and "slow" in p and p["fast"] >= p["slow"])
    ]
    if not combos:
        raise ValueError("无有效参数组合可回测")

    engine_kwargs = dict(
        symbol=symbol,
        period=period,
        commission=commission,
        slippage=slippage,
        stop_loss=stop_loss,
        take_profit=take_profit,
        vol_target=vol_target,
        vol_window=vol_window,
        max_leverage=max_leverage,
        cost_model=cost_model,
        exec_price=exec_price,
        trading_rules=trading_rules,
    )

    workers = os.cpu_count() or 1 if n_jobs is None else max(1, n_jobs)
    total = len(combos)
    rows: list[dict] = []
    if workers > 1 and total >= PARALLEL_MIN_COMBOS:
        # 数据与配置经 initializer 一次性下发；map 保持提交顺序，
        # 保证结果与串行逐行一致。
        with ProcessPoolExecutor(
            max_workers=min(workers, total),
            initializer=_init_worker,
            initargs=(df, strategy_cls, fixed, engine_kwargs),
        ) as pool:
            for row in pool.map(_eval_combo, combos):
                rows.append(row)
                if progress:
                    progress(len(rows), total)
    else:
        _init_worker(df, strategy_cls, fixed, engine_kwargs)
        for params in combos:
            rows.append(_eval_combo(params))
            if progress:
                progress(len(rows), total)

    table = pd.DataFrame(rows)
    if metric not in table.columns:
        metric_cols = [c for c in table.columns if c not in keys]
        raise KeyError(f"未知指标 '{metric}'，可选：{metric_cols}")

    table = table.sort_values(metric, ascending=False).reset_index(drop=True)
    if top_n is not None:
        table = table.head(top_n)
    return table
