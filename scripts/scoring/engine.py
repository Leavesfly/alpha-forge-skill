"""买点三灯引擎：价 / 势 / 时三个正交维度 + 决策矩阵。

回答的不是「这家公司好不好」，而是把「现在能不能买」拆成三个**互相独立**的问题，
各自亮灯（绿/黄/红/灰，灰=数据不可用，诚实标注不猜测）：

- **价**（值不值得拥有）：基本面硬伤否决（ST/连续亏损/资不抵债）+
  估值历史分位（PE/PB 相对自身近 N 年历史的位置）；
- **势**（市场是否认同）：趋势分（风险调整动量 55% + 相对基准强度 35% +
  Kaufman 趋势效率 10%）+ MA60/MA200/周线结构 + 大盘环境；
- **时**（是不是好买点）：偏离 MA20 过热、距 60 日高点回撤、RSI 过热、
  量价背离、近 30 天事件风险。

三灯经**决策矩阵**输出行动结论（七态），而非单一分数阈值：

- 势绿+时绿 → 「趋势买点」（价红非硬伤时降为「纯趋势仓」，强制估值警示）；
- 势绿+时非绿 → 「等回踩」（给回踩参考位）；
- 价绿+势弱 → 「左侧观察」（进观察名单，给触发条件，不抄底；价灯深绿
  且无硬伤时附「左侧分批计划」，引导 DCA 分批而非一次性抄底）；
- 价硬伤红 → 「回避」（一票否决）；势弱且价无吸引力 → 「回避」；
- 持仓且势红/价硬伤 → 「持仓需减风险」（不等待回本）。

动态自适应阈值：时灯的固定阈值受波动率缩放因子 vol_k 调整
（高波动放宽、低波动收紧）。三灯规则为纪律预设值，未经样本外验证；
``scoring.replay`` 提供历史回放 + 前瞻收益事件研究自证（仅覆盖势/时维度），
结论不应理解为已验证的选股 alpha。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from utils import resolve_time_index, safe_round, series_last

from .indicators import annualized_vol, atr, compute_rsi, efficiency_ratio
from .plan import build_trade_plan

#: 结论七态（机器码 -> 中文展示）
VERDICT_CN = {
    "trend_entry": "趋势买点",
    "trend_only": "纯趋势仓",
    "wait_pullback": "等回踩",
    "left_watch": "左侧观察",
    "avoid": "回避",
    "reduce_risk": "持仓需减风险",
    "unrated": "无法评分",
}
VERDICTS = tuple(VERDICT_CN)

#: 可给交易计划的行动态（等回踩也给：回踩参考位就是计划的一部分）
ACTIONABLE_VERDICTS = ("trend_entry", "trend_only", "wait_pullback")

#: 三灯维度（机器名 -> 中文单字）
LIGHT_CN = {"value": "价", "trend": "势", "timing": "时"}
LIGHTS = tuple(LIGHT_CN)

#: 灯色中文
COLOR_CN = {"green": "绿", "yellow": "黄", "red": "红", "gray": "灰"}

#: 有效评分所需最少 K 线数（MA200 + 动量窗口需要足够历史）
MIN_BARS = 250

#: 趋势分 -> 势灯的阈值（≥60 绿灯候选，<45 红灯）
TREND_GREEN = 60.0
TREND_RED = 45.0

#: 估值分位均值 -> 价灯的阈值（≤0.4 绿，>0.7 红）
VAL_GREEN = 0.4
VAL_RED = 0.7

#: 价灯「深绿」阈值：分位均值 ≤0.25 才够便宜，左侧观察附分批计划（引导 DCA）
VAL_DEEP = 0.25

#: 波动率缩放因子边界（动态阈值）
VOL_K_MIN = 0.8
VOL_K_MAX = 1.4

#: 各市场后缀的默认基准（可被 --benchmark 覆盖）
DEFAULT_BENCHMARKS = {
    "SH": "510300.SH",
    "SZ": "510300.SH",
    "BJ": "510300.SH",
    "HK": "02800.HK",
    "US": "SPY.US",
}


def default_benchmark(symbol: str) -> str | None:
    """按市场后缀返回默认基准；期货等无基准市场返回 None（降级评分）。"""
    suffix = symbol.rsplit(".", 1)[-1].upper() if "." in symbol else ""
    return DEFAULT_BENCHMARKS.get(suffix)


@dataclass
class ScoreResult:
    """单标的买点三灯结果。"""

    symbol: str
    verdict: str  # trend_entry / trend_only / wait_pullback / left_watch / avoid / reduce_risk / unrated
    lights: dict  # {value|trend|timing: {color, reasons, detail}}
    trend_score: float | None
    components: dict  # 趋势分三项子分与权重
    snapshot: dict  # 关键指标快照
    plan: dict | None  # 交易计划（仅行动态）
    benchmark: str | None
    asof: str  # 最近一根 K 线日期
    n_bars: int
    decision: dict = field(default_factory=dict)  # 矩阵裁决 {rule, triggers}
    position: dict | None = None  # 持仓联动（只改操作建议）
    risk_events: list[dict] = field(default_factory=list)  # 触发的风险事件
    evidence: list[dict] = field(default_factory=list)  # 结构化证据链（Agent 可引用）
    left_plan: dict | None = None  # 左侧分批计划（仅左侧观察且价深绿+无硬伤）

    @property
    def verdict_cn(self) -> str:
        return VERDICT_CN[self.verdict]

    @property
    def lights_summary(self) -> str:
        """三灯速览，如「价绿 · 势绿 · 时黄」。"""
        return " · ".join(
            f"{LIGHT_CN[name]}{COLOR_CN.get(self.lights.get(name, {}).get('color', 'gray'), '灰')}"
            for name in LIGHTS
        )

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "verdict": self.verdict,
            "verdict_cn": self.verdict_cn,
            "lights": self.lights,
            "lights_summary": self.lights_summary,
            "trend_score": self.trend_score,
            "components": self.components,
            "decision": self.decision,
            "evidence": self.evidence,
            "snapshot": self.snapshot,
            "plan": self.plan,
            "benchmark": self.benchmark,
            "asof": self.asof,
            "n_bars": self.n_bars,
            "position": self.position,
            "risk_events": self.risk_events,
            "left_plan": self.left_plan,
        }


def score_symbol(
    df: pd.DataFrame,
    symbol: str = "",
    benchmark_close: pd.Series | None = None,
    benchmark_symbol: str | None = None,
    risk_events: list[dict] | None = None,
    position: dict | None = None,
    fundamentals: dict | None = None,
    valuation=None,
) -> ScoreResult:
    """对单标的执行买点三灯评估。

    Args:
        df: 含 ``close``（可选 high/low/volume）的 OHLCV DataFrame（时间升序）。
        symbol: 标的代码（用于展示）。
        benchmark_close: 基准收盘价序列（None 时相对强度权重并入动量并标注降级）。
        benchmark_symbol: 基准代码（用于展示）。
        risk_events: 事件风险列表 ``[{date, risk, note}]``，high 亮红时灯，利好不加分。
        position: 持仓信息 ``{cost, shares, source}``，只改操作建议不改灯色。
        fundamentals: 基本面数据 ``{eps_recent, is_st, net_asset_per_share, source}``，
            None 时价灯的硬伤检查跳过。
        valuation: 估值分位（``data.valuation.ValuationPercentile`` 或同构 dict），
            None 时价灯降级为灰（诚实标注，不猜测）。

    Returns:
        ScoreResult。评估仅使用截至最近一根已完成 K 线的数据，无前视。
    """
    close = df["close"].astype(float).reset_index(drop=True)
    index = resolve_time_index(df)
    close.index = index
    asof = str(index[-1])[:10] if len(index) else ""
    n = int(close.notna().sum())

    if n < MIN_BARS:
        reason = (
            f"有效 K 线仅 {n} 根，低于评估所需 {MIN_BARS} 根"
            "（MA200/动量窗口无法形成），不用猜测补齐"
        )
        return ScoreResult(
            symbol=symbol,
            verdict="unrated",
            lights={name: {"color": "gray", "reasons": [reason], "detail": {}} for name in LIGHTS},
            trend_score=None,
            components={},
            snapshot={"close": series_last(close), "n_bars": n},
            plan=None,
            benchmark=benchmark_symbol,
            asof=asof,
            n_bars=n,
            decision={"rule": "数据不足，无法评估", "triggers": [f"补足历史至 {MIN_BARS} 根以上"]},
        )

    volume = df["volume"].astype(float).reset_index(drop=True) if "volume" in df.columns else None
    if volume is not None:
        volume.index = index

    # ---------- 动态阈值：波动率缩放因子 ----------
    vol_k = _vol_regime(close)

    # ---------- 价灯：基本面硬伤 + 估值分位 ----------
    value_light = _value_light(fundamentals, valuation)

    # ---------- 势灯：趋势分 + 均线/周线/大盘结构 ----------
    trend_light, trend_score, components, snapshot = _trend_light(close, benchmark_close)

    # ---------- 时灯：过热/回调/RSI/量价/事件风险 ----------
    timing_light, snapshot2, triggered_events = _timing_light(
        df, close, volume, vol_k, risk_events, index
    )
    snapshot.update(snapshot2)

    # ---------- 决策矩阵 ----------
    verdict, decision = _decide(value_light, trend_light, timing_light, snapshot)

    # ---------- 交易计划（仅行动态） ----------
    atr14 = series_last(atr(df.reset_index(drop=True), 14))
    snapshot["atr14"] = atr14
    plan = None
    if verdict in ACTIONABLE_VERDICTS:
        plan = build_trade_plan(series_last(close), snapshot.get("ma20"), atr14)

    # ---------- 持仓联动（只改操作建议，不改灯色） ----------
    position_out = None
    if position is not None and position.get("cost"):
        verdict, position_out = _position_overlay(
            position, close, atr14, verdict, value_light, trend_light
        )

    # ---------- 左侧分批计划（仅左侧观察且价深绿+无硬伤，引导 DCA） ----------
    left_plan = None
    if verdict == "left_watch":
        left_plan = _left_side_plan(value_light, decision, symbol)

    snapshot["close"] = series_last(close)
    snapshot["vol_k"] = round(vol_k, 3)

    lights = {"value": value_light, "trend": trend_light, "timing": timing_light}

    # ---------- 结构化证据链（Agent 可引用编号转述） ----------
    evidence = _build_evidence(lights, trend_score, components, snapshot, vol_k)

    return ScoreResult(
        symbol=symbol,
        verdict=verdict,
        lights=lights,
        trend_score=round(trend_score, 1),
        components=components,
        snapshot={k: safe_round(v) for k, v in snapshot.items()},
        plan=plan,
        benchmark=benchmark_symbol,
        asof=asof,
        n_bars=n,
        decision=decision,
        position=position_out,
        risk_events=triggered_events,
        evidence=evidence,
        left_plan=left_plan,
    )


# ---------------------------------------------------------------- 价灯


def _value_light(fundamentals: dict | None, valuation) -> dict:
    """价灯：基本面硬伤一票红灯 + 估值分位定绿/黄/红；无数据亮灰。

    硬伤规则（红灯 + hard_flaw=True）：ST/*ST、每股净资产 < 0（资不抵债）、
    最近 4 季 EPS 均 < 0（连续亏损）。由盈转亏不算硬伤，但价灯封顶黄。
    估值分位：PE/PB 分位均值 ≤0.4 绿、0.4~0.7 黄、>0.7 红。
    """
    reasons: list[str] = []
    red_reasons: list[str] = []
    yellow_reasons: list[str] = []
    hard_flaw = False
    profit_to_loss = False

    if fundamentals is not None:
        source = fundamentals.get("source", "unknown")
        if fundamentals.get("is_st"):
            reasons.append("ST/*ST 标的，退市风险（硬伤）")
            red_reasons.append(reasons[-1])
            hard_flaw = True
        naps = fundamentals.get("net_asset_per_share")
        if naps is not None and isinstance(naps, (int, float)) and naps < 0:
            reasons.append(f"每股净资产 {naps:.2f} < 0，资不抵债（硬伤）")
            red_reasons.append(reasons[-1])
            hard_flaw = True
        eps_recent = fundamentals.get("eps_recent")
        if eps_recent and len(eps_recent) >= 4 and not hard_flaw:
            eps_vals = [float(e) for e in eps_recent[-4:] if e is not None]
            if len(eps_vals) >= 4:
                if all(e < 0 for e in eps_vals):
                    reasons.append(
                        f"最近 4 季 EPS 均为负（{', '.join(f'{e:.3f}' for e in eps_vals)}），连续亏损（硬伤）"
                    )
                    red_reasons.append(reasons[-1])
                    hard_flaw = True
                elif eps_vals[-1] < 0 and eps_vals[-2] < 0 and eps_vals[-3] > 0:
                    profit_to_loss = True
                    reasons.append(
                        f"由盈转亏：第 3 季 EPS {eps_vals[-3]:.3f} > 0，"
                        f"近 2 季 {eps_vals[-2]:.3f}/{eps_vals[-1]:.3f} < 0，价灯封顶黄"
                    )
                    yellow_reasons.append(reasons[-1])
        if not hard_flaw and not profit_to_loss:
            reasons.append(f"基本面未见硬伤（数据源：{source}）")

    val_avg, pe_pct, pb_pct, val_note = _valuation_percentiles(valuation)

    detail = {
        "hard_flaw": hard_flaw,
        "profit_to_loss": profit_to_loss,
        "valuation_avg": safe_round(val_avg, 4) if val_avg is not None else None,
        "pe_percentile": safe_round(pe_pct, 4) if pe_pct is not None else None,
        "pb_percentile": safe_round(pb_pct, 4) if pb_pct is not None else None,
    }

    if hard_flaw:
        color = "red"
    elif val_avg is not None:
        if val_avg > VAL_RED:
            color = "red"
            reasons.append(f"估值分位均值 {val_avg:.0%} > {VAL_RED:.0%}，相对自身历史高估")
            red_reasons.append(reasons[-1])
        elif val_avg > VAL_GREEN:
            color = "yellow"
            reasons.append(f"估值分位均值 {val_avg:.0%}，处于自身历史中枢区间")
            yellow_reasons.append(reasons[-1])
        else:
            color = "yellow" if profit_to_loss else "green"
            reasons.append(f"估值分位均值 {val_avg:.0%} ≤ {VAL_GREEN:.0%}，相对自身历史偏低")
        if val_note:
            reasons.append(val_note)
    else:
        color = "yellow" if profit_to_loss else "gray"
        reasons.append("无估值分位数据：价维度无法判断，结论仅基于势/时（诚实降级，不猜测）")

    detail["red_reasons"] = red_reasons
    detail["yellow_reasons"] = yellow_reasons
    if not reasons:
        reasons.append("无基本面与估值数据，价灯灰")
    return {"color": color, "reasons": reasons, "detail": detail}


def _valuation_percentiles(valuation) -> tuple[float | None, float | None, float | None, str]:
    """从 ValuationPercentile 对象或同构 dict 提取 PE/PB 分位与均值。"""
    if valuation is None:
        return None, None, None, ""
    if isinstance(valuation, dict):
        pe_pct = valuation.get("pe_percentile")
        pb_pct = valuation.get("pb_percentile")
        source = valuation.get("source", "")
    else:
        pe_pct = getattr(valuation, "pe_percentile", None)
        pb_pct = getattr(valuation, "pb_percentile", None)
        source = getattr(valuation, "source", "")
    pcts = [float(p) for p in (pe_pct, pb_pct) if p is not None]
    if not pcts:
        return None, pe_pct, pb_pct, ""
    note = "估值分位为近似口径（历史价格/当前 EPS）" if "approx" in str(source) else ""
    return sum(pcts) / len(pcts), pe_pct, pb_pct, note


# ---------------------------------------------------------------- 势灯


def _trend_light(
    close: pd.Series, benchmark_close: pd.Series | None
) -> tuple[dict, float, dict, dict]:
    """势灯：趋势分 + MA60/MA200/周线结构 + 大盘环境，去重后单一定色。

    红：收盘 < MA200 或 趋势分 < 45；
    绿：站上 MA200/MA60、周线完好、大盘非 risk-off 且趋势分 ≥ 60；
    黄：其余（结构部分走弱或分数居中）。
    """
    trend_score, components, reasons = _trend_score(close, benchmark_close)

    last = float(close.iloc[-1])
    ma20 = series_last(close.rolling(20).mean())
    ma60 = series_last(close.rolling(60).mean())
    ma200 = series_last(close.rolling(200).mean())

    red = False
    yellow = False
    red_reasons: list[str] = []
    yellow_reasons: list[str] = []
    detail: dict = {"trend_score": round(trend_score, 1)}

    below_ma200 = not math.isnan(ma200) and last < ma200
    detail["below_ma200"] = below_ma200
    if below_ma200:
        red = True
        reasons.append(f"收盘 {last:.2f} 低于 MA200 {ma200:.2f}，长期趋势逆势（红灯）")
        red_reasons.append(reasons[-1])

    if trend_score < TREND_RED:
        red = True
        reasons.append(f"趋势分 {trend_score:.1f} 低于 {TREND_RED:.0f}，动能不足（红灯）")
        red_reasons.append(reasons[-1])
    elif trend_score < TREND_GREEN:
        yellow = True
        reasons.append(f"趋势分 {trend_score:.1f} 未达 {TREND_GREEN:.0f}，动能一般")
        yellow_reasons.append(reasons[-1])

    below_ma60 = not math.isnan(ma60) and last < ma60
    detail["below_ma60"] = below_ma60
    if below_ma60 and not below_ma200:
        yellow = True
        reasons.append(f"收盘 {last:.2f} 低于 MA60 {ma60:.2f}，中期趋势走弱")
        yellow_reasons.append(reasons[-1])

    weekly = _weekly_close(close)
    weekly_broken = False
    if len(weekly.dropna()) >= 30:
        wma30 = float(weekly.rolling(30).mean().iloc[-1])
        wlast = float(weekly.iloc[-1])
        weekly_broken = wlast < wma30
        if weekly_broken:
            yellow = True
            reasons.append(f"周线收盘 {wlast:.2f} 低于周线 MA30 {wma30:.2f}，周线结构走坏")
            yellow_reasons.append(reasons[-1])
    else:
        reasons.append("周线样本不足 30 根，跳过周线结构检查")
    detail["weekly_broken"] = weekly_broken

    bench_risk_off = False
    if benchmark_close is not None:
        bench = benchmark_close.dropna().astype(float)
        if len(bench) >= 200:
            bench_risk_off = float(bench.iloc[-1]) < float(bench.rolling(200).mean().iloc[-1])
            if bench_risk_off:
                yellow = True
                reasons.append("基准收盘低于其 MA200（大盘 risk-off），势灯封顶黄")
                yellow_reasons.append(reasons[-1])
        else:
            reasons.append("基准样本不足 200 根，跳过大盘环境检查")
    detail["bench_risk_off"] = bench_risk_off

    color = "red" if red else ("yellow" if yellow else "green")
    if color == "green":
        reasons.append("价格站上 MA60/MA200、周线与大盘环境完好，趋势结构健康")
    detail["red_reasons"] = red_reasons
    detail["yellow_reasons"] = yellow_reasons

    snapshot = {"ma20": ma20, "ma60": ma60, "ma200": ma200}
    return {"color": color, "reasons": reasons, "detail": detail}, trend_score, components, snapshot


def _trend_score(
    close: pd.Series, benchmark_close: pd.Series | None
) -> tuple[float, dict, list[str]]:
    """趋势分：动量 55 / 相对强度 35 / 趋势效率 10（0~100，扫描排序用）。

    无横截面对手时，各子分经 tanh 压缩映射到 0~100 的分位标尺；
    无基准时相对强度权重并入动量并在理由中标注降级。
    """
    ret60 = float(close.iloc[-1] / close.iloc[-61] - 1.0)
    vol60 = float(annualized_vol(close, 60).iloc[-1])
    ram = ret60 / vol60 if vol60 > 1e-12 else math.copysign(4.0, ret60) if ret60 else 0.0
    mom_score = 50.0 * (1.0 + math.tanh(ram))

    er = float(efficiency_ratio(close, 20).iloc[-1])
    er = 0.0 if math.isnan(er) else er
    er_score = er * 100.0

    reasons = [
        f"风险调整动量：60 日收益 {ret60 * 100:+.1f}%，年化波动 {vol60 * 100:.1f}%，子分 {mom_score:.0f}",
        f"趋势效率 ER20 = {er:.2f}，子分 {er_score:.0f}",
    ]

    rs_score = None
    excess60 = None
    if benchmark_close is not None and len(benchmark_close.dropna()) >= 61:
        bench = benchmark_close.dropna().astype(float)
        bench_ret60 = float(bench.iloc[-1] / bench.iloc[-61] - 1.0)
        excess60 = ret60 - bench_ret60
        rs_score = 50.0 * (1.0 + math.tanh(5.0 * excess60))
        reasons.insert(1, f"相对基准强度：60 日超额 {excess60 * 100:+.1f}%，子分 {rs_score:.0f}")
        weights = {"momentum": 0.55, "rel_strength": 0.35, "efficiency": 0.10}
        score = 0.55 * mom_score + 0.35 * rs_score + 0.10 * er_score
    else:
        weights = {"momentum": 0.90, "rel_strength": 0.0, "efficiency": 0.10}
        score = 0.90 * mom_score + 0.10 * er_score
        reasons.append("无可用基准：相对强度权重并入动量（降级评估）")

    components = {
        "momentum": {"score": round(mom_score, 1), "ret60": ret60, "vol60": vol60},
        "rel_strength": {
            "score": round(rs_score, 1) if rs_score is not None else None,
            "excess60": excess60,
        },
        "efficiency": {"score": round(er_score, 1), "er20": round(er, 3)},
        "weights": weights,
    }
    return float(score), components, reasons


# ---------------------------------------------------------------- 时灯


def _timing_light(
    df: pd.DataFrame,
    close: pd.Series,
    volume: pd.Series | None,
    vol_k: float,
    risk_events: list[dict] | None,
    index: pd.Index,
) -> tuple[dict, dict, list[dict]]:
    """时灯：过热追高/事件风险亮红；回调进行中/RSI 过热/量价背离亮黄。

    动态阈值：MA20 偏离（15%×vol_k）、RSI 过热（78×vol_k）、
    量能背离（0.7×vol_k）均受波动率缩放因子调整。
    """
    last = float(close.iloc[-1])
    ma20 = series_last(close.rolling(20).mean())
    dev20 = last / ma20 - 1.0 if not math.isnan(ma20) and ma20 > 0 else float("nan")
    high60 = float(close.rolling(60).max().iloc[-1])
    dd60 = last / high60 - 1.0 if high60 > 0 else float("nan")
    rsi14 = float(compute_rsi(close, 14).iloc[-1])

    reasons: list[str] = []
    red = False
    yellow = False
    red_reasons: list[str] = []
    yellow_reasons: list[str] = []
    detail: dict = {}

    # 过热追高（红）
    dev_threshold = 0.15 * vol_k
    overheated = not math.isnan(dev20) and dev20 > dev_threshold
    detail["overheated"] = overheated
    if overheated:
        red = True
        reasons.append(
            f"收盘偏离 MA20 达 {dev20 * 100:+.1f}%（>{dev_threshold * 100:.0f}%），过热追高，等回踩（红灯）"
        )
        red_reasons.append(reasons[-1])

    # 回调状态（黄）
    below_ma20 = not math.isnan(ma20) and last < ma20
    detail["below_ma20"] = below_ma20
    if below_ma20:
        yellow = True
        reasons.append(f"收盘 {last:.2f} 低于 MA20 {ma20:.2f}，回调进行中，等企稳")
        yellow_reasons.append(reasons[-1])
    if not math.isnan(dd60) and dd60 <= -0.08:
        yellow = True
        reasons.append(f"距 60 日高点回撤 {dd60 * 100:.1f}%（超 8%），短期结构未修复")
        yellow_reasons.append(reasons[-1])

    # RSI 过热（黄）
    rsi_threshold = 78.0 * vol_k
    rsi_hot = rsi14 > rsi_threshold
    detail["rsi_hot"] = rsi_hot
    if rsi_hot:
        yellow = True
        reasons.append(f"RSI14 = {rsi14:.1f} > {rsi_threshold:.0f}，短期过热")
        yellow_reasons.append(reasons[-1])

    # 量价背离（黄）
    vol_threshold = 0.7 * vol_k
    if volume is not None and len(volume) >= 20:
        high20 = float(close.rolling(20).max().iloc[-1])
        vol_ma20 = float(volume.rolling(20).mean().iloc[-1])
        if last >= high20 - 1e-9 and float(volume.iloc[-1]) < vol_threshold * vol_ma20:
            yellow = True
            reasons.append(f"价创 20 日新高但量能低于 20 日均量 {vol_threshold:.0%}（量价背离）")
            yellow_reasons.append(reasons[-1])

    # 事件风险：近 30 天 high 红灯，medium 黄灯（利好不加分）
    triggered_events = _recent_events(risk_events, index)
    for ev in triggered_events:
        if ev["risk"] == "high":
            red = True
            reasons.append(f"高风险事件 {ev['date']}：{ev['note'] or '（未注明）'}（红灯，等事件落地）")
            red_reasons.append(reasons[-1])
        else:
            yellow = True
            reasons.append(f"中风险事件 {ev['date']}：{ev['note'] or '（未注明）'}（黄灯提示）")
            yellow_reasons.append(reasons[-1])

    color = "red" if red else ("yellow" if yellow else "green")
    if color == "green":
        reasons.append(
            f"收在 MA20 上方、距 60 日高点回撤 {dd60 * 100:.1f}%（<8%）且无过热/事件风险，入场结构有序"
        )
    detail["red_reasons"] = red_reasons
    detail["yellow_reasons"] = yellow_reasons

    snapshot = {"rsi14": rsi14, "dev20": dev20, "dd60": dd60}
    return {"color": color, "reasons": reasons, "detail": detail}, snapshot, triggered_events


def _recent_events(risk_events: list[dict] | None, index: pd.Index) -> list[dict]:
    """筛出近 30 天内的 high/medium 风险事件。"""
    if not risk_events:
        return []
    asof = index[-1] if isinstance(index, pd.DatetimeIndex) else None
    out: list[dict] = []
    for ev in risk_events:
        risk = str(ev.get("risk", "")).strip().lower()
        if risk not in ("high", "medium"):
            continue
        ts = pd.to_datetime(ev.get("date"), errors="coerce")
        if asof is not None and (pd.isna(ts) or ts < asof - pd.Timedelta(days=30) or ts > asof):
            continue
        out.append({"date": str(ev.get("date", ""))[:10], "risk": risk, "note": str(ev.get("note", ""))})
    return out


# ---------------------------------------------------------------- 决策矩阵


def _decide(value: dict, trend: dict, timing: dict, snapshot: dict) -> tuple[str, dict]:
    """三灯 -> 行动结论。矩阵是纪律预设，宁可错过、不可逆势/追高/踩雷。"""
    v, t, m = value["color"], trend["color"], timing["color"]
    label = f"价{COLOR_CN[v]}+势{COLOR_CN[t]}+时{COLOR_CN[m]}"

    if value["detail"].get("hard_flaw"):
        return "avoid", {
            "rule": f"{label} → 回避：价灯硬伤（ST/连续亏损/资不抵债）一票否决，利好不能救",
            "triggers": [],
        }

    if t == "green":
        if m == "green":
            if v == "red":
                return "trend_only", {
                    "rule": f"{label} → 纯趋势仓：趋势与时机俱佳但估值过高，"
                    "只适合短线纪律仓，止损必须严格，不宜重仓长持",
                    "triggers": [],
                }
            return "trend_entry", {
                "rule": f"{label} → 趋势买点：趋势健康且入场结构有序"
                + ("（价维度无数据，仅代表势/时判断）" if v == "gray" else ""),
                "triggers": [],
            }
        return "wait_pullback", {
            "rule": f"{label} → 等回踩：趋势健康但入场时机受限，不追高不抢跑",
            "triggers": _pullback_triggers(timing, snapshot),
        }

    if v == "green":
        return "left_watch", {
            "rule": f"{label} → 左侧观察：估值有吸引力但趋势未认同，进观察名单，不抄底",
            "triggers": _watch_triggers(trend),
        }
    return "avoid", {
        "rule": f"{label} → 回避：趋势走弱且价维度无吸引力，没有参与理由",
        "triggers": _watch_triggers(trend),
    }


def _pullback_triggers(timing: dict, snapshot: dict) -> list[str]:
    """等回踩的再评估触发条件。"""
    triggers: list[str] = []
    ma20 = snapshot.get("ma20")
    ma20_str = f"（{ma20:.2f}）" if isinstance(ma20, float) and not math.isnan(ma20) else ""
    detail = timing.get("detail", {})
    if detail.get("overheated"):
        triggers.append(f"回踩 MA20{ma20_str} 附近企稳后再评估")
    if detail.get("below_ma20"):
        triggers.append(f"收复 MA20{ma20_str} 后再评估")
    if detail.get("rsi_hot"):
        triggers.append("RSI 回落至过热阈值以下")
    if any("风险事件" in r for r in timing.get("reasons", [])):
        triggers.append("风险事件落地后再评估")
    return triggers or [f"回踩 MA20{ma20_str} 附近企稳后再评估"]


def _watch_triggers(trend: dict) -> list[str]:
    """左侧观察/回避的再评估触发条件（趋势修复信号）。"""
    triggers: list[str] = []
    detail = trend.get("detail", {})
    if detail.get("below_ma200"):
        triggers.append("收盘站回 MA200 之上")
    elif detail.get("below_ma60"):
        triggers.append("收盘站回 MA60 之上")
    if detail.get("weekly_broken"):
        triggers.append("周线收盘收复周线 MA30")
    if detail.get("trend_score", 100.0) < TREND_GREEN:
        triggers.append(f"趋势分回升至 {TREND_GREEN:.0f} 以上")
    if detail.get("bench_risk_off"):
        triggers.append("基准收复其 MA200（大盘转多）")
    return triggers


# ---------------------------------------------------------------- 左侧分批计划


def _left_side_plan(value: dict, decision: dict, symbol: str) -> dict | None:
    """左侧观察的分批计划：价深绿 + 无硬伤时引导 DCA 分批，不改「不抄底」纪律。

    左侧不预测底部价格，用时间分批（定投）替代价格网格：越便宜投越多由
    run_dca 的 smart/dip 模式执行；由盈转亏、硬伤或估值修复到中枢即停止加码。
    不满足深绿/无硬伤条件时返回 None（仍只进观察名单）。
    """
    detail = value.get("detail", {})
    val_avg = detail.get("valuation_avg")
    if val_avg is None or val_avg > VAL_DEEP:
        return None
    if detail.get("hard_flaw") or detail.get("profit_to_loss"):
        return None
    sym = symbol or "<代码>"
    dca_cmd = f"run_dca.py --symbol {sym} --mode smart"
    if sym.upper().endswith((".SH", ".SZ", ".BJ")):
        dca_cmd += " --dividends auto"  # A 股可显式建模分红（红利股收益大头）
    return {
        "reason": f"估值分位均值 {val_avg:.0%} ≤ {VAL_DEEP:.0%}（价灯深绿）且基本面无硬伤",
        "approach": "时间分批（DCA）替代一次性抄底：左侧不预测底部，用纪律分批摊低成本",
        "position_cap": "左侧累计仓位建议不超过目标仓位的一半，剩余等趋势修复（右侧触发条件满足）再加",
        "stop_conditions": [
            "出现基本面硬伤（ST/连续亏损/资不抵债）或分红大幅削减：立即停止加码并离场",
            f"估值分位回升至 {VAL_GREEN:.0%} 以上（修复到中枢）：停止加码，改按右侧触发条件评估",
        ],
        "right_side_triggers": decision.get("triggers", []),
        "suggested_command": dca_cmd,
    }


# ---------------------------------------------------------------- 持仓联动


def _position_overlay(
    position: dict,
    close: pd.Series,
    atr14: float,
    verdict: str,
    value_light: dict,
    trend_light: dict,
) -> tuple[str, dict]:
    """持仓联动：只改操作建议，不改灯色。

    势红或价硬伤且有持仓时输出「持仓需减风险」（不等待回本）。
    """
    last = float(close.iloc[-1])
    cost = float(position["cost"])
    shares = position.get("shares")
    pnl_pct = last / cost - 1.0 if cost > 0 else float("nan")
    stop_ref = last - 2.0 * atr14 if not math.isnan(atr14) else None

    if trend_light["color"] == "red" or value_light["detail"].get("hard_flaw"):
        verdict = "reduce_risk"
        advice = "趋势结构已破坏（或基本面硬伤），按纪律应减仓或离场，不等待回本"
    elif verdict in ("trend_entry", "trend_only"):
        advice = "继续持有；回踩 MA20 可按交易计划加仓"
        if verdict == "trend_only":
            advice += "（估值偏高，加仓宜谨慎）"
    else:
        advice = "继续持有观察，不加仓；跌破止损参考位应离场"

    out = {
        "cost": round(cost, 4),
        "shares": float(shares) if shares else None,
        "market_value": round(float(shares) * last, 2) if shares else None,
        "pnl_pct": round(pnl_pct, 4),
        "stop_ref": round(stop_ref, 2) if stop_ref else None,
        "stop_distance_pct": (
            round(last / stop_ref - 1.0, 4) if stop_ref and stop_ref > 0 else None
        ),
        "advice": advice,
        "source": position.get("source", "cli"),
    }
    return verdict, out


# ---------------------------------------------------------------- 辅助


def _weekly_close(close: pd.Series) -> pd.Series:
    """周线收盘：时间索引按自然周重采样，否则按 5 根近似一周。"""
    if isinstance(close.index, pd.DatetimeIndex):
        return close.resample("W").last().dropna()
    grp = np.arange(len(close)) // 5
    return close.groupby(grp).last()


def _vol_regime(close: pd.Series, window: int = 20) -> float:
    """波动率缩放因子：当前 20 日年化波动率 / 历史中位波动率，clamp 到 [0.8, 1.4]。

    用于动态缩放时灯的固定阈值：
    - 高波动（vol_k > 1）→ 放宽阈值，避免正常波动被误杀；
    - 低波动（vol_k < 1）→ 收紧阈值，小偏离更有意义。
    数据不足时返回 1.0（退化为固定阈值）。
    """
    if len(close) < window * 3:
        return 1.0
    ret = close.pct_change()
    roll_vol = ret.rolling(window).std(ddof=0) * math.sqrt(252.0)
    vol_hist = roll_vol.dropna()
    if len(vol_hist) < window:
        return 1.0
    cur_vol = float(vol_hist.iloc[-1])
    median_vol = float(vol_hist.median())
    if median_vol <= 0 or math.isnan(cur_vol):
        return 1.0
    ratio = cur_vol / median_vol
    return max(VOL_K_MIN, min(VOL_K_MAX, ratio))


# ---------------------------------------------------------------- 证据链构建


def _build_evidence(
    lights: dict, trend_score: float, components: dict, snapshot: dict, vol_k: float
) -> list[dict]:
    """从三灯结果与指标快照构建结构化证据链。

    每条证据含：
    - id: 编号（E01, E02, ...），Agent 转述时可引用
    - light: 产生维度（value/trend/timing）
    - indicator: 指标机器名
    - value: 实际值
    - threshold: 对比阈值（无则 None）
    - triggered: 是否触发红/黄
    - impact: 影响（red/yellow/none）
    - claim: 一句话自然语言断言（Agent 可直接引用）
    """
    evidence: list[dict] = []
    seq = 0

    def add(light: str, indicator: str, value, threshold, triggered: bool, impact: str, claim: str):
        nonlocal seq
        seq += 1
        evidence.append({
            "id": f"E{seq:02d}",
            "light": light,
            "indicator": indicator,
            "value": value,
            "threshold": threshold,
            "triggered": triggered,
            "impact": impact,
            "claim": claim,
        })

    # 价灯
    v_detail = lights["value"]["detail"]
    if v_detail.get("hard_flaw"):
        add(
            "value", "fundamental_hard_flaw", True, None, True, "red",
            f"价灯硬伤：{lights['value']['reasons'][0]}",
        )
    val_avg = v_detail.get("valuation_avg")
    if val_avg is not None:
        expensive = val_avg > VAL_RED
        cheap = val_avg <= VAL_GREEN
        add(
            "value", "valuation_percentile", val_avg, VAL_RED, expensive,
            "red" if expensive else "none",
            f"PE/PB 分位均值 {val_avg:.0%}，"
            + ("相对自身历史高估" if expensive else "相对自身历史偏低" if cheap else "处于历史中枢"),
        )

    # 势灯
    add(
        "trend", "trend_score", round(trend_score, 1), TREND_GREEN,
        trend_score < TREND_GREEN,
        "red" if trend_score < TREND_RED else ("yellow" if trend_score < TREND_GREEN else "none"),
        f"趋势分 {trend_score:.1f}（动量 {components.get('momentum', {}).get('score')}/"
        f"相对强度 {components.get('rel_strength', {}).get('score')}/"
        f"趋势效率 {components.get('efficiency', {}).get('score')}），"
        + (f"≥{TREND_GREEN:.0f} 动能达标" if trend_score >= TREND_GREEN else f"< {TREND_GREEN:.0f} 动能不足"),
    )
    last = snapshot.get("close")
    ma200 = snapshot.get("ma200")
    ma60 = snapshot.get("ma60")
    if isinstance(ma200, float) and not math.isnan(ma200) and last is not None:
        below = bool(last < ma200)
        add(
            "trend", "close_vs_ma200", safe_round(last), safe_round(ma200), below,
            "red" if below else "none",
            f"收盘 {last:.2f} {'<' if below else '>'} MA200({ma200:.2f})，"
            + ("长期趋势逆势，势灯红" if below else "长期趋势未破坏"),
        )
    if isinstance(ma60, float) and not math.isnan(ma60) and last is not None:
        below = bool(last < ma60)
        add(
            "trend", "close_vs_ma60", safe_round(last), safe_round(ma60), below,
            "yellow" if below else "none",
            f"收盘 {last:.2f} {'<' if below else '>'} MA60({ma60:.2f})，"
            + ("中期走弱" if below else "中期趋势健康"),
        )

    # 时灯
    dev20 = snapshot.get("dev20")
    if isinstance(dev20, float) and not math.isnan(dev20):
        dev_th = 0.15 * vol_k
        hot = dev20 > dev_th
        add(
            "timing", "ma20_deviation", safe_round(dev20), safe_round(dev_th), hot,
            "red" if hot else "none",
            f"偏离 MA20 达 {dev20 * 100:+.1f}%"
            + (f" > {dev_th * 100:.0f}%，过热追高，等回踩" if hot else "，入场偏离度正常"),
        )
    rsi14 = snapshot.get("rsi14")
    if isinstance(rsi14, float) and not math.isnan(rsi14):
        rsi_th = 78.0 * vol_k
        hot = rsi14 > rsi_th
        add(
            "timing", "rsi14", safe_round(rsi14), safe_round(rsi_th), hot,
            "yellow" if hot else "none",
            f"RSI14={rsi14:.1f}" + (f" > {rsi_th:.0f}，短期过热" if hot else "，未过热"),
        )
    for r in lights["timing"]["reasons"]:
        if "风险事件" in r:
            add(
                "timing", "event_risk", "triggered", None, True,
                "red" if "高风险" in r else "yellow", r,
            )
            break

    return evidence
