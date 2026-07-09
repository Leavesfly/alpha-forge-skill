#!/usr/bin/env python3
"""多标的组合回测 CLI：拉取多标的数据 -> 轮动权重 -> 组合回测 -> 报告 -> 可选出图。

示例：
    uv run python run_portfolio.py --symbols 600000.SH,000001.SZ,600519.SH --strategy momentum
    uv run python run_portfolio.py --symbols AAPL.US,MSFT.US,TSLA.US --strategy inverse_vol --plot
    uv run python run_portfolio.py --symbols 600000.SH,600519.SH,000858.SZ --strategy momentum \
        --lookback 60 --top-k 2 --rebalance 20
"""

from __future__ import annotations

import argparse

from backtest.metrics import format_report
from datafeed import fetch_prices
from naming import default_output
from portfolio import get_weights, run_portfolio_backtest
from portfolio.rotation import ROTATIONS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpha Forge 多标的组合轮动回测")
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
    parser.add_argument("--plot", action="store_true", help="生成组合回测图表")
    parser.add_argument("--output", default=None, help="图表输出路径；默认按 ../outputs/portfolio_<策略>_<标的数>syms.png 命名")
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
    args = build_parser().parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if len(symbols) < 2:
        raise SystemExit("组合回测至少需要 2 个标的（--symbols 逗号分隔）。")

    print(f"拉取 {len(symbols)} 个标的 {args.period} K 线（{args.count} 根）...")
    prices = fetch_prices(symbols, period=args.period, count=args.count)
    print(f"对齐后共同交易日 {len(prices)} 天，标的：{list(prices.columns)}")

    weights = get_weights(args.strategy, prices, **build_weight_params(args))
    result = run_portfolio_backtest(
        prices,
        weights,
        period=args.period,
        commission=args.commission,
        slippage=args.slippage,
    )

    strategy_name = args.strategy
    print()
    print(format_report(result.metrics, title=f"组合 [{strategy_name}]"))
    print(f"调仓次数      : {result.rebalance_count}")
    print()
    print(format_report(result.benchmark_metrics, title="等权基准"))

    if args.plot:
        from portfolio.plot import plot_portfolio

        output = args.output or default_output("portfolio", args.strategy, f"{len(symbols)}syms")
        path = plot_portfolio(result, strategy_name=strategy_name, output=output)
        print(f"\n图表已保存：{path}")


if __name__ == "__main__":
    main()
