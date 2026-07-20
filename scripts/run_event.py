#!/usr/bin/env python3
"""事件研究 CLI：给定事件日期列表，输出事件窗 AAR/CAAR 与可选 CAAR 曲线。

示例：
    # 两次财报日的事件反应（默认窗口 [-10, +20] 交易日）
    uv run python run_event.py --symbol 600000.SH --events 2025-04-30,2025-08-30

    # 相对指数基准的超额反应 + 出图
    uv run python run_event.py --symbol 600519.SH --events 2025-04-25 \
        --benchmark 510300.SH --pre -5 --post 15 --plot
"""

from __future__ import annotations

import argparse

import pandas as pd

from cli_common import check_symbol, make_parser, run_cli
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from naming import default_output
from report import frame_table, print_text
from research.event_study import event_study


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 事件研究（AAR/CAAR）", __doc__)
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH")
    parser.add_argument(
        "--events", required=True, help="事件日期，逗号分隔，如 2025-04-30,2025-08-30"
    )
    parser.add_argument("--benchmark", default=None, help="基准标的（算超额收益），如 510300.SH")
    parser.add_argument("--pre", type=int, default=-10, help="事件窗起点（相对交易日），默认 -10")
    parser.add_argument("--post", type=int, default=20, help="事件窗终点（相对交易日），默认 20")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=800, help="K 线数量，默认 800")
    parser.add_argument("--adjust", default="forward", help="复权口径，默认前复权")
    parser.add_argument("--plot", action="store_true", help="绘制 AAR/CAAR 曲线")
    parser.add_argument("--output", default=None, help="图表输出路径；默认自动命名")
    return parser


def _close_series(symbol: str, args) -> pd.Series:
    df = fetch_ohlcv(symbol, period=args.period, count=args.count, adjust=args.adjust)
    date_col = next(
        (c for c in ("trade_date", "date", "datetime", "time") if c in df.columns), None
    )
    if date_col is None:
        raise SystemExit("数据缺少时间列，无法做事件研究。")
    return pd.Series(
        df["close"].astype(float).to_numpy(), index=pd.to_datetime(df[date_col])
    )


def main() -> None:
    args = parse_args_with_config(build_parser())
    check_symbol(args.symbol)
    if args.benchmark:
        check_symbol(args.benchmark)
    events = [e.strip() for e in args.events.split(",") if e.strip()]
    if not events:
        raise SystemExit(
            "[error] --events 不能为空，格式为逗号分隔的日期，如 2025-04-30,2025-08-30"
        )

    print_text(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根）...")
    prices = _close_series(args.symbol, args)
    bench = None
    if args.benchmark:
        print_text(f"拉取基准 {args.benchmark} ...")
        bench = _close_series(args.benchmark, args)

    out = event_study(
        prices, events, window=(args.pre, args.post), benchmark=bench
    )
    table = out["table"]

    mode = "超额（相对基准）" if bench is not None else "原始"
    print_text(
        f"\n事件数：{out['n_used']} 个参与统计，{out['n_skipped']} 个因窗口不完整被剔除；"
        f"收益口径：{mode}"
    )
    show = table.reset_index()
    frame_table(
        show,
        title=f"{args.symbol} 事件窗 AAR/CAAR（[{args.pre}, +{args.post}] 交易日）",
        pct_cols=("AAR", "CAAR"),
    )
    frame_table(
        out["per_event"].reset_index(),
        title="各事件窗口累计（超额）收益",
        pct_cols=("cum_abnormal_return",),
    )
    caar_end = float(table["CAAR"].iloc[-1])
    print_text(f"\n结论：事件后至 +{args.post} 日的平均累计{mode}收益为 {caar_end * 100:+.2f}%。")
    print_text("注意：小样本事件研究噪声很大，事件数 < 10 时结论仅供参考。")

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.rcParams["font.sans-serif"] = [
            "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
            "Arial Unicode MS", "DejaVu Sans",
        ]
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.bar(table.index, table["AAR"] * 100, alpha=0.4, label="AAR (%)")
        ax.plot(table.index, table["CAAR"] * 100, color="#c0392b", label="CAAR (%)")
        ax.axvline(0, color="#7f8c8d", linestyle="--", linewidth=1)
        ax.set_xlabel("相对事件日（交易日）")
        ax.set_title(f"{args.symbol} 事件研究（{out['n_used']} 个事件，{mode}收益）")
        ax.legend(loc="best")
        ax.grid(True, alpha=0.3)
        from pathlib import Path

        output = args.output or default_output("event", args.symbol, f"{out['n_used']}ev")
        out_path = Path(output).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.tight_layout()
        fig.savefig(str(out_path), dpi=120, bbox_inches="tight")
        plt.close(fig)
        print_text(f"\n图表已保存：{out_path}")


if __name__ == "__main__":
    run_cli(main)
