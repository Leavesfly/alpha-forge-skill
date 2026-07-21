#!/usr/bin/env python3
"""多策略对比 CLI：同一标的一次回测多个策略，并排比较绩效。

示例：
    # 全部内置策略对比（默认参数）
    uv run python run_compare.py --symbol 600000.SH

    # 指定策略子集 + 净值叠加图 + HTML 对比报告
    uv run python run_compare.py --symbol AAPL.US --strategies ma_cross,macd,rsi \
        --plot --report

    # 结构化 JSON 输出（stdout 仅留 JSON）
    uv run python run_compare.py --symbol 600000.SH --json > compare.json
"""

from __future__ import annotations

import argparse

from backtest.engine import run_backtest
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
from report import attach_meta, metrics_table, render_compare_report
from strategies import STRATEGIES, get_strategy


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 多策略对比回测", __doc__)
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH")
    parser.add_argument(
        "--strategies",
        default=None,
        help=f"策略名逗号分隔（默认全部）：{','.join(STRATEGIES)}",
    )
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=500, help="K 线数量，默认 500")
    parser.add_argument(
        "--adjust",
        default="forward",
        help="复权口径：forward/qfq(默认) / backward/hfq / none",
    )
    parser.add_argument("--no-cache", action="store_true", help="禁用本地缓存")
    add_cost_args(parser)
    add_market_args(parser)
    parser.add_argument("--allow-short", action="store_true", help="开启做空")
    parser.add_argument("--stop-loss", type=float, default=None, help="止损比例")
    parser.add_argument("--take-profit", type=float, default=None, help="止盈比例")
    parser.add_argument("--vol-target", type=float, default=None, help="年化目标波动率")
    parser.add_argument(
        "--sort", default="sharpe", help="终端表排序指标，默认 sharpe"
    )
    parser.add_argument("--plot", action="store_true", help="生成净值叠加对比图")
    parser.add_argument("--output", default=None, help="图表输出路径；默认自动命名")
    parser.add_argument(
        "--report",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="生成自包含 HTML 对比报告；不带值用默认命名",
    )
    add_json_arg(parser)
    return parser


def main() -> None:
    args = parse_args_with_config(build_parser())
    check_symbol(args.symbol)
    json_stdout, log = init_log(args)

    names = (
        [s.strip() for s in args.strategies.split(",") if s.strip()]
        if args.strategies
        else list(STRATEGIES)
    )
    unknown = [n for n in names if n not in STRATEGIES]
    if unknown:
        raise SystemExit(
            f"[error] 未知策略：{unknown}，可选：{list(STRATEGIES)}"
        )

    log(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根，复权：{args.adjust}）...")
    df = fetch_ohlcv(
        args.symbol,
        period=args.period,
        count=args.count,
        adjust=args.adjust,
        use_cache=not args.no_cache,
    )
    log(f"已获取 {len(df)} 根 K 线，对比 {len(names)} 个策略：{names}")

    cost_model, trading_rules = build_cost_and_rules(args)
    params = {"allow_short": True} if args.allow_short else {}

    results = {}
    name_map = {}  # display_name -> 注册名（regime 策略族判断用）
    for name in names:
        strategy = get_strategy(name, **params)
        name_map[strategy.display_name] = name
        results[strategy.display_name] = run_backtest(
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
        )

    # 按指定指标降序排列展示
    ordered = dict(
        sorted(
            results.items(),
            key=lambda kv: kv[1].metrics.get(args.sort, 0.0),
            reverse=True,
        )
    )
    first = next(iter(ordered.values()))

    log("")
    named = {name: res.metrics for name, res in ordered.items()}
    named["基准 Buy&Hold"] = first.benchmark_metrics
    metrics_table(
        named,
        title=f"{args.symbol} 多策略对比（按 {args.sort} 排序）",
        stderr=json_stdout,
    )

    best_name = next(iter(ordered))
    log(f"\n{args.sort} 最优：{best_name}"
        f"（{ordered[best_name].metrics.get(args.sort, 0.0):.2f}）"
        "；样本内比较存在选择性偏差。")

    # 市场状态：提示当前状态更适合哪一族策略，冠军与状态不符时预警
    from research.regime import detect_regime, format_regime

    regime = detect_regime(df["close"])
    log(format_regime(regime))
    best_key = name_map.get(best_name)
    regime_warning = None
    if regime["suited_strategies"] and best_key and best_key not in regime["suited_strategies"]:
        regime_warning = (
            f"⚠️ 样本内冠军 {best_name} 不属于当前状态更适合的{regime['suited_family']}，"
            "状态延续时实盘表现可能不及样本内；建议结合样本外验证判断。"
        )
        log(regime_warning)
    log_next_steps(
        log,
        f"对胜出策略寻优 run_optimize.py --symbol {args.symbol} --strategy <策略名>",
        "样本外复核 run_validate.py（避免「选冠军」的幸存者偏差）",
    )

    config = {
        "复权": args.adjust,
        "成本模型": cost_model.describe(),
        "成交价": args.exec_price,
        "交易规则": (
            f"A股涨跌停 {trading_rules.limit_pct:.0%}" if trading_rules else "无"
        ),
    }

    if args.plot:
        from backtest.plot import plot_compare

        output = args.output or default_output(
            "compare", args.symbol, f"{len(names)}strats"
        )
        path = plot_compare(ordered, symbol=args.symbol, output=output)
        log(f"\n图表已保存：{path}")

    if args.report is not None:
        out = args.report or default_output(
            "compare_report", args.symbol, f"{len(names)}strats", ext="html"
        )
        path = render_compare_report(
            ordered, symbol=args.symbol, config=config, output=out
        )
        log(f"HTML 对比报告已保存：{path}")

    if args.json is not None:
        best_strat = next(iter(ordered))
        best_m = ordered[best_strat].metrics
        payload = attach_meta(
            {
                "symbol": args.symbol,
                "period": args.period,
                "config": config,
                "sort": args.sort,
                "strategies": [
                    {
                        "name": name,
                        "metrics": dict(res.metrics),
                    }
                    for name, res in ordered.items()
                ],
                "benchmark_metrics": dict(first.benchmark_metrics),
                "regime": regime,
                "regime_warning": regime_warning,
                "summary": (
                    f"{args.symbol} 多策略对比（{len(names)} 个）："
                    f"按{args.sort}排序最优为 {best_strat}"
                    f"（{args.sort}={best_m.get(args.sort, 0):.2f}）。"
                    f"当前市场状态：{regime['regime_cn']}。"
                    f"样本内选冠军存在选择性偏差，建议 run_validate 复核。"
                ),
                "next_steps": build_next_steps(
                    {"action": "optimize", "reason": "对胜出策略寻找最优参数",
                     "command": f"run_optimize.py --symbol {args.symbol} --strategy {best_strat} --json"},
                    {"action": "validate", "reason": "样本外复核避免幸存者偏差",
                     "command": f"run_validate.py --symbol {args.symbol} --strategy {best_strat} --json"},
                ),
            },
            command="compare",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
