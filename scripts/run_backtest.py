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

from backtest.engine import run_backtest
from backtest.ledger import run_backtest_ledger
from backtest.metrics import relative_metrics
from cli_common import (
    add_cost_args,
    add_json_arg,
    add_market_args,
    add_risk_args,
    build_cost_and_rules,
    build_next_steps,
    check_symbol,
    default_lot_size,
    emit_json,
    init_log,
    log_next_steps,
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
    add_cost_args(parser)
    add_market_args(parser)
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
    parser.add_argument("--kelly", action="store_true", help="半 Kelly 连续仓位（仓位=信号×0.5μ/σ²，与 --vol-target 互斥且优先；仅向量引擎）")
    parser.add_argument("--kelly-window", type=int, default=60, help="Kelly 估计滚动窗口，默认 60")
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
    json_stdout, log = init_log(args)

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

    cost_model, trading_rules = build_cost_and_rules(args)
    log(f"成本模型：{cost_model.describe()}；成交价：{args.exec_price}")
    if trading_rules:
        log(f"已启用 A 股交易规则：涨跌停 {trading_rules.limit_pct:.0%} + 停牌")

    if args.engine == "ledger":
        lot = args.lot_size or default_lot_size(args.market)
        if args.kelly:
            log("[warn] 账本引擎暂不支持 --kelly，已忽略（可用向量引擎）。")
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
            kelly=args.kelly,
            kelly_window=args.kelly_window,
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

    # 基准相对指标：信息比率/跟踪误差/Beta/Alpha
    benchmark_returns = result.benchmark_equity.pct_change().fillna(0.0)
    relative = relative_metrics(result.returns, benchmark_returns, period=args.period)
    log(
        f"\n相对基准：信息比率 {relative['information_ratio']:.2f}、"
        f"跟踪误差 {relative['tracking_error'] * 100:.2f}%、"
        f"Beta {relative['beta']:.2f}、Alpha(年化) {relative['alpha'] * 100:+.2f}%"
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

    log_next_steps(
        log,
        f"多策略对比 run_compare.py --symbol {args.symbol}",
        f"参数寻优 run_optimize.py --symbol {args.symbol} --strategy {args.strategy}（含 DSR 诊断）",
        "样本外验证 run_validate.py",
    )

    if args.json is not None:
        payload = result_to_dict(result, strategy_name=strategy.display_name, config=config)
        payload["relative"] = relative
        # Agent 友好：自然语言结论 + 结构化下一步
        m = result.metrics
        bm = result.benchmark_metrics
        beat = "跑赢" if m.get("sharpe", 0) > bm.get("sharpe", 0) else "跑输"
        payload["summary"] = (
            f"{args.symbol} 使用{strategy.display_name}策略回测："
            f"累计收益 {m.get('total_return', 0) * 100:+.1f}%，"
            f"夏普 {m.get('sharpe', 0):.2f}，最大回撤 {m.get('max_drawdown', 0) * 100:.1f}%，"
            f"{beat}基准 Buy&Hold（夏普 {bm.get('sharpe', 0):.2f}）。"
            f"回测不代表未来收益。"
        )
        payload["next_steps"] = build_next_steps(
            {"action": "compare", "reason": "对比全部策略选出最优",
             "command": f"run_compare.py --symbol {args.symbol} --json"},
            {"action": "optimize", "reason": "寻找最优参数（含 DSR 过拟合诊断）",
             "command": f"run_optimize.py --symbol {args.symbol} --strategy {args.strategy} --json"},
            {"action": "validate", "reason": "样本外验证策略稳健性",
             "command": f"run_validate.py --symbol {args.symbol} --strategy {args.strategy} --json"},
        )
        payload = attach_meta(payload, command="backtest")
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
