#!/usr/bin/env python3
"""机器学习策略 CLI：拉取数据 -> 特征 -> 走步训练/预测 -> 样本外回测 -> 报告 -> 可选出图。

用可插拔模型（LightGBM / Ridge / Logistic）学习技术指标与未来收益方向的关系，
走步（walk-forward）重训练并只在样本外（OOS）段计价，天然规避前视与未来数据泄露。
默认还会跑一个 Ridge 线性基线：LightGBM 若跑不赢线性模型，应视为过拟合警报。

示例：
    uv run python run_ml.py --symbol 600000.SH --count 800 --plot
    uv run python run_ml.py --symbol AAPL.US --count 1000 --horizon 5 --allow-short
    uv run python run_ml.py --symbol 600519.SH --model ridge --count 800
    uv run python run_ml.py --symbol 600000.SH --prob-sizing   # 概率置信度连续仓位
"""

from __future__ import annotations

import argparse

from cli_common import check_symbol, make_parser, run_cli
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from ml import run_ml_strategy
from ml.model import MODELS
from naming import default_output
from report import metrics_table


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 机器学习策略（方向预测 + 走步 OOS）", __doc__)
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=800, help="K 线数量，默认 800（越多样本外越充分）")
    parser.add_argument("--model", choices=list(MODELS), default="lgbm", help="预测模型，默认 lgbm")
    parser.add_argument("--prob-sizing", action="store_true", help="按预测置信度线性映射为连续仓位")
    parser.add_argument("--no-baseline", action="store_true", help="跳过 Ridge 线性基线对照（仅 lgbm 时默认加跑）")
    parser.add_argument("--horizon", type=int, default=5, help="预测的未来收益周期数，默认 5")
    parser.add_argument("--train-window", type=int, default=250, help="走步滚动训练样本数，默认 250")
    parser.add_argument("--test-window", type=int, default=20, help="每次走步向前预测的周期数，默认 20")
    parser.add_argument("--threshold", type=float, default=0.05, help="中性带宽度，默认 0.05（proba 偏离 0.5 超过才入场）")
    parser.add_argument("--allow-short", action="store_true", help="开启做空（预测下跌输出 -1）")
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0005, help="单边滑点率")
    parser.add_argument("--plot", action="store_true", help="生成机器学习策略图表")
    parser.add_argument("--output", default=None, help="图表输出路径；默认按 ../outputs/ml_<标的>.png 命名")
    return parser


def main() -> None:
    args = parse_args_with_config(build_parser())
    check_symbol(args.symbol)

    print(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根）...")
    df = fetch_ohlcv(args.symbol, period=args.period, count=args.count)
    print(f"已获取 {len(df)} 根 K 线，开始走步训练与样本外预测（模型：{args.model}）...")

    ml_kwargs = dict(
        symbol=args.symbol,
        period=args.period,
        horizon=args.horizon,
        train_window=args.train_window,
        test_window=args.test_window,
        threshold=args.threshold,
        allow_short=args.allow_short,
        commission=args.commission,
        slippage=args.slippage,
        prob_sizing=args.prob_sizing,
    )
    result = run_ml_strategy(df, model=args.model, **ml_kwargs)

    metrics = result.backtest.metrics
    print()
    print(f"走步模型数    : {result.n_models}（每块训练一次）")
    print(f"特征维度      : {result.n_features}")
    print(f"样本外起点    : {result.oos_start_label}（该点右侧为 OOS）")
    print()

    named = {
        f"{args.model}（OOS）": metrics,
        "基准 Buy&Hold": result.backtest.benchmark_metrics,
    }

    # 线性基线对照：LightGBM 跑不赢 Ridge 就是过拟合警报
    baseline = None
    if args.model == "lgbm" and not args.no_baseline:
        print("加跑 Ridge 线性基线对照...")
        baseline = run_ml_strategy(df, model="ridge", **ml_kwargs)
        named["ridge 基线（OOS）"] = baseline.backtest.metrics

    metrics_table(named, title=f"{args.symbol} 机器学习策略（样本外 OOS）")

    if baseline is not None:
        lgbm_sharpe = metrics.get("sharpe", 0.0)
        ridge_sharpe = baseline.backtest.metrics.get("sharpe", 0.0)
        if lgbm_sharpe <= ridge_sharpe:
            print(
                f"\n[警惕] LightGBM 样本外夏普 {lgbm_sharpe:.2f} 未跑赢 Ridge 线性基线 "
                f"{ridge_sharpe:.2f}：额外的模型容量没有带来增益，大概率在拟合噪声，"
                "应优先用更简单的模型或重新审视特征。"
            )

    # 回测安全铁律：夏普 > 3 优先怀疑方法论问题
    if metrics.get("sharpe", 0.0) > 3.0:
        print(
            "\n[警惕] 样本外夏普比率 > 3，请优先排查未来数据泄露、过拟合或样本偏差，"
            "而非当作策略有效——真金白银的高频机构才可能合理跑出如此高值。"
        )

    print(f"\n=== 特征重要度 Top 10（{args.model}，走步各模型均值） ===")
    for name, val in result.feature_importance.head(10).items():
        print(f"  {name:14s}: {val:.1f}")

    if args.plot:
        from ml.plot import plot_ml

        output = args.output or default_output("ml", args.symbol)
        path = plot_ml(result, title=f"机器学习策略 {args.symbol}", output=output)
        print(f"\n图表已保存：{path}")


if __name__ == "__main__":
    run_cli(main)
