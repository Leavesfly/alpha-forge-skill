#!/usr/bin/env python3
"""模拟盘 CLI：每日跑信号 -> 虚拟成交 -> 追踪与回测预期的偏差。

用真实行情做「纸面交易」，检验回测的成本/成交假设是否贴近现实：
- 状态持久化在 ../outputs/paper_<标的>_<策略>.json（虚拟现金/持股/成交流水）；
- 每次运行按最新收盘价执行当日信号（同一交易日重复运行幂等，不会重复成交）；
- 同步用账本引擎重放同区间回测，输出「模拟盘 vs 回测预期」的净值偏差。

除信号策略外，还支持按**纪律评分**纸面执行（--mode score，决策→跟踪闭环）：
是=按计划建仓，观察=持有不加仓，否/持仓需减风险=离场，无法评分=不动；
状态文件记录每日裁决历史，run_score.py 会自动探测该持仓反向联动。

示例：
    # 首次运行：初始化 10 万虚拟资金并执行今日信号
    uv run python run_paper.py --symbol 600000.SH --strategy ma_cross --capital 100000

    # 之后每个交易日收盘后运行一次即可
    uv run python run_paper.py --symbol 600000.SH --strategy ma_cross

    # 纪律评分模式：按评分结论纸面执行（无需 --strategy）
    uv run python run_paper.py --symbol 600000.SH --mode score

    # 组合级聚合：扫描全部模拟盘状态，输出账户级净值/权重/集中度提示
    uv run python run_paper.py --summary

    # 重置状态重新开始
    uv run python run_paper.py --symbol 600000.SH --strategy ma_cross --reset
"""

from __future__ import annotations

import argparse
import sys
import time

from backtest.costs import CostModel
from backtest.ledger import run_backtest_ledger
from cli_common import (
    add_json_arg,
    build_next_steps,
    check_symbol,
    default_lot_size,
    emit_json,
    init_log,
    log_next_steps,
    make_parser,
    parse_params,
    run_cli,
)
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from paper import load_state, run_summary, save_state, state_path
from report import attach_meta, frame_table
from strategies import STRATEGIES, get_strategy

DISCLAIMER = "模拟盘为纸面交易，仅供研究参考，不构成投资建议。"


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 模拟盘（纸面交易 + 偏差追踪）", __doc__)
    parser.add_argument("--symbol", default=None, help="标的代码，如 600000.SH（--summary 时可省）")
    parser.add_argument(
        "--summary",
        action="store_true",
        help="组合级聚合：扫描全部模拟盘状态文件，输出账户级净值/权重/集中度提示",
    )
    parser.add_argument(
        "--mode",
        default="strategy",
        choices=["strategy", "score"],
        help="执行模式：strategy=信号策略（需 --strategy），score=纪律评分裁决，默认 strategy",
    )
    parser.add_argument("--strategy", default=None, choices=list(STRATEGIES), help="策略名称（strategy 模式必填）")
    parser.add_argument("--benchmark", default=None, help="score 模式的评分基准；默认按市场自动选择")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=1250, help="K 线数量，默认 1250（约 5 年）")
    parser.add_argument("--adjust", default="forward", help="复权口径，默认前复权")
    parser.add_argument("--params", nargs="*", default=[], help="策略参数，形如 fast=10 slow=30")
    parser.add_argument("--capital", type=float, default=100_000.0, help="初始虚拟资金（仅首次生效），默认 10 万")
    parser.add_argument("--market", choices=["generic", "astock"], default="astock", help="成本预设，默认 astock")
    parser.add_argument("--lot-size", type=int, default=None, help="最小交易单位；默认 astock=100，其余=1")
    parser.add_argument("--reset", action="store_true", help="重置模拟盘状态重新开始")
    add_json_arg(parser)
    return parser


def _score_verdict(args, df, state, log):
    """score 模式：跑四层纪律评分，把结论映射为目标仓位。

    映射约定：是=满仓（1.0），否/持仓需减风险=空仓（0.0），
    观察/无法评分=维持现仓（返回 None，不交易）。持仓时把近似成本
    传入评分引擎，使「持仓需减风险」裁决能被触发。
    """
    from scoring import default_benchmark, score_symbol

    bench_symbol = args.benchmark or default_benchmark(args.symbol)
    bench_close = None
    if bench_symbol:
        try:
            import pandas as pd

            bdf = fetch_ohlcv(bench_symbol, period=args.period, count=args.count, adjust=args.adjust)
            bench_close = bdf["close"].astype(float).reset_index(drop=True)
            if "trade_date" in bdf.columns:
                bench_close.index = pd.DatetimeIndex(pd.to_datetime(bdf["trade_date"]))
        except Exception as exc:
            print(f"[warn] 基准 {bench_symbol} 拉取失败（{type(exc).__name__}），降级为无基准评分", file=sys.stderr)
            bench_symbol = None

    position = None
    if state["shares"] > 0:
        cost = (state["initial_capital"] - state["cash"]) / state["shares"]
        if cost > 0:
            position = {"cost": cost, "shares": state["shares"], "source": "paper"}

    res = score_symbol(
        df,
        symbol=args.symbol,
        benchmark_close=bench_close,
        benchmark_symbol=bench_symbol,
        position=position,
    )
    if res.verdict == "yes":
        target = 1.0
    elif res.verdict in ("no", "reduce_risk"):
        target = 0.0
    else:  # watch / unrated：维持现仓，不加仓不减仓
        target = None
    log(f"纪律评分结论：{res.verdict_cn}（排名分 {res.alpha_score}）→ "
        + {1.0: "目标满仓", 0.0: "目标空仓", None: "维持现仓"}[target])
    return target, res


def main() -> None:
    args = parse_args_with_config(build_parser())
    if args.summary:
        run_summary(args)
        return
    if not args.symbol:
        raise SystemExit("[error] 需要 --symbol 指定标的（或用 --summary 看组合总览）")
    check_symbol(args.symbol)
    if args.mode == "strategy" and not args.strategy:
        raise SystemExit(
            "[error] strategy 模式需要 --strategy 指定策略名（run_list.py 可查）；"
            "或改用 --mode score 按纪律评分执行。"
        )
    json_stdout, log = init_log(args)
    params = parse_params(args.params)
    lot = args.lot_size or default_lot_size(args.market)
    tag = args.strategy if args.mode == "strategy" else "score"
    path = state_path(args.symbol, tag)

    if args.reset and path.exists():
        path.unlink()
        log(f"已重置模拟盘状态：{path}")

    log(f"拉取 {args.symbol} {args.period} 最新 K 线...")
    df = fetch_ohlcv(
        args.symbol, period=args.period, count=args.count, adjust=args.adjust
    )
    date_col = next(
        (c for c in ("trade_date", "date", "datetime", "time") if c in df.columns), None
    )
    last_date = str(df[date_col].iloc[-1])[:10] if date_col else str(len(df) - 1)
    price = float(df["close"].iloc[-1])

    state = load_state(path)
    if state is None:
        state = {
            "symbol": args.symbol,
            "strategy": tag,
            "mode": args.mode,
            "params": params,
            "market": args.market,
            "lot_size": lot,
            "initial_capital": args.capital,
            "cash": args.capital,
            "shares": 0,
            "start_date": last_date,
            "last_date": None,
            "trades": [],
            "verdicts": [],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        log(f"初始化模拟盘：虚拟资金 {args.capital:,.0f}，lot_size={lot}")

    # 目标仓位：信号策略或纪律评分裁决
    score_result = None
    if args.mode == "score":
        target_frac, score_result = _score_verdict(args, df, state, log)
        display_name = "纪律评分"
    else:
        strategy = get_strategy(args.strategy, **params)
        target_frac = float(strategy.generate_signals(df).astype(float).iloc[-1])
        display_name = strategy.display_name

    model = CostModel.preset(state["market"])
    executed_today = state["last_date"] == last_date
    if executed_today:
        log(f"今日（{last_date}）已执行过，跳过成交（幂等）。")
    else:
        equity_now = state["cash"] + state["shares"] * price
        if target_frac is None:  # score 模式的「维持现仓」
            desired = state["shares"]
        else:
            desired = int(target_frac * equity_now / (price * state["lot_size"])) * state["lot_size"]
        delta = desired - state["shares"]
        if delta != 0:
            notional = abs(delta) * price
            cost = notional * (model.commission + model.slippage + model.transfer_fee)
            if delta < 0:
                cost += notional * model.stamp_duty
            state["cash"] -= delta * price + cost
            state["shares"] = desired
            action = "买入" if delta > 0 else "卖出"
            trade = {"date": last_date, "action": action, "shares": abs(delta),
                     "price": price, "cost": round(cost, 2)}
            if score_result is not None:
                trade["verdict"] = score_result.verdict
            state["trades"].append(trade)
            log(f"{last_date} {action} {abs(delta)} 股 @ {price:.3f}（成本 {cost:.2f}）")
        else:
            hint = "维持现仓" if target_frac is None else f"目标仓位 {target_frac:.0%}"
            log(f"{last_date} 无需调仓（{hint}）。")
        if score_result is not None:
            state.setdefault("verdicts", []).append(
                {"date": last_date, "verdict": score_result.verdict,
                 "alpha_score": score_result.alpha_score}
            )
        state["last_date"] = last_date
        state.setdefault("equity_history", []).append(
            {"date": last_date, "equity": round(state["cash"] + state["shares"] * price, 2)}
        )
        save_state(path, state)

    # 当前净值与回测预期对比（同区间账本引擎重放；仅信号策略模式）
    equity = state["cash"] + state["shares"] * price
    paper_nav = equity / state["initial_capital"]
    log("")
    log(f"===== 模拟盘状态（{args.symbol} {display_name}）=====")
    log(f"持股          : {state['shares']} 股（市值 {state['shares'] * price:,.2f}）")
    log(f"现金          : {state['cash']:,.2f}")
    log(f"净值          : {paper_nav:.4f}（起始日 {state['start_date']}）")
    log(f"累计成交      : {len(state['trades'])} 笔")
    if score_result is not None and state.get("verdicts"):
        recent = state["verdicts"][-5:]
        log("近期裁决      : " + " → ".join(f"{v['date'][5:]} {v['verdict']}" for v in recent))

    exp_nav = None
    if args.mode == "score":
        log("回测预期基线  : （评分模式无策略基线；评分有效性用 run_score.py --replay 验证）")
    elif date_col is not None:
        # 回放用全量历史（保证指标预热与模拟盘一致），再按起始日归一
        expected = run_backtest_ledger(
            df,
            get_strategy(args.strategy, **params),
            symbol=args.symbol,
            period=args.period,
            initial_capital=state["initial_capital"],
            cost_model=model,
            lot_size=state["lot_size"],
        )
        dates = df[date_col].astype(str).str[:10].to_numpy()
        start_pos = int((dates >= state["start_date"]).argmax())
        eq = expected.equity.reset_index(drop=True)
        if float(eq.iloc[start_pos]) > 0:
            exp_nav = float(eq.iloc[-1] / eq.iloc[start_pos])
            log(f"回测预期净值  : {exp_nav:.4f}（同区间账本引擎重放）")
            log(f"实现偏差      : {(paper_nav - exp_nav) * 100:+.2f}%"
                "（偏差大意味着执行时点/成本假设与现实有出入）")

    if state["trades"]:
        import pandas as pd

        frame_table(
            pd.DataFrame(state["trades"]).tail(10),
            title="最近成交（后 10 笔）",
            stderr=json_stdout,
        )
    log(f"\n状态文件：{path}")
    log(DISCLAIMER)

    if args.mode == "score":
        log_next_steps(
            log,
            "每日收盘后重跑本命令持续追踪评分纪律",
            f"评分详情与回放验证 run_score.py --symbol {args.symbol} --replay 120",
        )
    else:
        log_next_steps(
            log,
            "每日收盘后重跑本命令持续追踪",
            "偏差持续扩大时核对成本/执行价假设（references/live-signal.md）",
        )

    if args.json is not None:
        # Agent 友好：自然语言结论 + 结构化下一步
        nav_pct = (paper_nav - 1) * 100
        verdict_str = f"，评分结论「{score_result.verdict_cn}」" if score_result is not None else ""
        summary = (
            f"{args.symbol} 模拟盘（{tag}）：净值 {paper_nav:.4f}（{nav_pct:+.2f}%）"
            f"{verdict_str}，持股 {state['shares']} 股，现金 {state['cash']:,.0f}。"
            f"仅纸面跟踪，不自动下单。"
        )
        if args.mode == "score":
            ns = build_next_steps(
                {"action": "daily", "reason": "每日收盘后重跑本命令持续追踪",
                 "command": f"run_paper.py --symbol {args.symbol} --mode score --json"},
                {"action": "replay", "reason": "回放验证评分有效性",
                 "command": f"run_score.py --symbol {args.symbol} --replay 120 --json"},
            )
        else:
            ns = build_next_steps(
                {"action": "daily", "reason": "每日收盘后重跑本命令持续追踪",
                 "command": f"run_paper.py --symbol {args.symbol} --strategy {args.strategy} --json"},
                {"action": "validate", "reason": "偏差持续扩大时复核策略稳健性",
                 "command": f"run_validate.py --symbol {args.symbol} --strategy {args.strategy} --json"},
            )
        payload = attach_meta(
            {
                "symbol": args.symbol,
                "mode": args.mode,
                "strategy": tag,
                "params": params,
                "date": last_date,
                "already_executed": bool(executed_today),
                "verdict": score_result.verdict if score_result is not None else None,
                "alpha_score": score_result.alpha_score if score_result is not None else None,
                "cash": float(state["cash"]),
                "shares": int(state["shares"]),
                "market_value": float(state["shares"] * price),
                "paper_nav": float(paper_nav),
                "expected_nav": exp_nav,
                "nav_deviation": (
                    float(paper_nav - exp_nav) if exp_nav is not None else None
                ),
                "trades_count": int(len(state["trades"])),
                "state_file": str(path),
                "disclaimer": DISCLAIMER,
                "summary": summary,
                "next_steps": ns,
            },
            command="paper",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
