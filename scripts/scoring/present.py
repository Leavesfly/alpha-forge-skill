"""买点三灯结果的终端渲染。

从 run_score.py 提取的展示逻辑，使 CLI 入口瘦身为纯适配层。
本模块属于 scoring 领域包，不导入 CLI 层（cli_common / cli_config）。
"""

from __future__ import annotations

from typing import Callable

from .engine import ACTIONABLE_VERDICTS, COLOR_CN, LIGHT_CN, LIGHTS, ScoreResult

DISCLAIMER = (
    "提示：三灯是纪律工具而非收益预测，规则阈值未经过样本外验证（可用 --replay 自证，"
    "仅覆盖势/时维度）；可用 run_backtest.py 验证策略、run_paper.py 跟踪模拟盘。不构成投资建议。"
)


def print_score_report(
    symbol: str,
    result: ScoreResult,
    regime: dict,
    bench_symbol: str | None,
    brief: bool,
    log: Callable[..., None],
    valuation=None,
    macro_regime=None,
) -> None:
    """终端输出：结论与三灯速览先行 + 三灯拆解 + 持仓状态 + 交易计划。"""
    from research.regime import format_regime

    from .plan import format_plan

    log()
    log(f"========== {symbol} 买点三灯（截至 {result.asof}）==========")
    log(f"结论          : {result.verdict_cn}（{result.lights_summary}）")
    if result.decision.get("rule"):
        log(f"裁决          : {result.decision['rule']}")
    log(format_regime(regime))

    # 估值历史分位（价灯数据源）
    if valuation is not None:
        from data.valuation import format_valuation

        log(format_valuation(valuation))

    # 宏观环境上下文（可选）
    if macro_regime is not None:
        from data.macro import format_macro_regime

        log(format_macro_regime(macro_regime))

    if result.trend_score is not None:
        comp = result.components
        parts = [f"动量 {comp['momentum']['score']:.0f}"]
        if comp['rel_strength']['score'] is not None:
            parts.append(f"相对强度 {comp['rel_strength']['score']:.0f}")
        parts.append(f"趋势效率 {comp['efficiency']['score']:.0f}")
        log(f"趋势分        : {result.trend_score:.1f}（{' / '.join(parts)}）")
    if bench_symbol:
        log(f"基准          : {bench_symbol}")

    if not brief:
        log("--- 三灯拆解（价·势·时各自独立） ---")
        for name in LIGHTS:
            light = result.lights.get(name, {})
            color = COLOR_CN.get(light.get("color", "gray"), "灰")
            log(f"[{LIGHT_CN[name]}] {color}灯")
            for reason in light.get("reasons", []):
                log(f"  · {reason}")
        triggers = result.decision.get("triggers") or []
        if triggers:
            log("--- 再评估触发条件 ---")
            for trig in triggers:
                log(f"  · {trig}")

    if result.position is not None:
        log("--- 持仓状态 ---")
        pos = result.position
        log(f"成本 {pos['cost']}，浮盈亏 {pos['pnl_pct'] * 100:+.2f}%" + (
            f"，市值 {pos['market_value']:,.2f}" if pos.get("market_value") else ""
        ))
        if pos.get("stop_ref"):
            log(f"止损参考 {pos['stop_ref']}（距当前 {pos['stop_distance_pct'] * 100:+.2f}%）")
        log(f"建议：{pos['advice']}")

    if result.plan is not None or result.verdict in ACTIONABLE_VERDICTS:
        log("--- 交易计划（风险管理参考，非订单指令） ---")
        for line in format_plan(result.plan):
            log(line)
