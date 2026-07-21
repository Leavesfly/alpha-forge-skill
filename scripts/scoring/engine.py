"""四层纪律评分引擎（分层否决架构）。

回答的不是「这个标的好不好」，而是：按当前价量结构、市场环境和风险约束，
**现在是否适合参与**。四层各司其职、单向降级，利好不能救弱势标的：

0. **基本面否决层**（可选，只否决/降级）：
   ST/*ST、连续 4 季亏损、资不抵债直接否决；由盈转亏封顶「观察」；
1. **ALPHA 加权层**（只产生排名分 0~100）：
   风险调整动量 55% + 相对基准强度 35% + Kaufman 趋势效率 10%；
2. **风险否决层**（只封顶/否决，不加分）：
   收盘 < MA200 直接「否」；MA60 / 周线结构 / 基准 risk-off 封顶「观察」；
3. **技术确认层**（只拦截「是」，不加分）：
   MACD 死叉、RSI 过热、KDJ 死叉、量能背离触发则「是」降「观察」；
   ADX 强趋势时放宽 RSI 阈值并豁免 KDJ 死叉；
4. **入场时机层**：偏离 MA20 过热追高降级，有序回调保持。

动态自适应阈值：确认层/时机层的固定阈值受波动率缩放因子 vol_k 调整
（高波动放宽、低波动收紧），避免牛市过早拦截、熊市过晚反应。

另有两类独立约束（P1）：事件风险只降级不加分；持仓状态只改操作建议
（「持仓需减风险」），不改排名分。

评分阈值为纪律预设值，未经过样本外验证；``scoring.replay`` 提供
历史回放 + 前瞻收益事件研究用于自证，评分不应理解为已验证的选股 alpha。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .indicators import (
    adx,
    annualized_vol,
    atr,
    compute_kdj,
    compute_rsi,
    efficiency_ratio,
    macd,
)
from .plan import build_trade_plan
from utils import resolve_time_index, safe_round, series_last

#: 结论五态（机器码 -> 中文展示）
VERDICT_CN = {
    "yes": "是",
    "watch": "观察",
    "no": "否",
    "reduce_risk": "持仓需减风险",
    "unrated": "无法评分",
}
VERDICTS = tuple(VERDICT_CN)

#: 有效评分所需最少 K 线数（MA200 + 动量窗口需要足够历史）
MIN_BARS = 250

#: ALPHA 排名分 -> 初始结论的阈值
ALPHA_YES = 60.0
ALPHA_WATCH = 45.0

#: 波动率缩放因子边界（动态阈值）
VOL_K_MIN = 0.8
VOL_K_MAX = 1.4

#: ADX 趋势强度阈值
ADX_STRONG = 30.0
ADX_MODERATE = 25.0

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
    """单标的纪律评分结果。"""

    symbol: str
    verdict: str  # yes / watch / no / reduce_risk / unrated
    alpha_score: float | None
    components: dict  # 三项子分与权重
    layers: list[dict]  # 各层 {name, status, reasons}
    snapshot: dict  # 关键指标快照
    plan: dict | None  # 交易计划（仅 是/观察）
    benchmark: str | None
    asof: str  # 最近一根 K 线日期
    n_bars: int
    position: dict | None = None  # 持仓联动（P1）
    risk_events: list[dict] = field(default_factory=list)  # 触发的风险事件（P1）

    @property
    def verdict_cn(self) -> str:
        return VERDICT_CN[self.verdict]

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "verdict": self.verdict,
            "verdict_cn": self.verdict_cn,
            "alpha_score": self.alpha_score,
            "components": self.components,
            "layers": self.layers,
            "snapshot": self.snapshot,
            "plan": self.plan,
            "benchmark": self.benchmark,
            "asof": self.asof,
            "n_bars": self.n_bars,
            "position": self.position,
            "risk_events": self.risk_events,
        }


def score_symbol(
    df: pd.DataFrame,
    symbol: str = "",
    benchmark_close: pd.Series | None = None,
    benchmark_symbol: str | None = None,
    risk_events: list[dict] | None = None,
    position: dict | None = None,
    fundamentals: dict | None = None,
) -> ScoreResult:
    """对单标的执行四层纪律评分。

    Args:
        df: 含 ``close``（可选 high/low/volume）的 OHLCV DataFrame（时间升序）。
        symbol: 标的代码（用于展示）。
        benchmark_close: 基准收盘价序列（None 时相对强度权重并入动量并标注降级）。
        benchmark_symbol: 基准代码（用于展示）。
        risk_events: 事件风险列表 ``[{date, risk, note}]``，high 只降级不加分。
        position: 持仓信息 ``{cost, shares, source}``，只改操作建议不改排名分。
        fundamentals: 基本面数据 ``{eps_recent, is_st, net_asset_per_share, source}``，
            None 时跳过基本面否决层（向后兼容）。

    Returns:
        ScoreResult。评分仅使用截至最近一根已完成 K 线的数据，无前视。
    """
    close = df["close"].astype(float).reset_index(drop=True)
    index = resolve_time_index(df)
    close.index = index
    asof = str(index[-1])[:10] if len(index) else ""
    n = int(close.notna().sum())

    if n < MIN_BARS:
        return ScoreResult(
            symbol=symbol,
            verdict="unrated",
            alpha_score=None,
            components={},
            layers=[
                {
                    "name": "data",
                    "status": "veto",
                    "reasons": [
                        f"有效 K 线仅 {n} 根，低于评分所需 {MIN_BARS} 根"
                        "（MA200/动量窗口无法形成），不用猜测补齐"
                    ],
                }
            ],
            snapshot={"close": series_last(close), "n_bars": n},
            plan=None,
            benchmark=benchmark_symbol,
            asof=asof,
            n_bars=n,
        )

    volume = df["volume"].astype(float).reset_index(drop=True) if "volume" in df.columns else None
    if volume is not None:
        volume.index = index

    # ---------- 动态阈值：波动率缩放因子 ----------
    vol_k = _vol_regime(close)

    # ---------- 基本面否决层（可选，在 ALPHA 之前） ----------
    layers: list[dict] = []
    fundamental_veto = False
    fundamental_cap = False
    if fundamentals is not None:
        f_verdict, f_layer = _fundamental_veto(fundamentals)
        layers.append(f_layer)
        if f_layer["status"] == "veto":
            fundamental_veto = True
        elif f_layer["status"] == "cap":
            fundamental_cap = True

    # ---------- 第 1 层：ALPHA 加权（只产生排名分） ----------
    alpha_score, components, alpha_reasons = _alpha_layer(close, benchmark_close)
    if fundamental_veto:
        verdict = "no"
    elif alpha_score >= ALPHA_YES:
        verdict = "yes"
    elif alpha_score >= ALPHA_WATCH:
        verdict = "watch"
    else:
        verdict = "no"
        alpha_reasons.append(f"排名分 {alpha_score:.1f} 低于 {ALPHA_WATCH:.0f}，动能不足不值得参与")
    # 基本面封顶：由盈转亏等情形将「是」降为「观察」
    if fundamental_cap and verdict == "yes":
        verdict = "watch"
    layers.append({"name": "alpha", "status": "pass", "score": round(alpha_score, 1), "reasons": alpha_reasons})

    # ---------- 第 2 层：风险否决（只封顶/否决） ----------
    verdict, veto_layer, snapshot = _veto_layer(close, benchmark_close, verdict)
    layers.append(veto_layer)

    # ---------- 第 3 层：技术确认（只拦截「是」） ----------
    adx_value = _compute_adx(df)
    verdict, confirm_layer, snapshot2 = _confirm_layer(df, close, volume, verdict, vol_k, adx_value)
    layers.append(confirm_layer)
    snapshot.update(snapshot2)

    # ---------- 第 4 层：入场时机 ----------
    verdict, timing_layer, snapshot3 = _timing_layer(close, verdict, vol_k)
    layers.append(timing_layer)
    snapshot.update(snapshot3)

    # ---------- 独立约束：事件风险（只降级不加分） ----------
    triggered_events: list[dict] = []
    if risk_events is not None:
        verdict, event_layer, triggered_events = _event_risk_layer(risk_events, index, verdict)
        layers.append(event_layer)

    # ---------- 交易计划（仅 是/观察） ----------
    atr14 = series_last(atr(df.reset_index(drop=True), 14))
    snapshot["atr14"] = atr14
    plan = None
    if verdict in ("yes", "watch"):
        plan = build_trade_plan(series_last(close), snapshot.get("ma20"), atr14)

    # ---------- 独立约束：持仓联动（只改操作建议） ----------
    position_out = None
    if position is not None and position.get("cost"):
        verdict, position_out = _position_overlay(position, close, atr14, verdict, layers)

    snapshot["close"] = series_last(close)
    snapshot["vol_k"] = round(vol_k, 3)
    snapshot["adx14"] = adx_value
    return ScoreResult(
        symbol=symbol,
        verdict=verdict,
        alpha_score=round(alpha_score, 1),
        components=components,
        layers=layers,
        snapshot={k: safe_round(v) for k, v in snapshot.items()},
        plan=plan,
        benchmark=benchmark_symbol,
        asof=asof,
        n_bars=n,
        position=position_out,
        risk_events=triggered_events,
    )


# ---------------------------------------------------------------- 各层实现


def _alpha_layer(
    close: pd.Series, benchmark_close: pd.Series | None
) -> tuple[float, dict, list[str]]:
    """ALPHA 加权层：动量 55 / 相对强度 35 / 趋势效率 10。

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
        reasons.append("无可用基准：相对强度权重并入动量（降级评分）")

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


def _veto_layer(
    close: pd.Series, benchmark_close: pd.Series | None, verdict: str
) -> tuple[str, dict, dict]:
    """风险否决层：MA200 否决、MA60/周线/基准 risk-off 封顶「观察」。"""
    last = float(close.iloc[-1])
    ma20 = series_last(close.rolling(20).mean())
    ma60 = series_last(close.rolling(60).mean())
    ma200 = series_last(close.rolling(200).mean())
    reasons: list[str] = []
    status = "pass"

    if not math.isnan(ma200) and last < ma200:
        verdict = "no"
        status = "veto"
        reasons.append(f"收盘 {last:.2f} 低于 MA200 {ma200:.2f}，长期趋势逆势，直接否决")
    elif not math.isnan(ma60) and last < ma60:
        if verdict == "yes":
            verdict = "watch"
            status = "cap"
        reasons.append(f"收盘 {last:.2f} 低于 MA60 {ma60:.2f}，中期趋势走弱，结论封顶「观察」")

    weekly = _weekly_close(close)
    if len(weekly.dropna()) >= 30:
        wma30 = float(weekly.rolling(30).mean().iloc[-1])
        wlast = float(weekly.iloc[-1])
        if wlast < wma30:
            if verdict == "yes":
                verdict = "watch"
                status = "cap" if status == "pass" else status
            reasons.append(f"周线收盘 {wlast:.2f} 低于周线 MA30 {wma30:.2f}，周线结构走坏，封顶「观察」")
    else:
        reasons.append("周线样本不足 30 根，跳过周线结构检查")

    if benchmark_close is not None:
        bench = benchmark_close.dropna().astype(float)
        if len(bench) >= 200:
            bma200 = float(bench.rolling(200).mean().iloc[-1])
            blast = float(bench.iloc[-1])
            if blast < bma200:
                if verdict == "yes":
                    verdict = "watch"
                    status = "cap" if status == "pass" else status
                reasons.append("基准收盘低于其 MA200（大盘 risk-off），封顶「观察」")
        else:
            reasons.append("基准样本不足 200 根，跳过大盘环境检查")

    if not reasons:
        reasons.append("价格站上 MA60/MA200，周线与大盘环境未触发否决")
    layer = {"name": "veto", "status": status, "reasons": reasons}
    snapshot = {"ma20": ma20, "ma60": ma60, "ma200": ma200}
    return verdict, layer, snapshot


def _confirm_layer(
    df: pd.DataFrame,
    close: pd.Series,
    volume: pd.Series | None,
    verdict: str,
    vol_k: float = 1.0,
    adx_value: float | None = None,
) -> tuple[str, dict, dict]:
    """技术确认层：只拦截「是」，任一触发则降级「观察」，不给排名加分。

    动态阈值：RSI 过热阈值受 vol_k 缩放；ADX 强趋势时额外放宽 RSI 并豁免 KDJ。
    """
    reasons: list[str] = []
    dif, dea = macd(close)
    dif_v, dea_v = float(dif.iloc[-1]), float(dea.iloc[-1])
    if dif_v < dea_v:
        reasons.append(f"MACD DIF {dif_v:.3f} < DEA {dea_v:.3f}（死叉状态）")

    # RSI 过热：基础 78 × vol_k，ADX 强趋势额外 +5，中等 +3
    rsi_base = 78.0 * vol_k
    adx_bonus = 0.0
    if adx_value is not None and adx_value >= ADX_STRONG:
        adx_bonus = 5.0
    elif adx_value is not None and adx_value >= ADX_MODERATE:
        adx_bonus = 3.0
    rsi_threshold = rsi_base + adx_bonus
    rsi14 = float(compute_rsi(close, 14).iloc[-1])
    if rsi14 > rsi_threshold:
        reasons.append(f"RSI14 = {rsi14:.1f} > {rsi_threshold:.0f}，短期过热")

    high = df["high"].astype(float).reset_index(drop=True) if "high" in df.columns else close.reset_index(drop=True)
    low = df["low"].astype(float).reset_index(drop=True) if "low" in df.columns else close.reset_index(drop=True)
    k, d, _ = compute_kdj(high, low, close.reset_index(drop=True), 9, 3, 3)
    k_v, d_v = float(k.iloc[-1]), float(d.iloc[-1])
    # KDJ 死叉：ADX 强趋势时仅记录不拦截
    kdj_dead = k_v < d_v
    if kdj_dead:
        if adx_value is not None and adx_value >= ADX_STRONG:
            reasons.append(f"KDJ K {k_v:.1f} < D {d_v:.1f}（死叉，但 ADX={adx_value:.0f} 强趋势豁免拦截）")
        else:
            reasons.append(f"KDJ K {k_v:.1f} < D {d_v:.1f}（死叉状态）")

    # 量价背离：量能阈值受 vol_k 缩放
    vol_threshold = 0.7 * vol_k
    if volume is not None and len(volume) >= 20:
        high20 = float(close.rolling(20).max().iloc[-1])
        vol_ma20 = float(volume.rolling(20).mean().iloc[-1])
        if float(close.iloc[-1]) >= high20 - 1e-9 and float(volume.iloc[-1]) < vol_threshold * vol_ma20:
            reasons.append(f"价创 20 日新高但量能低于 20 日均量 {vol_threshold:.0%}（量价背离）")

    # ADX 状态标注（仅当有实际放宽行为时添加）
    if adx_value is not None and adx_bonus > 0 and (rsi14 > rsi_base or kdj_dead):
        strength = "强趋势" if adx_value >= ADX_STRONG else "中等趋势"
        reasons.append(f"ADX={adx_value:.0f}（{strength}），RSI 拦截阈值放宽至 {rsi_threshold:.0f}")

    # 判断是否降级：KDJ 死叉在强趋势时不参与降级决策，ADX 标注也不参与
    blocking_reasons = [r for r in reasons if "豁免" not in r and "ADX=" not in r]
    status = "pass"
    if blocking_reasons and verdict == "yes":
        verdict = "watch"
        status = "downgrade"
    if not reasons:
        reasons.append("MACD/RSI/KDJ/量价确认均未触发拦截")
    layer = {"name": "confirm", "status": status, "reasons": reasons}
    snapshot = {"rsi14": rsi14, "macd_dif": dif_v, "macd_dea": dea_v, "kdj_k": k_v, "kdj_d": d_v}
    return verdict, layer, snapshot


def _timing_layer(close: pd.Series, verdict: str, vol_k: float = 1.0) -> tuple[str, dict, dict]:
    """入场时机层：偏离 MA20 过热追高降级；有序回调保持并注明。

    动态阈值：MA20 偏离阈值受 vol_k 缩放（高波动放宽，低波动收紧）。
    """
    last = float(close.iloc[-1])
    ma20 = series_last(close.rolling(20).mean())
    dev20 = last / ma20 - 1.0 if not math.isnan(ma20) and ma20 > 0 else float("nan")
    high60 = float(close.rolling(60).max().iloc[-1])
    dd60 = last / high60 - 1.0 if high60 > 0 else float("nan")

    # 动态偏离阈值：基础 15% × vol_k
    dev_threshold = 0.15 * vol_k
    reasons: list[str] = []
    status = "pass"
    if not math.isnan(dev20) and dev20 > dev_threshold:
        if verdict == "yes":
            verdict = "watch"
            status = "downgrade"
        reasons.append(f"收盘偏离 MA20 达 {dev20 * 100:+.1f}%（>{dev_threshold * 100:.0f}%），过热追高，等回踩")
    elif not math.isnan(dd60) and dd60 > -0.08 and not math.isnan(ma20) and last > ma20:
        reasons.append(f"距 60 日高点回撤 {dd60 * 100:.1f}%（<8%）且收在 MA20 上方，趋势结构有序")
    else:
        reasons.append(f"偏离 MA20 {dev20 * 100:+.1f}%，距 60 日高点回撤 {dd60 * 100:.1f}%")
    layer = {"name": "timing", "status": status, "reasons": reasons}
    return verdict, layer, {"dev20": dev20, "dd60": dd60}


def _event_risk_layer(
    risk_events: list[dict], index: pd.Index, verdict: str
) -> tuple[str, dict, list[dict]]:
    """事件风险层：近 30 天存在 high 风险事件时「是」降「观察」；利好不加分。"""
    triggered: list[dict] = []
    noted: list[dict] = []
    asof = index[-1] if isinstance(index, pd.DatetimeIndex) else None
    for ev in risk_events:
        risk = str(ev.get("risk", "")).strip().lower()
        if risk not in ("high", "medium"):
            continue
        ts = pd.to_datetime(ev.get("date"), errors="coerce")
        if asof is not None and (pd.isna(ts) or ts < asof - pd.Timedelta(days=30) or ts > asof):
            continue
        item = {"date": str(ev.get("date", ""))[:10], "risk": risk, "note": str(ev.get("note", ""))}
        (triggered if risk == "high" else noted).append(item)

    reasons: list[str] = []
    status = "pass"
    if triggered:
        if verdict == "yes":
            verdict = "watch"
            status = "downgrade"
        for ev in triggered:
            reasons.append(f"高风险事件 {ev['date']}：{ev['note'] or '（未注明）'} → 结论降级")
    for ev in noted:
        reasons.append(f"中风险事件 {ev['date']}：{ev['note'] or '（未注明）'}（仅提示，不降级）")
    if not reasons:
        reasons.append("近 30 天无 high/medium 风险事件")
    layer = {"name": "event_risk", "status": status, "reasons": reasons}
    return verdict, layer, triggered + noted


def _position_overlay(
    position: dict, close: pd.Series, atr14: float, verdict: str, layers: list[dict]
) -> tuple[str, dict]:
    """持仓联动：只改操作建议，不改排名分。

    结论为「否」（含否决层触发）且有持仓时输出「持仓需减风险」。
    """
    last = float(close.iloc[-1])
    cost = float(position["cost"])
    shares = position.get("shares")
    pnl_pct = last / cost - 1.0 if cost > 0 else float("nan")
    stop_ref = last - 2.0 * atr14 if not math.isnan(atr14) else None
    veto_hit = any(l["name"] == "veto" and l["status"] == "veto" for l in layers)

    if verdict == "no" or veto_hit:
        verdict = "reduce_risk"
        advice = "趋势结构已破坏，按纪律应减仓或离场，不等待回本"
    elif verdict == "yes":
        advice = "继续持有；回踩 MA20 可按交易计划加仓"
    else:
        advice = "继续持有观察；跌破止损参考位应离场"

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


def _weekly_close(close: pd.Series) -> pd.Series:
    """周线收盘：时间索引按自然周重采样，否则按 5 根近似一周。"""
    if isinstance(close.index, pd.DatetimeIndex):
        return close.resample("W").last().dropna()
    grp = np.arange(len(close)) // 5
    return close.groupby(grp).last()


def _vol_regime(close: pd.Series, window: int = 20) -> float:
    """波动率缩放因子：当前 20 日年化波动率 / 历史中位波动率，clamp 到 [0.8, 1.4]。

    用于动态缩放确认层/时机层的固定阈值：
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


def _compute_adx(df: pd.DataFrame) -> float | None:
    """计算最新 ADX(14) 值；数据不足或异常时返回 None。"""
    try:
        adx_series = adx(df.reset_index(drop=True), 14)
        val = float(adx_series.iloc[-1])
        if math.isnan(val):
            return None
        return round(val, 1)
    except Exception:
        return None


def _fundamental_veto(fundamentals: dict) -> tuple[str, dict]:
    """基本面否决层：只否决/降级，不加分。

    规则（优先级从高到低）：
    - ST/*ST → 直接否决
    - 每股净资产 < 0 → 直接否决（资不抵债）
    - 最近 4 季 EPS 均 < 0 → 直接否决（连续亏损）
    - 最近 2 季 EPS < 0 且第 3 季 > 0（由盈转亏）→ 封顶「观察」

    Returns:
        (verdict_hint, layer_dict)。verdict_hint 为 "no"/"watch"/"pass"，
        调用方根据 layer status 决定是否采纳。
    """
    reasons: list[str] = []
    status = "pass"
    verdict_hint = "pass"
    source = fundamentals.get("source", "unknown")

    # ST 检查
    if fundamentals.get("is_st"):
        reasons.append("ST/*ST 标的，退市风险，直接否决")
        status = "veto"
        verdict_hint = "no"

    # 每股净资产
    naps = fundamentals.get("net_asset_per_share")
    if naps is not None and isinstance(naps, (int, float)) and naps < 0:
        reasons.append(f"每股净资产 {naps:.2f} < 0，资不抵债，直接否决")
        status = "veto"
        verdict_hint = "no"

    # EPS 连续亏损
    eps_recent = fundamentals.get("eps_recent")
    if eps_recent and len(eps_recent) >= 4 and status != "veto":
        eps_vals = [float(e) for e in eps_recent[-4:] if e is not None]
        if len(eps_vals) >= 4:
            if all(e < 0 for e in eps_vals):
                reasons.append(
                    f"最近 4 季 EPS 均为负（{', '.join(f'{e:.3f}' for e in eps_vals)}），连续亏损，直接否决"
                )
                status = "veto"
                verdict_hint = "no"
            elif len(eps_vals) >= 3 and eps_vals[-1] < 0 and eps_vals[-2] < 0 and eps_vals[-3] > 0:
                reasons.append(
                    f"由盈转亏：第 3 季 EPS {eps_vals[-3]:.3f} > 0，"
                    f"近 2 季 {eps_vals[-2]:.3f}/{eps_vals[-1]:.3f} < 0，封顶「观察」"
                )
                if status == "pass":
                    status = "cap"
                verdict_hint = "watch"

    if not reasons:
        reasons.append(f"基本面未见硬伤（数据源：{source}）")

    layer = {"name": "fundamental", "status": status, "reasons": reasons}
    return verdict_hint, layer

