#!/usr/bin/env python3
"""稳健性验证 CLI：走步样本外验证 + 过拟合概率（PBO）。

回答一个核心问题：寻优挑出来的策略，到了没见过的数据上还灵不灵？

示例：
    # 走步样本外验证（滚动重寻优，只在样本外计价）
    uv run python run_validate.py --symbol 600000.SH --strategy ma_cross

    # 加做 PBO（组合对称交叉验证，估计过拟合概率）
    uv run python run_validate.py --symbol AAPL.US --strategy macd --pbo --count 800
"""

from __future__ import annotations

import argparse
import itertools

import pandas as pd

from backtest.costs import CostModel
from backtest.engine import run_backtest
from backtest.rules import TradingRules
from cli_common import check_symbol, make_parser, run_cli
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from report import frame_table, metrics_table
from research.validation import probability_of_backtest_overfitting
from research.walk_forward import walk_forward
from strategies import STRATEGIES


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 稳健性验证（走步 + PBO）", __doc__)
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH")
    parser.add_argument("--strategy", required=True, choices=list(STRATEGIES), help="策略名称")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=800, help="K 线数量，默认 800")
    parser.add_argument("--adjust", default="forward", help="复权口径，默认前复权")
    parser.add_argument("--metric", default="sharpe", help="训练窗选参指标，默认 sharpe")
    parser.add_argument("--train-window", type=int, default=250, help="训练窗长度，默认 250")
    parser.add_argument("--test-window", type=int, default=60, help="测试块长度/步长，默认 60")
    parser.add_argument("--anchored", action="store_true", help="锚定式走步（训练窗起点固定为 0）")
    parser.add_argument("--allow-short", action="store_true", help="开启做空")
    parser.add_argument("--market", choices=["generic", "astock"], default="generic", help="成本预设")
    parser.add_argument("--exec-price", choices=["close", "open"], default="close", help="成交价约定")
    parser.add_argument("--limit-board", choices=["main", "star", "chinext", "st"], default=None, help="A 股涨跌停板块")
    parser.add_argument("--pbo", action="store_true", help="额外计算过拟合概率 PBO")
    parser.add_argument("--pbo-splits", type=int, default=10, help="PBO 的 CSCV 分块数，默认 10")
    return parser


def _pbo_returns_matrix(df, strategy_cls, fixed, cost_model, exec_price, trading_rules, period):
    """对参数网格每个组合在全样本上回测，收集逐周期收益矩阵（周期 × 组合）。"""
    grid = strategy_cls.param_grid
    keys = list(grid.keys())
    cols = {}
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        if "fast" in params and "slow" in params and params["fast"] >= params["slow"]:
            continue
        strat = strategy_cls(**{**fixed, **params})
        res = run_backtest(
            df, strat, period=period,
            cost_model=cost_model, exec_price=exec_price, trading_rules=trading_rules,
        )
        label = ",".join(f"{k}={v}" for k, v in params.items())
        cols[label] = res.returns.reset_index(drop=True)
    return pd.DataFrame(cols)


def main() -> None:
    args = parse_args_with_config(build_parser())
    check_symbol(args.symbol)
    strategy_cls = STRATEGIES[args.strategy]
    fixed = {"allow_short": True} if args.allow_short else {}

    print(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根，复权：{args.adjust}）...")
    df = fetch_ohlcv(args.symbol, period=args.period, count=args.count, adjust=args.adjust)
    print(f"已获取 {len(df)} 根 K 线\n")

    cost_model = CostModel.preset(args.market)
    trading_rules = TradingRules.astock(args.limit_board) if args.limit_board else None

    print(
        f"走步验证：train={args.train_window} / test={args.test_window} / "
        f"{'锚定' if args.anchored else '滚动'}，选参指标={args.metric}\n"
    )
    wf = walk_forward(
        df, strategy_cls, metric=args.metric,
        train_window=args.train_window, test_window=args.test_window,
        anchored=args.anchored, period=args.period, fixed_params=fixed,
        cost_model=cost_model, exec_price=args.exec_price, trading_rules=trading_rules,
    )

    metrics_table(
        {
            "样本外(OOS)": wf.oos_metrics,
            "基准 Buy&Hold（同区间）": wf.benchmark_metrics,
        },
        title=f"{args.symbol} {strategy_cls.display_name} 走步样本外验证",
    )
    print(f"\n共 {len(wf.folds)} 个走步折；各折选参与样本外收益：")
    frame_table(wf.folds)

    oos = wf.oos_metrics
    bench = wf.benchmark_metrics
    verdict = "跑赢" if oos["sharpe"] > bench["sharpe"] else "跑输"
    print(f"\n结论：样本外夏普 {oos['sharpe']:.2f} vs 基准 {bench['sharpe']:.2f}，策略{verdict}基准。")

    if args.pbo:
        print("\n计算过拟合概率 PBO（组合对称交叉验证 CSCV）...")
        matrix = _pbo_returns_matrix(
            df, strategy_cls, fixed, cost_model, args.exec_price, trading_rules, args.period
        )
        if matrix.shape[1] < 2:
            print("  参数组合不足 2 个，跳过 PBO。")
        else:
            out = probability_of_backtest_overfitting(matrix, n_splits=args.pbo_splits)
            print(f"  组合数（配置）: {matrix.shape[1]}")
            print(f"  CSCV 组合数   : {out['n_combinations']}")
            print(f"  PBO           : {out['pbo']:.2%}")
            if out["pbo"] > 0.5:
                print("  ⚠️  PBO > 50%：样本内最优在样本外多半沦为下半区，过拟合风险高。")
            else:
                print("  ✅ PBO <= 50%：样本内择优在样本外仍有一定延续性。")


if __name__ == "__main__":
    main()
