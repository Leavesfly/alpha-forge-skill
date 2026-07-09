#!/usr/bin/env python3
"""多因子选股 CLI：股票池/数据 -> 因子打分 -> 选股 -> 分层回测 -> 报告 -> 可选出图。

示例：
    # 从 A 股股票池取前 30 只，全因子（财务因子需 API Key 及权限，否则自动跳过）
    uv run python run_factor.py --universe CN_Equity_A --limit 30

    # 仅价格类因子（动量+低波），无需财务权限
    uv run python run_factor.py --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ,600809.SH \
        --factors momentum,low_vol --plot

    # 指定选股分位与分层数
    uv run python run_factor.py --universe CN_Equity_A --limit 50 --top-quantile 0.2 --layers 5
"""

from __future__ import annotations

import argparse

from backtest.metrics import format_report
from datafeed import fetch_fundamentals, fetch_prices, fetch_universe
from factors import FACTORS, run_factor_model
from naming import default_output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpha Forge 多因子选股与分层回测")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--universe", help="股票池名称，如 CN_Equity_A / US_Equity / HK_Equity")
    src.add_argument("--symbols", help="标的代码，逗号分隔（与 --universe 二选一）")
    parser.add_argument("--limit", type=int, default=30, help="股票池成分数量上限，默认 30")
    parser.add_argument(
        "--factors",
        default="",
        help=f"启用因子，逗号分隔，默认全部：{','.join(FACTORS)}",
    )
    parser.add_argument("--top-quantile", type=float, default=0.2, help="选股分位，默认 0.2")
    parser.add_argument("--layers", type=int, default=5, help="分层数，默认 5")
    parser.add_argument("--lookback", type=int, default=60, help="价格因子回看/warmup，默认 60")
    parser.add_argument("--lag-days", type=int, default=60, help="财务因子报告期滞后天数，默认 60")
    parser.add_argument("--rebalance", type=int, default=20, help="调仓周期，默认 20")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=500, help="K 线数量，默认 500")
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0005, help="单边滑点率")
    parser.add_argument("--plot", action="store_true", help="生成多因子回测图表")
    parser.add_argument("--output", default=None, help="图表输出路径；默认按 ../outputs/factor_<股票池或标的数>.png 命名")
    return parser


def resolve_symbols(args) -> list[str]:
    if args.symbols:
        return [s.strip() for s in args.symbols.split(",") if s.strip()]
    return fetch_universe(args.universe, limit=args.limit)


def main() -> None:
    args = build_parser().parse_args()
    factors = [f.strip() for f in args.factors.split(",") if f.strip()] or None

    symbols = resolve_symbols(args)
    if len(symbols) < 3:
        raise SystemExit("多因子选股至少需要 3 个标的。")
    print(f"标的数量：{len(symbols)}")

    print(f"拉取 {args.period} K 线（{args.count} 根）...")
    prices = fetch_prices(symbols, period=args.period, count=args.count)
    print(f"对齐后共同交易日 {len(prices)} 天，有效标的 {prices.shape[1]} 只")

    # 仅当启用了基本面因子时才拉取财务数据，避免无谓请求/限流
    active = factors or list(FACTORS)
    need_fund = any(
        name in FACTORS and FACTORS[name].category != "price" for name in active
    )
    if need_fund:
        print("获取财务指标（用于价值/质量/规模因子）...")
        fundamentals = fetch_fundamentals(list(prices.columns))
    else:
        fundamentals = None

    result = run_factor_model(
        prices,
        fundamentals,
        factors=factors,
        top_quantile=args.top_quantile,
        layers=args.layers,
        lookback=args.lookback,
        lag_days=args.lag_days,
        rebalance=args.rebalance,
        period=args.period,
        commission=args.commission,
        slippage=args.slippage,
    )

    print()
    print(f"启用因子：{', '.join(result.factors_used)}")
    if result.skipped:
        print(f"跳过因子（数据不可用）：{', '.join(result.skipped)}")
    print()
    print(format_report(result.top_portfolio.metrics, title=f"Top {args.top_quantile:.0%} 组合"))
    print(f"调仓次数      : {result.top_portfolio.rebalance_count}")
    print()
    print(format_report(result.top_portfolio.benchmark_metrics, title="等权基准"))

    print("\n=== 分层累计收益（单调性检验，L1=最高分层） ===")
    for i, layer in enumerate(result.layers):
        cum = (layer.equity.iloc[-1] - 1.0) * 100
        print(f"  L{i + 1}: {cum:+.2f}%")

    print(f"\n=== 最新选股（{result.latest_date}，Top {args.top_quantile:.0%}） ===")
    for sym, score in result.latest_picks.items():
        print(f"  {sym}: 综合得分 {score:+.3f}")

    if args.plot:
        from factors.plot import plot_factor

        output = args.output or default_output(
            "factor", f"{len(symbols)}syms" if args.symbols else args.universe
        )
        path = plot_factor(result, title="多因子选股", output=output)
        print(f"\n图表已保存：{path}")


if __name__ == "__main__":
    main()
