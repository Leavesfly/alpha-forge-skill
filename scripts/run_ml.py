#!/usr/bin/env python3
"""机器学习策略 CLI：拉取数据 -> 特征 -> 走步训练/预测 -> 样本外回测 -> 报告 -> 可选出图。

用 LightGBM 学习技术指标与未来收益方向的关系，走步（walk-forward）重训练并
只在样本外（OOS）段计价，天然规避前视与未来数据泄露。

示例：
    uv run python run_ml.py --symbol 600000.SH --count 800 --plot
    uv run python run_ml.py --symbol AAPL.US --count 1000 --horizon 5 --allow-short
    uv run python run_ml.py --symbol 600519.SH --count 800 --train-window 300 --threshold 0.08
"""

from __future__ import annotations

import argparse

from backtest.metrics import format_report
from datafeed import fetch_ohlcv
from ml import run_ml_strategy
from naming import default_output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpha Forge 机器学习策略（LightGBM 方向预测 + 走步 OOS）")
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=800, help="K 线数量，默认 800（越多样本外越充分）")
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
    args = build_parser().parse_args()

    print(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根）...")
    df = fetch_ohlcv(args.symbol, period=args.period, count=args.count)
    print(f"已获取 {len(df)} 根 K 线，开始走步训练与样本外预测...")

    result = run_ml_strategy(
        df,
        symbol=args.symbol,
        period=args.period,
        horizon=args.horizon,
        train_window=args.train_window,
        test_window=args.test_window,
        threshold=args.threshold,
        allow_short=args.allow_short,
        commission=args.commission,
        slippage=args.slippage,
    )

    metrics = result.backtest.metrics
    print()
    print(f"走步模型数    : {result.n_models}（每块训练一次）")
    print(f"特征维度      : {result.n_features}")
    print(f"样本外起点    : {result.oos_start_label}（该点右侧为 OOS）")
    print()
    print(format_report(metrics, title=f"{args.symbol} 机器学习（样本外 OOS）"))
    print()
    print(format_report(result.backtest.benchmark_metrics, title="基准 Buy & Hold"))

    # 回测安全铁律：夏普 > 3 优先怀疑方法论问题
    if metrics.get("sharpe", 0.0) > 3.0:
        print(
            "\n[警惕] 样本外夏普比率 > 3，请优先排查未来数据泄露、过拟合或样本偏差，"
            "而非当作策略有效——真金白银的高频机构才可能合理跑出如此高值。"
        )

    print("\n=== 特征重要度 Top 10（走步各模型均值） ===")
    for name, val in result.feature_importance.head(10).items():
        print(f"  {name:14s}: {val:.1f}")

    if args.plot:
        from ml.plot import plot_ml

        output = args.output or default_output("ml", args.symbol)
        path = plot_ml(result, title=f"机器学习策略 {args.symbol}", output=output)
        print(f"\n图表已保存：{path}")


if __name__ == "__main__":
    main()
