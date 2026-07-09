#!/usr/bin/env python3
"""参数寻优 CLI：对策略参数网格搜索并按指定指标排序输出。

示例：
    uv run python run_optimize.py --symbol 600000.SH --strategy ma_cross
    uv run python run_optimize.py --symbol AAPL.US --strategy rsi --metric calmar --top 5
"""

from __future__ import annotations

import argparse

import pandas as pd

from backtest.optimize import grid_search
from datafeed import fetch_ohlcv
from strategies import STRATEGIES

# 展示用的关键指标列
DISPLAY_METRICS = [
    "total_return",
    "annual_return",
    "sharpe",
    "max_drawdown",
    "calmar",
    "win_rate",
    "num_trades",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpha Forge 策略参数寻优")
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
        "--metric",
        default="sharpe",
        help="排序指标：sharpe/total_return/annual_return/calmar/win_rate",
    )
    parser.add_argument("--top", type=int, default=10, help="展示前 N 组，默认 10")
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0005, help="单边滑点率")
    parser.add_argument("--allow-short", action="store_true", help="开启做空（策略输出 -1）")
    parser.add_argument("--stop-loss", type=float, default=None, help="止损比例，如 0.05")
    parser.add_argument("--take-profit", type=float, default=None, help="止盈比例，如 0.10")
    parser.add_argument("--vol-target", type=float, default=None, help="年化目标波动率，如 0.15")
    parser.add_argument("--vol-window", type=int, default=20, help="波动率滚动窗口，默认 20")
    parser.add_argument("--max-leverage", type=float, default=1.0, help="仓位上限，默认 1.0")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    strategy_cls = STRATEGIES[args.strategy]

    print(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根）...")
    df = fetch_ohlcv(args.symbol, period=args.period, count=args.count)
    print(f"已获取 {len(df)} 根 K 线，开始寻优（按 {args.metric} 排序）...\n")

    table = grid_search(
        df,
        strategy_cls,
        symbol=args.symbol,
        period=args.period,
        metric=args.metric,
        commission=args.commission,
        slippage=args.slippage,
        top_n=args.top,
        fixed_params={"allow_short": True} if args.allow_short else None,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        vol_target=args.vol_target,
        vol_window=args.vol_window,
        max_leverage=args.max_leverage,
    )

    param_cols = [c for c in strategy_cls.param_grid.keys() if c in table.columns]
    cols = param_cols + [m for m in DISPLAY_METRICS if m in table.columns]

    # 百分比列格式化便于阅读
    show = table[cols].copy()
    for col in ("total_return", "annual_return", "max_drawdown", "win_rate"):
        if col in show.columns:
            show[col] = (show[col] * 100).round(2)

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 160)
    print(f"===== {args.symbol} {strategy_cls.display_name} 寻优结果（Top {args.top}）=====")
    print("（收益/回撤/胜率单位为 %）\n")
    print(show.to_string(index=False))

    best = table.iloc[0]
    best_params = {c: _clean(best[c]) for c in param_cols}
    print(f"\n最优参数（{args.metric}={best[args.metric]:.4f}）：{best_params}")


def _clean(value):
    """将 numpy 标量转为原生类型，整数值的浮点转为 int。"""
    v = getattr(value, "item", lambda: value)()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


if __name__ == "__main__":
    main()
