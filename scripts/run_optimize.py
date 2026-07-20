#!/usr/bin/env python3
"""参数寻优 CLI：对策略参数网格搜索并按指定指标排序输出。

示例：
    uv run python run_optimize.py --symbol 600000.SH --strategy ma_cross
    uv run python run_optimize.py --symbol AAPL.US --strategy rsi --metric calmar --top 5
    uv run python run_optimize.py --symbol 600000.SH --strategy ma_cross --json > opt.json
"""

from __future__ import annotations

import argparse

from backtest.costs import CostModel
from backtest.engine import run_backtest
from backtest.metrics import periods_per_year
from backtest.optimize import grid_search
from backtest.rules import TradingRules
from cli_common import (
    add_json_arg,
    check_symbol,
    emit_json,
    make_logger,
    make_parser,
    run_cli,
)
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from report import ProgressBar, attach_meta, frame_records, frame_table
from research.validation import deflated_sharpe_ratio, sharpe_stats
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
    parser = make_parser("Alpha Forge 策略参数寻优", __doc__)
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
        choices=["sharpe", "sortino", "total_return", "annual_return", "calmar", "win_rate"],
        help="排序指标，默认 sharpe",
    )
    parser.add_argument("--top", type=int, default=10, help="展示前 N 组，默认 10")
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="并行进程数；默认取 CPU 核数，1 为串行",
    )
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0005, help="单边滑点率")
    parser.add_argument(
        "--market",
        choices=["generic", "astock"],
        default="generic",
        help="成本预设：generic(默认) / astock(A股卖出印花税 + 双边过户费)",
    )
    parser.add_argument(
        "--exec-price",
        choices=["close", "open"],
        default="close",
        help="成交价约定：close(默认) / open(次日开盘成交)",
    )
    parser.add_argument(
        "--limit-board",
        choices=["main", "star", "chinext", "st"],
        default=None,
        help="启用 A 股涨跌停/停牌规则并指定板块",
    )
    parser.add_argument("--allow-short", action="store_true", help="开启做空（策略输出 -1）")
    parser.add_argument("--stop-loss", type=float, default=None, help="止损比例，如 0.05")
    parser.add_argument("--take-profit", type=float, default=None, help="止盈比例，如 0.10")
    parser.add_argument("--vol-target", type=float, default=None, help="年化目标波动率，如 0.15")
    parser.add_argument("--vol-window", type=int, default=20, help="波动率滚动窗口，默认 20")
    parser.add_argument("--max-leverage", type=float, default=1.0, help="仓位上限，默认 1.0")
    add_json_arg(parser)
    return parser


def main() -> None:
    args = parse_args_with_config(build_parser())
    check_symbol(args.symbol)
    json_stdout = args.json == "-"
    log = make_logger(json_stdout)
    strategy_cls = STRATEGIES[args.strategy]

    log(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根）...")
    df = fetch_ohlcv(args.symbol, period=args.period, count=args.count)
    log(f"已获取 {len(df)} 根 K 线，开始寻优（按 {args.metric} 排序）...\n")

    cost_model = CostModel.preset(
        args.market, commission=args.commission, slippage=args.slippage
    )
    trading_rules = (
        TradingRules.astock(args.limit_board) if args.limit_board else None
    )

    table = None
    with ProgressBar(total=1, description="参数寻优") as bar:
        table = grid_search(
            df,
            strategy_cls,
            symbol=args.symbol,
            period=args.period,
            metric=args.metric,
            top_n=args.top,
            fixed_params={"allow_short": True} if args.allow_short else None,
            stop_loss=args.stop_loss,
            take_profit=args.take_profit,
            vol_target=args.vol_target,
            vol_window=args.vol_window,
            max_leverage=args.max_leverage,
            cost_model=cost_model,
            exec_price=args.exec_price,
            trading_rules=trading_rules,
            n_jobs=args.jobs,
            progress=bar.update,
        )

    param_cols = [c for c in strategy_cls.param_grid.keys() if c in table.columns]
    cols = param_cols + [m for m in DISPLAY_METRICS if m in table.columns]

    frame_table(
        table[cols],
        title=f"{args.symbol} {strategy_cls.display_name} 寻优结果（Top {args.top}，按 {args.metric} 排序）",
        pct_cols=("total_return", "annual_return", "max_drawdown", "win_rate"),
        stderr=json_stdout,
    )

    best = table.iloc[0]
    best_params = {c: _clean(best[c]) for c in param_cols}
    log(f"\n最优参数（{args.metric}={best[args.metric]:.4f}）：{best_params}")

    # 过拟合诊断：Deflated Sharpe Ratio（对寻优试验次数做惩罚）
    ann = periods_per_year(args.period)
    trial_sr = (table["sharpe"] / (ann ** 0.5)).to_numpy()
    fixed = {"allow_short": True} if args.allow_short else {}
    best_res = run_backtest(
        df, strategy_cls(**{**fixed, **best_params}), period=args.period,
        cost_model=cost_model, exec_price=args.exec_price, trading_rules=trading_rules,
    )
    stats = sharpe_stats(best_res.returns)
    dsr = deflated_sharpe_ratio(
        trial_sr, n=stats.n, skew=stats.skew, kurtosis=stats.kurtosis
    )
    log("\n过拟合诊断（Deflated Sharpe Ratio）：")
    log(f"  寻优试验数 N : {dsr['n_trials']}")
    log(f"  期望最大夏普 : {dsr['sr_star']:.4f}（纯噪声在 N 次试验下可达的逐周期值）")
    log(f"  DSR         : {dsr['dsr']:.2%}")
    if dsr["dsr"] < 0.90:
        log("  ⚠️  DSR < 90%：最优参数很可能是寻优的运气，建议用 run_validate.py 做走步样本外验证。")
    else:
        log("  ✅ DSR >= 90%：在多重检验惩罚后仍显著。")

    if args.json is not None:
        payload = attach_meta(
            {
                "symbol": args.symbol,
                "strategy": args.strategy,
                "metric": args.metric,
                "num_periods": int(len(df)),
                "results": frame_records(table[cols]),
                "best_params": best_params,
                "best_metric_value": float(best[args.metric]),
                "dsr": {
                    "n_trials": int(dsr["n_trials"]),
                    "sr_star": float(dsr["sr_star"]),
                    "dsr": float(dsr["dsr"]),
                },
            },
            command="optimize",
        )
        emit_json(args.json, payload, log)


def _clean(value):
    """将 numpy 标量转为原生类型，整数值的浮点转为 int。"""
    v = getattr(value, "item", lambda: value)()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


if __name__ == "__main__":
    run_cli(main)
