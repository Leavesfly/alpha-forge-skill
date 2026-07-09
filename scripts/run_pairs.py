#!/usr/bin/env python3
"""配对交易 CLI：手动/自动选对 -> 价差 z-score 开平仓 -> 市场中性回测 -> 报告 -> 可选出图。

示例：
    # 手动指定一对（如两只银行股）
    uv run python run_pairs.py --symbols 600000.SH,601398.SH --plot

    # 从股票池自动筛选最佳配对
    uv run python run_pairs.py --universe CN_Equity_A --limit 40 --top-pairs 3

    # 自定义开平仓阈值
    uv run python run_pairs.py --symbols 600000.SH,601398.SH --entry 2.5 --exit 0.3 --stop 4.0
"""

from __future__ import annotations

import argparse

import numpy as np

from backtest.metrics import format_report
from datafeed import fetch_prices, fetch_universe
from naming import default_output
from pairs import hedge_ratio, pair_signals, pair_spread, pair_weights, select_pairs, zscore
from portfolio import run_portfolio_backtest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpha Forge 配对交易（市场中性统计套利）")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--symbols", help="手动指定一对标的，逗号分隔，如 600000.SH,601398.SH")
    src.add_argument("--universe", help="股票池名称，自动筛选配对，如 CN_Equity_A")
    parser.add_argument("--limit", type=int, default=40, help="股票池成分上限，默认 40")
    parser.add_argument("--top-pairs", type=int, default=3, help="自动筛选返回的候选对数，默认 3")
    parser.add_argument("--min-corr", type=float, default=0.7, help="配对相关性阈值，默认 0.7")
    parser.add_argument("--lookback", type=int, default=60, help="z-score 滚动窗口，默认 60")
    parser.add_argument("--entry", type=float, default=2.0, help="开仓 z 阈值，默认 2.0")
    parser.add_argument("--exit", type=float, default=0.5, help="平仓 z 阈值，默认 0.5")
    parser.add_argument("--stop", type=float, default=3.5, help="止损 z 阈值，默认 3.5")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=500, help="K 线数量，默认 500")
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0005, help="单边滑点率")
    parser.add_argument("--plot", action="store_true", help="生成配对交易图表")
    parser.add_argument("--output", default=None, help="图表输出路径；默认按 ../outputs/pairs_<标的A>_<标的B>.png 命名")
    return parser


def choose_pair(args):
    """返回 (pair_prices, a, b, beta, extra_info)。"""
    if args.symbols:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
        if len(syms) != 2:
            raise SystemExit("手动模式需恰好 2 个标的，例如 --symbols 600000.SH,601398.SH")
        prices = fetch_prices(syms, period=args.period, count=args.count)
        a, b = syms[0], syms[1]
        beta = hedge_ratio(np.log(prices[a]), np.log(prices[b]))
        return prices[[a, b]], a, b, beta, None

    print(f"从股票池 {args.universe} 拉取成分并筛选配对...")
    symbols = fetch_universe(args.universe, limit=args.limit)
    prices = fetch_prices(symbols, period=args.period, count=args.count)
    pairs = select_pairs(prices, top_n=args.top_pairs, min_corr=args.min_corr)
    if not pairs:
        raise SystemExit("未筛选到满足条件的配对，可降低 --min-corr 或增大 --limit。")
    print(f"候选配对（按半衰期升序，共 {len(pairs)} 对）：")
    for p in pairs:
        print(f"  {p.a} ~ {p.b}: 相关性 {p.corr:.3f}, beta {p.beta:.3f}, 半衰期 {p.half_life:.1f} 天")
    best = pairs[0]
    return prices[[best.a, best.b]], best.a, best.b, best.beta, best


def main() -> None:
    args = build_parser().parse_args()
    pair_prices, a, b, beta, _info = choose_pair(args)
    pair_prices = pair_prices.dropna()

    spread = pair_spread(pair_prices, a, b, beta)
    position = pair_signals(spread, args.lookback, args.entry, args.exit, args.stop)
    weights = pair_weights(pair_prices, a, b, position)

    result = run_portfolio_backtest(
        pair_prices, weights, period=args.period,
        commission=args.commission, slippage=args.slippage,
    )

    opens = int(((position.shift(1).fillna(0.0) == 0.0) & (position != 0.0)).sum())
    z = zscore(spread, args.lookback)

    print()
    print(f"配对：多 {a} / 空 {b}（对冲比率 beta={beta:.3f}）")
    print(format_report(result.metrics, title=f"配对组合 [{a}~{b}]"))
    print(f"开仓次数      : {opens}")
    print(f"当前持仓      : {'多价差' if position.iloc[-1] > 0 else '空价差' if position.iloc[-1] < 0 else '空仓'}"
          f"（最新 z={z.iloc[-1]:.2f}）")

    if args.plot:
        from pairs.plot import plot_pairs

        output = args.output or default_output("pairs", a, b)
        path = plot_pairs(
            z, position, result.equity, title=f"配对交易 {a}~{b}",
            entry=args.entry, exit=args.exit, stop=args.stop, output=output,
        )
        print(f"\n图表已保存：{path}")


if __name__ == "__main__":
    main()
