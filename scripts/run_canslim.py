#!/usr/bin/env python3
"""CAN SLIM 检查清单 CLI：欧奈尔七项法则的纪律化核查（单标的详评 / 多标的横截面）。

C 当季EPS增长 / A 年度EPS复合增长 / N 新高 / S 量能供求 / L 相对强度 /
I 机构认同（无免费数据源，诚实标注）/ M 市场方向（否决项）。
A 股自动拉取季度 EPS/ROE（akshare 免 Key）；港美股自动用 yfinance 利润表
兜底（财年口径），离线可用 --fundamentals-csv 注入（列：period_end,eps[,roe]），
缺基本面时结论封顶「观察」。
多标的模式下 L 用横截面 RS 百分位（≥70 通过），更贴近欧奈尔 RS 评分口径。

这是纪律过滤而非收益预测；阈值为原著预设，可用 --c-growth/--a-growth/--roe 本土化。

示例：
    # 单标的七项详评（A 股自动取基本面，免费日 K 即可）
    uv run python run_canslim.py --symbol 600519.SH

    # 多标的横截面：RS 百分位排名 + 通过数排序
    uv run python run_canslim.py --symbols 600519.SH,000858.SZ,600000.SH,300750.SZ

    # 阈值本土化（A 股高增长稀缺，放宽到 15%）+ 结构化输出
    uv run python run_canslim.py --symbol 300750.SZ --c-growth 0.15 --a-growth 0.15 --json

    # 港美股：自动用 yfinance 利润表兜底；离线可用 CSV 注入（period_end,eps[,roe]）
    uv run python run_canslim.py --symbol AAPL.US
    uv run python run_canslim.py --symbol AAPL.US --fundamentals-csv aapl_eps.csv
"""

from __future__ import annotations

import argparse

from cli_common import (
    add_json_arg,
    build_next_steps,
    check_symbol,
    emit_json,
    log_next_steps,
    make_logger,
    make_parser,
    run_cli,
    split_symbols,
)
from cli_config import parse_args_with_config

_STATUS_ICON = {"pass": "✓", "fail": "✗", "unavailable": "—"}


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge CAN SLIM 检查清单（欧奈尔成长股法则）", __doc__)
    parser.add_argument("--symbol", default=None, help="单标的详评（与 --symbols 二选一）")
    parser.add_argument("--symbols", default=None, help="逗号分隔的多标的（横截面 RS 百分位模式）")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=600, help="K 线数量，默认 600（52 周高点与 12 个月 RS 需 ≥260）")
    parser.add_argument("--benchmark", default=None, help="基准代码（L/M 依赖）；默认按市场自动选择（510300.SH/02800.HK/SPY.US）")
    parser.add_argument("--c-growth", type=float, default=0.25, help="C：当季 EPS 同比增速阈值，默认 0.25（欧奈尔原著）")
    parser.add_argument("--a-growth", type=float, default=0.25, help="A：年度 EPS 复合增速阈值，默认 0.25")
    parser.add_argument("--roe", type=float, default=0.17, help="A：ROE 质量注记阈值，默认 0.17")
    parser.add_argument("--no-fundamentals", action="store_true", help="跳过基本面获取（C/A 标注 unavailable，结论封顶「观察」）")
    parser.add_argument("--fundamentals-csv", default=None, help="基本面 CSV 路径（列：period_end,eps[,roe]；仅单标的模式）")
    add_json_arg(parser)
    return parser


def _fetch_benchmark_close(bench_sym: str, period: str, count: int, log):
    """拉取基准收盘价序列；失败降级为 None（stderr 告警）。"""
    import pandas as pd

    from datafeed import fetch_ohlcv

    try:
        df = fetch_ohlcv(bench_sym, period=period, count=count)
        close = df["close"].astype(float).reset_index(drop=True)
        if "trade_date" in df.columns:
            close.index = pd.DatetimeIndex(pd.to_datetime(df["trade_date"]))
        return close
    except Exception as exc:
        log(f"[warn] 基准 {bench_sym} 拉取失败（{type(exc).__name__}），L/M 降级为无基准检查")
        return None


def _resolve_fundamentals(symbol: str, args, log) -> dict | None:
    """按参数优先级解析基本面：--fundamentals-csv > 自动获取 > None。"""
    from canslim import fetch_fundamentals, is_a_share, load_fundamentals_csv
    from canslim.fundamentals import YF_SUFFIXES

    if args.no_fundamentals:
        return None
    if args.fundamentals_csv:
        return load_fundamentals_csv(args.fundamentals_csv)
    if is_a_share(symbol):
        log(f"拉取 {symbol} 季度 EPS/ROE（akshare 财务摘要）...")
        return fetch_fundamentals(symbol)
    if symbol.upper().endswith(YF_SUFFIXES):
        log(f"拉取 {symbol} EPS（yfinance 利润表，财年口径）...")
        return fetch_fundamentals(symbol)
    log(f"[warn] {symbol} 基本面自动获取不支持该市场；可用 --fundamentals-csv 注入")
    return None


def _print_result(res, log) -> None:
    """单标的七项详评的终端输出。"""
    log(f"\n===== CAN SLIM 检查：{res.symbol}（截至 {res.asof}）=====")
    for c in res.checks:
        icon = _STATUS_ICON[c["status"]]
        log(f"  [{icon}] {c['letter']}  {c['name']}")
        for reason in c["reasons"]:
            log(f"        {reason}")
    log(f"\n结论：{res.verdict_cn}（通过 {res.passed} / 失败 {res.failed} / 不可评 {res.unavailable}）")
    for note in res.notes:
        log(f"  · {note}")


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout = args.json == "-"
    log = make_logger(json_stdout)

    if bool(args.symbol) == bool(args.symbols):
        raise SystemExit("[error] 需要 --symbol（单标的详评）或 --symbols（多标的横截面）之一。")
    if args.fundamentals_csv and args.symbols:
        raise SystemExit("[error] --fundamentals-csv 仅支持单标的模式（多标的请依赖 A 股自动获取）。")

    from datafeed import fetch_ohlcv
    from canslim import canslim_check, rs_weighted_return
    from scoring import default_benchmark

    if args.symbol:
        results = [_run_single(args, log, fetch_ohlcv, canslim_check, default_benchmark)]
    else:
        results = _run_cross_section(args, log, fetch_ohlcv, canslim_check, rs_weighted_return, default_benchmark)

    log("\n提示：CAN SLIM 是纪律检查清单而非收益预测，阈值未经 A 股样本外验证，不构成投资建议。")
    log_next_steps(
        log,
        "对结论「是」的标的做四层纪律评分复核 run_score.py --symbol <代码>（含交易计划与仓位）",
        "回测动量/突破族策略验证价格趋势 run_backtest.py --symbol <代码> --strategy donchian",
    )

    if args.json is not None:
        from report import attach_meta

        n_yes = sum(1 for r in results if r.verdict == "yes")
        n_watch = sum(1 for r in results if r.verdict == "watch")
        top = max(results, key=lambda r: (r.passed, r.rs_raw or -9.9))
        payload = attach_meta(
            {
                "mode": "single" if args.symbol else "cross_section",
                "thresholds": {"c_growth": args.c_growth, "a_growth": args.a_growth, "roe": args.roe},
                "results": [r.to_dict() for r in results],
                "summary": (
                    f"CAN SLIM 检查 {len(results)} 只标的：{n_yes} 只「是」、{n_watch} 只「观察」。"
                    f"最优：{top.symbol}（通过 {top.passed}/7）。这是纪律清单核查，不是收益预测。"
                ),
                "next_steps": build_next_steps(
                    {"action": "score", "reason": "对达标标的做四层纪律评分复核（交易计划+仓位）",
                     "command": "run_score.py --symbol <代码> --json"},
                    {"action": "backtest", "reason": "用突破/动量族策略回测验证价格趋势",
                     "command": "run_backtest.py --symbol <代码> --strategy donchian --json"},
                ),
            },
            command="canslim",
        )
        emit_json(args.json, payload, log)


def _run_single(args, log, fetch_ohlcv, canslim_check, default_benchmark):
    """单标的详评：自动基准 + 基本面获取 + 七项输出。"""
    symbol = check_symbol(args.symbol)
    log(f"拉取 {symbol} K 线（period={args.period}, count={args.count}）...")
    df = fetch_ohlcv(symbol, period=args.period, count=args.count)

    bench_sym = args.benchmark or default_benchmark(symbol)
    bench_close = _fetch_benchmark_close(bench_sym, args.period, args.count, log) if bench_sym else None

    fundamentals = _resolve_fundamentals(symbol, args, log)
    res = canslim_check(
        df,
        symbol=symbol,
        benchmark_close=bench_close,
        fundamentals=fundamentals,
        c_growth=args.c_growth,
        a_growth=args.a_growth,
        roe_min=args.roe,
    )
    _print_result(res, log)
    return res


def _run_cross_section(args, log, fetch_ohlcv, canslim_check, rs_weighted_return, default_benchmark):
    """多标的横截面：先算全体 RS 原始值取百分位，再逐个七项检查。"""
    import pandas as pd

    symbols = split_symbols(args.symbols, min_count=2, what="横截面检查")
    log(f"横截面检查 {len(symbols)} 个标的（period={args.period}, count={args.count}）...")

    frames: dict[str, pd.DataFrame] = {}
    skipped: list[str] = []
    for sym in symbols:
        try:
            frames[sym] = fetch_ohlcv(sym, period=args.period, count=args.count)
        except Exception as exc:
            skipped.append(sym)
            log(f"[warn] {sym} 拉取失败（{type(exc).__name__}），跳过")

    # 横截面 RS 百分位（中位秩），对齐欧奈尔「RS 评分」口径
    rs_map = {
        sym: rs_weighted_return(df["close"].astype(float).reset_index(drop=True))
        for sym, df in frames.items()
    }
    valid_rs = pd.Series({s: v for s, v in rs_map.items() if v is not None})

    bench_cache: dict = {}

    def get_bench(sym: str):
        bsym = args.benchmark or default_benchmark(sym)
        if not bsym:
            return None
        if bsym not in bench_cache:
            bench_cache[bsym] = _fetch_benchmark_close(bsym, args.period, args.count, log)
        return bench_cache[bsym]

    results = []
    for sym, df in frames.items():
        pct = None
        if rs_map[sym] is not None and len(valid_rs) >= 2:
            pct = float(
                ((valid_rs < rs_map[sym]).mean() + 0.5 * (valid_rs == rs_map[sym]).mean())
            )
        fundamentals = None
        if not args.no_fundamentals:
            from canslim import fetch_fundamentals

            # 自动适配市场：A 股 akshare，港美股 yfinance，其余返回 None
            fundamentals = fetch_fundamentals(sym)
        results.append(
            canslim_check(
                df,
                symbol=sym,
                benchmark_close=get_bench(sym),
                fundamentals=fundamentals,
                rs_percentile=pct,
                c_growth=args.c_growth,
                a_growth=args.a_growth,
                roe_min=args.roe,
            )
        )
    if not results:
        raise SystemExit("[error] 所有标的均拉取失败，无法横截面检查。")

    results.sort(key=lambda r: (r.passed, r.rs_raw or -9.9), reverse=True)
    log(f"\n===== CAN SLIM 横截面排名（{len(results)} 只，按通过数/RS 排序）=====")
    for i, r in enumerate(results, 1):
        letters = " ".join(f"{c['letter']}{_STATUS_ICON[c['status']]}" for c in r.checks)
        pct = r.snapshot.get("rs_percentile")
        pct_str = f"RS分位 {pct * 100:.0f}" if pct is not None else "RS分位 N/A"
        log(f"{i:>3}. {r.symbol:<12} {r.verdict_cn:<4} 通过 {r.passed}/7  {pct_str}  {letters}")
    fail_lines = [
        (r.symbol, c) for r in results for c in r.checks if c["status"] == "fail"
    ]
    if fail_lines:
        log("\n主要失败原因（首条）：")
        seen = set()
        for sym, c in fail_lines:
            if sym in seen:
                continue
            seen.add(sym)
            log(f"  {sym:<12} {c['letter']}：{c['reasons'][0]}")
    if skipped:
        log(f"\n跳过 {len(skipped)} 个拉取失败标的：{', '.join(skipped)}")
    return results


if __name__ == "__main__":
    run_cli(main)
