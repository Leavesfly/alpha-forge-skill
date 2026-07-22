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

    # 推送今日信号到 webhook（钉钉/企微/飞书机器人自动适配；配合 cron 每日定时）
    uv run python run_signal.py --symbols 600000.SH,600519.SH --strategy ma_cross \
        --notify https://oapi.dingtalk.com/robot/send?access_token=xxx --notify-only-changes
"""

from __future__ import annotations

import argparse
import os

import pandas as pd

from cli_common import (
    add_json_arg,
    build_next_steps,
    emit_json,
    init_log,
    log_next_steps,
    make_parser,
    parse_params,
    run_cli,
    split_symbols,
)
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from notify import send_webhook
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
    parser.add_argument("--count", type=int, default=1250, help="K 线数量，默认 1250（约 5 年）")
    parser.add_argument("--adjust", default="forward", help="复权口径，默认前复权")
    parser.add_argument(
        "--params", nargs="*", default=[], help="策略参数，形如 fast=10 slow=30"
    )
    parser.add_argument("--allow-short", action="store_true", help="开启做空")
    parser.add_argument(
        "--no-cache", action="store_true", help="禁用本地缓存（信号场景建议开启以取最新价）"
    )
    parser.add_argument(
        "--notify",
        default=None,
        help="webhook 地址，把今日信号推送到钉钉/企微/飞书机器人或通用 webhook；"
        "也可用环境变量 ALPHA_FORGE_WEBHOOK 配置",
    )
    parser.add_argument(
        "--notify-only-changes",
        action="store_true",
        help="仅当存在买入/卖出动作时才推送（避免每日无意义打扰）",
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
    json_stdout, log = init_log(args)

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

    # webhook 推送（失败只告警不中断）
    webhook = args.notify or os.environ.get("ALPHA_FORGE_WEBHOOK")
    notified = None
    if webhook:
        changes = [r for r in rows if r["action"] in ("买入/加仓", "卖出/减仓")]
        if args.notify_only_changes and not changes:
            log("无买卖动作，按 --notify-only-changes 约定跳过推送。")
        else:
            lines = [
                f"{r['symbol']} {r['date']}：{r['action']}（目标仓位 {r['target_position']:.0%}，"
                f"收盘 {r['close']:.3f}）"
                for r in rows
            ]
            text = (
                f"【{STRATEGIES[args.strategy].display_name}】今日信号（次日执行）\n"
                + "\n".join(lines)
                + f"\n{DISCLAIMER}"
            )
            notified = send_webhook(webhook, text, title="Alpha Forge 每日信号")
            log("webhook 推送成功。" if notified else "webhook 推送失败（见 stderr 告警）。")

    log_next_steps(
        log,
        f"虚拟资金演练并逐日追踪 run_paper.py --symbol <代码> --strategy {args.strategy}",
        "每日收盘后重跑本命令即可巡检最新信号",
    )

    if args.json is not None:
        # 构建自然语言摘要：统计各动作数量
        action_counts: dict = {}
        for r in rows:
            action_counts[r["action"]] = action_counts.get(r["action"], 0) + 1
        action_desc = "、".join(f"{k} {v} 只" for k, v in action_counts.items())
        payload = attach_meta(
            {
                "strategy": args.strategy,
                "period": args.period,
                "params": params,
                "signals": rows,
                "notified": notified,
                "disclaimer": DISCLAIMER,
                "summary": (
                    f"{args.strategy} 策略今日信号（{len(rows)} 只标的）：{action_desc}。"
                    f"仅供研究参考，不构成投资建议。"
                ),
                "next_steps": build_next_steps(
                    {"action": "paper", "reason": "用模拟盘纸面跟踪信号表现",
                     "command": f"run_paper.py --symbol <代码> --strategy {args.strategy} --json"},
                    {"action": "score", "reason": "用纪律评分复核某只标的",
                     "command": "run_score.py --symbol <代码> --json"},
                ),
            },
            command="signal",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
