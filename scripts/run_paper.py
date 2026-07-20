#!/usr/bin/env python3
"""模拟盘 CLI：每日跑信号 -> 虚拟成交 -> 追踪与回测预期的偏差。

用真实行情做「纸面交易」，检验回测的成本/成交假设是否贴近现实：
- 状态持久化在 ../outputs/paper_<标的>_<策略>.json（虚拟现金/持股/成交流水）；
- 每次运行按最新收盘价执行当日信号（同一交易日重复运行幂等，不会重复成交）；
- 同步用账本引擎重放同区间回测，输出「模拟盘 vs 回测预期」的净值偏差。

示例：
    # 首次运行：初始化 10 万虚拟资金并执行今日信号
    uv run python run_paper.py --symbol 600000.SH --strategy ma_cross --capital 100000

    # 之后每个交易日收盘后运行一次即可
    uv run python run_paper.py --symbol 600000.SH --strategy ma_cross

    # 重置状态重新开始
    uv run python run_paper.py --symbol 600000.SH --strategy ma_cross --reset
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from backtest.costs import CostModel
from backtest.ledger import run_backtest_ledger
from cli_common import check_symbol, make_parser, parse_params, run_cli
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from naming import sanitize
from report import frame_table, print_text
from strategies import STRATEGIES, get_strategy

DISCLAIMER = "模拟盘为纸面交易，仅供研究参考，不构成投资建议。"


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 模拟盘（纸面交易 + 偏差追踪）", __doc__)
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH")
    parser.add_argument("--strategy", required=True, choices=list(STRATEGIES), help="策略名称")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=500, help="K 线数量，默认 500")
    parser.add_argument("--adjust", default="forward", help="复权口径，默认前复权")
    parser.add_argument("--params", nargs="*", default=[], help="策略参数，形如 fast=10 slow=30")
    parser.add_argument("--capital", type=float, default=100_000.0, help="初始虚拟资金（仅首次生效），默认 10 万")
    parser.add_argument("--market", choices=["generic", "astock"], default="astock", help="成本预设，默认 astock")
    parser.add_argument("--lot-size", type=int, default=None, help="最小交易单位；默认 astock=100，其余=1")
    parser.add_argument("--reset", action="store_true", help="重置模拟盘状态重新开始")
    return parser


def state_path(symbol: str, strategy: str) -> Path:
    out_dir = Path(__file__).resolve().parent.parent / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"paper_{sanitize(symbol)}_{sanitize(strategy)}.json"


def load_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    args = parse_args_with_config(build_parser())
    check_symbol(args.symbol)
    params = parse_params(args.params)
    lot = args.lot_size or (100 if args.market == "astock" else 1)
    path = state_path(args.symbol, args.strategy)

    if args.reset and path.exists():
        path.unlink()
        print_text(f"已重置模拟盘状态：{path}")

    print_text(f"拉取 {args.symbol} {args.period} 最新 K 线...")
    df = fetch_ohlcv(
        args.symbol, period=args.period, count=args.count, adjust=args.adjust
    )
    date_col = next(
        (c for c in ("trade_date", "date", "datetime", "time") if c in df.columns), None
    )
    last_date = str(df[date_col].iloc[-1])[:10] if date_col else str(len(df) - 1)
    price = float(df["close"].iloc[-1])

    strategy = get_strategy(args.strategy, **params)
    target_frac = float(strategy.generate_signals(df).astype(float).iloc[-1])

    state = load_state(path)
    if state is None:
        state = {
            "symbol": args.symbol,
            "strategy": args.strategy,
            "params": params,
            "market": args.market,
            "lot_size": lot,
            "initial_capital": args.capital,
            "cash": args.capital,
            "shares": 0,
            "start_date": last_date,
            "last_date": None,
            "trades": [],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        print_text(f"初始化模拟盘：虚拟资金 {args.capital:,.0f}，lot_size={lot}")

    model = CostModel.preset(state["market"])
    if state["last_date"] == last_date:
        print_text(f"今日（{last_date}）已执行过，跳过成交（幂等）。")
    else:
        equity_now = state["cash"] + state["shares"] * price
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
            state["trades"].append(
                {"date": last_date, "action": action, "shares": abs(delta),
                 "price": price, "cost": round(cost, 2)}
            )
            print_text(f"{last_date} {action} {abs(delta)} 股 @ {price:.3f}（成本 {cost:.2f}）")
        else:
            print_text(f"{last_date} 无需调仓（目标仓位 {target_frac:.0%}）。")
        state["last_date"] = last_date
        save_state(path, state)

    # 当前净值与回测预期对比（同区间账本引擎重放）
    equity = state["cash"] + state["shares"] * price
    paper_nav = equity / state["initial_capital"]
    print_text("")
    print_text(f"===== 模拟盘状态（{args.symbol} {strategy.display_name}）=====")
    print_text(f"持股          : {state['shares']} 股（市值 {state['shares'] * price:,.2f}）")
    print_text(f"现金          : {state['cash']:,.2f}")
    print_text(f"净值          : {paper_nav:.4f}（起始日 {state['start_date']}）")
    print_text(f"累计成交      : {len(state['trades'])} 笔")

    if date_col is not None:
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
            print_text(f"回测预期净值  : {exp_nav:.4f}（同区间账本引擎重放）")
            print_text(f"实现偏差      : {(paper_nav - exp_nav) * 100:+.2f}%"
                       "（偏差大意味着执行时点/成本假设与现实有出入）")

    if state["trades"]:
        import pandas as pd

        frame_table(pd.DataFrame(state["trades"]).tail(10), title="最近成交（后 10 笔）")
    print_text(f"\n状态文件：{path}")
    print_text(DISCLAIMER)


if __name__ == "__main__":
    run_cli(main)
