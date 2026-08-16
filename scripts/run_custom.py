#!/usr/bin/env python3
"""自定义规则策略回测 CLI：加载 TOML 规则文件 -> 解析 DSL -> 回测 -> 绩效报告。

Agent 可根据用户自然语言描述生成规则文件，再用本命令回测验证。
规则格式见 examples/custom_rule.toml，支持白名单指标（SMA/EMA/RSI/MACD/布林/ATR/
唐奇安/KDJ/动量/ROC）+ 受限条件表达式（>/</crosses_above/crosses_below）。

示例：
    # 用示例规则回测（金叉+RSI过滤）
    uv run python run_custom.py --symbol 600000.SH --rules examples/custom_rule.toml --plot

    # 指定 K 线数量与成本模型
    uv run python run_custom.py --symbol 600519.SH --rules my_rule.toml --count 800 --market astock

    # 结构化 JSON 输出（Agent 消费）
    uv run python run_custom.py --symbol AAPL.US --rules examples/custom_rule.toml --json
"""

from __future__ import annotations

import argparse
from pathlib import Path

from backtest.engine import run_backtest
from backtest.metrics import relative_metrics
from cli_common import (
    add_cost_args,
    add_json_arg,
    add_market_args,
    build_cost_and_rules,
    build_next_steps,
    check_symbol,
    emit_json,
    init_log,
    log_next_steps,
    make_parser,
    run_cli,
)
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from naming import default_output
from report import attach_meta, metrics_table, render_backtest_report, result_to_dict
from strategies.custom import CustomStrategy, DSLValidationError, load_rules

#: 「刚离场」的观察窗口：离场后 N 根 K 线内提示「刚离场观察」，
#: 更早则视为常规的「空仓等待」。
_RECENT_EXIT_BARS = 10


def describe_current_state(strategy, df) -> dict:
    """按规则信号判断标的**现在**处于什么状态（回测之外的实时结论）。

    只看最新一根 K 线的规则信号与最近的信号变化，输出四种状态：
    持仓（让利润奔跑）/ 建仓中（金字塔未满）/ 刚离场观察 / 空仓等待信号。

    Returns:
        {"signal": 最新信号值, "state": 状态中文, "note": 一句话应对}
    """
    sig = strategy.generate_signals(df)
    last = float(sig.iloc[-1])
    if last >= 1.0:
        return {
            "signal": round(last, 4), "state": "持仓",
            "note": "规则信号仍在持有：离场条件未触发，按纪律让利润奔跑，不猜顶。",
        }
    if last > 0:
        return {
            "signal": round(last, 4), "state": "建仓中",
            "note": "试探仓已建立但金字塔未加满：仅在浮盈继续扩大时按规则加码，不加亏损仓。",
        }
    if last <= -1.0:
        return {
            "signal": round(last, 4), "state": "持空",
            "note": "规则信号持有空头（allow_short 开启），仅适用于可做空市场。",
        }
    if last < 0:
        return {
            "signal": round(last, 4), "state": "建空仓中",
            "note": "空头试探仓已建立但未加满，仅适用于可做空市场。",
        }
    # 空仓：区分「刚离场」与「常规等待」
    nonzero = sig[sig != 0]
    if not nonzero.empty:
        bars_ago = len(sig) - 1 - int(sig.index.get_loc(nonzero.index[-1]))
        if bars_ago <= _RECENT_EXIT_BARS:
            return {
                "signal": 0.0, "state": "刚离场观察",
                "note": f"离场信号于 {bars_ago} 根 K 线前触发，趋势刚被破坏；"
                        "按书中纪律先离场后判断，等新信号再动。",
            }
    return {
        "signal": 0.0, "state": "空仓等待",
        "note": "入场条件尚未满足：等待关键点出现，不追不抢，错过就错过。",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 自定义规则策略回测（DSL）", __doc__)
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH")
    parser.add_argument(
        "--rules",
        required=True,
        help="TOML 规则文件路径（定义指标与入场/离场条件）",
    )
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=1250, help="K 线数量，默认 1250（约 5 年）")
    parser.add_argument(
        "--adjust",
        default="forward",
        help="复权口径：forward/qfq(前复权,默认) / backward/hfq(后复权) / none(不复权)",
    )
    parser.add_argument("--no-cache", action="store_true", help="禁用本地缓存，强制重新拉取")
    add_cost_args(parser)
    add_market_args(parser)
    parser.add_argument("--allow-short", action="store_true", help="开启做空（离场后持空头）")
    parser.add_argument("--stop-loss", type=float, default=None, help="止损比例，如 0.05 表示浮亏 5%%")
    parser.add_argument("--take-profit", type=float, default=None, help="止盈比例，如 0.10 表示浮盈 10%%")
    parser.add_argument("--vol-target", type=float, default=None, help="年化目标波动率，如 0.15（开启连续仓位）")
    parser.add_argument("--vol-window", type=int, default=20, help="波动率滚动窗口，默认 20")
    parser.add_argument("--max-leverage", type=float, default=1.0, help="仓位上限，默认 1.0")
    parser.add_argument("--plot", action="store_true", help="生成回测图表")
    parser.add_argument("--output", default=None, help="图表输出路径")
    add_json_arg(parser)
    parser.add_argument(
        "--report",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="生成自包含 HTML 研究报告",
    )
    return parser


def main() -> None:
    args = parse_args_with_config(build_parser())
    check_symbol(args.symbol)
    json_stdout, log = init_log(args)

    # 加载并校验规则文件
    rules_path = Path(args.rules).expanduser()
    if not rules_path.exists():
        raise SystemExit(
            f"[error] 规则文件不存在：{args.rules}。"
            "参考 examples/custom_rule.toml 创建规则文件。"
        )
    try:
        rules = load_rules(rules_path)
    except DSLValidationError as exc:
        raise SystemExit(f"[error] 规则文件校验失败：{exc}") from exc

    strategy = CustomStrategy(rules)
    if args.allow_short:
        strategy._allow_short = True

    rule_name = rules.get("meta", {}).get("name", "custom")
    rule_desc = rules.get("meta", {}).get("description", rule_name)
    log(f"规则：{rule_name}（{rule_desc}）")
    log(f"入场条件（{rules.get('entry', {}).get('logic', 'and').upper()}）：")
    for c in rules.get("entry", {}).get("conditions", []):
        log(f"  • {c}")
    log(f"离场条件（{rules.get('exit', {}).get('logic', 'or').upper()}）：")
    for c in rules.get("exit", {}).get("conditions", []):
        log(f"  • {c}")
    pyramid = rules.get("pyramid")
    if pyramid:
        log(
            f"金字塔加仓：分 {pyramid['units']} 批建仓（首批 {1 / pyramid['units']:.0%}），"
            f"每浮盈 {pyramid['step']:.1%} 加一批"
        )

    log(f"\n拉取 {args.symbol} {args.period} K 线（{args.count} 根，复权：{args.adjust}）...")
    df = fetch_ohlcv(
        args.symbol,
        period=args.period,
        count=args.count,
        adjust=args.adjust,
        use_cache=not args.no_cache,
    )
    log(f"已获取 {len(df)} 根 K 线")

    cost_model, trading_rules = build_cost_and_rules(args)
    log(f"成本模型：{cost_model.describe()}；成交价：{args.exec_price}")

    result = run_backtest(
        df,
        strategy,
        symbol=args.symbol,
        period=args.period,
        cost_model=cost_model,
        exec_price=args.exec_price,
        trading_rules=trading_rules,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        vol_target=args.vol_target,
        vol_window=args.vol_window,
        max_leverage=args.max_leverage,
    )

    config = {
        "规则文件": str(rules_path),
        "规则名称": rule_name,
        "复权": args.adjust,
        "成本模型": cost_model.describe(),
        "成交价": args.exec_price,
    }

    # 回测之外的实时结论：按规则信号告诉用户标的现在处于什么状态
    state = describe_current_state(strategy, df)
    log(f"\n当前状态：{state['state']}（信号 {state['signal']:+.2f}）——{state['note']}")

    log("")
    metrics_table(
        {"策略": result.metrics, "基准 Buy&Hold": result.benchmark_metrics},
        title=f"{args.symbol} [{rule_name}] 回测绩效",
        stderr=json_stdout,
    )

    # 基准相对指标
    benchmark_returns = result.benchmark_equity.pct_change().fillna(0.0)
    relative = relative_metrics(result.returns, benchmark_returns, period=args.period)
    log(
        f"\n相对基准：信息比率 {relative['information_ratio']:.2f}、"
        f"跟踪误差 {relative['tracking_error'] * 100:.2f}%、"
        f"Beta {relative['beta']:.2f}、Alpha(年化) {relative['alpha'] * 100:+.2f}%"
    )

    if args.plot:
        from backtest.plot import plot_result

        output = args.output or default_output("backtest", args.symbol, rule_name)
        path = plot_result(result, strategy_name=rule_desc, output=output)
        log(f"\n图表已保存：{path}")

    if args.report is not None:
        out = args.report or default_output("report", args.symbol, rule_name, ext="html")
        path = render_backtest_report(
            result, strategy_name=rule_desc, config=config, output=out,
        )
        log(f"HTML 报告已保存：{path}")

    log_next_steps(
        log,
        "参数寻优（调整规则中的 period 等参数）：修改规则文件后重跑对比",
        f"对比内置策略 run_compare.py --symbol {args.symbol}",
    )

    if args.json is not None:
        payload = result_to_dict(result, strategy_name=rule_desc, config=config)
        payload["relative"] = relative
        payload["rules"] = strategy.rules_summary()
        # Agent 友好：自然语言结论 + 结构化下一步
        m = result.metrics
        bm = result.benchmark_metrics
        beat = "跑赢" if m.get("sharpe", 0) > bm.get("sharpe", 0) else "跑输"
        payload["current_state"] = state
        payload["summary"] = (
            f"{args.symbol} 使用自定义规则「{rule_name}」回测："
            f"累计收益 {m.get('total_return', 0) * 100:+.1f}%，"
            f"夏普 {m.get('sharpe', 0):.2f}，最大回撤 {m.get('max_drawdown', 0) * 100:.1f}%，"
            f"{beat}基准 Buy&Hold（夏普 {bm.get('sharpe', 0):.2f}）。"
            f"按该规则，当前处于「{state['state']}」——{state['note']}"
            f"回测不代表未来收益。"
        )
        payload["next_steps"] = build_next_steps(
            {"action": "compare", "reason": "对比内置 14 个策略，看自定义规则是否更优",
             "command": f"run_compare.py --symbol {args.symbol} --json"},
            {"action": "optimize", "reason": "调整规则文件中的 period 等参数后重跑对比",
             "command": f"run_custom.py --symbol {args.symbol} --rules {rules_path} --json"},
        )
        payload = attach_meta(payload, command="custom")
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
