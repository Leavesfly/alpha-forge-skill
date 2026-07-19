#!/usr/bin/env python3
"""定投（定期定额）回测 CLI：拉取数据 -> 按周期定投 -> 资金加权收益(IRR) -> 报告 -> 可选出图。

定投按固定周期注入现金、累积份额，靠摊薄成本获利，因此单独用现金流账本建模，
核心指标为资金加权年化收益率（XIRR），并与「一次性投入」「纯定投」两条基准对比。

支持 5 种模式（--mode）：
    fixed      纯定投（每期定额）
    ma         均线加码（低于均线投 boost 倍，单档）
    smart      智能定投（按偏离均线幅度分档加码/减码/暂停）
    dip        超跌回撤加码（按距近期高点回撤深度分档 + RSI 超卖触发）
    value_avg  价值平均（盯住目标市值增长线，涨多可卖出）

示例：
    # 每月纯定投 1000（默认），免费日 K 即可
    uv run python run_dca.py --symbol 600000.SH

    # 智能定投：按偏离 60 日均线分档加码
    uv run python run_dca.py --symbol 600519.SH --mode smart --ma-window 60 --plot

    # 超跌回撤加码
    uv run python run_dca.py --symbol AAPL.US --mode dip --dip-window 120 --count 1000 --plot

    # 价值平均：每期目标市值增长 1000
    uv run python run_dca.py --symbol 600000.SH --mode value_avg --amount 1000 --plot
"""

from __future__ import annotations

import argparse

from datafeed import fetch_ohlcv
from dca import format_dca_report, format_lumpsum_report, run_dca_backtest
from naming import default_output

MODE_DESC = {
    "fixed": "纯定投",
    "ma": "均线加码",
    "smart": "智能定投(分档)",
    "dip": "超跌回撤加码",
    "value_avg": "价值平均",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpha Forge 定投（定期定额）回测")
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH")
    parser.add_argument(
        "--freq",
        default="monthly",
        choices=["daily", "weekly", "monthly"],
        help="定投频率，默认 monthly（每月首个交易日投入）",
    )
    parser.add_argument("--amount", type=float, default=1000.0, help="每期基准投入金额；value_avg 下为每期目标市值增量")
    parser.add_argument(
        "--mode",
        default="fixed",
        choices=["fixed", "ma", "smart", "dip", "value_avg"],
        help="定投模式：fixed/ma/smart/dip/value_avg，默认 fixed",
    )
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=500, help="K 线数量，默认 500")
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0005, help="单边滑点率")
    parser.add_argument("--ma-window", type=int, default=60, help="ma/smart 模式均线窗口，默认 60")
    parser.add_argument("--boost", type=float, default=2.0, help="加码基准倍数（ma 低于均线倍数；smart/dip 分档以此缩放），默认 2.0")
    parser.add_argument("--dip-window", type=int, default=120, help="dip 模式回撤参考高点滚动窗口，默认 120")
    parser.add_argument("--plot", action="store_true", help="生成定投图表")
    parser.add_argument("--output", default=None, help="图表输出路径；默认按 ../outputs/dca_<标的>_<模式>_<频率>.png 命名")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    print(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根）...")
    df = fetch_ohlcv(args.symbol, period=args.period, count=args.count)
    print(f"已获取 {len(df)} 根 K 线，定投频率：{args.freq}，每期 {args.amount}，模式：{MODE_DESC[args.mode]}")

    result = run_dca_backtest(
        df,
        symbol=args.symbol,
        period=args.period,
        freq=args.freq,
        amount=args.amount,
        commission=args.commission,
        slippage=args.slippage,
        mode=args.mode,
        ma_window=args.ma_window,
        boost=args.boost,
        dip_window=args.dip_window,
    )

    print()
    print(format_dca_report(result.metrics, title=f"{args.symbol} 定投·{MODE_DESC[args.mode]}（{args.freq}）"))
    if result.dca_metrics is not None:
        print()
        print(format_dca_report(result.dca_metrics, title="基准 A：纯定投（同参数）"))
    print()
    title = "基准 B：一次性投入" if result.dca_metrics is not None else "一次性投入基准（同等本金期初买入）"
    print(format_lumpsum_report(result.lumpsum_metrics, title=title))

    if args.plot:
        from dca.plot import plot_dca

        output = args.output or default_output("dca", args.symbol, args.mode, args.freq)
        path = plot_dca(
            result, title=f"{args.symbol} 定投·{MODE_DESC[args.mode]}（{args.freq}）", output=output
        )
        print(f"\n图表已保存：{path}")


if __name__ == "__main__":
    main()
