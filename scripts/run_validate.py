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

from backtest.engine import run_backtest
from cli_common import (
    add_cost_args,
    add_json_arg,
    add_market_args,
    build_cost_and_rules,
    build_next_steps,
    check_symbol,
    emit_json,
    init_log,
    make_parser,
    run_cli,
)
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from report import attach_meta, frame_records, frame_table, metrics_table
from research.validation import probability_of_backtest_overfitting
from research.walk_forward import walk_forward
from strategies import STRATEGIES


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 稳健性验证（走步 + PBO）", __doc__)
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH")
    parser.add_argument("--strategy", required=True, choices=list(STRATEGIES), help="策略名称")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=1250, help="K 线数量，默认 1250（约 5 年）")
    parser.add_argument("--adjust", default="forward", help="复权口径，默认前复权")
    parser.add_argument("--metric", default="sharpe", help="训练窗选参指标，默认 sharpe")
    parser.add_argument("--train-window", type=int, default=250, help="训练窗长度，默认 250")
    parser.add_argument("--test-window", type=int, default=60, help="测试块长度/步长，默认 60")
    parser.add_argument("--anchored", action="store_true", help="锚定式走步（训练窗起点固定为 0）")
    parser.add_argument("--allow-short", action="store_true", help="开启做空")
    add_cost_args(parser)
    add_market_args(parser)
    parser.add_argument("--pbo", action="store_true", help="额外计算过拟合概率 PBO")
    parser.add_argument("--pbo-splits", type=int, default=10, help="PBO 的 CSCV 分块数，默认 10")
    add_json_arg(parser)
    return parser


def _pbo_returns_matrix(df, strategy_cls, fixed, cost_model, exec_price, trading_rules, period):
    """对参数网格每个组合在全样本上回测，收集逐周期收益矩阵（周期 × 组合）。"""
    grid = strategy_cls.param_grid
    keys = list(grid.keys())
    cols = {}
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        try:
            strat = strategy_cls(**{**fixed, **params})
        except ValueError:
            continue  # 非法参数组合（如 fast >= slow）直接跳过
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
    json_stdout, log = init_log(args)
    strategy_cls = STRATEGIES[args.strategy]
    fixed = {"allow_short": True} if args.allow_short else {}

    log(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根，复权：{args.adjust}）...")
    df = fetch_ohlcv(args.symbol, period=args.period, count=args.count, adjust=args.adjust)
    log(f"已获取 {len(df)} 根 K 线\n")

    cost_model, trading_rules = build_cost_and_rules(args)

    log(
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
        stderr=json_stdout,
    )
    log(f"\n共 {len(wf.folds)} 个走步折；各折选参与样本外收益：")
    frame_table(wf.folds, stderr=json_stdout)

    oos = wf.oos_metrics
    bench = wf.benchmark_metrics
    verdict = "跑赢" if oos["sharpe"] > bench["sharpe"] else "跑输"
    log(f"\n结论：样本外夏普 {oos['sharpe']:.2f} vs 基准 {bench['sharpe']:.2f}，策略{verdict}基准。")

    pbo_out = None
    if args.pbo:
        log("\n计算过拟合概率 PBO（组合对称交叉验证 CSCV）...")
        matrix = _pbo_returns_matrix(
            df, strategy_cls, fixed, cost_model, args.exec_price, trading_rules, args.period
        )
        if matrix.shape[1] < 2:
            log("  参数组合不足 2 个，跳过 PBO。")
        else:
            out = probability_of_backtest_overfitting(matrix, n_splits=args.pbo_splits)
            pbo_out = {
                "n_configs": int(matrix.shape[1]),
                "n_combinations": int(out["n_combinations"]),
                "pbo": float(out["pbo"]),
            }
            log(f"  组合数（配置）: {matrix.shape[1]}")
            log(f"  CSCV 组合数   : {out['n_combinations']}")
            log(f"  PBO           : {out['pbo']:.2%}")
            if out["pbo"] > 0.5:
                log("  ⚠️  PBO > 50%：样本内最优在样本外多半沦为下半区，过拟合风险高。")
            else:
                log("  ✅ PBO <= 50%：样本内择优在样本外仍有一定延续性。")

    if args.json is not None:
        pbo_str = ""
        if pbo_out:
            pbo_str = f"PBO={pbo_out['pbo']:.0%}（{'过拟合风险高' if pbo_out['pbo'] > 0.5 else '延续性尚可'}）。"
        payload = attach_meta(
            {
                "symbol": args.symbol,
                "strategy": args.strategy,
                "period": args.period,
                "train_window": args.train_window,
                "test_window": args.test_window,
                "anchored": bool(args.anchored),
                "oos_metrics": dict(wf.oos_metrics),
                "benchmark_metrics": dict(wf.benchmark_metrics),
                "folds": frame_records(wf.folds),
                "verdict": verdict,
                "pbo": pbo_out,
                "summary": (
                    f"{args.symbol} {args.strategy} 走步样本外验证："
                    f"OOS 夏普 {oos['sharpe']:.2f} vs 基准 {bench['sharpe']:.2f}，策略{verdict}基准。"
                    f"{pbo_str}以样本外为准，回测不代表未来。"
                ),
                "next_steps": build_next_steps(
                    {"action": "backtest", "reason": "用验证过的参数复跑并出报告",
                     "command": f"run_backtest.py --symbol {args.symbol} --strategy {args.strategy} --report --json"},
                    {"action": "paper", "reason": "开始纸面跟踪验证过的策略",
                     "command": f"run_paper.py --symbol {args.symbol} --strategy {args.strategy} --json"},
                ),
            },
            command="validate",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
