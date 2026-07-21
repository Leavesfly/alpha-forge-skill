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
    # 三重障碍标签（止盈/止损/最长持有期先触发者定标签，更贴近真实交易）
    uv run python run_ml.py --symbol 600000.SH --label triple --pt-mult 2 --sl-mult 1
    # meta-labeling：二级模型过滤 ma_cross 的假信号（对比过滤前后 OOS 绩效）
    uv run python run_ml.py --symbol 600000.SH --meta ma_cross --count 800
"""

from __future__ import annotations

import argparse

from cli_common import (
    add_cost_args,
    add_json_arg,
    build_next_steps,
    check_symbol,
    emit_json,
    init_log,
    make_parser,
    run_cli,
)
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from ml import run_meta_strategy, run_ml_strategy
from ml.model import LABEL_MODES, MODELS
from naming import default_output
from report import attach_meta, metrics_table
from strategies import STRATEGIES, get_strategy


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 机器学习策略（方向预测 + 走步 OOS）", __doc__)
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=800, help="K 线数量，默认 800（越多样本外越充分）")
    parser.add_argument("--model", choices=list(MODELS), default="lgbm", help="预测模型，默认 lgbm")
    parser.add_argument(
        "--label",
        choices=list(LABEL_MODES),
        default="fixed",
        help="标签模式：fixed(固定持有期方向，默认) / triple(三重障碍：止盈/止损/最长持有期)",
    )
    parser.add_argument("--pt-mult", type=float, default=2.0, help="triple 止盈障碍宽度（×波动率），默认 2.0")
    parser.add_argument("--sl-mult", type=float, default=1.0, help="triple 止损障碍宽度（×波动率），默认 1.0")
    parser.add_argument(
        "--meta",
        default=None,
        choices=list(STRATEGIES),
        help="meta-labeling 模式：二级模型过滤指定一级策略的假信号（如 ma_cross）",
    )
    parser.add_argument("--prob-sizing", action="store_true", help="按预测置信度线性映射为连续仓位")
    parser.add_argument("--no-baseline", action="store_true", help="跳过 Ridge 线性基线对照（仅 lgbm 时默认加跑）")
    parser.add_argument("--horizon", type=int, default=5, help="预测的未来收益周期数，默认 5")
    parser.add_argument("--train-window", type=int, default=250, help="走步滚动训练样本数，默认 250")
    parser.add_argument("--test-window", type=int, default=20, help="每次走步向前预测的周期数，默认 20")
    parser.add_argument("--threshold", type=float, default=0.05, help="中性带宽度，默认 0.05（proba 偏离 0.5 超过才入场）")
    parser.add_argument("--allow-short", action="store_true", help="开启做空（预测下跌输出 -1）")
    add_cost_args(parser)
    parser.add_argument("--plot", action="store_true", help="生成机器学习策略图表")
    parser.add_argument("--output", default=None, help="图表输出路径；默认按 ../outputs/ml_<标的>.png 命名")
    add_json_arg(parser)
    return parser


def run_meta(args, df, log, json_stdout) -> None:
    """meta-labeling 子流程：一级策略信号 → 二级模型过滤 → 前后对比。"""
    strategy = get_strategy(args.meta)
    base_signals = strategy.generate_signals(df.reset_index(drop=True)).astype(float)
    log(f"一级策略 {strategy.display_name} 信号已生成，走步训练二级过滤模型（{args.model}）...")
    result = run_meta_strategy(
        df,
        base_signals,
        symbol=args.symbol,
        period=args.period,
        horizon=args.horizon,
        train_window=args.train_window,
        test_window=args.test_window,
        threshold=args.threshold,
        commission=args.commission,
        slippage=args.slippage,
        model=args.model,
        pt_mult=args.pt_mult,
        sl_mult=args.sl_mult,
    )

    base_m = result.base_backtest.metrics
    filt_m = result.filtered_backtest.metrics
    log()
    log(f"走步模型数    : {result.n_models}")
    log(f"样本外起点    : {result.oos_start_label}")
    log(f"信号过滤      : 原始 {result.n_signals} 个持仓 bar，被过滤 {result.n_filtered} 个")
    log()
    metrics_table(
        {
            f"原始 {strategy.display_name}（OOS）": base_m,
            f"meta 过滤后（OOS）": filt_m,
            "基准 Buy&Hold": result.base_backtest.benchmark_metrics,
        },
        title=f"{args.symbol} meta-labeling（{args.meta} × {args.model}）",
        stderr=json_stdout,
    )
    improved = filt_m.get("sharpe", 0.0) > base_m.get("sharpe", 0.0)
    log(
        "\n过滤后夏普 " + ("提升" if improved else "未提升")
        + "：meta-labeling 只在一级策略本身有正期望时才可能增益，"
        "未提升时应优先换一级策略而非调二级模型。"
    )

    if args.json is not None:
        payload = attach_meta(
            {
                "symbol": args.symbol,
                "mode": "meta",
                "base_strategy": args.meta,
                "model": args.model,
                "horizon": args.horizon,
                "n_models": int(result.n_models),
                "n_signals": int(result.n_signals),
                "n_filtered": int(result.n_filtered),
                "oos_start": str(result.oos_start_label),
                "base_metrics": dict(base_m),
                "filtered_metrics": dict(filt_m),
                "benchmark_metrics": dict(result.base_backtest.benchmark_metrics),
                "summary": (
                    f"{args.symbol} meta-labeling（{args.meta}）：过滤 {result.n_filtered}/"
                    f"{result.n_signals} 个持仓 bar，样本外夏普 "
                    f"{base_m.get('sharpe', 0):.2f} → {filt_m.get('sharpe', 0):.2f}，"
                    + ("过滤有正增益。" if improved else "过滤未增益，一级策略本身可能无正期望。")
                    + "样本外结果，回测不代表未来。"
                ),
                "next_steps": build_next_steps(
                    {"action": "validate", "reason": "对一级策略做走步样本外验证",
                     "command": f"run_validate.py --symbol {args.symbol} --strategy {args.meta} --json"},
                    {"action": "compare", "reason": "换其他一级策略对比",
                     "command": f"run_compare.py --symbol {args.symbol} --json"},
                ),
            },
            command="ml",
        )
        emit_json(args.json, payload, log)


def main() -> None:
    args = parse_args_with_config(build_parser())
    check_symbol(args.symbol)
    json_stdout, log = init_log(args)

    log(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根）...")
    df = fetch_ohlcv(args.symbol, period=args.period, count=args.count)

    if args.meta:
        run_meta(args, df, log, json_stdout)
        return

    log(f"已获取 {len(df)} 根 K 线，开始走步训练与样本外预测（模型：{args.model}）...")

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
        label=args.label,
        pt_mult=args.pt_mult,
        sl_mult=args.sl_mult,
    )
    result = run_ml_strategy(df, model=args.model, **ml_kwargs)

    metrics = result.backtest.metrics
    log()
    log(f"走步模型数    : {result.n_models}（每块训练一次）")
    log(f"特征维度      : {result.n_features}")
    log(f"样本外起点    : {result.oos_start_label}（该点右侧为 OOS）")
    log()

    named = {
        f"{args.model}（OOS）": metrics,
        "基准 Buy&Hold": result.backtest.benchmark_metrics,
    }

    # 线性基线对照：LightGBM 跑不赢 Ridge 就是过拟合警报
    baseline = None
    if args.model == "lgbm" and not args.no_baseline:
        log("加跑 Ridge 线性基线对照...")
        baseline = run_ml_strategy(df, model="ridge", **ml_kwargs)
        named["ridge 基线（OOS）"] = baseline.backtest.metrics

    metrics_table(
        named, title=f"{args.symbol} 机器学习策略（样本外 OOS）", stderr=json_stdout
    )

    if baseline is not None:
        lgbm_sharpe = metrics.get("sharpe", 0.0)
        ridge_sharpe = baseline.backtest.metrics.get("sharpe", 0.0)
        if lgbm_sharpe <= ridge_sharpe:
            log(
                f"\n[警惕] LightGBM 样本外夏普 {lgbm_sharpe:.2f} 未跑赢 Ridge 线性基线 "
                f"{ridge_sharpe:.2f}：额外的模型容量没有带来增益，大概率在拟合噪声，"
                "应优先用更简单的模型或重新审视特征。"
            )

    # 回测安全铁律：夏普 > 3 优先怀疑方法论问题
    if metrics.get("sharpe", 0.0) > 3.0:
        log(
            "\n[警惕] 样本外夏普比率 > 3，请优先排查未来数据泄露、过拟合或样本偏差，"
            "而非当作策略有效——真金白银的高频机构才可能合理跑出如此高值。"
        )

    log(f"\n=== 特征重要度 Top 10（{args.model}，走步各模型均值） ===")
    for name, val in result.feature_importance.head(10).items():
        log(f"  {name:14s}: {val:.1f}")

    if args.plot:
        from ml.plot import plot_ml

        output = args.output or default_output("ml", args.symbol)
        path = plot_ml(result, title=f"机器学习策略 {args.symbol}", output=output)
        log(f"\n图表已保存：{path}")

    if args.json is not None:
        sharpe = metrics.get("sharpe", 0)
        bm_sharpe = result.backtest.benchmark_metrics.get("sharpe", 0)
        beat = "跑赢" if sharpe > bm_sharpe else "跑输"
        warn = ""
        if sharpe > 3.0:
            warn = "夏普>3，优先怀疑过拟合或数据泄露。"
        payload = attach_meta(
            {
                "symbol": args.symbol,
                "period": args.period,
                "model": args.model,
                "label": args.label,
                "horizon": args.horizon,
                "n_models": int(result.n_models),
                "n_features": int(result.n_features),
                "oos_start": str(result.oos_start_label),
                "metrics": dict(metrics),
                "benchmark_metrics": dict(result.backtest.benchmark_metrics),
                "baseline_metrics": (
                    dict(baseline.backtest.metrics) if baseline is not None else None
                ),
                "feature_importance_top": {
                    str(k): float(v)
                    for k, v in result.feature_importance.head(10).items()
                },
                "summary": (
                    f"{args.symbol} 机器学习策略（{args.model}）样本外回测："
                    f"夏普 {sharpe:.2f}，{beat}基准（夏普 {bm_sharpe:.2f}）。"
                    f"{warn}样本外结果，回测不代表未来。"
                ),
                "next_steps": build_next_steps(
                    {"action": "backtest", "reason": "用经典策略回测对比",
                     "command": f"run_backtest.py --symbol {args.symbol} --strategy ma_cross --json"},
                    {"action": "validate", "reason": "走步样本外进一步验证",
                     "command": f"run_validate.py --symbol {args.symbol} --strategy ma_cross --json"},
                ),
            },
            command="ml",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
