#!/usr/bin/env python3
"""信号服务 CLI：用最新行情跑策略，输出今日调仓指令（研究参考，人工执行）。

这是回测到实盘之间风险最低的一步：不下单、不托管资金，只回答
「按该策略，今天收盘后的目标仓位是多少、相比昨天要怎么调」。

示例：
    # 单标的：今日 ma_cross 信号
    uv run python run_signal.py --symbols 600000.SH --strategy ma_cross

    # 多标的批量 + 指定参数
    uv run python run_signal.py --symbols 600000.SH,000001.SZ,AAPL.US \
        --strategy turtle --params entry=20 exit=10

    # 结构化 JSON（供 agent/脚本消费）
    uv run python run_signal.py --symbols 600000.SH --strategy macd --json
"""

from __future__ import annotations

import argparse

import pandas as pd

from cli_common import (
    add_json_arg,
    emit_json,
    make_logger,
    make_parser,
    parse_params,
    run_cli,
    split_symbols,
)
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from report import attach_meta, frame_table
from strategies import STRATEGIES, get_strategy

DISCLAIMER = "仅供研究参考，不构成投资建议；执行与风险自担。"


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 策略信号服务（不下单）", __doc__)
    parser.add_argument(
        "--symbols", required=True, help="标的代码，逗号分隔，如 600000.SH,AAPL.US"
    )
    parser.add_argument(
        "--strategy", required=True, choices=list(STRATEGIES), help="策略名称"
    )
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=500, help="K 线数量，默认 500")
    parser.add_argument("--adjust", default="forward", help="复权口径，默认前复权")
    parser.add_argument(
        "--params", nargs="*", default=[], help="策略参数，形如 fast=10 slow=30"
    )
    parser.add_argument("--allow-short", action="store_true", help="开启做空")
    parser.add_argument(
        "--no-cache", action="store_true", help="禁用本地缓存（信号场景建议开启以取最新价）"
    )
    add_json_arg(parser)
    return parser


def _action(current: float, target: float) -> str:
    if target > current + 1e-9:
        return "买入/加仓"
    if target < current - 1e-9:
        return "卖出/减仓"
    return "持有" if abs(target) > 1e-9 else "观望"


def latest_signal(df: pd.DataFrame, strategy) -> dict:
    """计算单标的最新信号：当前应持仓位（昨日信号）与目标仓位（今日信号）。"""
    signals = strategy.generate_signals(df).astype(float)
    date_col = next(
        (c for c in ("trade_date", "date", "datetime", "time") if c in df.columns), None
    )
    last_date = str(df[date_col].iloc[-1])[:10] if date_col else "-"
    current = float(signals.iloc[-2]) if len(signals) > 1 else 0.0
    target = float(signals.iloc[-1])
    return {
        "date": last_date,
        "close": float(df["close"].iloc[-1]),
        "current_position": current,
        "target_position": target,
        "action": _action(current, target),
    }


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout = args.json == "-"
    log = make_logger(json_stdout)

    symbols = split_symbols(args.symbols, min_count=1, what="信号服务")
    params = parse_params(args.params)
    if args.allow_short:
        params["allow_short"] = True

    rows = []
    for sym in symbols:
        log(f"拉取 {sym} {args.period} 最新 K 线...")
        df = fetch_ohlcv(
            sym,
            period=args.period,
            count=args.count,
            adjust=args.adjust,
            use_cache=not args.no_cache,
        )
        strategy = get_strategy(args.strategy, **params)
        sig = latest_signal(df, strategy)
        rows.append({"symbol": sym, **sig})

    table = pd.DataFrame(rows)[
        ["symbol", "date", "close", "current_position", "target_position", "action"]
    ].rename(
        columns={
            "symbol": "标的",
            "date": "信号日",
            "close": "收盘价",
            "current_position": "当前仓位",
            "target_position": "目标仓位",
            "action": "动作",
        }
    )
    log("")
    frame_table(
        table,
        title=f"{STRATEGIES[args.strategy].display_name} 今日信号（次日执行）",
        stderr=json_stdout,
    )
    log(f"\n说明：目标仓位为策略在最新收盘后的输出，按惯例次一交易日执行；{DISCLAIMER}")

    if args.json is not None:
        payload = attach_meta(
            {
                "strategy": args.strategy,
                "period": args.period,
                "params": params,
                "signals": rows,
                "disclaimer": DISCLAIMER,
            },
            command="signal",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
