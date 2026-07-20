"""评分历史回放验证：逐日重算结论 + 前瞻收益事件研究。

回答「这套纪律评分在历史上有没有用」：

- **回放**：对最近 N 个交易日，逐日只用截至当日（含）的数据重算结论
  （final-only，无前视）；
- **事件研究**：把结论首次转为「是」的日期作为事件日，复用
  ``research.event_study`` 计算 21/63 日的前瞻绝对与相对基准收益
  （事件日取入场信号的**次一交易日**，度量信号之后的收益，不含信号当日）；
- **诚实约定**：非重叠样本 < 10 时明确标注 inconclusive，
  不据此确认或否定评分有效性。
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from research.event_study import event_study

from .engine import MIN_BARS, score_symbol

#: 前瞻研究窗口（交易日）
HORIZONS = (21, 63)

#: 非重叠样本低于该值时标注 inconclusive
MIN_SAMPLES = 10


def replay_verdicts(
    df: pd.DataFrame,
    benchmark_close: pd.Series | None = None,
    days: int = 250,
    symbol: str = "",
) -> pd.Series:
    """逐日回放最近 ``days`` 个交易日的评分结论（无前视）。

    每个交易日 t 只用 df 前缀 [0, t] 重新评分；历史不足 MIN_BARS 的
    起始段自动跳过。返回以时间为索引的结论码 Series。
    """
    n = len(df)
    start = max(MIN_BARS - 1, n - days)
    if start >= n:
        raise ValueError(
            f"历史 K 线共 {n} 根，不足以回放（评分至少需要 {MIN_BARS} 根）；请加大 --count。"
        )

    idx = _datetime_index(df)  # 只解析一次，避免逐日重复转换
    verdicts = {}
    for i in range(start, n):
        sub = df.iloc[: i + 1]
        bench_sub = None
        if benchmark_close is not None:
            ts = idx[i] if idx is not None else None
            bench_sub = (
                benchmark_close.loc[:ts]
                if ts is not None and isinstance(benchmark_close.index, pd.DatetimeIndex)
                else benchmark_close.iloc[: i + 1]
            )
        res = score_symbol(sub, symbol=symbol, benchmark_close=bench_sub)
        verdicts[idx[i] if idx is not None else i] = res.verdict
    return pd.Series(verdicts, name="verdict")


def replay_study(
    df: pd.DataFrame,
    verdicts: pd.Series,
    benchmark_close: pd.Series | None = None,
) -> dict:
    """对回放结论做 21/63 日前瞻收益事件研究。

    事件 = 结论从非「是」转为「是」的次一交易日；每个窗口分别统计
    绝对收益与相对基准超额收益的 CAAR，并给出非重叠样本数。
    """
    close = df["close"].astype(float).reset_index(drop=True)
    index = _datetime_index(df)
    if index is None:
        raise RuntimeError("回放事件研究需要日期索引（数据缺少 trade_date 等时间列）。")
    close.index = index

    codes = verdicts.to_numpy()
    prev = np.concatenate([["_"], codes[:-1]])
    entry_dates = [t for t, cur, pre in zip(verdicts.index, codes, prev) if cur == "yes" and pre != "yes"]

    distribution = verdicts.value_counts().to_dict()
    out: dict = {
        "days": int(len(verdicts)),
        "verdict_distribution": {k: int(v) for k, v in distribution.items()},
        "n_yes_entries": len(entry_dates),
        "horizons": {},
    }
    if not entry_dates:
        out["inconclusive"] = True
        out["note"] = "回放期内没有任何「是」信号，无法做前瞻收益研究"
        return out

    # 事件日 = 入场信号次一交易日（度量信号之后的收益）
    pos = index.searchsorted(pd.DatetimeIndex(entry_dates))
    next_pos = [int(p) + 1 for p in pos if int(p) + 1 < len(index)]
    events = [index[p] for p in next_pos]

    bench = None
    if benchmark_close is not None and isinstance(benchmark_close.index, pd.DatetimeIndex):
        bench = benchmark_close.reindex(index).ffill()

    inconclusive = False
    for h in HORIZONS:
        nonoverlap = _nonoverlap_count(next_pos, h)
        entry: dict = {"n_nonoverlap": nonoverlap}
        for label, benchmark in (("absolute", None), ("excess", bench)):
            if label == "excess" and bench is None:
                entry["excess"] = None
                continue
            try:
                study = event_study(close, events, window=(0, h - 1), benchmark=benchmark)
                entry[label] = {
                    "n_used": int(study["n_used"]),
                    "n_skipped": int(study["n_skipped"]),
                    "caar_end": float(study["table"]["CAAR"].iloc[-1]),
                    "mean_win": float((study["per_event"]["cum_abnormal_return"] > 0).mean()),
                }
            except RuntimeError as exc:  # 全部窗口越界
                entry[label] = None
                print(f"[warn] {h} 日窗口事件研究不可用：{exc}", file=sys.stderr)
        if nonoverlap < MIN_SAMPLES:
            inconclusive = True
        out["horizons"][str(h)] = entry

    out["inconclusive"] = inconclusive
    if inconclusive:
        out["note"] = (
            f"非重叠样本不足 {MIN_SAMPLES} 个，结果为 inconclusive：不能据此确认或否定评分有效性"
        )
    return out


def format_replay_report(study: dict) -> list[str]:
    """回放研究的终端展示行。"""
    lines = [f"回放交易日    : {study['days']}"]
    dist = study.get("verdict_distribution", {})
    from .engine import VERDICT_CN

    lines.append(
        "结论分布      : "
        + "，".join(f"{VERDICT_CN.get(k, k)} {v}" for k, v in sorted(dist.items(), key=lambda kv: -kv[1]))
    )
    lines.append(f"「是」信号次数: {study.get('n_yes_entries', 0)}（非「是」转「是」）")
    for h, entry in study.get("horizons", {}).items():
        if entry is None:
            continue
        parts = [f"{h} 日窗口：非重叠样本 {entry['n_nonoverlap']}"]
        absolute = entry.get("absolute")
        if absolute:
            parts.append(
                f"绝对 CAAR {absolute['caar_end'] * 100:+.2f}%（{absolute['n_used']} 事件，胜率 {absolute['mean_win'] * 100:.0f}%）"
            )
        excess = entry.get("excess")
        if excess:
            parts.append(f"超额 CAAR {excess['caar_end'] * 100:+.2f}%")
        lines.append("  " + "，".join(parts))
    if study.get("inconclusive"):
        lines.append(f"⚠️  {study.get('note', 'inconclusive')}")
    return lines


def calibrate_threshold(
    df: pd.DataFrame,
    benchmark_close: pd.Series | None = None,
    days: int = 250,
    horizon: int = 21,
    symbol: str = "",
    min_samples: int = 10,
) -> dict:
    """回放驱动的评分阈值自校准：找最优 alpha_score 入场阈值。

    对最近 ``days`` 个交易日逐日重算 alpha_score，并计算每个 bar 的
    前瞻 ``horizon`` 日收益；然后在阈值网格上统计「alpha_score >= 阈值」
    的子集的胜率与平均前瞻收益，返回最优阈值与全网格统计。

    最优标准：在样本数 >= min_samples 的阈值中，选胜率最高者；
    胜率相同时选平均收益更高者。

    Returns:
        {"best_threshold": float, "best_hit_rate": float,
         "best_avg_return": float, "best_n": int,
         "grid": [{"threshold", "n", "hit_rate", "avg_return"}],
         "horizon": int, "total_days": int}
    """
    n = len(df)
    start = max(MIN_BARS - 1, n - days)
    if start >= n:
        raise ValueError(
            f"历史 K 线共 {n} 根，不足以回放（评分至少需要 {MIN_BARS} 根）；请加大 --count。"
        )

    close = df["close"].astype(float).reset_index(drop=True)
    idx = _datetime_index(df)

    # 逐日回放收集 (alpha_score, forward_return)
    records: list[tuple[float, float]] = []
    for i in range(start, n - horizon):
        sub = df.iloc[: i + 1]
        bench_sub = None
        if benchmark_close is not None:
            ts = idx[i] if idx is not None else None
            bench_sub = (
                benchmark_close.loc[:ts]
                if ts is not None and isinstance(benchmark_close.index, pd.DatetimeIndex)
                else benchmark_close.iloc[: i + 1]
            )
        res = score_symbol(sub, symbol=symbol, benchmark_close=bench_sub)
        if res.alpha_score is None:
            continue
        fwd_ret = float(close.iloc[i + horizon] / close.iloc[i] - 1.0)
        records.append((res.alpha_score, fwd_ret))

    if len(records) < min_samples:
        return {
            "best_threshold": None,
            "best_hit_rate": None,
            "best_avg_return": None,
            "best_n": len(records),
            "grid": [],
            "horizon": horizon,
            "total_days": len(records),
            "note": f"有效样本仅 {len(records)} 个（< {min_samples}），无法可靠校准",
        }

    scores = np.array([r[0] for r in records])
    rets = np.array([r[1] for r in records])

    # 阈值网格：从数据分布的 20%~90% 分位，步长 5
    lo = max(20.0, float(np.percentile(scores, 10)))
    hi = min(90.0, float(np.percentile(scores, 90)))
    thresholds = np.arange(np.floor(lo / 5) * 5, np.ceil(hi / 5) * 5 + 1, 5.0)

    grid = []
    for th in thresholds:
        mask = scores >= th
        cnt = int(mask.sum())
        if cnt < min_samples:
            continue
        sub_rets = rets[mask]
        hit_rate = float((sub_rets > 0).mean())
        avg_ret = float(sub_rets.mean())
        grid.append({
            "threshold": float(th),
            "n": cnt,
            "hit_rate": hit_rate,
            "avg_return": avg_ret,
        })

    if not grid:
        return {
            "best_threshold": None,
            "best_hit_rate": None,
            "best_avg_return": None,
            "best_n": len(records),
            "grid": [],
            "horizon": horizon,
            "total_days": len(records),
            "note": "所有阈值候选的样本数均不足，无法校准",
        }

    # 最优：胜率最高，平局时选平均收益更高
    best = max(grid, key=lambda g: (g["hit_rate"], g["avg_return"]))
    return {
        "best_threshold": best["threshold"],
        "best_hit_rate": best["hit_rate"],
        "best_avg_return": best["avg_return"],
        "best_n": best["n"],
        "grid": grid,
        "horizon": horizon,
        "total_days": len(records),
    }


def _nonoverlap_count(positions: list[int], horizon: int) -> int:
    """贪心统计间隔 ≥ horizon 根 K 线的非重叠事件数。"""
    count = 0
    last = -(10**9)
    for p in sorted(positions):
        if p - last >= horizon:
            count += 1
            last = p
    return count


def _datetime_index(df: pd.DataFrame) -> pd.DatetimeIndex | None:
    for col in ("trade_date", "date", "datetime", "time"):
        if col in df.columns:
            try:
                return pd.DatetimeIndex(pd.to_datetime(df[col]))
            except (ValueError, TypeError):
                return None
    return None

