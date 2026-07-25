#!/usr/bin/env python3
"""低估值/潜力机会全市场筛选 CLI：基本面硬阈值漏斗（PE/PB/ROE/负债/分红/增速/现金流/位置）。

定位：用绝对估值/质量/分红/成长阈值从全市场中筛出低估+优质+潜力标的。
与 run_scan.py（趋势动量纪律过滤）和 run_factor.py（多因子截面排名）互补。

A 股走 akshare 免费批量接口（无需 API Key）：
  Phase 1 全市场快照过滤 PE/PB/市值 → Phase 2 逐只深度过滤 ROE/负债/增速/现金流
  → Phase 3 位置过滤（仅启用 --max-price-pos 时，逐只拉日 K）
  → 技术面过滤（仅启用猛兽股维度时：高位/均线多头/RS 线/量价/大势）。
港美股走 yfinance 逐只拉取（需 --symbols 手动指定）。

内置预设（--preset，显式参数可覆盖预设项）：
- multibagger：十倍股统计特征筛选，取自 Yartseva(2025) 464 只美股十倍股实证
  与 Alta Fox(2020) 研究：小市值(15~200亿) + 便宜(PB<1.6) + 财务健康(ROE>5%)
  + 现金流收益率>6% + 聪明增长(资产增速<利润增速) + 52 周区间下半部(左侧)。
- hundredbagger：百倍股质量成长筛选，取自迈耶《如何找到100倍回报的股票》
  365 只百倍股研究(1962-2014)：高 ROE(>20%) + 营收/利润双高增(>15%)
  + 小市值起点(15~100亿) + 低杠杆(负债<60%) + 合理价格(PE<50，不卡 PB)。
  与 multibagger 的关键差异：质量成长路线（不要求便宜、不左侧择时，
  买对拿住靠长期复利）vs 便宜左侧路线。
- monster：猛兽股右侧强势筛选，取自波伊克《猛兽股》(Monster Stocks, 2007)
  年内翻倍股的必要条件与共同特征：大势确认上行(必要条件，基准站上
  MA50/MA200) + 盈利高增领导股(增速>25%, ROE>15%) + 52 周区间上四分之一
  (买强不买弱) + RS 线跑赢基准 + 上涨放量下跌缩量(量比>1.2) + 沿 MA50
  上行的多头结构。与两个左侧/质量预设互补：monster 是右侧趋势追踪，
  大势不对时纪律性空仓不筛（书中纪律：猛兽股几乎只在新一轮升势中产生）。
  注意：这些是历史翻倍股的统计共性，不是收益预测；命中靠组合持有而非单点押注。

筛选基于公开财务快照，不构成投资建议；数据为最近报告期，存在滞后。

注意事项：
- 默认负债率上限 70% 会剔除银行/保险/券商（金融业负债率普遍 85%~93%），
  如需纳入金融股请加 --max-debt 0（或调高阈值）。
- 静态低 PE 可能是周期股盈利顶部的假象（煤炭/航运/养殖等），
  建议对周期行业加 --valuation-pct 用估值历史分位交叉验证。
- 聪明增长维度（--smart-growth）依赖资产增速数据，仅 A 股支持；
  港美股会因数据缺失被剔除。

示例：
    # A 股全市场默认筛选（PE<20, PB<3, ROE>10%, 负债<70%, 市值>30亿）
    uv run python run_screener.py

    # 十倍股特征筛选（小市值+便宜+现金流好+聪明增长+低位左侧）
    uv run python run_screener.py --preset multibagger

    # 百倍股质量成长筛选（高 ROE+双高增+小市值+低杠杆，迈耶书中标准）
    uv run python run_screener.py --preset hundredbagger

    # 猛兽股右侧强势筛选（大势确认+盈利高增+接近新高+RS 强势+量价吸筹）
    uv run python run_screener.py --preset monster

    # 十倍股预设 + 局部调整（显式参数覆盖预设：放宽市值上限到 300 亿）
    uv run python run_screener.py --preset multibagger --max-cap 300

    # 高分红低估值策略（股息率>3%, PE<15, PB<2）
    uv run python run_screener.py --max-pe 15 --max-pb 2 --min-div 3

    # 成长+质量策略（ROE>15%, 增速>20%, 负债<60%）
    uv run python run_screener.py --min-roe 15 --min-growth 20 --max-debt 60

    # 纳入银行/保险等高杠杆金融股（放开负债率维度）
    uv run python run_screener.py --max-debt 0

    # 港美股手动列表筛选
    uv run python run_screener.py --symbols AAPL.US,00700.HK,600519.SH --json

    # 按 ROE 排序，输出前 20 名
    uv run python run_screener.py --sort roe --top 20
"""

from __future__ import annotations

import argparse
import sys

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
from screener import PRESETS, ScreenCriteria, run_screen


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 低估值/潜力机会全市场筛选", __doc__)

    # 数据源
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--symbols", default=None,
        help="手动标的列表（逗号分隔）；港美股必须用此模式",
    )

    # 预设方案
    parser.add_argument(
        "--preset", default=None, choices=sorted(PRESETS),
        help="预设筛选方案：multibagger=十倍股统计特征（小市值+便宜+现金流+聪明增长+低位）；hundredbagger=百倍股质量成长（高ROE+营收/利润双高增+小市值+低杠杆，迈耶书中标准）；monster=猛兽股右侧强势（大势确认+盈利高增+接近新高+RS跑赢基准+量价吸筹，波伊克书中标准）；显式参数可覆盖预设项",
    )

    # 阈值参数
    parser.add_argument("--max-pe", type=float, default=20.0, help="市盈率上限，默认 20（0=不限）")
    parser.add_argument("--max-pb", type=float, default=3.0, help="市净率上限，默认 3.0（0=不限）")
    parser.add_argument("--min-roe", type=float, default=10.0, help="ROE 下限(%%)，默认 10（0=不限）")
    parser.add_argument("--max-debt", type=float, default=70.0, help="资产负债率上限(%%)，默认 70（会剔除银行/保险等高杠杆金融股，0=不限）")
    parser.add_argument("--min-div", type=float, default=0.0, help="股息率下限(%%)，默认 0（0=不限）")
    parser.add_argument("--min-growth", type=float, default=0.0, help="净利润增速下限(%%)，默认 0（0=不限）")
    parser.add_argument("--min-rev-growth", type=float, default=0.0, help="营收增速下限(%%)，默认 0（0=不限；百倍股标准：增长须由营收驱动）")
    parser.add_argument("--min-cap", type=float, default=30.0, help="总市值下限(亿)，默认 30")
    parser.add_argument("--max-cap", type=float, default=0.0, help="总市值上限(亿)，默认 0=不限（十倍股研究：小市值起步）")
    parser.add_argument("--min-cash-yield", type=float, default=0.0, help="现金流收益率下限(%%)，默认 0=不限（A 股=每股经营现金流/股价，港美股=FCF/市值）")
    parser.add_argument("--smart-growth", action="store_true", help="启用聪明增长过滤：要求资产增速 < 净利润增速（扩张有效率，仅 A 股有数据）")
    parser.add_argument("--max-price-pos", type=float, default=0.0, help="52 周价格位置上限(0~1)，默认 0=不限；如 0.5=只要区间下半部（左侧低位，逐只拉日 K 较慢）")

    # 猛兽股技术面维度（逐只拉日 K 较慢，与 --max-price-pos 的左侧口径互斥）
    parser.add_argument("--min-price-pos", type=float, default=0.0, help="52 周价格位置下限(0~1)，默认 0=不限；如 0.75=只要区间上四分之一（猛兽股：买强不买弱）")
    parser.add_argument("--trend-filter", action="store_true", help="启用多头趋势结构过滤：收盘 > MA50 且 MA50 > MA200（沿 50 日线上行）")
    parser.add_argument("--rs-filter", action="store_true", help="启用 RS 线过滤：加权相对强度（3/6/9/12 月）跑赢对应市场基准")
    parser.add_argument("--min-updown-vol", type=float, default=0.0, help="近 50 日上涨日均量/下跌日均量下限，默认 0=不限；如 1.2=上涨放量下跌缩量（吸筹特征）")
    parser.add_argument("--market-filter", action="store_true", help="启用大势确认：基准站上 MA50 与 MA200 才筛选（猛兽股必要条件，大势不对不筛）")

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


def _apply_preset(args: argparse.Namespace, argv: list[str]) -> argparse.Namespace:
    """应用预设方案：仅覆盖命令行未显式提供的参数（显式参数 > 预设 > 默认）。"""
    if not args.preset:
        return args
    explicit = {
        a.split("=")[0].lstrip("-").replace("-", "_")
        for a in argv if a.startswith("--")
    }
    for dest, value in PRESETS[args.preset].items():
        if dest not in explicit:
            setattr(args, dest, value)
    return args


def main() -> None:
    args = parse_args_with_config(build_parser())
    args = _apply_preset(args, sys.argv[1:])
    if args.max_price_pos > 0 and args.min_price_pos > 0:
        build_parser().error("--max-price-pos（左侧低位）与 --min-price-pos（右侧高位）互斥，只能启用其一")
    json_stdout, log = init_log(args)

    criteria = ScreenCriteria(
        max_pe=args.max_pe,
        max_pb=args.max_pb,
        min_roe=args.min_roe,
        max_debt=args.max_debt,
        min_div=args.min_div,
        min_growth=args.min_growth,
        min_rev_growth=args.min_rev_growth,
        min_cap=args.min_cap,
        max_cap=args.max_cap,
        min_cash_yield=args.min_cash_yield,
        smart_growth=args.smart_growth,
        max_price_pos=args.max_price_pos,
        min_price_pos=args.min_price_pos,
        trend_filter=args.trend_filter,
        rs_filter=args.rs_filter,
        min_updown_vol=args.min_updown_vol,
        market_filter=args.market_filter,
        use_valuation_pct=args.valuation_pct,
        valuation_lookback=args.valuation_lookback,
    )

    symbols = None
    if args.symbols:
        symbols = split_symbols(args.symbols, min_count=1, what="筛选")

    # 打印筛选条件
    if args.preset:
        log(f"预设方案：{args.preset}（显式参数已覆盖对应预设项）")
    active_dims = _active_dimensions(criteria)
    log(f"筛选条件：{active_dims}")
    if symbols:
        log(f"标的范围：手动列表 {len(symbols)} 只")
    else:
        log("标的范围：A 股全市场（akshare 免费快照）")
    log()

    # 执行筛选
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
    market_info = result.get("market_regime")

    # 大势状态（仅启用 --market-filter 且走 A 股全市场时返回）
    if market_info is not None:
        state = "确认上行" if market_info["uptrend"] else "未确认上行（大势不对不筛）"
        log(
            f"大势状态：{state}  基准收盘 {market_info['close']}"
            f"  MA50 {market_info['ma50']}  MA200 {market_info['ma200']}"
        )

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
            rev_str = f"营收 {item['revenue_growth']:+.0f}%" if item.get("revenue_growth") else ""
            cash_str = f"现金流 {item['cash_yield']:.1f}%" if item.get("cash_yield") else ""
            pos_str = f"52周位置 {item['price_pos']:.0%}" if item.get("price_pos") is not None else ""
            rs_str = f"RS超额 {item['rs_excess']:+.1f}pp" if item.get("rs_excess") is not None else ""
            vol_str = f"量比 {item['updown_vol_ratio']:.2f}" if item.get("updown_vol_ratio") is not None else ""
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
                f"综合 {item['score']:>5.1f}  {pe_str}  {pb_str}  {roe_str}  {div_str}  {growth_str}  {rev_str}  {cash_str}  {pos_str}  {rs_str}  {vol_str}  {val_str}"
            )
    elif market_info is not None and not market_info["uptrend"]:
        log("（本次未筛选：大势未确认上行。《猛兽股》纪律为大势不对不买，等基准重新站上 MA50/MA200 后再筛。）")
    else:
        log("（无达标标的。当前阈值下全市场无满足条件的标的，可放宽阈值重试。）")

    log("\n提示：筛选基于公开财务快照，不构成投资建议。数据为最近报告期，存在滞后。")
    if args.preset == "multibagger":
        log("提示：multibagger 是历史十倍股的统计共性筛选，不是收益预测；"
            "十倍股为极右尾事件（A 股占比约 2%），建议组合持有 20~50 只候选并用移动止损让赢家奔跑。")
    if args.preset == "hundredbagger":
        log("提示：hundredbagger 是迈耶百倍股研究的质量成长筛选，不是收益预测；"
            "书中百倍回报平均需 20~25 年复利，命中靠买对拿住（咖啡罐组合）而非频繁交易；"
            "单期同比增速仅是复合增速的近似，建议接 run_canslim.py 验证盈利持续性。")
    if args.preset == "monster":
        log("提示：monster 是《猛兽股》共同特征的右侧强势筛选，不是收益预测；"
            "买强势股需严格止损纪律（书中：跌破 MA50 放量即退出），"
            "建议接 run_score.py 生成含止损位的交易计划，并用趋势策略回测验证。")
    if criteria.max_debt > 0:
        log(f"提示：负债率<{criteria.max_debt:.0f}% 会剔除银行/保险等高杠杆金融股，纳入请加 --max-debt 0。")
    if not criteria.use_valuation_pct:
        log("提示：低 PE 可能是周期股盈利顶部假象，可加 --valuation-pct 用估值历史分位交叉验证。")
    if args.preset in ("multibagger", "hundredbagger"):
        log_next_steps(
            log,
            "对候选做 CAN SLIM 成长面交叉确认 run_canslim.py --symbols <候选列表>（盈利加速+RS 强度）",
            "候选组合回测（含移动止损） run_portfolio.py --symbols <候选列表>",
        )
    elif args.preset == "monster":
        log_next_steps(
            log,
            "对候选做纪律评分并生成含止损位的交易计划 run_score.py --symbol <代码>（买强势股必须带止损）",
            "趋势策略回测验证 run_backtest.py --symbol <代码> --strategy supertrend（让利润奔跑，跌破趋势退出）",
        )
    else:
        log_next_steps(
            log,
            "对候选标的做纪律评分复核 run_score.py --symbol <代码>（含技术面确认与交易计划）",
            "回测验证候选标的 run_backtest.py --symbol <代码> --strategy ma_cross",
        )

    # JSON 输出
    if args.json is not None:
        top_sym = candidates[0]["symbol"] if candidates else "无"
        if market_info is not None and not market_info["uptrend"]:
            summary = (
                "本次未筛选：大势未确认上行（基准未站上 MA50/MA200）。"
                "猛兽股纪律为大势不对不买，建议等待市场确认上行后重新筛选。"
            )
        else:
            summary = (
                f"扫描 {n_scanned} 只标的：{n_final} 只达标。"
                f"最优候选：{top_sym}。筛选基于基本面快照，非收益预测。"
            )
        if args.preset in ("multibagger", "hundredbagger"):
            next_steps = build_next_steps(
                {"action": "canslim", "reason": "对候选做 CAN SLIM 成长面交叉确认（盈利加速+RS 强度）",
                 "command": "run_canslim.py --symbols <候选列表> --json"},
                {"action": "portfolio", "reason": "候选组合回测，用移动止损让赢家奔跑",
                 "command": "run_portfolio.py --symbols <候选列表> --json"},
            )
        elif args.preset == "monster":
            next_steps = build_next_steps(
                {"action": "score", "reason": "对候选做纪律评分并生成含止损位的交易计划（买强势股必须带止损）",
                 "command": "run_score.py --symbol <代码> --json"},
                {"action": "backtest", "reason": "趋势策略回测验证，让利润奔跑跌破趋势退出",
                 "command": "run_backtest.py --symbol <代码> --strategy supertrend --json"},
            )
        else:
            next_steps = build_next_steps(
                {"action": "score", "reason": "对达标候选做技术面纪律评分复核",
                 "command": "run_score.py --symbol <代码> --json"},
                {"action": "backtest", "reason": "回测验证候选标的策略表现",
                 "command": "run_backtest.py --symbol <代码> --strategy ma_cross --json"},
            )
        payload = attach_meta(
            {
                "criteria": criteria.to_dict(),
                "preset": args.preset,
                "n_scanned": n_scanned,
                "n_phase1": result.get("n_phase1"),
                "n_final": n_final,
                "candidates": candidates,
                **({"market_regime": market_info} if market_info is not None else {}),
                "summary": summary,
                "next_steps": next_steps,
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
    if criteria.min_rev_growth > 0:
        parts.append(f"营收增速>{criteria.min_rev_growth:.0f}%")
    if criteria.min_cap > 0:
        parts.append(f"市值>{criteria.min_cap:.0f}亿")
    if criteria.max_cap > 0:
        parts.append(f"市值<{criteria.max_cap:.0f}亿")
    if criteria.min_cash_yield > 0:
        parts.append(f"现金流收益率>{criteria.min_cash_yield:.0f}%")
    if criteria.smart_growth:
        parts.append("聪明增长(资产增速<利润增速)")
    if criteria.max_price_pos > 0:
        parts.append(f"52周位置<{criteria.max_price_pos:.0%}")
    if criteria.min_price_pos > 0:
        parts.append(f"52周位置>{criteria.min_price_pos:.0%}(买强不买弱)")
    if criteria.trend_filter:
        parts.append("多头结构(收盘>MA50>MA200)")
    if criteria.rs_filter:
        parts.append("RS线跑赢基准")
    if criteria.min_updown_vol > 0:
        parts.append(f"上涨/下跌量比>{criteria.min_updown_vol:.1f}")
    if criteria.market_filter:
        parts.append("大势确认(基准站上MA50/MA200)")
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
