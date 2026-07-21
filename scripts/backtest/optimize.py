"""参数寻优。

对策略参数做网格搜索（method="grid"，穷举全部组合）、随机搜索
（method="random"，从网格空间无放回采样 n_iter 组，固定 seed 可复现）
或贝叶斯自适应搜索（method="bayes"，TPE 风格：把已评估组合按指标分为
好/坏两组，按似然比挑选下一批最有希望的候选，同预算下通常优于随机），
逐组回测，按指定指标排序，返回结果表。随机/贝叶斯在组合数大时显著提速，
且试验数更少→多重检验惩罚更轻（DSR 更可信）。
组合数较多时可用多进程并行评估（n_jobs），结果与串行完全一致。
"""

from __future__ import annotations

import itertools
import math
import os
import random as _random
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from typing import Any, TypedDict

import pandas as pd

from strategies.base import Strategy

from .costs import CostModel
from .engine import run_backtest
from .rules import TradingRules

#: 组合数低于此值时不启用多进程（进程启动开销得不偿失）
PARALLEL_MIN_COMBOS = 8

#: 贝叶斯搜索：好组占比（TPE 的 gamma）
BAYES_GAMMA = 0.25
#: 贝叶斯搜索：每轮评估的候选批量（固定值保证串行/并行结果一致）
BAYES_BATCH = 4


class _WorkerContext(TypedDict):
    """子进程内的共享上下文结构。"""

    df: pd.DataFrame
    strategy_cls: type[Strategy]
    fixed: dict[str, Any]
    engine_kwargs: dict[str, Any]


#: 子进程内的共享上下文（由 initializer 填充，避免逐任务重复 pickle 数据）
_WORKER_CTX: _WorkerContext = {}  # type: ignore[typeddict-item]


def _init_worker(
    df: pd.DataFrame,
    strategy_cls: type[Strategy],
    fixed: dict[str, Any],
    engine_kwargs: dict[str, Any],
) -> None:
    """进程池 initializer：每个 worker 只接收一次数据与回测配置。"""
    _WORKER_CTX["df"] = df
    _WORKER_CTX["strategy_cls"] = strategy_cls
    _WORKER_CTX["fixed"] = fixed
    _WORKER_CTX["engine_kwargs"] = engine_kwargs


def _eval_combo(params: dict[str, Any]) -> dict[str, Any]:
    """评估单组参数（串行与并行共用，保证结果一致）。"""
    ctx = _WORKER_CTX
    strategy = ctx["strategy_cls"](**{**ctx["fixed"], **params})
    result = run_backtest(ctx["df"], strategy, **ctx["engine_kwargs"])
    return {**params, **result.metrics}


def _combo_valid(
    strategy_cls: type[Strategy], fixed: dict[str, Any], params: dict[str, Any]
) -> bool:
    """尝试构造策略实例，参数校验失败（ValueError）即视为非法组合。"""
    try:
        strategy_cls(**{**fixed, **params})
        return True
    except ValueError:
        return False


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
    method: str = "grid",
    n_iter: int = 60,
    seed: int = 42,
) -> pd.DataFrame:
    """网格/随机参数寻优。

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
        method: ``grid``（默认，穷举全部组合）、``random``（从网格空间
            无放回随机采样 ``n_iter`` 组）或 ``bayes``（TPE 风格自适应搜索，
            共评估 ``n_iter`` 组）；组合数 <= n_iter 时后两者退化为 grid。
        n_iter: random/bayes 方法的评估组数，默认 60。
        seed: random/bayes 方法的随机种子（固定可复现）。

    Returns:
        按 metric 降序排列的 DataFrame，含各参数列与全部绩效指标。
    """
    grid = param_grid or strategy_cls.param_grid
    if not grid:
        raise ValueError(f"策略 {strategy_cls.name} 未定义 param_grid，无法寻优")

    fixed = fixed_params or {}
    keys = list(grid.keys())
    combos = [dict(zip(keys, c)) for c in itertools.product(*(grid[k] for k in keys))]
    # 跳过非法组合：以策略自身 validate_params 为准（如 fast >= slow）
    combos = [p for p in combos if _combo_valid(strategy_cls, fixed, p)]
    if not combos:
        raise ValueError("无有效参数组合可回测")

    if method not in ("grid", "random", "bayes"):
        raise ValueError(f"未知寻优方法 '{method}'，可选：grid / random / bayes")
    if method in ("random", "bayes") and n_iter < 1:
        raise ValueError(f"n_iter 应为正整数，收到 {n_iter}")
    if method == "bayes" and n_iter >= len(combos):
        method = "grid"  # 预算覆盖全空间时退化为穷举
    if method == "random" and n_iter < len(combos):
        # 无放回采样（固定 seed 可复现）；保持原网格顺序，与串行/并行无关
        picked = sorted(_random.Random(seed).sample(range(len(combos)), n_iter))
        combos = [combos[i] for i in picked]

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

    if method == "bayes":
        rows = _bayes_search(
            combos, keys, metric, n_iter, seed, workers,
            df, strategy_cls, fixed, engine_kwargs, progress,
        )
        table = pd.DataFrame(rows)
        if metric not in table.columns:
            metric_cols = [c for c in table.columns if c not in keys]
            raise KeyError(f"未知指标 '{metric}'，可选：{metric_cols}")
        table = table.sort_values(metric, ascending=False).reset_index(drop=True)
        if top_n is not None:
            table = table.head(top_n)
        return table

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


def _metric_val(row: dict, metric: str) -> float:
    """取排序指标值，NaN 视为负无穷（排在最差）。"""
    v = row.get(metric)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float("-inf")
    return f if f == f else float("-inf")


def _bayes_search(
    combos: list[dict],
    keys: list[str],
    metric: str,
    n_iter: int,
    seed: int,
    workers: int,
    df: pd.DataFrame,
    strategy_cls: type,
    fixed: dict,
    engine_kwargs: dict,
    progress: Callable[[int, int], None] | None,
) -> list[dict]:
    """TPE 风格离散贝叶斯搜索：好/坏两组的参数值频率似然比挑候选。

    评估顺序仅由 seed 与历史结果决定（批量大小固定），因此串行与并行
    的最终结果表完全一致。
    """
    rng = _random.Random(seed)
    n_init = min(max(BAYES_BATCH * 2, n_iter // 4), n_iter)
    pending = sorted(rng.sample(range(len(combos)), n_init))
    evaluated: dict[int, dict] = {}

    pool = None
    try:
        if workers > 1 and n_iter >= PARALLEL_MIN_COMBOS:
            pool = ProcessPoolExecutor(
                max_workers=min(workers, BAYES_BATCH * 2),
                initializer=_init_worker,
                initargs=(df, strategy_cls, fixed, engine_kwargs),
            )
        else:
            _init_worker(df, strategy_cls, fixed, engine_kwargs)

        def _run_batch(idxs: list[int]) -> None:
            batch = [combos[i] for i in idxs]
            results = list(pool.map(_eval_combo, batch)) if pool else [
                _eval_combo(p) for p in batch
            ]
            for i, row in zip(idxs, results):
                evaluated[i] = row
                if progress:
                    progress(len(evaluated), n_iter)

        _run_batch(pending)
        if metric not in next(iter(evaluated.values())):
            metric_cols = [c for c in next(iter(evaluated.values())) if c not in keys]
            raise KeyError(f"未知指标 '{metric}'，可选：{metric_cols}")

        while len(evaluated) < n_iter:
            ranked = sorted(
                evaluated, key=lambda i: (-_metric_val(evaluated[i], metric), i)
            )
            n_good = max(1, math.ceil(BAYES_GAMMA * len(ranked)))
            good, bad = set(ranked[:n_good]), set(ranked[n_good:])

            # 每个参数维度上，好/坏组内各取值的拉普拉斯平滑频率
            values = {k: sorted({repr(c[k]) for c in combos}) for k in keys}
            ratio: dict[str, dict[str, float]] = {}
            for k in keys:
                n_vals = len(values[k])
                g_cnt = {v: 1.0 for v in values[k]}
                b_cnt = {v: 1.0 for v in values[k]}
                for i in good:
                    g_cnt[repr(combos[i][k])] += 1
                for i in bad:
                    b_cnt[repr(combos[i][k])] += 1
                g_tot, b_tot = len(good) + n_vals, len(bad) + n_vals
                ratio[k] = {
                    v: math.log((g_cnt[v] / g_tot) / (b_cnt[v] / b_tot))
                    for v in values[k]
                }

            remaining = [i for i in range(len(combos)) if i not in evaluated]
            scored = sorted(
                remaining,
                key=lambda i: (
                    -sum(ratio[k][repr(combos[i][k])] for k in keys),
                    i,
                ),
            )
            batch = scored[: min(BAYES_BATCH, n_iter - len(evaluated))]
            _run_batch(batch)
    finally:
        if pool:
            pool.shutdown()

    # 按评估先后无关的稳定顺序（组合原始下标）返回
    return [evaluated[i] for i in sorted(evaluated)]
