"""阶段定位引擎：把个股定位到「台阶式循环」的哪一段。

回答的不是「能不能买」（那是 ``scoring`` 三灯的职责），而是**现在走到哪了**：

    低位平台 → 突破 → 上升推进 → 高位平台 → 跌破 → 下降趋势 → 重新筑底

七态定义（机器码 -> 中文）见 :data:`STAGE_CN`。判定只用**日线价量**，
不碰基本面/估值，因此便宜、可批量、可历史回放。

与既有能力的分工：

- ``research.regime``：给的是统计属性（趋势/震荡/高波动），**没有位置概念**——
  低位平台与高位平台都会落进 ``range``，但二者一个是买入点前夜、一个是卖出
  前夜。本模块用 :func:`_price_position`（近 250 日 min-max 分位）补上这一维；
- ``scoring``：三灯 + 决策矩阵回答「能不能买」，需基本面/估值/基准；
- ``examples/wisdom_rule.toml``：把同一套突破逻辑做成可回测的机械策略，
  回答「这么做历史上赚不赚钱」。

**阶段判定是描述性统计而非预测**：与 regime 一样存在滞后，状态只能事后确认。
阈值均为纪律预设值，未经样本外验证。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from scoring.indicators import efficiency_ratio  # 公式单一来源，避免重复实现
from utils import resolve_time_index, safe_round

from .box import Box, crossed_above, crossed_below, find_box

#: 七态（机器码 -> 中文展示）
#: 注：``base`` 是一个伞形态，涵盖低位箱体/中位箱体/结构不清三种子情况，
#: 故中文名取「筑底整理」而不断言「低位」——具体是否处于低位看
#: ``rule`` 与 ``price_position``（避免位置分位 50% 却被说成「低位」）。
STAGE_CN = {
    "base": "筑底整理",
    "breakout": "突破确认",
    "advance": "上升推进",
    "top": "高位派发",
    "breakdown": "破位下行",
    "decline": "下降趋势",
    "unknown": "无法判定",
}
STAGES = tuple(STAGE_CN)

#: 有效判定所需最少 K 线（MA200 + 250 日位置分位）
MIN_BARS = 250

#: 位置分位回看窗口（交易日）
POSITION_WINDOW = 250

#: 位置分位阈值：<=低位 / >=高位
POSITION_LOW = 0.4
POSITION_HIGH = 0.6

#: 高位派发需要的「前置升幅」：箱体中轴相对箱体前 PRIOR_WINDOW 根收盘的涨幅
PRIOR_WINDOW = 120
PRIOR_GAIN = 0.30

#: 突破放量确认倍数（突破当根量 / 20 日均量）
VOL_CONFIRM = 1.5

#: 趋势成立的效率比门槛（与 research.regime 的 ER_TREND_THRESHOLD 对齐）
ER_TREND = 0.25

#: 趋势窗口（效率比与均线斜率）
TREND_WINDOW = 60

#: 默认箱体窗口与突破检验窗口
DEFAULT_WINDOW = 60
DEFAULT_CONFIRM_DAYS = 5

#: 各阶段的应对姿态（只给通用原则，买卖裁决交回三灯）
_STAGE_POSTURE = {
    "base": (
        "观察等待",
        "平台未破不预判方向；记下上沿作为突破触发价，跌破下沿则平台失败。",
    ),
    "breakout": (
        "关键点已出现",
        "顺势方向确立，但突破需时间确认；是否买、买多少交给三灯与交易计划。",
    ),
    "advance": (
        "持有 / 顺势加码",
        "趋势未破坏就不猜顶；金字塔加仓须建立在浮盈之上，跟踪 MA20 作离场参考。",
    ),
    "top": (
        "警戒 / 不加仓",
        "高位横盘是派发与再突破的岔路口，跌破下沿即视为趋势破坏，不等回本。",
    ),
    "breakdown": (
        "回避 / 减风险",
        "平台已破，先离场后判断；反弹不代表趋势修复，需重新构筑平台。",
    ),
    "decline": (
        "回避 / 不抄底",
        "下降趋势中每次反弹都可能是下一段的起点；等新平台成立再谈买点。",
    ),
    "unknown": (
        "数据不足",
        f"有效判定需 {MIN_BARS} 根以上日线，加大 --count 后重试。",
    ),
}


@dataclass
class StageResult:
    """单标的阶段定位结果。"""

    symbol: str
    stage: str  # base / breakout / advance / top / breakdown / decline / unknown
    confidence: str  # high / medium / low
    price_position: float | None  # 近 250 日 min-max 区间位置（0~1）
    box: dict  # 驱动判定的箱体（突破/破位用「排除尾部」的箱体，其余用当前箱体）
    trigger: dict  # 关键价位：突破价/破位价及当前距离
    structure: dict  # 均线/效率比/量比等结构指标
    evidence: list[dict] = field(default_factory=list)  # 结构化证据链（Agent 可引用）
    posture: dict = field(default_factory=dict)  # {posture, note}
    asof: str = ""  # 最近一根 K 线日期
    n_bars: int = 0
    rule: str = ""  # 命中的判定规则（可解释性）

    @property
    def stage_cn(self) -> str:
        return STAGE_CN[self.stage]

    def to_dict(self) -> dict:
        """JSON 友好的字典（供 CLI --json 直接展开）。"""
        return {
            "symbol": self.symbol,
            "stage": self.stage,
            "stage_cn": self.stage_cn,
            "confidence": self.confidence,
            "price_position": safe_round(self.price_position),
            "box": self.box,
            "trigger": self.trigger,
            "structure": self.structure,
            "evidence": self.evidence,
            "posture": self.posture,
            "rule": self.rule,
            "asof": self.asof,
            "n_bars": self.n_bars,
        }


def detect_stage(
    df: pd.DataFrame,
    symbol: str = "",
    window: int = DEFAULT_WINDOW,
    confirm_days: int = DEFAULT_CONFIRM_DAYS,
) -> StageResult:
    """识别当前所处阶段。

    判定优先级（箱体类先于趋势类；箱体成立已隐含低效率比，二者天然互斥）::

        1. K 线不足 MIN_BARS                          -> unknown
        2. 近 confirm_days 内上穿「前置箱体」上沿      -> breakout
        3. 近 confirm_days 内下破「前置箱体」下沿      -> breakdown
        4. 当前箱体成立 + 位置>=高位 + 前置升幅>=30%   -> top
        5. 当前箱体成立                                -> base（低位 high / 中位 medium）
        6. 箱体不成立 + 首次跌破下行的 MA60             -> breakdown（兜底）
        7. MA 多头排列 + 站上 MA60 + ER>=0.25          -> advance
        8. MA 空头排列 + 位于 MA60 下方 + ER>=0.25     -> decline
        9. 其余（结构不清）                            -> base + confidence=low

    突破与破位同时出现时，取**更近**的那次（后发生的事件覆盖先前的）。

    Args:
        df: 含 ``close``（建议含 ``high``/``low``/``volume``）的 OHLCV，时间升序。
        symbol: 标的代码（仅用于回填结果）。
        window: 箱体窗口，默认 60。
        confirm_days: 突破/破位检验窗口，默认 5。

    Returns:
        :class:`StageResult`。
    """
    df = df.reset_index(drop=True)
    n = len(df)
    asof = _asof(df)
    if n < MIN_BARS:
        return _unknown(symbol, n, asof)

    close = df["close"].astype(float)
    structure = _structure(df, close, window)
    position = _price_position(df, close)

    # 箱体：当前箱体给价位，前置箱体（排除待检验尾部）判突破/破位
    box_recent = find_box(df, window=window, exclude_tail=0)
    box_prior = find_box(df, window=window, exclude_tail=confirm_days)

    up = crossed_above(df, box_prior, confirm_days) if box_prior.valid else None
    dn = crossed_below(df, box_prior, confirm_days) if box_prior.valid else None
    # 同时出现取更近的一次（bars_ago 越小越近）
    if up is not None and dn is not None:
        if dn <= up:
            up = None
        else:
            dn = None

    prior_gain = _prior_gain(close, box_recent, window)
    ev: list[dict] = []

    if up is not None:
        vol_ratio = _vol_ratio_at(df, up)
        structure["breakout_vol_ratio"] = safe_round(vol_ratio)
        confirmed = vol_ratio is not None and vol_ratio >= VOL_CONFIRM
        ev.append(_ev("box", f"箱体上沿 {box_prior.high:.2f}（{window} 日，排除近 {confirm_days} 根）", box_prior.high))
        ev.append(_ev("break", f"{up} 根 K 线前收盘有效上穿上沿（缓冲 0.5%）", up))
        ev.append(_ev(
            "volume",
            f"突破当根量能 {vol_ratio:.2f}× 20 日均量，{'放量确认' if confirmed else '未达 1.5× 放量门槛，可信度打折'}"
            if vol_ratio is not None else "无量能数据，无法确认放量",
            safe_round(vol_ratio),
        ))
        return _build(
            symbol, "breakout", "high" if confirmed else "medium",
            position, box_prior, structure, ev, n, asof,
            rule="近 N 日上穿前置箱体上沿",
        )

    if dn is not None:
        ev.append(_ev("box", f"箱体下沿 {box_prior.low:.2f}（{window} 日，排除近 {confirm_days} 根）", box_prior.low))
        ev.append(_ev("break", f"{dn} 根 K 线前收盘有效下破下沿（缓冲 0.5%）", dn))
        return _build(
            symbol, "breakdown", "high", position, box_prior, structure, ev, n, asof,
            rule="近 N 日下破前置箱体下沿",
        )

    if box_recent.valid:
        ev.append(_ev(
            "box",
            f"箱体成立：{box_recent.low:.2f} ~ {box_recent.high:.2f}"
            f"（高度 {box_recent.height_pct:.1%}，上沿触及 {box_recent.touches_high} 次 / 下沿 {box_recent.touches_low} 次）",
            box_recent.height_pct,
        ))
        ev.append(_ev("position", f"位置分位 {position:.0%}（近 {POSITION_WINDOW} 日区间）", safe_round(position)))
        if position >= POSITION_HIGH and prior_gain is not None and prior_gain >= PRIOR_GAIN:
            ev.append(_ev("prior_gain", f"平台前 {PRIOR_WINDOW} 日累计升幅 {prior_gain:+.1%}（≥{PRIOR_GAIN:.0%}，具备派发前提）", safe_round(prior_gain)))
            return _build(
                symbol, "top", "high", position, box_recent, structure, ev, n, asof,
                rule="高位箱体 + 前置升幅达标",
            )
        if prior_gain is not None:
            ev.append(_ev("prior_gain", f"平台前 {PRIOR_WINDOW} 日累计升幅 {prior_gain:+.1%}", safe_round(prior_gain)))
        low_pos = position <= POSITION_LOW
        return _build(
            symbol, "base", "high" if low_pos else "medium",
            position, box_recent, structure, ev, n, asof,
            rule="低位箱体" if low_pos else "中位箱体（位置未达低位）",
        )

    # 箱体不成立时的破位兜底：首次跌破下行的 MA60
    # （必须排在箱体态之后：否则低位平台上 MA60 微幅下行 + 价格下穿均线
    #   会被误判为破位，而那只是平台内的正常震荡）
    if _broke_ma60(close, structure, confirm_days):
        ev.append(_ev("box", f"箱体不成立（{box_recent.reason or '无'}）", None))
        ev.append(_ev("trend", f"近 {confirm_days} 日首次跌破下行的 MA60", structure["ma60"]))
        ev.append(_ev("trend", f"MA60 斜率 {structure['ma60_slope']:+.2%}（{structure['slope_window']} 日）", structure["ma60_slope"]))
        return _build(
            symbol, "breakdown", "medium", position, box_recent, structure, ev, n, asof,
            rule="首次跌破下行 MA60（箱体不成立时兜底）",
        )

    er = structure["er"]
    if structure["ma_bull"] and structure["above_ma60"] and er is not None and er >= ER_TREND:
        ev.append(_ev("trend", "均线多头排列 MA20>MA60>MA200 且站上 MA60", structure["ma60"]))
        ev.append(_ev("trend", f"效率比 {er:.2f}（≥{ER_TREND}，单边推进）", safe_round(er)))
        ev.append(_ev("position", f"位置分位 {position:.0%}（近 {POSITION_WINDOW} 日区间）", safe_round(position)))
        return _build(
            symbol, "advance", "high" if er >= ER_TREND + 0.1 else "medium",
            position, box_recent, structure, ev, n, asof,
            rule="均线多头排列 + 高效率比",
        )

    if structure["ma_bear"] and not structure["above_ma60"] and er is not None and er >= ER_TREND:
        ev.append(_ev("trend", "均线空头排列 MA20<MA60 且位于 MA60 下方", structure["ma60"]))
        ev.append(_ev("trend", f"效率比 {er:.2f}（≥{ER_TREND}，单边下行）", safe_round(er)))
        ev.append(_ev("position", f"位置分位 {position:.0%}（近 {POSITION_WINDOW} 日区间）", safe_round(position)))
        return _build(
            symbol, "decline", "high" if er >= ER_TREND + 0.1 else "medium",
            position, box_recent, structure, ev, n, asof,
            rule="均线空头排列 + 高效率比",
        )

    # 兜底：既无成立箱体也无明确趋势，诚实标注结构不清而非编造状态
    ev.append(_ev("box", f"箱体不成立（{box_recent.reason or '无'}）", None))
    ev.append(_ev("trend", f"效率比 {er:.2f} 未达趋势门槛 {ER_TREND}" if er is not None else "效率比不可用", safe_round(er)))
    ev.append(_ev("position", f"位置分位 {position:.0%}（近 {POSITION_WINDOW} 日区间）", safe_round(position)))
    return _build(
        symbol, "base", "low", position, box_recent, structure, ev, n, asof,
        rule="结构不清（无成立箱体且无明确趋势）",
    )


def stage_history(
    df: pd.DataFrame,
    days: int = 120,
    window: int = DEFAULT_WINDOW,
    confirm_days: int = DEFAULT_CONFIRM_DAYS,
) -> dict:
    """逐日重算阶段，输出阶段序列与迁移点（无前视）。

    每一天只用截至当日的数据（``df.iloc[:i+1]``）重新判定，因此序列可直接
    用于观察「一个完整循环」如何演进，也不会把未来信息泄漏到过去。

    Args:
        df: OHLCV（升序）。
        days: 回看天数。
        window: 箱体窗口。
        confirm_days: 突破检验窗口。

    Returns:
        dict，含 ``days``/``series``（date+stage）/``transitions``（迁移点）/
        ``current``（最新阶段）；K 线不足时 ``series`` 为空列表。
    """
    n = len(df)
    idx = resolve_time_index(df)
    start = max(MIN_BARS, n - days)
    series: list[dict] = []
    transitions: list[dict] = []
    prev: str | None = None
    for i in range(start, n):
        res = detect_stage(df.iloc[: i + 1], window=window, confirm_days=confirm_days)
        date = _fmt_date(idx[i])
        series.append({"date": date, "stage": res.stage, "stage_cn": res.stage_cn})
        if prev is not None and res.stage != prev:
            transitions.append({"date": date, "from": prev, "from_cn": STAGE_CN[prev],
                                "to": res.stage, "to_cn": res.stage_cn})
        prev = res.stage
    return {
        "days": len(series),
        "series": series,
        "transitions": transitions,
        "current": series[-1]["stage"] if series else "unknown",
    }


# ---------------------------------------------------------------------------
# 内部助手
# ---------------------------------------------------------------------------


def _structure(df: pd.DataFrame, close: pd.Series, window: int) -> dict:
    """均线/效率比/量比等结构指标（末值）。"""
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    ma200 = close.rolling(200).mean()
    slope_lag = max(5, TREND_WINDOW // 3)
    ma60_last = float(ma60.iloc[-1])
    ma60_prev = float(ma60.iloc[-1 - slope_lag]) if len(ma60) > slope_lag else float("nan")
    slope = (ma60_last / ma60_prev - 1.0) if np.isfinite(ma60_prev) and ma60_prev > 0 else float("nan")
    er_series = efficiency_ratio(close, TREND_WINDOW)
    er = float(er_series.iloc[-1]) if len(er_series) and np.isfinite(er_series.iloc[-1]) else None
    last = float(close.iloc[-1])
    v20, v60, v200 = float(ma20.iloc[-1]), ma60_last, float(ma200.iloc[-1])
    return {
        "close": safe_round(last),
        "ma20": safe_round(v20),
        "ma60": safe_round(v60),
        "ma200": safe_round(v200),
        "ma60_slope": safe_round(slope),
        "slope_window": slope_lag,
        "er": safe_round(er),
        "er_window": TREND_WINDOW,
        "above_ma60": bool(last > v60) if np.isfinite(v60) else False,
        "ma_bull": bool(v20 > v60 > v200) if np.isfinite(v200) else False,
        "ma_bear": bool(v20 < v60) if np.isfinite(v60) else False,
        "box_window": window,
    }


def _price_position(df: pd.DataFrame, close: pd.Series) -> float:
    """当前价在近 POSITION_WINDOW 日 min-max 区间中的位置（0~1）。

    这是区分「低位平台」与「高位平台」的唯一凭据——两者的箱体几何可能完全
    相同，含义却相反（一个是买入点前夜，一个是卖出前夜）。
    """
    seg = df.iloc[-POSITION_WINDOW:]
    high = float(seg["high"].astype(float).max()) if "high" in seg.columns else float(seg["close"].max())
    low = float(seg["low"].astype(float).min()) if "low" in seg.columns else float(seg["close"].min())
    span = high - low
    if span <= 0:
        return 0.5
    return float(np.clip((float(close.iloc[-1]) - low) / span, 0.0, 1.0))


def _prior_gain(close: pd.Series, box: Box, window: int) -> float | None:
    """箱体中轴相对「箱体前 PRIOR_WINDOW 根收盘价」的涨幅。

    高位派发要求平台之前确实有一段升幅——否则只是高位横盘的假象。
    """
    if not np.isfinite(box.mid):
        return None
    base_idx = len(close) - window - PRIOR_WINDOW
    if base_idx < 0:
        base_idx = 0
    base = float(close.iloc[base_idx])
    if base <= 0:
        return None
    return box.mid / base - 1.0


def _vol_ratio_at(df: pd.DataFrame, bars_ago: int) -> float | None:
    """指定 K 线（距今 bars_ago 根）的量能 / 其 20 日均量。"""
    if "volume" not in df.columns:
        return None
    vol = df["volume"].astype(float)
    vma = vol.rolling(20).mean()
    pos = len(vol) - 1 - bars_ago
    if pos < 0 or pos >= len(vol):
        return None
    base = float(vma.iloc[pos])
    if not np.isfinite(base) or base <= 0:
        return None
    return float(vol.iloc[pos]) / base


def _broke_ma60(close: pd.Series, structure: dict, confirm_days: int) -> bool:
    """近 confirm_days 内是否首次跌破下行的 MA60（箱体不成立时的破位兜底）。"""
    slope = structure.get("ma60_slope")
    # slope 不可用 / MA60 未下行 / 仍站在 MA60 上方 -> 都不算破位
    if slope is None or slope >= 0 or structure.get("above_ma60"):
        return False
    ma60 = close.rolling(60).mean()
    below = close < ma60
    prev_above = close.shift(1) >= ma60.shift(1)
    cross = (below & prev_above).iloc[-confirm_days:]
    return bool(cross.any())


def _ev(kind: str, text: str, value) -> dict:
    """一条结构化证据：kind（box/break/trend/position/volume/prior_gain）+ 文字 + 数值。"""
    return {"kind": kind, "text": text, "value": value}


def _build(
    symbol: str, stage: str, confidence: str, position: float,
    box: Box, structure: dict, evidence: list[dict], n: int, asof: str, rule: str,
) -> StageResult:
    """组装 StageResult（统一填充 trigger 与 posture）。"""
    posture, note = _STAGE_POSTURE[stage]
    return StageResult(
        symbol=symbol,
        stage=stage,
        confidence=confidence,
        price_position=position,
        box=box.to_dict(),
        trigger=_trigger(box, structure.get("close")),
        structure=structure,
        evidence=evidence,
        posture={"posture": posture, "note": note},
        asof=asof,
        n_bars=n,
        rule=rule,
    )


def _trigger(box: Box, close: float | None) -> dict:
    """关键价位：突破价/破位价及当前价到二者的距离（箱体不成立时为 None）。"""
    if not np.isfinite(box.high) or not np.isfinite(box.low):
        return {"breakout_price": None, "breakdown_price": None,
                "distance_to_breakout_pct": None, "distance_to_breakdown_pct": None,
                "box_valid": bool(box.valid)}
    up, dn = box.breakout_price, box.breakdown_price
    d_up = d_dn = None
    if close:
        d_up = safe_round(up / close - 1.0)
        d_dn = safe_round(dn / close - 1.0)
    return {
        "breakout_price": round(up, 4),
        "breakdown_price": round(dn, 4),
        "distance_to_breakout_pct": d_up,
        "distance_to_breakdown_pct": d_dn,
        "box_valid": bool(box.valid),
    }


def _unknown(symbol: str, n: int, asof: str) -> StageResult:
    """K 线不足时的诚实降级（不猜测）。"""
    posture, note = _STAGE_POSTURE["unknown"]
    return StageResult(
        symbol=symbol, stage="unknown", confidence="low", price_position=None,
        box={"valid": False, "reason": f"K 线仅 {n} 根，不足 {MIN_BARS} 根"},
        trigger={"breakout_price": None, "breakdown_price": None,
                 "distance_to_breakout_pct": None, "distance_to_breakdown_pct": None,
                 "box_valid": False},
        structure={},
        evidence=[_ev("data", f"K 线仅 {n} 根，有效判定需 {MIN_BARS} 根以上", n)],
        posture={"posture": posture, "note": note},
        asof=asof, n_bars=n, rule="K 线不足",
    )


def _asof(df: pd.DataFrame) -> str:
    """最近一根 K 线的日期字符串。"""
    if not len(df):
        return ""
    return _fmt_date(resolve_time_index(df)[-1])


def _fmt_date(value) -> str:
    """时间戳 -> YYYY-MM-DD；非时间类型原样转字符串。"""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return str(value)
