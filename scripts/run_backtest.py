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
from backtest.metrics import format_report
from datafeed import fetch_ohlcv
from naming import default_output
from strategies import STRATEGIES, get_strategy


def parse_params(pairs: list[str] | None) -> dict:
    """将 ["fast=10", "slow=30"] 解析为 {"fast": 10, "slow": 30}。

    同时兼容空格分隔（``--params fast=10 slow=30``）与
    逗号分隔（``--params fast=10,slow=30``）两种写法。
    """
    result: dict = {}
    for token in pairs or []:
        for item in token.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" not in item:
                raise ValueError(f"参数格式应为 key=value，收到：{item}")
            key, value = item.split("=", 1)
            result[key.strip()] = _cast(value.strip())
    return result


def _cast(value: str):
    """尝试转为 int/float，失败则保留字符串。"""
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            continue
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpha Forge 策略回测")
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
        "--params",
        nargs="*",
        default=[],
        help="策略参数，形如 fast=10 slow=30",
    )
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0005, help="单边滑点率")
    parser.add_argument("--allow-short", action="store_true", help="开启做空（策略输出 -1）")
    parser.add_argument("--stop-loss", type=float, default=None, help="止损比例，如 0.05 表示浮亏 5%%")
    parser.add_argument("--take-profit", type=float, default=None, help="止盈比例，如 0.10 表示浮盈 10%%")
    parser.add_argument("--vol-target", type=float, default=None, help="年化目标波动率，如 0.15（开启连续仓位）")
    parser.add_argument("--vol-window", type=int, default=20, help="波动率滚动窗口，默认 20")
    parser.add_argument("--max-leverage", type=float, default=1.0, help="仓位上限，默认 1.0")
    parser.add_argument("--plot", action="store_true", help="生成回测图表")
    parser.add_argument("--output", default=None, help="图表输出路径；默认按 ../outputs/backtest_<标的>_<策略>.png 命名")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    params = parse_params(args.params)
    if args.allow_short:
        params["allow_short"] = True
    strategy = get_strategy(args.strategy, **params)

    print(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根）...")
    df = fetch_ohlcv(args.symbol, period=args.period, count=args.count)
    print(f"已获取 {len(df)} 根 K 线，策略：{strategy}")

    result = run_backtest(
        df,
        strategy,
        symbol=args.symbol,
        period=args.period,
        commission=args.commission,
        slippage=args.slippage,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        vol_target=args.vol_target,
        vol_window=args.vol_window,
        max_leverage=args.max_leverage,
    )

    print()
    print(format_report(result.metrics, title=f"{args.symbol} {strategy.display_name}"))
    print()
    print(format_report(result.benchmark_metrics, title="基准 Buy & Hold"))

    if args.plot:
        from backtest.plot import plot_result

        output = args.output or default_output("backtest", args.symbol, args.strategy)
        path = plot_result(result, strategy_name=strategy.display_name, output=output)
        print(f"\n图表已保存：{path}")


if __name__ == "__main__":
    main()
