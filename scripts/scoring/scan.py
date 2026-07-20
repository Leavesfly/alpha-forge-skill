"""全市场扫描漏斗：流动性初筛 → 批量四层评分 → 达标/降级分列。

扫描是**纪律过滤**，不是收益预测或选股 alpha 排名：
- 达标候选 = 结论「是」且排名分 ≥ min_score，按排名分排序；
- 被否决/降级候选单独列出主要原因，信息不丢失；
- 拉取失败或 K 线不足的标的跳过并汇总，不中断整体扫描。
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

from .engine import default_benchmark, score_symbol


def scan_symbols(
    symbols: list[str],
    fetch: Callable[[str], pd.DataFrame],
    fetch_benchmark: Callable[[str], pd.Series | None] | None = None,
    pool: int | None = None,
    min_score: float = 60.0,
    on_progress: Callable[[int, str], None] | None = None,
) -> dict:
    """对标的列表执行扫描漏斗。

    Args:
        symbols: 标的代码列表。
        fetch: 拉取单标的 OHLCV 的函数（内部走缓存）。
        fetch_benchmark: 按基准代码返回收盘价序列的函数（内部应缓存，
            同市场标的复用同一基准）；None 时全部降级为无基准评分。
        pool: 流动性初筛保留的标的数（按近 20 日均成交额排序）；None 不过滤。
        min_score: 达标候选的最低排名分。
        on_progress: 进度回调 ``(已完成数, 当前标的)``。

    Returns:
        {"candidates": [...], "rejected": [...], "filtered": [...], "skipped": [...]}
    """
    frames: list[tuple[str, pd.DataFrame, float]] = []
    skipped: list[dict] = []
    done = 0
    for sym in symbols:
        try:
            df = fetch(sym)
            frames.append((sym, df, _avg_turnover(df)))
        except Exception as exc:  # 单标的失败不中断扫描
            skipped.append({"symbol": sym, "reason": f"{type(exc).__name__}: {exc}"})
        done += 1
        if on_progress:
            on_progress(done, sym)

    # 流动性初筛：按近 20 日均成交额排序，保留前 pool 名
    frames.sort(key=lambda item: item[2], reverse=True)
    filtered: list[dict] = []
    if pool is not None and pool < len(frames):
        for sym, _, turnover in frames[pool:]:
            filtered.append({"symbol": sym, "reason": "流动性初筛未入围", "avg_turnover_20d": turnover})
        frames = frames[:pool]

    candidates: list[dict] = []
    rejected: list[dict] = []
    for sym, df, turnover in frames:
        bench_close = None
        bench_sym = default_benchmark(sym)
        if bench_sym and fetch_benchmark is not None:
            bench_close = fetch_benchmark(bench_sym)
        res = score_symbol(df, symbol=sym, benchmark_close=bench_close, benchmark_symbol=bench_sym)
        summary = {
            "symbol": sym,
            "verdict": res.verdict,
            "verdict_cn": res.verdict_cn,
            "alpha_score": res.alpha_score,
            "asof": res.asof,
            "avg_turnover_20d": turnover,
            "close": res.snapshot.get("close"),
        }
        if res.verdict == "yes" and (res.alpha_score or 0.0) >= min_score:
            summary["plan"] = res.plan
            candidates.append(summary)
        else:
            summary["reason"] = _primary_reason(res)
            rejected.append(summary)

    candidates.sort(key=lambda item: item["alpha_score"] or 0.0, reverse=True)
    rejected.sort(key=lambda item: item["alpha_score"] or 0.0, reverse=True)
    return {
        "candidates": candidates,
        "rejected": rejected,
        "filtered": filtered,
        "skipped": skipped,
    }


def _avg_turnover(df: pd.DataFrame) -> float:
    """近 20 日平均成交额（无 volume 列时返回 0，排在末位）。"""
    if "volume" not in df.columns or "close" not in df.columns:
        return 0.0
    turnover = df["close"].astype(float) * df["volume"].astype(float)
    tail = turnover.tail(20)
    return float(tail.mean()) if len(tail) else 0.0


def _primary_reason(res) -> str:
    """从各层记录中提取主要否决/降级原因（首个非 pass 层的首条理由）。"""
    for layer in res.layers:
        if layer.get("status") not in ("pass", None) and layer.get("reasons"):
            return layer["reasons"][0]
    if res.verdict == "watch":
        return f"排名分 {res.alpha_score}，未达「是」阈值"
    if res.verdict == "yes":
        return "排名分低于 --min-score 阈值"
    return "；".join(res.layers[0].get("reasons", [])[:1]) if res.layers else ""
