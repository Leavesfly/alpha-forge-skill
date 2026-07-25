#!/usr/bin/env python3
"""全市场扫描 CLI：流动性初筛 -> 批量买点三灯 -> 达标/降级候选分列。

扫描是纪律过滤，不是收益预测或选股 alpha 排名。达标候选（结论「趋势买点」且
趋势分达标）按趋势分排序输出；被否决/降级的候选单独列出主要原因；
拉取失败的标的跳过不中断。批量扫描不拉估值数据（价灯灰，仅覆盖势/时），
入选者建议再跑 run_score.py 补全价维度。数据获取走本地缓存，二次扫描不重复拉网。

示例：
    # 从股票池扫描（需 TICKFLOW_API_KEY 的股票池权限）
    uv run python run_scan.py --universe CN_Equity_A --limit 50

    # 手动标的列表扫描（免费日 K 即可）
    uv run python run_scan.py --symbols 600000.SH,600519.SH,000858.SZ,AAPL.US

    # 流动性初筛保留前 30 名，取趋势分前 10 的候选，结构化输出
    uv run python run_scan.py --universe CN_Equity_A --limit 100 --pool 30 --top 10 --json
"""

from __future__ import annotations

import argparse

from cli_common import (
    add_json_arg,
    build_next_steps,
    emit_json,
    init_log,
    log_next_steps,
    make_parser,
    run_cli,
    split_symbols,
)
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv, fetch_universe
from report import ProgressBar, attach_meta
from scoring import scan_symbols


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 全市场扫描（买点三灯漏斗）", __doc__)
    parser.add_argument("--symbols", default=None, help="逗号分隔的标的列表（与 --universe 二选一）")
    parser.add_argument("--universe", default=None, help="股票池名称，如 CN_Equity_A（需 API Key）")
    parser.add_argument("--limit", type=int, default=50, help="股票池最多取多少只，默认 50")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=1250, help="每标的 K 线数量，默认 1250（约 5 年）")
    parser.add_argument("--pool", type=int, default=None, help="流动性初筛保留标的数（按近 20 日均成交额）；默认不过滤")
    parser.add_argument("--top", type=int, default=20, help="达标候选最多输出数，默认 20")
    parser.add_argument("--min-score", type=float, default=60.0, help="达标候选最低趋势分，默认 60")
    parser.add_argument(
        "--exclude-held",
        action="store_true",
        help="排除统一账户（run_account.py）已持有的标的；缺省仅标注「已持有」不排除",
    )
    add_json_arg(parser)
    return parser


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout, log = init_log(args)

    if args.symbols:
        symbols = split_symbols(args.symbols, min_count=2, what="扫描")
    elif args.universe:
        log(f"拉取股票池 {args.universe}（前 {args.limit} 只）...")
        symbols = fetch_universe(args.universe, limit=args.limit)
        if not symbols:
            raise SystemExit(
                f"[error] 股票池 {args.universe} 为空或不可用；"
                "可改用 --symbols 手动指定标的，或检查 TICKFLOW_API_KEY 权限。"
            )
    else:
        raise SystemExit("[error] 需要 --symbols 或 --universe 之一指定扫描范围。")

    log(f"开始扫描 {len(symbols)} 个标的（period={args.period}, count={args.count}）...")

    # 同市场基准只拉一次；单个基准失败降级为无基准评分
    bench_cache: dict = {}

    def fetch_benchmark(bench_sym: str):
        if bench_sym not in bench_cache:
            try:
                df = fetch_ohlcv(bench_sym, period=args.period, count=args.count)
                import pandas as pd

                close = df["close"].astype(float).reset_index(drop=True)
                if "trade_date" in df.columns:
                    close.index = pd.DatetimeIndex(pd.to_datetime(df["trade_date"]))
                bench_cache[bench_sym] = close
            except Exception as exc:
                log(f"[warn] 基准 {bench_sym} 拉取失败（{type(exc).__name__}），相关标的降级为无基准评分")
                bench_cache[bench_sym] = None
        return bench_cache[bench_sym]

    with ProgressBar(total=len(symbols), description="买点三灯") as bar:
        result = scan_symbols(
            symbols,
            fetch=lambda s: fetch_ohlcv(s, period=args.period, count=args.count),
            fetch_benchmark=fetch_benchmark,
            pool=args.pool,
            min_score=args.min_score,
            on_progress=lambda done, _sym: bar.update(done),
        )

    candidates = result["candidates"][: args.top]

    # 统一账户联动：标注已持有标的，--exclude-held 时从候选中排除
    from account import held_symbols

    try:
        held = set(held_symbols())
    except RuntimeError:  # 账户文件损坏不阻断扫描
        held = set()
    for item in candidates:
        item["held"] = item["symbol"] in held
    excluded_held = []
    if args.exclude_held and held:
        excluded_held = [c for c in candidates if c["held"]]
        candidates = [c for c in candidates if not c["held"]]

    log()
    log(f"===== 达标候选（结论「趋势买点」且趋势分 ≥ {args.min_score:.0f}，前 {len(candidates)} 名）=====")
    if candidates:
        for i, item in enumerate(candidates, 1):
            plan = item.get("plan") or {}
            plan_str = (
                f" 入场 {plan['entry']} / 止损 {plan['stop']} / 2R {plan['target_2r']}"
                if plan
                else ""
            )
            held_tag = "（已持有）" if item.get("held") else ""
            log(f"{i:>3}. {item['symbol']:<12} 趋势分 {item['trend_score']:>5.1f} 收盘 {item['close']}{plan_str}{held_tag}")
    else:
        log("（无。纪律过滤本就苛刻：宁可错过，不可逆势。）")
    if excluded_held:
        log(f"（已排除 {len(excluded_held)} 个账户已持标的："
            + ", ".join(c["symbol"] for c in excluded_held) + "）")

    rejected = result["rejected"]
    log(f"\n===== 被否决/降级候选（{len(rejected)} 个，信息不丢失）=====")
    for item in rejected[:30]:
        score = f"{item['trend_score']:.1f}" if item["trend_score"] is not None else "N/A"
        log(f"  {item['symbol']:<12} {item['verdict_cn']:<6} 趋势分 {score:>5}  {item['reason']}")
    if len(rejected) > 30:
        log(f"  ...（其余 {len(rejected) - 30} 个见 --json 输出）")

    if result["filtered"]:
        log(f"\n流动性初筛淘汰 {len(result['filtered'])} 个（--pool {args.pool}）")
    if result["skipped"]:
        log(f"\n跳过 {len(result['skipped'])} 个标的（拉取失败/数据不足）：")
        for item in result["skipped"][:10]:
            log(f"  {item['symbol']}: {item['reason']}")

    log("\n提示：扫描是纪律过滤而非收益预测；价灯未评估，入选者请用 run_score 补全价维度。")
    log_next_steps(
        log,
        "单标的详情复核 run_score.py --symbol <代码>（含价灯估值分位、交易计划与 --replay 回放）",
        "候选标的纸面跟踪 run_paper.py --symbol <代码> --mode score",
    )

    if args.json is not None:
        n_pass = len(candidates)
        n_reject = len(rejected)
        top_sym = candidates[0]["symbol"] if candidates else "无"
        payload = attach_meta(
            {
                "universe": args.universe,
                "n_symbols": len(symbols),
                "period": args.period,
                "count": args.count,
                "min_score": args.min_score,
                "pool": args.pool,
                "candidates": candidates,
                "excluded_held": [c["symbol"] for c in excluded_held],
                "rejected": rejected,
                "filtered": result["filtered"],
                "skipped": result["skipped"],
                "summary": (
                    f"扫描 {len(symbols)} 只标的：{n_pass} 只达标、{n_reject} 只被否决/降级。"
                    f"最优候选：{top_sym}。扫描仅覆盖势/时维度（价灯未评估），是纪律过滤而非收益预测。"
                ),
                "next_steps": build_next_steps(
                    {"action": "score", "reason": "对达标候选单标的复核（补全价维度与交易计划）",
                     "command": "run_score.py --symbol <代码> --json"},
                    {"action": "paper", "reason": "对候选标的纸面跟踪",
                     "command": "run_paper.py --symbol <代码> --mode score --json"},
                ),
            },
            command="scan",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
