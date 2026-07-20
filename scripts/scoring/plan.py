"""交易计划生成：把纪律评分的结论落成具体价位。

价位来自 ATR 与均线结构，用于风险管理参考，不是订单指令：

- 入场参考   = 最新收盘价；
- 回踩参考   = MA20（回调低吸位）；
- 止损       = 入场 − 2×ATR(14)；
- R          = 入场 − 止损（单位风险）；
- 止盈       = 入场 + 2R / 入场 + 3R；
- 追价上限   = 入场 + 0.5×ATR(14)（超过则视为追高，等回踩）。

另提供**建议仓位**（:func:`attach_position_sizing`）：风险预算法，
单笔交易最大亏损 = 资金 × 风险比例（默认 1%），股数 = 风险额 / R，
回答「买多少」而不只是「买不买」。
"""

from __future__ import annotations

import math


def build_trade_plan(close: float, ma20: float, atr14: float) -> dict | None:
    """由最新收盘/MA20/ATR 生成交易计划价位（2 位小数）。

    ATR 或收盘价无效（NaN/非正）时返回 None，表示无法给出计划。
    """
    if not _valid(close) or not _valid(atr14):
        return None
    entry = close
    stop = entry - 2.0 * atr14
    r = entry - stop
    if r <= 0:
        return None
    plan = {
        "entry": entry,
        "pullback_ref": ma20 if _valid(ma20) else None,
        "stop": stop,
        "r": r,
        "target_2r": entry + 2.0 * r,
        "target_3r": entry + 3.0 * r,
        "chase_limit": entry + 0.5 * atr14,
        "atr": atr14,
    }
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in plan.items()}


def attach_position_sizing(
    plan: dict | None,
    capital: float,
    risk_pct: float = 0.01,
    lot_size: int = 1,
) -> dict | None:
    """在交易计划上附加风险预算法建议仓位（就地补充键，返回同一 dict）。

    股数 = 资金 × 风险比例 / R（按 lot_size 向下取整），市值不超过资金；
    意义：若买入后触发止损，亏损约为资金的 risk_pct。

    Args:
        plan: :func:`build_trade_plan` 的输出（None 直接透传）。
        capital: 可用资金。
        risk_pct: 单笔交易风险预算占资金比例，默认 0.01（1%）。
        lot_size: 最小交易单位（A 股 100，其余 1）。
    """
    if plan is None or capital <= 0 or risk_pct <= 0:
        return plan
    r = plan.get("r")
    entry = plan.get("entry")
    if not _valid(r) or not _valid(entry):
        return plan
    risk_amount = capital * risk_pct
    raw_shares = risk_amount / r
    # 市值不超过可用资金（低波动标的 R 小时风险预算法会算出超额仓位）
    raw_shares = min(raw_shares, capital / entry)
    shares = int(raw_shares // lot_size) * lot_size
    plan["sizing"] = {
        "capital": capital,
        "risk_pct": risk_pct,
        "risk_amount": round(risk_amount, 2),
        "suggested_shares": shares,
        "position_value": round(shares * entry, 2),
        "position_pct": round(shares * entry / capital, 4),
        "lot_size": lot_size,
    }
    return plan


def format_plan(plan: dict | None) -> list[str]:
    """交易计划的终端展示行（plan 为 None 时给出提示）。"""
    if plan is None:
        return ["交易计划      : 数据不足（ATR 未形成），无法给出价位"]
    lines = [
        f"入场参考      : {plan['entry']:.2f}（追价上限 {plan['chase_limit']:.2f}，超过请等回踩）",
    ]
    if plan.get("pullback_ref") is not None:
        lines.append(f"回踩参考      : {plan['pullback_ref']:.2f}（MA20）")
    lines += [
        f"止损          : {plan['stop']:.2f}（2×ATR14，R = {plan['r']:.2f}）",
        f"止盈          : {plan['target_2r']:.2f}（2R）/ {plan['target_3r']:.2f}（3R）",
    ]
    sizing = plan.get("sizing")
    if sizing:
        if sizing["suggested_shares"] > 0:
            lines.append(
                f"建议仓位      : {sizing['suggested_shares']} 股（市值约 {sizing['position_value']:,.0f}，"
                f"占资金 {sizing['position_pct'] * 100:.1f}%；若止损约亏 {sizing['risk_amount']:,.0f} "
                f"= 资金的 {sizing['risk_pct'] * 100:.1f}%）"
            )
        else:
            lines.append(
                f"建议仓位      : 资金不足一手（lot={sizing['lot_size']}），"
                "可降低风险比例或增加资金"
            )
    return lines


def _valid(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x) and x > 0
