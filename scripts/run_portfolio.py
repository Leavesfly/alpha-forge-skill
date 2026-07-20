#!/usr/bin/env python3
"""多标的组合回测 CLI：拉取多标的数据 -> 轮动权重 -> 组合回测 -> 报告 -> 可选出图。

示例：
    uv run python run_portfolio.py --symbols 600000.SH,000001.SZ,600519.SH --strategy momentum
    uv run python run_portfolio.py --symbols AAPL.US,MSFT.US,TSLA.US --strategy inverse_vol --plot
    uv run python run_portfolio.py --symbols 600000.SH,600519.SH,000858.SZ --strategy momentum \
        --lookback 60 --top-k 2 --rebalance 20
    uv run python run_portfolio.py --symbols 600000.SH,000001.SZ,600519.SH --json > pf.json
"""

from __future__ import annotations

import argparse

from cli_common import (
    add_json_arg,
    emit_json,
    make_logger,
    make_parser,
    run_cli,
    split_symbols,
)
from cli_config import parse_args_with_config
from datafeed import fetch_prices
from naming import default_output
from portfolio import get_weights, run_portfolio_backtest
from portfolio.rotation import ROTATIONS
from report import attach_meta, metrics_table
from risk.attribution import return_contribution
from risk.limits import apply_exposure_limits
from risk.metrics import risk_report


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 多标的组合轮动回测", __doc__)
    parser.add_argument(
        "--symbols",
        required=True,
        help="标的代码，逗号分隔，如 600000.SH,000001.SZ,600519.SH",
    )
    parser.add_argument(
        "--strategy",
        default="momentum",
        choices=list(ROTATIONS),
        help="轮动策略：momentum/equal_weight/inverse_vol",
    )
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=500, help="K 线数量，默认 500")
    parser.add_argument("--lookback", type=int, default=60, help="回看周期（动量/波动率），默认 60")
    parser.add_argument("--top-k", type=int, default=2, help="动量轮动持有标的数，默认 2")
    parser.add_argument("--rebalance", type=int, default=20, help="调仓周期，默认 20")
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0005, help="单边滑点率")
    parser.add_argument("--max-weight", type=float, default=None, help="单标的权重上限（如 0.4）")
    parser.add_argument("--max-gross", type=float, default=None, help="总暴露上限 Σ|w|（如 1.0）")
    parser.add_argument("--risk", action="store_true", help="额外输出组合风险报告（VaR/CVaR/溃疡指数）")
    parser.add_argument("--stress", action="store_true", help="额外输出压力测试（历史情景 + 蒙特卡洛冲击）")
    parser.add_argument("--attribution", action="store_true", help="额外输出各标的收益贡献归因")
    parser.add_argument("--plot", action="store_true", help="生成组合回测图表")
    parser.add_argument("--output", default=None, help="图表输出路径；默认按 ../outputs/portfolio_<策略>_<标的数>syms.png 命名")
    add_json_arg(parser)
    return parser


def build_weight_params(args) -> dict:
    """按策略组织权重函数参数。"""
    params: dict = {"rebalance": args.rebalance}
    if args.strategy == "momentum":
        params.update(lookback=args.lookback, top_k=args.top_k)
    elif args.strategy == "inverse_vol":
        params.update(lookback=args.lookback)
    elif args.strategy == "min_variance":
        params.update(lookback=args.lookback)
    elif args.strategy == "max_sharpe":
        params.update(lookback=args.lookback, period=args.period)
    return params


def main() -> None:
    args = parse_args_with_config(build_parser())
    symbols = split_symbols(args.symbols, min_count=2, what="组合回测")
    json_stdout = args.json == "-"
    log = make_logger(json_stdout)

    log(f"拉取 {len(symbols)} 个标的 {args.period} K 线（{args.count} 根）...")
    prices = fetch_prices(symbols, period=args.period, count=args.count)
    log(f"对齐后共同交易日 {len(prices)} 天，标的：{list(prices.columns)}")

    weights = get_weights(args.strategy, prices, **build_weight_params(args))

    # 事前风控：单标的权重上限 / 总暴露上限
    if args.max_weight is not None or args.max_gross is not None:
        weights = apply_exposure_limits(
            weights, max_weight=args.max_weight, max_gross=args.max_gross
        )
        log(
            f"已施加暴露约束：单标的≤{args.max_weight}，总暴露≤{args.max_gross}"
        )

    result = run_portfolio_backtest(
        prices,
        weights,
        period=args.period,
        commission=args.commission,
        slippage=args.slippage,
    )

    strategy_name = args.strategy
    log()
    metrics_table(
        {f"组合 [{strategy_name}]": result.metrics, "等权基准": result.benchmark_metrics},
        title="组合回测绩效",
        stderr=json_stdout,
    )
    log(f"调仓次数      : {result.rebalance_count}")

    risk_rep = None
    if args.risk:
        risk_rep = risk_report(result.returns, result.equity, period=args.period)
        log("\n===== 组合风险报告 (95%) =====")
        log(f"历史 VaR      : {risk_rep['var'] * 100:.2f}%（单日潜在亏损）")
        log(f"CVaR/ES      : {risk_rep['cvar'] * 100:.2f}%（尾部平均亏损）")
        log(f"参数法 VaR    : {risk_rep['parametric_var'] * 100:.2f}%")
        log(f"年化 VaR      : {risk_rep['annualized_var'] * 100:.2f}%")
        log(f"下行偏差      : {risk_rep['downside_deviation'] * 100:.2f}%")
        log(f"尾部比率      : {risk_rep['tail_ratio']:.2f}")
        log(f"溃疡指数      : {risk_rep['ulcer_index'] * 100:.2f}%")

    contrib = None
    if args.attribution:
        asset_returns = prices.pct_change().fillna(0.0)
        contrib = return_contribution(result.weights, asset_returns)
        log("\n===== 收益贡献归因（按标的）=====")
        for sym, val in contrib.items():
            log(f"  {sym:<12}: {val * 100:+.2f}%")

    if args.stress:
        from report import frame_table
        from risk.stress import stress_tables

        scen_df, mc_df = stress_tables(result.returns)
        log()
        if scen_df.empty:
            log("压力测试：回测区间未覆盖任何预置历史情景。")
        else:
            frame_table(
                scen_df, title="压力测试：历史情景重放",
                pct_cols=("期间收益", "最大回撤"),
                stderr=json_stdout,
            )
        if not mc_df.empty:
            frame_table(
                mc_df, title="压力测试：蒙特卡洛冲击（最大回撤分位）",
                pct_cols=("回撤p50", "回撤p95", "回撤p99"),
                stderr=json_stdout,
            )

    if args.plot:
        from portfolio.plot import plot_portfolio

        output = args.output or default_output("portfolio", args.strategy, f"{len(symbols)}syms")
        path = plot_portfolio(result, strategy_name=strategy_name, output=output)
        log(f"\n图表已保存：{path}")

    if args.json is not None:
        idx = result.equity.index
        payload = attach_meta(
            {
                "symbols": symbols,
                "strategy": strategy_name,
                "range": {
                    "start": str(idx[0]) if len(idx) else None,
                    "end": str(idx[-1]) if len(idx) else None,
                    "num_periods": int(len(idx)),
                },
                "metrics": dict(result.metrics),
                "benchmark_metrics": dict(result.benchmark_metrics),
                "rebalance_count": int(result.rebalance_count),
                "risk": risk_rep,
                "attribution": (
                    {k: float(v) for k, v in contrib.items()} if contrib is not None else None
                ),
            },
            command="portfolio",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
