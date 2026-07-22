#!/usr/bin/env python3
"""低估值/潜力机会全市场筛选 CLI：基本面硬阈值漏斗（PE/PB/ROE/负债/分红/增速）。

定位：用绝对估值/质量/分红/成长阈值从全市场中筛出低估+优质+潜力标的。
与 run_scan.py（趋势动量纪律过滤）和 run_factor.py（多因子截面排名）互补。

A 股走 akshare 免费批量接口（无需 API Key）：
  Phase 1 全市场快照过滤 PE/PB/市值 → Phase 2 逐只深度过滤 ROE/负债/增速。
港美股走 yfinance 逐只拉取（需 --symbols 手动指定）。

筛选基于公开财务快照，不构成投资建议；数据为最近报告期，存在滞后。

示例：
    # A 股全市场默认筛选（PE<20, PB<3, ROE>10%, 市值>30亿）
    uv run python run_screener.py

    # 高分红低估值策略（股息率>3%, PE<15, PB<2）
    uv run python run_screener.py --max-pe 15 --max-pb 2 --min-div 3

    # 成长+质量策略（ROE>15%, 增速>20%, 负债<60%）
    uv run python run_screener.py --min-roe 15 --min-growth 20 --max-debt 60

    # 港美股手动列表筛选
    uv run python run_screener.py --symbols AAPL.US,00700.HK,600519.SH --json

    # 按 ROE 排序，输出前 20 名
    uv run python run_screener.py --sort roe --top 20
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
from report import ProgressBar, attach_meta
from screener import ScreenCriteria, run_screen


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 低估值/潜力机会全市场筛选", __doc__)

    # 数据源
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--symbols", default=None,
        help="手动标的列表（逗号分隔）；港美股必须用此模式",
    )

    # 阈值参数
    parser.add_argument("--max-pe", type=float, default=20.0, help="市盈率上限，默认 20（0=不限）")
    parser.add_argument("--max-pb", type=float, default=3.0, help="市净率上限，默认 3.0（0=不限）")
    parser.add_argument("--min-roe", type=float, default=10.0, help="ROE 下限(%%)，默认 10（0=不限）")
    parser.add_argument("--max-debt", type=float, default=70.0, help="资产负债率上限(%%)，默认 70（0=不限）")
    parser.add_argument("--min-div", type=float, default=0.0, help="股息率下限(%%)，默认 0（0=不限）")
    parser.add_argument("--min-growth", type=float, default=0.0, help="净利润增速下限(%%)，默认 0（0=不限）")
    parser.add_argument("--min-cap", type=float, default=30.0, help="总市值下限(亿)，默认 30")

    # 输出控制
    parser.add_argument("--top", type=int, default=30, help="最多输出达标标的数，默认 30")
    parser.add_argument(
        "--sort", default="score",
        choices=["score", "pe", "pb", "roe", "div", "growth"],
        help="排序字段，默认 score（综合评分）",
    )
    # 估值分位增强
    parser.add_argument(
        "--valuation-pct",
        action="store_true",
        help="启用估值历史分位增强：拉取候选标的近 N 年 PE/PB 历史，计算当前分位并调整评分（较慢）",
    )
    parser.add_argument(
        "--valuation-lookback", type=int, default=5,
        help="估值分位回看年数，默认 5",
    )
    add_json_arg(parser)
    return parser


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout, log = init_log(args)

    criteria = ScreenCriteria(
        max_pe=args.max_pe,
        max_pb=args.max_pb,
        min_roe=args.min_roe,
        max_debt=args.max_debt,
        min_div=args.min_div,
        min_growth=args.min_growth,
        min_cap=args.min_cap,
        use_valuation_pct=args.valuation_pct,
        valuation_lookback=args.valuation_lookback,
    )

    symbols = None
    if args.symbols:
        symbols = split_symbols(args.symbols, min_count=1, what="筛选")

    # 打印筛选条件
    active_dims = _active_dimensions(criteria)
    log(f"筛选条件：{active_dims}")
    if symbols:
        log(f"标的范围：手动列表 {len(symbols)} 只")
    else:
        log("标的范围：A 股全市场（akshare 免费快照）")
    log()

    # 执行筛选
    n_total = len(symbols) if symbols else "全市场"
    with ProgressBar(total=len(symbols) if symbols else 0, description="价值筛选") as bar:
        result = run_screen(
            criteria,
            symbols=symbols,
            top=args.top,
            sort_by=args.sort,
            log=log,
            on_progress=lambda done, _sym: bar.update(done),
        )

    candidates = result["candidates"]
    n_final = result["n_final"]
    n_scanned = result["n_scanned"]

    # 输出结果
    log()
    log(f"===== 达标候选（{n_final} 只，按{_sort_label(args.sort)}排序，前 {len(candidates)} 名）=====")
    if candidates:
        for i, item in enumerate(candidates, 1):
            pe_str = f"PE {item['pe']:.1f}" if item.get("pe") else "PE N/A"
            pb_str = f"PB {item['pb']:.2f}" if item.get("pb") else "PB N/A"
            roe_str = f"ROE {item['roe']:.1f}%" if item.get("roe") else "ROE N/A"
            div_str = f"股息 {item['div_yield']:.1f}%" if item.get("div_yield") else ""
            growth_str = f"增速 {item['profit_growth']:+.0f}%" if item.get("profit_growth") else ""
            # 估值分位（可选）
            val_str = ""
            if item.get("valuation"):
                vp = item["valuation"]
                pcts = []
                if vp.get("pe_percentile") is not None:
                    pcts.append(f"PE{vp['pe_percentile']:.0%}")
                if vp.get("pb_percentile") is not None:
                    pcts.append(f"PB{vp['pb_percentile']:.0%}")
                if pcts:
                    val_str = f"分位 {'/'.join(pcts)}"
            name = item.get("name", "")[:6]
            log(
                f"{i:>3}. {item['symbol']:<12} {name:<8} "
                f"综合 {item['score']:>5.1f}  {pe_str}  {pb_str}  {roe_str}  {div_str}  {growth_str}  {val_str}"
            )
    else:
        log("（无达标标的。当前阈值下全市场无满足条件的标的，可放宽阈值重试。）")

    log("\n提示：筛选基于公开财务快照，不构成投资建议。数据为最近报告期，存在滞后。")
    log_next_steps(
        log,
        "对候选标的做纪律评分复核 run_score.py --symbol <代码>（含技术面确认与交易计划）",
        "回测验证候选标的 run_backtest.py --symbol <代码> --strategy ma_cross",
    )

    # JSON 输出
    if args.json is not None:
        top_sym = candidates[0]["symbol"] if candidates else "无"
        payload = attach_meta(
            {
                "criteria": criteria.to_dict(),
                "n_scanned": n_scanned,
                "n_phase1": result.get("n_phase1"),
                "n_final": n_final,
                "candidates": candidates,
                "summary": (
                    f"扫描 {n_scanned} 只标的：{n_final} 只达标。"
                    f"最优候选：{top_sym}。筛选基于基本面快照，非收益预测。"
                ),
                "next_steps": build_next_steps(
                    {"action": "score", "reason": "对达标候选做技术面纪律评分复核",
                     "command": "run_score.py --symbol <代码> --json"},
                    {"action": "backtest", "reason": "回测验证候选标的策略表现",
                     "command": "run_backtest.py --symbol <代码> --strategy ma_cross --json"},
                ),
            },
            command="screener",
        )
        emit_json(args.json, payload, log)


def _active_dimensions(criteria: ScreenCriteria) -> str:
    """生成人类可读的筛选条件描述。"""
    parts = []
    if criteria.max_pe > 0:
        parts.append(f"PE<{criteria.max_pe:.0f}")
    if criteria.max_pb > 0:
        parts.append(f"PB<{criteria.max_pb:.1f}")
    if criteria.min_roe > 0:
        parts.append(f"ROE>{criteria.min_roe:.0f}%")
    if criteria.max_debt > 0:
        parts.append(f"负债<{criteria.max_debt:.0f}%")
    if criteria.min_div > 0:
        parts.append(f"股息>{criteria.min_div:.1f}%")
    if criteria.min_growth > 0:
        parts.append(f"增速>{criteria.min_growth:.0f}%")
    if criteria.min_cap > 0:
        parts.append(f"市值>{criteria.min_cap:.0f}亿")
    if criteria.use_valuation_pct:
        parts.append(f"估值分位增强(近{criteria.valuation_lookback}年)")
    return "、".join(parts) if parts else "无限制"


def _sort_label(sort_by: str) -> str:
    """排序字段的中文标签。"""
    labels = {
        "score": "综合评分",
        "pe": "PE（低→高）",
        "pb": "PB（低→高）",
        "roe": "ROE",
        "div": "股息率",
        "growth": "增速",
    }
    return labels.get(sort_by, sort_by)


if __name__ == "__main__":
    run_cli(main)
