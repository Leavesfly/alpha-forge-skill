#!/usr/bin/env python3
"""回测 CLI：拉取数据 -> 运行策略 -> 回测 -> 绩效报告 -> 可选出图。

示例：
    uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --plot
    uv run python run_backtest.py --symbol AAPL.US --strategy macd --count 800
    uv run python run_backtest.py --symbol 600519.SH --strategy ma_cross \
        --params fast=10 slow=30
"""

from __future__ import annotations

import argparse

from backtest.costs import CostModel
from backtest.engine import run_backtest
from backtest.ledger import run_backtest_ledger
from backtest.rules import TradingRules
from cli_common import (
    add_json_arg,
    check_symbol,
    emit_json,
    make_logger,
    make_parser,
    parse_params,
    run_cli,
)
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from naming import default_output
from report import attach_meta, metrics_table, render_backtest_report, result_to_dict
from strategies import STRATEGIES, get_strategy


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 策略回测", __doc__)
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH")
    parser.add_argument(
        "--strategy",
        required=True,
        choices=list(STRATEGIES),
        help="策略名称",
    )
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=500, help="K 线数量，默认 500")
    parser.add_argument(
        "--adjust",
        default="forward",
        help="复权口径：forward/qfq(前复权,默认) / backward/hfq(后复权) / none(不复权)",
    )
    parser.add_argument("--no-cache", action="store_true", help="禁用本地缓存，强制重新拉取")
    parser.add_argument(
        "--params",
        nargs="*",
        default=[],
        help="策略参数，形如 fast=10 slow=30",
    )
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0005, help="单边滑点率")
    parser.add_argument(
        "--market",
        choices=["generic", "astock"],
        default="generic",
        help="成本预设：generic(默认) / astock(A股卖出印花税 + 双边过户费)",
    )
    parser.add_argument(
        "--exec-price",
        choices=["close", "open"],
        default="close",
        help="成交价约定：close(收盘成交,默认) / open(次日开盘成交,更贴近现实)",
    )
    parser.add_argument(
        "--limit-board",
        choices=["main", "star", "chinext", "st"],
        default=None,
        help="启用 A 股涨跌停/停牌规则并指定板块(main 10%%/star,chinext 20%%/st 5%%)",
    )
    parser.add_argument("--allow-short", action="store_true", help="开启做空（策略输出 -1）")
    parser.add_argument(
        "--engine",
        choices=["vector", "ledger"],
        default="vector",
        help="回测引擎：vector(向量化,默认) / ledger(现金+整数股账本,更高保真度)",
    )
    parser.add_argument(
        "--lot-size",
        type=int,
        default=None,
        help="最小交易单位（股）；仅 ledger 引擎生效，默认 astock=100，其余=1",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=1_000_000.0,
        help="初始资金，默认 100 万；ledger 引擎下真实影响可建仓数量",
    )
    parser.add_argument("--stop-loss", type=float, default=None, help="止损比例，如 0.05 表示浮亏 5%%")
    parser.add_argument("--take-profit", type=float, default=None, help="止盈比例，如 0.10 表示浮盈 10%%")
    parser.add_argument("--vol-target", type=float, default=None, help="年化目标波动率，如 0.15（开启连续仓位）")
    parser.add_argument("--vol-window", type=int, default=20, help="波动率滚动窗口，默认 20")
    parser.add_argument("--max-leverage", type=float, default=1.0, help="仓位上限，默认 1.0")
    parser.add_argument("--plot", action="store_true", help="生成回测图表")
    parser.add_argument("--stress", action="store_true", help="输出压力测试（历史情景重放 + 蒙特卡洛冲击）")
    parser.add_argument("--output", default=None, help="图表输出路径；默认按 ../outputs/backtest_<标的>_<策略>.png 命名")
    add_json_arg(parser)
    parser.add_argument(
        "--report",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="生成自包含 HTML 研究报告；不带值用默认命名 ../outputs/report_<标的>_<策略>.html",
    )
    return parser


def main() -> None:
    args = parse_args_with_config(build_parser())
    check_symbol(args.symbol)

    # --json 不带路径时，stdout 只留给 JSON，进度/报告转到 stderr
    json_stdout = args.json == "-"
    log = make_logger(json_stdout)

    params = parse_params(args.params)
    if args.allow_short:
        params["allow_short"] = True
    strategy = get_strategy(args.strategy, **params)

    log(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根，复权：{args.adjust}）...")
    df = fetch_ohlcv(
        args.symbol,
        period=args.period,
        count=args.count,
        adjust=args.adjust,
        use_cache=not args.no_cache,
    )
    log(f"已获取 {len(df)} 根 K 线，策略：{strategy}")

    cost_model = CostModel.preset(
        args.market, commission=args.commission, slippage=args.slippage
    )
    trading_rules = (
        TradingRules.astock(args.limit_board) if args.limit_board else None
    )
    log(f"成本模型：{cost_model.describe()}；成交价：{args.exec_price}")
    if trading_rules:
        log(f"已启用 A 股交易规则：涨跌停 {trading_rules.limit_pct:.0%} + 停牌")

    if args.engine == "ledger":
        lot = args.lot_size or (100 if args.market == "astock" else 1)
        log(f"账本引擎：初始资金 {args.capital:,.0f}，lot_size={lot}")
        result = run_backtest_ledger(
            df,
            strategy,
            symbol=args.symbol,
            period=args.period,
            initial_capital=args.capital,
            cost_model=cost_model,
            exec_price=args.exec_price,
            trading_rules=trading_rules,
            stop_loss=args.stop_loss,
            take_profit=args.take_profit,
            vol_target=args.vol_target,
            vol_window=args.vol_window,
            max_leverage=args.max_leverage,
            lot_size=lot,
        )
    else:
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
        "引擎": args.engine,
        "复权": args.adjust,
        "成本模型": cost_model.describe(),
        "成交价": args.exec_price,
        "交易规则": (
            f"A股涨跌停 {trading_rules.limit_pct:.0%}" if trading_rules else "无"
        ),
    }

    log("")
    metrics_table(
        {"策略": result.metrics, "基准 Buy&Hold": result.benchmark_metrics},
        title=f"{args.symbol} {strategy.display_name} 回测绩效",
        stderr=json_stdout,
    )

    stress = None
    if args.stress:
        from report import frame_table
        from risk.stress import stress_tables

        scen_df, mc_df = stress_tables(result.returns)
        stress = {"scenarios": scen_df, "monte_carlo": mc_df}
        log("")
        if scen_df.empty:
            log("压力测试：回测区间未覆盖任何预置历史情景。")
        else:
            frame_table(
                scen_df, title="压力测试：历史情景重放",
                pct_cols=("期间收益", "最大回撤"), stderr=json_stdout,
            )
        if not mc_df.empty:
            frame_table(
                mc_df, title="压力测试：蒙特卡洛冲击（最大回撤分位）",
                pct_cols=("回撤p50", "回撤p95", "回撤p99"), stderr=json_stdout,
            )

    if args.plot:
        from backtest.plot import plot_result

        output = args.output or default_output("backtest", args.symbol, args.strategy)
        path = plot_result(result, strategy_name=strategy.display_name, output=output)
        log(f"\n图表已保存：{path}")

    if args.report is not None:
        out = args.report or default_output(
            "report", args.symbol, args.strategy, ext="html"
        )
        path = render_backtest_report(
            result, strategy_name=strategy.display_name, config=config, output=out,
            stress=stress,
        )
        log(f"HTML 报告已保存：{path}")

    if args.json is not None:
        payload = attach_meta(
            result_to_dict(result, strategy_name=strategy.display_name, config=config),
            command="backtest",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
