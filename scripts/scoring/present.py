"""评分结果的终端渲染。

从 run_score.py 提取的展示逻辑，使 CLI 入口瘦身为纯适配层。
本模块属于 scoring 领域包，不导入 CLI 层（cli_common / cli_config）。
"""

from __future__ import annotations

from typing import Callable

from .engine import ScoreResult

#: 层级中文名映射
LAYER_CN = {
    "data": "数据检查",
    "alpha": "ALPHA 加权",
    "veto": "风险否决",
    "confirm": "技术确认",
    "timing": "入场时机",
    "event_risk": "事件风险",
}

DISCLAIMER = (
    "提示：评分是纪律工具而非收益预测，阈值未经过样本外验证（可用 --replay 自证）；"
    "可用 run_backtest.py 验证策略、run_paper.py 跟踪模拟盘。不构成投资建议。"
)


def print_score_report(
    symbol: str,
    result: ScoreResult,
    regime: dict,
    bench_symbol: str | None,
    brief: bool,
    log: Callable[..., None],
) -> None:
    """终端输出：裁决式结论先行 + 分层拆解 + 持仓状态 + 交易计划。"""
    from research.regime import format_regime

    from .plan import format_plan

    log()
    log(f"========== {symbol} 纪律评分（截至 {result.asof}）==========")
    log(f"结论          : {result.verdict_cn}")
    log(format_regime(regime))
    if result.alpha_score is not None:
        comp = result.components
        parts = [f"动量 {comp['momentum']['score']:.0f}"]
        if comp['rel_strength']['score'] is not None:
            parts.append(f"相对强度 {comp['rel_strength']['score']:.0f}")
        parts.append(f"趋势效率 {comp['efficiency']['score']:.0f}")
        log(f"排名分        : {result.alpha_score:.1f}（{' / '.join(parts)}）")
    if bench_symbol:
        log(f"基准          : {bench_symbol}")

    if not brief:
        log("--- 关键证据（分层拆解） ---")
        for layer in result.layers:
            tag = LAYER_CN.get(layer["name"], layer["name"])
            status = {"pass": "通过", "veto": "否决", "cap": "封顶", "downgrade": "降级"}.get(
                layer["status"], layer["status"]
            )
            log(f"[{tag}] {status}")
            for reason in layer["reasons"]:
                log(f"  · {reason}")

    if result.position is not None:
        log("--- 持仓状态 ---")
        pos = result.position
        log(f"成本 {pos['cost']}，浮盈亏 {pos['pnl_pct'] * 100:+.2f}%" + (
            f"，市值 {pos['market_value']:,.2f}" if pos.get("market_value") else ""
        ))
        if pos.get("stop_ref"):
            log(f"止损参考 {pos['stop_ref']}（距当前 {pos['stop_distance_pct'] * 100:+.2f}%）")
        log(f"建议：{pos['advice']}")

    if result.plan is not None or result.verdict in ("yes", "watch"):
        log("--- 交易计划（风险管理参考，非订单指令） ---")
        for line in format_plan(result.plan):
            log(line)
