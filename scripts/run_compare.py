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

from backtest.costs import CostModel
from backtest.engine import run_backtest
from backtest.rules import TradingRules
from cli_common import (
    add_json_arg,
    check_symbol,
    emit_json,
    make_logger,
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
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0005, help="单边滑点率")
    parser.add_argument(
        "--market",
        choices=["generic", "astock"],
        default="generic",
        help="成本预设：generic(默认) / astock",
    )
    parser.add_argument(
        "--exec-price",
        choices=["close", "open"],
        default="close",
        help="成交价约定：close(默认) / open",
    )
    parser.add_argument(
        "--limit-board",
        choices=["main", "star", "chinext", "st"],
        default=None,
        help="启用 A 股涨跌停/停牌规则并指定板块",
    )
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
    json_stdout = args.json == "-"
    log = make_logger(json_stdout)

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

    cost_model = CostModel.preset(
        args.market, commission=args.commission, slippage=args.slippage
    )
    trading_rules = TradingRules.astock(args.limit_board) if args.limit_board else None
    params = {"allow_short": True} if args.allow_short else {}

    results = {}
    for name in names:
        strategy = get_strategy(name, **params)
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
        "；样本内比较存在选择性偏差，建议用 run_validate.py 复核。")

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
            },
            command="compare",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
