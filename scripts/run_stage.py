#!/usr/bin/env python3
"""个股阶段定位 CLI：现在走到「台阶式循环」的哪一段。

独立能力，自给自足：只回答**现在在哪**，不依赖也不串联买点三灯
（run_score.py 是另一项独立能力，仅当用户另行提出买卖问题时才单独运行）：

    低位筑底 → 突破确认 → 上升推进 → 高位派发 → 破位下行 → 下降趋势 →（重新筑底）

判定只用日线价量：箱体（平台）识别 + 位置分位（近 250 日 min-max）+ 均线结构。
位置分位是关键——低位平台与高位平台的箱体几何可能完全相同，含义却相反
（一个是买入点前夜，一个是卖出前夜），而市场状态识别（run_score 的 regime）
把两者都归为「震荡」。

阶段是描述性统计而非预测，存在滞后；阈值为纪律预设值，未经样本外验证。

示例：
    # 当前阶段 + 关键价位（箱体上下沿派生的突破价/破位价）
    uv run python run_stage.py --symbol 600000.SH

    # 只要结论（简短模式）
    uv run python run_stage.py --symbol AAPL.US --brief

    # 阶段迁移轨迹：最近 120 日逐日重算（无前视）+ 阶段着色图
    uv run python run_stage.py --symbol 600519.SH --history 120 --plot

    # 放宽箱体窗口（周期更长的平台）并输出结构化 JSON
    uv run python run_stage.py --symbol 000001.SZ --window 90 --json
"""

from __future__ import annotations

import argparse

from cli_common import (
    add_json_arg,
    build_next_steps,
    check_symbol,
    emit_json,
    init_log,
    make_parser,
    run_cli,
)
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from naming import default_output
from report import attach_meta
from stage import (
    DEFAULT_CONFIRM_DAYS,
    DEFAULT_WINDOW,
    POSITION_LOW,
    STAGE_DISCLAIMER,
    detect_stage,
    print_stage_report,
    stage_history,
)
from utils import extract_close


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 个股阶段定位（箱体 + 位置分位 + 均线结构）", __doc__)
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH / AAPL.US")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d（阶段按日线纪律设计）")
    parser.add_argument(
        "--count", type=int, default=1250,
        help="K 线数量，默认 1250（约 5 年，有效判定至少需 250 根）",
    )
    parser.add_argument(
        "--window", type=int, default=DEFAULT_WINDOW,
        help=f"箱体窗口（交易日），默认 {DEFAULT_WINDOW}；平台周期更长时可放宽到 90/120",
    )
    parser.add_argument(
        "--confirm-days", type=int, default=DEFAULT_CONFIRM_DAYS,
        help=f"突破/破位检验窗口（交易日），默认 {DEFAULT_CONFIRM_DAYS}",
    )
    parser.add_argument(
        "--history", nargs="?", const=120, default=None, type=int, metavar="N",
        help="阶段迁移轨迹：最近 N 个交易日逐日重算（默认 120，无前视）",
    )
    parser.add_argument("--brief", action="store_true", help="简短模式：只输出阶段结论与关键价位")
    parser.add_argument("--plot", action="store_true", help="生成阶段图（价格 + MA + 箱体上下沿；有 --history 时背景按阶段着色）")
    parser.add_argument("--output", default=None, help="图表输出路径；默认 ../outputs/stage_<标的>.png")
    add_json_arg(parser)
    return parser


#: 各阶段的自然语言结论模板（Agent 可直接转述）
#: base 是伞形态（低位/中位/结构不清），故不用单一模板，改由 :func:`_base_core` 分支措辞。
_SUMMARY_TPL = {
    "breakout": "刚完成突破确认：已上穿平台上沿 {high:.2f}，关键点出现",
    "advance": "处于上升推进：均线多头排列且趋势顺畅，升势未被破坏",
    "top": "处于高位派发：{box}，位置分位 {pos:.0%} 且前期有明显升幅，是派发与再突破的岔路口",
    "breakdown": "已破位下行：跌破平台下沿 {low:.2f}，上升趋势被破坏",
    "decline": "处于下降趋势：均线空头排列且下行顺畅，尚未构筑新平台",
    "unknown": "无法判定阶段：K 线不足",
}


def _base_core(result, box_desc: str) -> str:
    """base 是伞形态，按子情况分别措辞，不把中位/结构不清说成「低位筑底」。"""
    pos = result.price_position or 0.0
    if result.confidence == "low":
        return (
            f"结构不清：{box_desc}，位置分位 {pos:.0%}，"
            "既无成立平台也无明确趋势，只能按整理对待（不得当作低位筑底解读）"
        )
    if pos > POSITION_LOW:
        return f"处于中位整理：{box_desc}，位置分位 {pos:.0%}（未达低位），尚未选择方向"
    return f"处于低位筑底：{box_desc}，位置分位 {pos:.0%}，尚未选择方向"


def _core_sentence(result, box_desc: str) -> str:
    """阶段结论主句：base 走子情况分支，其余走模板。"""
    if result.stage == "base":
        return _base_core(result, box_desc)
    box = result.box or {}
    return _SUMMARY_TPL[result.stage].format(
        box=box_desc,
        pos=result.price_position if result.price_position is not None else 0.0,
        high=box.get("high") or 0.0,
        low=box.get("low") or 0.0,
    )


def _build_summary(symbol: str, result) -> str:
    """拼接 1-2 句白话结论（含关键价位与免责）。"""
    box = result.box or {}
    box_desc = (
        f"平台 {box.get('low'):.2f} ~ {box.get('high'):.2f}"
        if box.get("high") and box.get("low") else "平台结构不清"
    )
    core = _core_sentence(result, box_desc)
    trigger = result.trigger or {}
    price_str = ""
    if trigger.get("breakout_price") and trigger.get("breakdown_price"):
        caveat = "" if trigger.get("box_valid") else "（箱体不成立，价位仅供参考）"
        price_str = (
            f"关键价位：突破价 {trigger['breakout_price']} / "
            f"破位价 {trigger['breakdown_price']}{caveat}。"
        )
    posture = result.posture.get("posture", "")
    return (
        f"{symbol} 阶段定位：{result.stage_cn}（置信度 {result.confidence}，"
        f"依据「{result.rule}」）。{core}。{price_str}"
        f"应对姿态：{posture}。{STAGE_DISCLAIMER}"
    )


def _build_next_steps(symbol: str, result) -> list[dict]:
    """结构化后续动作：只含阶段模块自身的动作，不引导去跑三灯（两能力互不串联）。"""
    return build_next_steps(
        {"action": "history", "reason": "回看阶段迁移轨迹，确认当前判定是否稳定（避免边界抖动）",
         "command": f"run_stage.py --symbol {symbol} --history 120 --json"},
        {"action": "backtest_wisdom", "reason": "已出现关键点突破，用《炒股的智慧》规则回测这套打法的历史表现",
         "condition": "stage == breakout",
         "command": f"run_custom.py --symbol {symbol} --rules examples/wisdom_rule.toml --stop-loss 0.05 --json"},
    )


def main() -> None:
    args = parse_args_with_config(build_parser())
    check_symbol(args.symbol)
    json_stdout, log = init_log(args)

    log(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根）...")
    df = fetch_ohlcv(args.symbol, period=args.period, count=args.count)

    result = detect_stage(
        df, symbol=args.symbol, window=args.window, confirm_days=args.confirm_days,
    )

    history = None
    if args.history is not None:
        log(f"逐日重算最近 {args.history} 个交易日的阶段（无前视）...")
        history = stage_history(
            df, days=args.history, window=args.window, confirm_days=args.confirm_days,
        )

    print_stage_report(result, log, brief=args.brief, history=history)
    log("")
    log(STAGE_DISCLAIMER)

    if args.plot:
        from stage.plot import plot_stage

        output = args.output or default_output("stage", args.symbol)
        path = plot_stage(
            extract_close(df), result, history=history,
            title=f"{args.symbol} 阶段定位：{result.stage_cn}（截至 {result.asof}）",
            output=output,
        )
        log(f"图表已保存：{path}")

    if args.json is not None:
        payload = attach_meta(
            {
                **result.to_dict(),
                "period": args.period,
                "count": args.count,
                "history": history,
                "summary": _build_summary(args.symbol, result),
                "next_steps": _build_next_steps(args.symbol, result),
            },
            command="stage",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
