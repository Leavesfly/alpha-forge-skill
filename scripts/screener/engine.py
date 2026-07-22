"""价值筛选引擎：两阶段漏斗（批量快照过滤 → 逐只深度过滤）+ 综合评分。

定位：用绝对估值/质量/分红/成长阈值从全市场中筛出低估+优质+潜力标的。
与 run_scan.py（趋势动量纪律过滤）和 run_factor.py（多因子截面排名）互补。

筛选是基本面快照过滤，不是收益预测；数据为最近公开报告期，存在滞后。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .data import (
    fetch_astock_detail,
    fetch_astock_snapshot,
    fetch_yfinance_metrics,
    is_a_share,
)


@dataclass
class ScreenCriteria:
    """六维筛选阈值（0 表示不启用该维度）+ 可选估值分位增强。"""

    max_pe: float = 20.0        # 市盈率上限
    max_pb: float = 3.0         # 市净率上限
    min_roe: float = 10.0       # ROE 下限(%)
    max_debt: float = 70.0      # 资产负债率上限(%)，默认会剔除高杠杆金融股，0=不筛
    min_div: float = 0.0        # 股息率下限(%)，0=不筛
    min_growth: float = 0.0     # 净利润增速下限(%)，0=不筛
    min_cap: float = 30.0       # 总市值下限(亿)
    use_valuation_pct: bool = False  # 是否启用估值历史分位增强（逐只拉取，较慢）
    valuation_lookback: int = 5      # 估值分位回看年数

    def to_dict(self) -> dict:
        d = {
            "max_pe": self.max_pe,
            "max_pb": self.max_pb,
            "min_roe": self.min_roe,
            "max_debt": self.max_debt,
            "min_div": self.min_div,
            "min_growth": self.min_growth,
            "min_cap": self.min_cap,
        }
        if self.use_valuation_pct:
            d["use_valuation_pct"] = True
            d["valuation_lookback"] = self.valuation_lookback
        return d


@dataclass
class ScreenResult:
    """单标的筛选结果。"""

    symbol: str
    name: str
    metrics: dict           # pe, pb, roe, debt_ratio, div_yield, profit_growth, total_mv, close
    score: float            # 综合评分 0~100
    passed: bool            # 是否通过全部启用维度
    fail_reasons: list[str] = field(default_factory=list)
    valuation: dict | None = None  # 估值历史分位（可选）

    def to_dict(self) -> dict:
        d = {
            "symbol": self.symbol,
            "name": self.name,
            "score": round(self.score, 1),
            "passed": self.passed,
            "fail_reasons": self.fail_reasons,
            **{k: _safe_round(v) for k, v in self.metrics.items()},
        }
        if self.valuation is not None:
            d["valuation"] = self.valuation
        return d


def _safe_round(v, ndigits: int = 2):
    """安全四舍五入：None/NaN 返回 None。"""
    if v is None:
        return None
    try:
        import math
        if math.isnan(float(v)):
            return None
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 综合评分
# ---------------------------------------------------------------------------

#: 各维度默认权重（未启用的维度权重重新归一化）
_WEIGHTS = {
    "pe": 0.25,
    "pb": 0.20,
    "roe": 0.25,
    "debt": 0.10,
    "div": 0.10,
    "growth": 0.10,
}


def composite_score(metrics: dict, criteria: ScreenCriteria) -> float:
    """综合评分：各维度达标程度加权，映射到 0~100。

    子分计算：
    - PE/PB/负债率（越低越好）：threshold / value，cap 到 2.0，再 /2 * 100
    - ROE/股息率/增速（越高越好）：value / threshold，cap 到 2.0，再 /2 * 100
    未启用的维度（阈值=0）不参与加权。
    """
    scores: dict[str, float] = {}
    weights: dict[str, float] = {}

    pe = metrics.get("pe")
    if criteria.max_pe > 0 and pe is not None and pe > 0:
        # PE 越低越好：阈值/实际值
        ratio = criteria.max_pe / pe
        scores["pe"] = min(ratio, 2.0) / 2.0 * 100.0
        weights["pe"] = _WEIGHTS["pe"]

    pb = metrics.get("pb")
    if criteria.max_pb > 0 and pb is not None and pb > 0:
        ratio = criteria.max_pb / pb
        scores["pb"] = min(ratio, 2.0) / 2.0 * 100.0
        weights["pb"] = _WEIGHTS["pb"]

    roe = metrics.get("roe")
    if criteria.min_roe > 0 and roe is not None:
        ratio = roe / criteria.min_roe if criteria.min_roe > 0 else 0
        scores["roe"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["roe"] = _WEIGHTS["roe"]

    debt = metrics.get("debt_ratio")
    if criteria.max_debt > 0 and debt is not None and debt > 0:
        ratio = criteria.max_debt / debt
        scores["debt"] = min(ratio, 2.0) / 2.0 * 100.0
        weights["debt"] = _WEIGHTS["debt"]

    div = metrics.get("div_yield")
    if criteria.min_div > 0 and div is not None:
        ratio = div / criteria.min_div if criteria.min_div > 0 else 0
        scores["div"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["div"] = _WEIGHTS["div"]

    growth = metrics.get("profit_growth")
    if criteria.min_growth > 0 and growth is not None:
        ratio = growth / criteria.min_growth if criteria.min_growth > 0 else 0
        scores["growth"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["growth"] = _WEIGHTS["growth"]

    if not weights:
        return 0.0

    # 权重归一化
    total_w = sum(weights.values())
    score = sum(scores[k] * weights[k] / total_w for k in scores)
    return round(score, 1)


# ---------------------------------------------------------------------------
# Phase 1: A 股批量快照过滤
# ---------------------------------------------------------------------------


def screen_astock_phase1(
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
) -> tuple[list[dict], int]:
    """A 股 Phase 1：批量快照过滤（PE/PB/市值/ST）。

    Returns:
        (存活标的列表[{code, name, pe, pb, total_mv, div_yield, close}], 总扫描数)
    """
    snapshot = fetch_astock_snapshot(log)
    if snapshot is None or len(snapshot) == 0:
        return [], 0

    total = len(snapshot)
    df = snapshot.copy()

    # 排除 ST/*ST
    if "name" in df.columns:
        mask_st = df["name"].astype(str).str.contains("ST", case=False, na=False)
        df = df[~mask_st]

    # PE 过滤（正值且 < max_pe）
    if criteria.max_pe > 0 and "pe" in df.columns:
        df = df[(df["pe"] > 0) & (df["pe"] <= criteria.max_pe)]

    # PB 过滤（正值且 < max_pb）
    if criteria.max_pb > 0 and "pb" in df.columns:
        df = df[(df["pb"] > 0) & (df["pb"] <= criteria.max_pb)]

    # 市值过滤
    if criteria.min_cap > 0 and "total_mv" in df.columns:
        df = df[df["total_mv"] >= criteria.min_cap]

    survivors = df.to_dict("records")
    if log:
        log(f"Phase 1 快照过滤：{total} 只 → {len(survivors)} 只存活")
    return survivors, total


# ---------------------------------------------------------------------------
# Phase 2: A 股逐只深度过滤
# ---------------------------------------------------------------------------


def screen_astock_phase2(
    survivors: list[dict],
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[ScreenResult]:
    """A 股 Phase 2：逐只拉取 ROE/负债率/增速并过滤。

    Args:
        survivors: Phase 1 存活列表。
        criteria: 筛选阈值。
        log: 日志函数。
        on_progress: 进度回调 (done, symbol)。

    Returns:
        通过全部启用维度的 ScreenResult 列表（按综合评分降序）。
    """
    # 判断是否需要 Phase 2（有深度指标阈值启用时才逐只拉取）
    need_detail = (
        criteria.min_roe > 0 or criteria.max_debt > 0 or criteria.min_growth > 0
    )

    results: list[ScreenResult] = []
    skipped = 0

    for i, row in enumerate(survivors):
        code = str(row.get("code", ""))
        name = str(row.get("name", ""))
        symbol = _code_to_symbol(code)

        metrics = {
            "pe": row.get("pe"),
            "pb": row.get("pb"),
            "total_mv": row.get("total_mv"),
            "div_yield": row.get("div_yield"),
            "close": row.get("close"),
            "roe": None,
            "debt_ratio": None,
            "profit_growth": None,
        }

        # 股息率 Phase 1 过滤（如果快照有该字段且阈值启用）
        if criteria.min_div > 0:
            div = metrics.get("div_yield")
            if div is None or div < criteria.min_div:
                if on_progress:
                    on_progress(i + 1, symbol)
                continue

        # 逐只深度拉取
        if need_detail:
            detail = fetch_astock_detail(code)
            if detail is None:
                skipped += 1
                if on_progress:
                    on_progress(i + 1, symbol)
                continue
            metrics["roe"] = detail.get("roe")
            metrics["debt_ratio"] = detail.get("debt_ratio")
            metrics["profit_growth"] = detail.get("profit_growth")

        # 深度过滤
        fail_reasons = _check_detail_criteria(metrics, criteria)
        passed = len(fail_reasons) == 0

        score = composite_score(metrics, criteria)
        results.append(ScreenResult(
            symbol=symbol,
            name=name,
            metrics=metrics,
            score=score,
            passed=passed,
            fail_reasons=fail_reasons,
        ))

        if on_progress:
            on_progress(i + 1, symbol)

    if log and skipped:
        log(f"Phase 2 跳过 {skipped} 只（财务指标拉取失败）")

    # 只保留通过的，按评分排序
    passed_results = [r for r in results if r.passed]
    passed_results.sort(key=lambda r: r.score, reverse=True)
    return passed_results


def _check_detail_criteria(metrics: dict, criteria: ScreenCriteria) -> list[str]:
    """检查深度指标是否达标，返回失败原因列表。"""
    reasons: list[str] = []

    if criteria.min_roe > 0:
        roe = metrics.get("roe")
        if roe is None:
            reasons.append("ROE 数据缺失")
        elif roe < criteria.min_roe:
            reasons.append(f"ROE {roe:.1f}% < {criteria.min_roe:.0f}%")

    if criteria.max_debt > 0:
        debt = metrics.get("debt_ratio")
        if debt is None:
            reasons.append("负债率数据缺失")
        elif debt > criteria.max_debt:
            reasons.append(f"负债率 {debt:.1f}% > {criteria.max_debt:.0f}%")

    if criteria.min_growth > 0:
        growth = metrics.get("profit_growth")
        if growth is None:
            reasons.append("增速数据缺失")
        elif growth < criteria.min_growth:
            reasons.append(f"净利润增速 {growth:.1f}% < {criteria.min_growth:.0f}%")

    return reasons


# ---------------------------------------------------------------------------
# 港美股：yfinance 逐只（Phase 1+2 合并）
# ---------------------------------------------------------------------------


def screen_yfinance(
    symbols: list[str],
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[ScreenResult]:
    """港美股逐只筛选（yfinance .info，Phase 1+2 合并）。

    Returns:
        通过全部启用维度的 ScreenResult 列表（按综合评分降序）。
    """
    results: list[ScreenResult] = []
    skipped = 0

    for i, symbol in enumerate(symbols):
        info = fetch_yfinance_metrics(symbol)
        if info is None:
            skipped += 1
            if on_progress:
                on_progress(i + 1, symbol)
            continue

        metrics = {
            "pe": info.get("pe"),
            "pb": info.get("pb"),
            "roe": info.get("roe"),
            "div_yield": info.get("div_yield"),
            "debt_ratio": info.get("debt_ratio"),
            "profit_growth": info.get("profit_growth"),
            "total_mv": info.get("total_mv"),
            "close": info.get("close"),
        }

        # 全维度过滤
        fail_reasons = _check_all_criteria(metrics, criteria)
        passed = len(fail_reasons) == 0
        score = composite_score(metrics, criteria)

        results.append(ScreenResult(
            symbol=symbol,
            name=info.get("name", symbol),
            metrics=metrics,
            score=score,
            passed=passed,
            fail_reasons=fail_reasons,
        ))

        if on_progress:
            on_progress(i + 1, symbol)

    if log and skipped:
        log(f"跳过 {skipped} 只（yfinance 拉取失败）")

    passed_results = [r for r in results if r.passed]
    passed_results.sort(key=lambda r: r.score, reverse=True)
    return passed_results


def _check_all_criteria(metrics: dict, criteria: ScreenCriteria) -> list[str]:
    """检查全部维度（港美股无分阶段，一次全检）。"""
    reasons: list[str] = []

    if criteria.max_pe > 0:
        pe = metrics.get("pe")
        if pe is None or pe <= 0:
            reasons.append("PE 无效或缺失")
        elif pe > criteria.max_pe:
            reasons.append(f"PE {pe:.1f} > {criteria.max_pe:.0f}")

    if criteria.max_pb > 0:
        pb = metrics.get("pb")
        if pb is None or pb <= 0:
            reasons.append("PB 无效或缺失")
        elif pb > criteria.max_pb:
            reasons.append(f"PB {pb:.2f} > {criteria.max_pb:.1f}")

    if criteria.min_cap > 0:
        mv = metrics.get("total_mv")
        if mv is not None and mv < criteria.min_cap:
            reasons.append(f"市值 {mv:.0f} 亿 < {criteria.min_cap:.0f} 亿")

    if criteria.min_div > 0:
        div = metrics.get("div_yield")
        if div is None or div < criteria.min_div:
            reasons.append(f"股息率 {div if div else 0:.1f}% < {criteria.min_div:.1f}%")

    reasons.extend(_check_detail_criteria(metrics, criteria))
    return reasons


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------


def run_screen(
    criteria: ScreenCriteria,
    symbols: list[str] | None = None,
    top: int = 30,
    sort_by: str = "score",
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> dict:
    """统一筛选入口：自动分流 A 股批量 / 港美股逐只。

    Args:
        criteria: 筛选阈值。
        symbols: 手动标的列表（含港美股时必须）；None 时走 A 股全市场。
        top: 最多返回达标数。
        sort_by: 排序字段（score/pe/pb/roe/div/growth）。
        log: 日志函数。
        on_progress: 进度回调。

    Returns:
        {"candidates": [...], "n_scanned": int, "n_phase1": int, "n_final": int}
    """
    if symbols:
        # 手动列表：按市场分流
        a_symbols = [s for s in symbols if is_a_share(s)]
        other_symbols = [s for s in symbols if not is_a_share(s)]

        all_results: list[ScreenResult] = []

        # A 股部分：也走两阶段（但 Phase 1 用手动列表而非全市场快照）
        if a_symbols:
            # 手动 A 股：直接逐只（无批量快照优势，合并为 yfinance 式逐只）
            a_results = _screen_astock_manual(a_symbols, criteria, log, on_progress)
            all_results.extend(a_results)

        # 港美股部分
        if other_symbols:
            yf_results = screen_yfinance(other_symbols, criteria, log, on_progress)
            all_results.extend(yf_results)

        n_scanned = len(symbols)
        n_phase1 = n_scanned  # 手动模式无分阶段
    else:
        # A 股全市场批量
        survivors, n_scanned = screen_astock_phase1(criteria, log)
        n_phase1 = len(survivors)
        all_results = screen_astock_phase2(survivors, criteria, log, on_progress)

    # 估值分位增强（可选，逐只拉取历史 PE/PB）
    if criteria.use_valuation_pct and all_results:
        all_results = _enrich_valuation(
            all_results, criteria.valuation_lookback, log, on_progress
        )

    # 排序
    all_results = _sort_results(all_results, sort_by)
    candidates = all_results[:top]

    return {
        "candidates": [r.to_dict() for r in candidates],
        "n_scanned": n_scanned,
        "n_phase1": n_phase1 if not symbols else None,
        "n_final": len(all_results),
    }


def _screen_astock_manual(
    symbols: list[str],
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[ScreenResult]:
    """手动 A 股列表：逐只拉取全部指标（无批量快照优势）。"""
    results: list[ScreenResult] = []
    skipped = 0

    for i, symbol in enumerate(symbols):
        code = symbol.split(".")[0]
        detail = fetch_astock_detail(code)

        # 尝试从快照获取 PE/PB（逐只无批量接口，用 yfinance 兜底）
        info = fetch_yfinance_metrics(symbol)
        if info is None and detail is None:
            skipped += 1
            if on_progress:
                on_progress(i + 1, symbol)
            continue

        metrics = {
            "pe": (info or {}).get("pe"),
            "pb": (info or {}).get("pb"),
            "roe": (detail or {}).get("roe"),
            "div_yield": (info or {}).get("div_yield"),
            "debt_ratio": (detail or {}).get("debt_ratio"),
            "profit_growth": (detail or {}).get("profit_growth"),
            "total_mv": (info or {}).get("total_mv"),
            "close": (info or {}).get("close"),
        }

        fail_reasons = _check_all_criteria(metrics, criteria)
        passed = len(fail_reasons) == 0
        score = composite_score(metrics, criteria)

        results.append(ScreenResult(
            symbol=symbol,
            name=(info or {}).get("name", symbol),
            metrics=metrics,
            score=score,
            passed=passed,
            fail_reasons=fail_reasons,
        ))

        if on_progress:
            on_progress(i + 1, symbol)

    if log and skipped:
        log(f"跳过 {skipped} 只 A 股（数据拉取失败）")

    passed_results = [r for r in results if r.passed]
    passed_results.sort(key=lambda r: r.score, reverse=True)
    return passed_results


def _enrich_valuation(
    results: list[ScreenResult],
    lookback_years: int,
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[ScreenResult]:
    """为候选标的附加估值历史分位（逐只拉取，较慢）。

    估值分位作为评分加成：低分位（便宜）加分，高分位（贵）减分。
    加成幅度：±10 分（在原始综合评分基础上）。
    """
    from data.valuation import fetch_valuation_percentile

    if log:
        log(f"估值分位增强：拉取 {len(results)} 只候选的历史 PE/PB（近 {lookback_years} 年）...")

    enriched: list[ScreenResult] = []
    for i, r in enumerate(results):
        vp = fetch_valuation_percentile(r.symbol, lookback_years)
        if vp is not None:
            r.valuation = vp.to_dict()
            # 评分加成：分位越低（越便宜）加分越多
            pcts = [p for p in (vp.pe_percentile, vp.pb_percentile) if p is not None]
            if pcts:
                avg_pct = sum(pcts) / len(pcts)
                # 分位 0% → +10，分位 50% → 0，分位 100% → -10
                bonus = (0.5 - avg_pct) * 20.0
                r.score = max(0.0, min(100.0, r.score + bonus))
        enriched.append(r)
        if on_progress:
            on_progress(i + 1, r.symbol)

    if log:
        n_ok = sum(1 for r in enriched if r.valuation is not None)
        log(f"估值分位增强完成：{n_ok}/{len(results)} 只成功获取")

    return enriched


def _sort_results(results: list[ScreenResult], sort_by: str) -> list[ScreenResult]:
    """按指定字段排序（降序，PE/PB 为升序）。"""
    if sort_by == "pe":
        return sorted(results, key=lambda r: r.metrics.get("pe") or 9999)
    if sort_by == "pb":
        return sorted(results, key=lambda r: r.metrics.get("pb") or 9999)
    if sort_by == "roe":
        return sorted(results, key=lambda r: r.metrics.get("roe") or 0, reverse=True)
    if sort_by == "div":
        return sorted(results, key=lambda r: r.metrics.get("div_yield") or 0, reverse=True)
    if sort_by == "growth":
        return sorted(results, key=lambda r: r.metrics.get("profit_growth") or 0, reverse=True)
    # 默认按综合评分
    return sorted(results, key=lambda r: r.score, reverse=True)


def _code_to_symbol(code: str) -> str:
    """A 股纯数字代码 → 带市场后缀（6→SH，0/3→SZ，4/8→BJ）。"""
    code = code.strip()
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"  # 默认深交所
